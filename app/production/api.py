"""Production API and same-origin workspace."""

from contextlib import asynccontextmanager
import csv
from dataclasses import dataclass, field
import hashlib
import hmac
from io import StringIO
import os
from pathlib import Path
import re
import secrets
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore, Lock, Thread
import time
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import Field, SecretStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

import httpx

from . import (
    agent,
    adapters,
    codex_cli,
    credentials,
    delivery,
    review_notes,
    editorial,
    experiments,
    media,
    project_profiles,
    thumbnails,
    youtube,
    models as m,
)
from .codex_login import DeviceLogin
from .store import RuleError, encode
from .workbench import Workbench, _deployment_draining
from .worker import Worker


WEB = Path(__file__).with_name("web")
COOKIE = "mpt_production_session"
AGENT_WRITES = re.compile(
    r"/api/agent/v1/projects/[a-f0-9]{32}/proposals(?:/validate)?"
)
MAX_JSON = 1024 * 1024
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PASSWORD_ITERATIONS = 600_000


class Login(m.Input):
    token: str | None = Field(default=None, max_length=256)
    username: str | None = Field(default=None, max_length=80)
    password: SecretStr | None = Field(default=None, max_length=256)


@dataclass(frozen=True)
class PublicLogin:
    origin: str
    username: str
    actor: str
    password_hash: str = field(repr=False)
    hostname: str = field(init=False)
    salt: bytes = field(init=False, repr=False)
    digest: bytes = field(init=False, repr=False)

    def __post_init__(self):
        host = re.fullmatch(r"https://([a-z0-9-]+(?:\.[a-z0-9-]+)+)", self.origin)
        password_hash = re.fullmatch(
            rf"pbkdf2_sha256\${PASSWORD_ITERATIONS}\$([0-9a-f]{{32}})\$([0-9a-f]{{64}})",
            self.password_hash,
        )
        if not host or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", self.username):
            raise ValueError("Configure a public HTTPS origin and an operator username")
        if not password_hash:
            raise ValueError("Configure a valid PBKDF2-SHA256 operator password hash")
        object.__setattr__(self, "hostname", host[1])
        object.__setattr__(self, "salt", bytes.fromhex(password_hash[1]))
        object.__setattr__(self, "digest", bytes.fromhex(password_hash[2]))

    def authenticate(self, username: str, password: str) -> str | None:
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), self.salt, PASSWORD_ITERATIONS
        )
        username_matches = hmac.compare_digest(
            username.encode(), self.username.encode()
        )
        password_matches = hmac.compare_digest(digest, self.digest)
        return self.actor if username_matches and password_matches else None


class BulkReview(m.ReviewInput):
    versions: list[m.Id]


class ProviderKey(m.Input):
    key: SecretStr = Field(min_length=12, max_length=240)


class RateLimit:
    def __init__(self):
        self.windows = {}
        self.lock = Lock()
        self.next_prune = 0

    def hit(self, key, limit, window):
        with self.lock:
            now = time.monotonic()
            if now >= self.next_prune:
                self.windows = {
                    key: value for key, value in self.windows.items() if value[0] > now
                }
                self.next_prune = now + 60
            reset, used = self.windows.get(key, (0, 0))
            if reset <= now:
                reset, used = now + window, 0
            if used >= limit:
                return False
            self.windows[key] = (reset, used + 1)
            return True


def create_app(
    directory,
    tokens,
    *,
    worker_enabled=True,
    allowed_hosts=("localhost", "127.0.0.1", "[::1]"),
    public_login: PublicLogin | None = None,
    youtube_settings: youtube.Settings | None = None,
    youtube_transport=None,
):
    if not tokens or any(
        len(token) < 32 or not token.isascii() or not actor
        for token, actor in tokens.items()
    ):
        raise ValueError(
            "Configure a unique production access token with at least 32 ASCII characters"
        )
    store = Workbench(Path(directory))
    project_agents = agent.AgentProjects(store)
    deliveries = delivery.Delivery(store)
    notes = review_notes.ReviewNotes(store)
    worker = Worker(store)
    setup_owner = public_login.actor if public_login else (next(iter(tokens.values())) if len(set(tokens.values())) == 1 else None)
    setup_key = next((token for token, owner in tokens.items() if owner == setup_owner), None)
    yt = youtube.YouTube(store, youtube_settings, transport=youtube_transport, setup_key=setup_key)
    yt.worker = Thread(target=yt.loop, args=(_deployment_draining,), daemon=True, name="mpt-youtube")
    sessions = {}
    sessions_lock = Lock()
    limiter = RateLimit()
    thumbnail_render = BoundedSemaphore()
    codex_login = DeviceLogin() if public_login else None

    @asynccontextmanager
    async def lifespan(_app):
        if worker_enabled:
            worker.thread.start()
            yt.worker.start()
        try:
            yield
        finally:
            worker.stop.set()
            yt.stop.set()
            if codex_login:
                await codex_login.cancel()
            if worker.thread.is_alive():
                await run_in_threadpool(worker.thread.join, 2)
            if yt.worker.is_alive():
                await run_in_threadpool(yt.worker.join, 2)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.youtube = yt
    hosts = list(allowed_hosts)
    if public_login:
        hosts.append(public_login.hostname)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    def is_public(request):
        return (
            public_login is not None and request.url.hostname == public_login.hostname
        )

    @app.middleware("http")
    async def boundary(request, call_next):
        public = is_public(request)
        if public and request.url.scheme != "https":
            return JSONResponse({"detail": "HTTPS required"}, status_code=403)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            expected_origin = (
                public_login.origin
                if public
                else f"http://{request.headers.get('host', '')}"
            )
            agent_cli = (
                not origin
                and request.method == "POST"
                and AGENT_WRITES.fullmatch(request.url.path)
                and agent.bearer_digest(request.headers.get("authorization"))
            )
            if (public or origin) and origin != expected_origin and not agent_cli:
                return JSONResponse({"detail": "Origin forbidden"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Origin forbidden"}, status_code=403)
            if not request.url.path.endswith("/uploads"):
                content_length = request.headers.get("content-length")
                if content_length and (
                    not content_length.isascii()
                    or not content_length.isdecimal()
                    or len(content_length) > 10
                    or int(content_length) > MAX_JSON
                ):
                    return JSONResponse(
                        {"detail": "Request exceeds byte limit"}, status_code=413
                    )
                chunks = bytearray()
                async for chunk in request.stream():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_JSON:
                        return JSONResponse(
                            {"detail": "Request exceeds byte limit"}, status_code=413
                        )
                request._body = bytes(chunks)
        range_header = request.headers.get("range")
        if range_header and not re.fullmatch(
            r"bytes=(?:\d{1,12}-\d{0,12}|-\d{1,12})", range_header
        ):
            return JSONResponse(
                {"detail": "Invalid or multiple media ranges"}, status_code=416
            )
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; img-src 'self'; media-src 'self'; "
                "script-src 'self'; style-src 'self'; frame-ancestors 'none'; "
                "object-src 'none'; base-uri 'none'; form-action 'self'",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
            }
        )
        return response

    @app.exception_handler(RuleError)
    async def rejected(request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.exception_handler(youtube.GoogleError)
    async def youtube_rejected(request, error):
        reason = error.reason
        detail = "YouTube did not complete the request"
        if re.fullmatch(r"[A-Za-z0-9_]{1,80}", reason):
            detail += f" ({reason})"
        return JSONResponse({"detail": detail}, status_code=502)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def invalid(request, error):
        locations = [
            ".".join(str(part) for part in item["loc"]) for item in error.errors()[:5]
        ]
        return JSONResponse(
            {"detail": f"Check request fields: {', '.join(locations)}"}, status_code=422
        )

    def token_actor(token):
        if not token or len(token) > 256 or not token.isascii():
            return None
        for candidate, actor in tokens.items():
            if hmac.compare_digest(token, candidate):
                return actor
        return None

    def session_actor(request):
        with sessions_lock:
            session = sessions.get(request.cookies.get(COOKIE))
        if session:
            actor, expires_at, public = session
            if expires_at >= time.monotonic() and public == is_public(request):
                return actor
        return None

    def authenticated(request: Request):
        header = request.headers.get("authorization")
        if header:
            actor = (
                token_actor(header.removeprefix("Bearer "))
                if header.startswith("Bearer ") and not is_public(request)
                else None
            )
        else:
            actor = session_actor(request)
            if (
                actor
                and request.method not in SAFE_METHODS
                and not request.headers.get("origin")
            ):
                raise HTTPException(403, "Same-origin request required")
        if not actor:
            raise HTTPException(401, "Production authentication required")
        limit = 240 if request.method in {"GET", "HEAD"} else 90
        if not limiter.hit((actor, request.method in {"GET", "HEAD"}), limit, 60):
            raise HTTPException(429, "Request rate exceeded")
        return actor

    def agent_credential(request: Request):
        digest = agent.bearer_digest(request.headers.get("authorization"))
        if not digest:
            raise HTTPException(401, "Agent authentication required")
        reading = request.method in {"GET", "HEAD"}
        if not limiter.hit(
            ("agent-ip", request.client.host, reading), 480 if reading else 180, 60
        ):
            raise HTTPException(429, "Agent request rate exceeded")
        if not limiter.hit(("agent", digest, reading), 240 if reading else 30, 60):
            raise HTTPException(429, "Agent request rate exceeded")
        return digest

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/youtube-privacy", include_in_schema=False)
    def youtube_privacy():
        return FileResponse(WEB / "youtube-privacy.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get("/api/health")
    def health(request: Request):
        if is_public(request):
            raise HTTPException(404, "Not found")
        # Serialize with job admission so deploys can see the final claim before draining.
        with store.transaction() as db:
            active = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='GENERATING'"
            ).fetchone()[0]
            active += db.execute(
                """SELECT COUNT(*) FROM youtube_publications WHERE state='UPLOADING'
                OR (state='VISIBILITY_PENDING' AND EXISTS (SELECT 1 FROM youtube_events e
                    WHERE e.project=youtube_publications.project AND e.export=youtube_publications.export
                    AND e.event IN ('public_visibility_authorized','scheduled_visibility_authorized') AND e.created>?))""",
                (time.time() - 180,),
            ).fetchone()[0]
        ready = not worker_enabled or (worker.thread.is_alive() and yt.worker.is_alive())
        return JSONResponse(
            {
                "status": "ready" if ready else "starting",
                "revision": os.getenv("MPT_PRODUCTION_RELEASE", ""),
                "active_jobs": active,
            },
            status_code=200 if ready else 503,
        )

    @app.get("/ui/{name}", include_in_schema=False)
    def static(name: Literal["workspace.js", "workspace.css"]):
        return FileResponse(WEB / name)

    @app.get("/api/session")
    def session(request: Request):
        state = {"authenticated": bool(session_actor(request))}
        if is_public(request):
            state["login_method"] = "password"
        return state

    @app.post("/api/session")
    def login(payload: Login, request: Request, response: Response):
        public = is_public(request)
        if not limiter.hit(("login", public, request.client.host), 10, 60):
            raise HTTPException(429, "Try signing in again in a minute")
        if public:
            if not limiter.hit(("public-login",), 120, 60):
                raise HTTPException(429, "Try signing in again in a minute")
            actor = public_login.authenticate(
                payload.username or "",
                payload.password.get_secret_value() if payload.password else "",
            )
            if not actor:
                raise HTTPException(401, "Invalid username or password")
        else:
            actor = token_actor(payload.token)
            if not actor:
                raise HTTPException(401, "Invalid production access token")
        session = secrets.token_urlsafe(32)
        with sessions_lock:
            now = time.monotonic()
            for expired in [key for key, (_, end, _) in sessions.items() if end < now]:
                del sessions[expired]
            sessions[session] = (actor, now + 12 * 60 * 60, public)
        response.set_cookie(
            COOKIE,
            session,
            httponly=True,
            secure=public,
            samesite="strict",
            max_age=43200,
        )
        return {"actor": actor}

    @app.delete("/api/session")
    def logout(request: Request, response: Response, actor=Depends(authenticated)):
        with sessions_lock:
            sessions.pop(request.cookies.get(COOKIE), None)
        response.delete_cookie(
            COOKIE, httponly=True, secure=is_public(request), samesite="strict"
        )
        return {"ok": True}

    @app.get("/api/projects")
    def projects(actor=Depends(authenticated)):
        return store.snapshot(actor)

    @app.get("/api/catalog")
    def editorial_catalog(actor=Depends(authenticated)):
        return editorial.catalog(store, actor)

    @app.put("/api/catalog")
    def save_editorial_catalog(body: editorial.Documents, actor=Depends(authenticated)):
        editorial.upload(store, actor, body)
        return {"ok": True}

    @app.get("/api/catalog/files/{name}")
    def editorial_file(
        name: Literal["catalog", "prompts", "plan"], actor=Depends(authenticated)
    ):
        return Response(
            editorial.document(store, actor, name),
            media_type="text/plain",
            headers={
                "Content-Disposition": f'attachment; filename="{editorial.DOCUMENTS[name]}"'
            },
        )

    @app.post("/api/catalog/episodes/{episode_id}/project")
    def editorial_project(
        episode_id: editorial.EpisodeId,
        body: editorial.ProjectOptions | None = None,
        actor=Depends(authenticated),
    ):
        return {
            "id": editorial.create_project(
                store, actor, episode_id, body or editorial.ProjectOptions()
            )
        }

    @app.get("/api/project-profiles/hyphotenic")
    def hyphotenic_profile(actor=Depends(authenticated)):
        return project_profiles.hyphotenic()

    @app.get("/api/project-profiles/hyphotenic/logo")
    def hyphotenic_logo(actor=Depends(authenticated)):
        return FileResponse(
            project_profiles.DIRECTORY / "hyphotenic-y.png", media_type="image/png"
        )

    @app.post("/api/projects", status_code=201)
    def create(
        body: m.Project | project_profiles.HyphotenicProject,
        actor=Depends(authenticated),
    ):
        if isinstance(body, project_profiles.HyphotenicProject):
            return {"id": project_profiles.create_hyphotenic(store, actor, body)}
        return {"id": store.create(actor, body.model_dump())}

    @app.get("/api/projects/{pid}")
    def project(pid: m.Id, actor=Depends(authenticated)):
        return store.snapshot(actor, pid)

    @app.get("/api/youtube")
    def youtube_status(actor=Depends(authenticated)):
        return yt.status(actor) | {"can_setup": bool(actor == setup_owner and setup_key and not yt.from_env)}

    @app.post("/api/youtube/setup")
    def youtube_setup(body: youtube.Setup, request: Request, actor=Depends(authenticated)):
        if actor != setup_owner or not setup_key or session_actor(request) != actor:
            raise HTTPException(403, "Use the owner workspace browser session")
        origin = public_login.origin if is_public(request) else f"http://{request.headers['host']}"
        yt.configure(body, origin)
        return youtube_status(actor)

    @app.post("/api/youtube/connect")
    def youtube_connect(body: youtube.Connect, request: Request, actor=Depends(authenticated)):
        browser = request.cookies.get(COOKIE)
        if not browser or session_actor(request) != actor:
            raise HTTPException(401, "Use an authenticated workspace browser session")
        origin = public_login.origin if is_public(request) else f"http://{request.headers['host']}"
        return {"url": yt.begin(actor, browser, body.mode, origin)}

    @app.get(youtube.CALLBACK, include_in_schema=False)
    def youtube_callback(
        request: Request,
        state: str = Query(max_length=100),
        code: str = Query(default="", max_length=2048),
        error: str = Query(default="", max_length=150),
    ):
        target = "/?view=channel&youtube="
        try:
            _, browser, actor, verifier, scopes = yt.consume_state(state)
            with sessions_lock:
                session = sessions.get(browser)
            if (
                not session or session[0] != actor or session[1] < time.monotonic()
                or session[2] != is_public(request)
            ):
                return RedirectResponse(target + "expired", status_code=303)
            if error or not code:
                return RedirectResponse(target + "cancelled", status_code=303)
            yt.connect(actor, verifier, scopes, code)
        except RuleError as exc:
            return RedirectResponse(target + ("mismatch" if exc.status == 409 else "error"), status_code=303)
        except (httpx.HTTPError, youtube.GoogleError, KeyError, ValueError):
            return RedirectResponse(target + "error", status_code=303)
        return RedirectResponse(target + "connected", status_code=303)

    @app.delete("/api/youtube")
    def youtube_disconnect(actor=Depends(authenticated)):
        return yt.disconnect(actor)

    @app.post("/api/youtube/reach")
    def youtube_enable_reach(body: youtube.Consent, actor=Depends(authenticated)):
        return yt.enable_reach(actor)

    @app.get("/api/projects/{pid}/youtube/{version}")
    def youtube_publication(pid: m.Id, version: m.Id, actor=Depends(authenticated)):
        return yt.snapshot(actor, pid, version)

    @app.post("/api/projects/{pid}/youtube/{version}/upload", status_code=202)
    def youtube_upload(pid: m.Id, version: m.Id, body: youtube.Upload, actor=Depends(authenticated)):
        return yt.queue(actor, pid, version, body)

    @app.post("/api/projects/{pid}/youtube/{version}/resume", status_code=202)
    def youtube_resume(pid: m.Id, version: m.Id, actor=Depends(authenticated)):
        return yt.resume(actor, pid, version)

    @app.post("/api/projects/{pid}/youtube/{version}/cancel")
    def youtube_cancel(pid: m.Id, version: m.Id, actor=Depends(authenticated)):
        return yt.cancel(actor, pid, version)

    @app.post("/api/projects/{pid}/youtube/{version}/link")
    def youtube_link(pid: m.Id, version: m.Id, body: youtube.Link, actor=Depends(authenticated)):
        return yt.link(actor, pid, version, body)

    @app.post("/api/projects/{pid}/youtube/{version}/refresh")
    def youtube_refresh(pid: m.Id, version: m.Id, actor=Depends(authenticated)):
        return yt.refresh(actor, pid, version)

    @app.post("/api/projects/{pid}/youtube/{version}/publish")
    def youtube_publish(pid: m.Id, version: m.Id, body: youtube.Visibility, actor=Depends(authenticated)):
        return yt.publish(actor, pid, version, body)

    @app.post("/api/projects/{pid}/youtube/{version}/assets")
    def youtube_assets(pid: m.Id, version: m.Id, body: youtube.Assets, actor=Depends(authenticated)):
        return yt.upload_assets(actor, pid, version, body)

    @app.post("/api/projects/{pid}/youtube/{version}/metrics/{days}")
    def youtube_metrics(pid: m.Id, version: m.Id, days: int, actor=Depends(authenticated)):
        if days not in (2, 7, 28):
            raise HTTPException(422, "Use observation day 2, 7 or 28")
        return yt.sync(actor, pid, version, days)

    @app.post("/api/projects/{pid}/youtube/{version}/reach/{days}")
    def youtube_reach(pid: m.Id, version: m.Id, days: int, actor=Depends(authenticated)):
        if days not in (2, 7, 28):
            raise HTTPException(422, "Use observation day 2, 7 or 28")
        return yt.sync_reach(actor, pid, version, days)

    @app.get("/api/projects/{pid}/experiments/{version}")
    def content_experiment(pid: m.Id, version: m.Id, actor=Depends(authenticated)):
        return experiments.snapshot(store, actor, pid, version)

    @app.put("/api/projects/{pid}/experiments/{version}")
    def save_content_experiment(
        pid: m.Id, version: m.Id, body: experiments.Plan, actor=Depends(authenticated)
    ):
        return experiments.save_plan(store, actor, pid, version, body)

    @app.post("/api/projects/{pid}/experiments/{version}/observations")
    def record_content_observation(
        pid: m.Id, version: m.Id, body: experiments.Observation, actor=Depends(authenticated)
    ):
        return experiments.save_observation(store, actor, pid, version, body)

    @app.put("/api/projects/{pid}/style")
    def style(pid: m.Id, body: m.Style, actor=Depends(authenticated)):
        with store.transaction() as db:
            project = store.project(db, actor, pid)
            data = dict(project["data"], style=body.model_dump())
            db.execute("UPDATE projects SET data=? WHERE id=?", (encode(data), pid))
            return {"style": data["style"]}

    @app.put("/api/projects/{pid}/defaults")
    def defaults(pid: m.Id, body: m.ProviderDefaults, actor=Depends(authenticated)):
        with store.transaction() as db:
            project = store.project(db, actor, pid)
            data = dict(project["data"], defaults=body.model_dump())
            db.execute("UPDATE projects SET data=? WHERE id=?", (encode(data), pid))
            return {"defaults": data["defaults"]}

    @app.post("/api/projects/{pid}/research")
    def research(pid: m.Id, body: m.Research, actor=Depends(authenticated)):
        with store.transaction() as db:
            store.project(db, actor, pid)
            return store.add(db, pid, "research", "research", body.model_dump())

    @app.post("/api/projects/{pid}/script")
    def script(pid: m.Id, body: m.Script, actor=Depends(authenticated)):
        with store.transaction() as db:
            store.project(db, actor, pid)
            return store.script(
                db, pid, body.model_dump(), store.require(db, pid, "research")
            )

    @app.post("/api/projects/{pid}/storyboard")
    def storyboard(pid: m.Id, actor=Depends(authenticated)):
        return {"scenes": store.storyboard(actor, pid)}

    @app.put("/api/projects/{pid}/scenes/{sid}")
    def scene(pid: m.Id, sid: m.Id, body: m.Scene, actor=Depends(authenticated)):
        return store.edit_scene(actor, pid, sid, body)

    @app.put("/api/projects/{pid}/order")
    def reorder(pid: m.Id, body: m.Move, actor=Depends(authenticated)):
        store.reorder(actor, pid, body.scenes)
        return {"scenes": body.scenes}

    @app.post("/api/projects/{pid}/scenes/{sid}/split")
    def split(pid: m.Id, sid: m.Id, body: m.Split, actor=Depends(authenticated)):
        return {"scenes": store.restructure(actor, pid, [sid], body)}

    @app.post("/api/projects/{pid}/scenes/merge")
    def merge(pid: m.Id, body: m.Merge, actor=Depends(authenticated)):
        return {"scenes": store.restructure(actor, pid, body.scenes)}

    @app.post("/api/projects/{pid}/scenes/{sid}/translation/{language}")
    def translation(
        pid: m.Id,
        sid: m.Id,
        language: m.Language,
        body: m.Translation,
        actor=Depends(authenticated),
    ):
        with store.transaction() as db:
            project = store.project(db, actor, pid)["data"]
            if (
                sid not in store.order(db, pid)
                or language not in project["languages"][1:]
            ):
                raise RuleError("Scene translation language not found", 404)
            scene_asset = store.require(db, pid, f"scene/{sid}")
            return store.add(
                db,
                pid,
                f"translation/{sid}/{language}",
                "translation",
                body.model_dump(),
                [{"speech": [sid, scene_asset["data"]["narration"]]}],
            )

    @app.get("/api/projects/{pid}/timeline")
    def timeline(
        pid: m.Id,
        response: Response,
        revision: m.Id | None = None,
        actor=Depends(authenticated),
    ):
        with store.transaction(write=False) as db:
            store.project(db, actor, pid)
            item = (
                store.asset(db, pid, revision)
                if revision
                else store.head(db, pid, "timeline")
            )
            if not item or item["slot"] != "timeline":
                raise RuleError("Timeline revision not found", 404)
            response.headers["ETag"] = '"' + item["id"] + '"'
            return item

    @app.put("/api/projects/{pid}/timeline")
    def save_timeline(
        pid: m.Id,
        body: m.Timeline,
        request: Request,
        response: Response,
        actor=Depends(authenticated),
    ):
        with store.transaction() as db:
            project = store.project(db, actor, pid)
            languages = set(body.languages) | {
                lang for clip in body.clips for lang in clip.captions
            }
            if {clip.scene for clip in body.clips} != set(
                store.order(db, pid)
            ) or not languages <= set(project["data"]["languages"]):
                raise RuleError(
                    "Timeline must match the active scenes and project languages"
                )
            data = body.model_dump()
            current = store.head(db, pid, "timeline")
            if current and current["data"] == data:
                item = current
            else:
                etag = '"' + current["id"] + '"' if current else None
                if (
                    "if-match" in request.headers
                    and request.headers["if-match"] != etag
                    or request.headers.get("if-none-match") == "*"
                    and current
                ):
                    raise RuleError(
                        "The saved timeline changed. Reload it before saving.", 412
                    )
                item = store.add(db, pid, "timeline", "timeline", data)
            response.headers["ETag"] = '"' + item["id"] + '"'
            return item

    @app.post("/api/projects/{pid}/reviews")
    def review(pid: m.Id, body: BulkReview, actor=Depends(authenticated)):
        if not 1 <= len(body.versions) <= 100:
            raise RuleError("Review 1 to 100 versions", 422)
        return store.review(actor, pid, body.versions, body)

    @app.get("/api/projects/{pid}/delivery")
    def delivery_review(pid: m.Id, actor=Depends(authenticated)):
        return deliveries.snapshot(actor, pid)

    @app.post("/api/projects/{pid}/delivery/materials")
    def approve_delivery_materials(
        pid: m.Id, body: delivery.MaterialReview, actor=Depends(authenticated)
    ):
        return deliveries.approve(actor, pid, body)

    @app.post("/api/projects/{pid}/delivery/prepare")
    def prepare_delivery(
        pid: m.Id, body: delivery.Preparation, actor=Depends(authenticated)
    ):
        return deliveries.prepare(actor, pid, body)

    @app.get("/api/projects/{pid}/assemblies/{version}/notes")
    def list_review_notes(pid: m.Id, version: m.Id, actor=Depends(authenticated)):
        return notes.list(actor, pid, version)

    @app.post("/api/projects/{pid}/assemblies/{version}/notes")
    def add_review_note(
        pid: m.Id, version: m.Id, body: review_notes.Note, actor=Depends(authenticated)
    ):
        return notes.create(actor, pid, version, body)

    @app.get("/api/projects/{pid}/assemblies/{version}/notes/{note}")
    def review_note_history(
        pid: m.Id, version: m.Id, note: m.Id, actor=Depends(authenticated)
    ):
        return notes.history(actor, pid, version, note)

    @app.put("/api/projects/{pid}/assemblies/{version}/notes/{note}/state")
    def update_review_note(
        pid: m.Id,
        version: m.Id,
        note: m.Id,
        body: review_notes.NoteState,
        actor=Depends(authenticated),
    ):
        return notes.change_state(actor, pid, version, note, body)

    @app.get("/api/projects/{pid}/agent-access")
    def list_agent_access(pid: m.Id, actor=Depends(authenticated)):
        return project_agents.access(actor, pid)

    @app.post("/api/projects/{pid}/agent-access", status_code=201)
    def issue_agent_access(pid: m.Id, body: agent.Access, actor=Depends(authenticated)):
        return project_agents.issue(actor, pid, body)

    @app.delete("/api/projects/{pid}/agent-access/{key}")
    def revoke_agent_access(pid: m.Id, key: m.Id, actor=Depends(authenticated)):
        project_agents.revoke(actor, pid, key)
        return {"ok": True}

    @app.get("/api/projects/{pid}/proposals")
    def review_proposals(pid: m.Id, actor=Depends(authenticated)):
        return project_agents.proposals(actor, pid)

    @app.get("/api/projects/{pid}/proposals/{proposal}")
    def review_proposal(pid: m.Id, proposal: m.Id, actor=Depends(authenticated)):
        return project_agents.proposals(actor, pid, proposal)

    @app.post("/api/projects/{pid}/proposals/{proposal}/decision")
    def decide_proposal(
        pid: m.Id, proposal: m.Id, body: agent.Decision, actor=Depends(authenticated)
    ):
        return project_agents.decide(actor, pid, proposal, body)

    @app.get("/api/agent/v1/projects/{pid}")
    def agent_context(pid: m.Id, digest=Depends(agent_credential)):
        return project_agents.context(pid, digest)

    @app.get("/api/agent/v1/projects/{pid}/assemblies/{version}/notes")
    def list_agent_review_notes(
        pid: m.Id, version: m.Id, digest=Depends(agent_credential)
    ):
        return project_agents.notes(pid, digest, version)

    @app.post("/api/agent/v1/projects/{pid}/proposals/validate")
    def validate_proposal(
        pid: m.Id, body: agent.EditPlan, digest=Depends(agent_credential)
    ):
        return project_agents.validate(pid, digest, body)

    @app.post("/api/agent/v1/projects/{pid}/proposals")
    def submit_proposal(
        pid: m.Id, body: agent.Proposal, digest=Depends(agent_credential)
    ):
        return project_agents.submit(pid, digest, body)

    @app.get("/api/agent/v1/projects/{pid}/proposals/{proposal}")
    def proposal_receipt(pid: m.Id, proposal: m.Id, digest=Depends(agent_credential)):
        return project_agents.receipt(pid, digest, proposal)

    @app.post("/api/projects/{pid}/thumbnails", status_code=201)
    def generate_thumbnails(
        pid: m.Id, body: thumbnails.Batch, actor=Depends(authenticated)
    ):
        if not thumbnail_render.acquire(blocking=False):
            raise HTTPException(
                429, "A thumbnail batch is rendering. Try again shortly."
            )
        try:
            return {
                "variants": thumbnails.generate(store, worker.root, actor, pid, body)
            }
        finally:
            thumbnail_render.release()

    @app.get("/api/projects/{pid}/thumbnails/bundle")
    def download_thumbnails(pid: m.Id, actor=Depends(authenticated)):
        return Response(
            thumbnails.bundle(store, worker.root, actor, pid),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="hyphotenic-thumbnails.zip"'
            },
        )

    @app.get("/api/projects/{pid}/retention/{version}.csv")
    def retention_sheet(pid: m.Id, version: m.Id, actor=Depends(authenticated)):
        with store.transaction(write=False) as db:
            store.project(db, actor, pid)
            asset = store.asset(db, pid, version)
        if asset["kind"] != "export" or not asset["data"].get("retention"):
            raise RuleError("Choose an export with a retention worksheet", 404)
        output = StringIO(newline="")
        sheet = csv.writer(output)
        sheet.writerow(
            [
                "export_id",
                "clip_id",
                "scene",
                "start_seconds",
                "end_seconds",
                "cue",
                "editorial_purpose",
                "audience_retention_percent",
                "views",
                "traffic_source",
                "observation",
            ]
        )

        def safe_cell(value):
            return "'" + value if value.lstrip()[:1] in {"=", "+", "-", "@"} else value

        for scene in asset["data"]["retention"]:
            prefix = [version, scene["video"], safe_cell(scene["scene"])]
            sheet.writerow(
                [*prefix, scene["start"], scene["end"], "scene", "", "", "", "", ""]
            )
            for cue in scene["cues"]:
                sheet.writerow(
                    [
                        *prefix,
                        cue["start"],
                        cue["end"],
                        cue["kind"],
                        safe_cell(cue["purpose"]),
                        "",
                        "",
                        "",
                        "",
                    ]
                )
        return Response(
            "\ufeff" + output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="retention-{version[:8]}.csv"'
            },
        )

    @app.get("/api/providers")
    def providers(request: Request, actor=Depends(authenticated)):
        options = store.providers(actor)
        if is_public(request) and actor == public_login.actor:
            for option in options:
                name = option["provider"]
                if name == "codex":
                    option["device_login"] = True
                if name in credentials.MANAGED:
                    option["manage_key"] = not os.getenv(
                        credentials.KEYS[name], ""
                    ).strip()
        return options

    def codex_operator(request: Request, actor=Depends(authenticated)):
        if not is_public(request) or actor != public_login.actor:
            raise HTTPException(404, "Not found")

    @app.put(
        "/api/providers/{provider}/credential", dependencies=[Depends(codex_operator)]
    )
    def save_provider_credential(
        provider: Literal["elevenlabs", "runway"],
        body: ProviderKey,
        actor=Depends(authenticated),
    ):
        if not limiter.hit(("provider-credential", actor), 6, 60):
            raise HTTPException(429, "Try validating the key again in a minute")
        key = body.key.get_secret_value()
        if not re.fullmatch(r"[!-~]{12,240}", key):
            raise HTTPException(422, "Enter a valid API key")
        try:
            adapters.validate_provider_key(provider, key)
        except adapters.ProviderError as error:
            raise HTTPException(
                422, f"Could not verify the provider key. {error}"
            ) from None
        except (httpx.HTTPError, TypeError, ValueError, KeyError):
            raise HTTPException(
                502, "Could not reach the provider to verify the key"
            ) from None
        store.save_provider_key(provider, key)
        return {"ok": True}

    @app.get("/api/providers/elevenlabs/voices")
    def list_elevenlabs_voices(
        cursor: str | None = Query(default=None, max_length=1024),
        actor=Depends(authenticated),
    ):
        if not limiter.hit(("voice-catalog", actor), 24, 60):
            raise HTTPException(429, "Try loading voices again in a minute")
        key = credentials.get(store.directory, "elevenlabs")
        if not key:
            raise HTTPException(409, "Connect ElevenLabs under Providers first")
        try:
            return adapters.elevenlabs_voices(key, cursor)
        except (
            adapters.ProviderError,
            httpx.HTTPError,
            TypeError,
            ValueError,
            KeyError,
        ):
            raise HTTPException(502, "Could not load voices from ElevenLabs") from None

    @app.post("/api/providers/codex/login", dependencies=[Depends(codex_operator)])
    async def start_codex_login():
        try:
            return await codex_login.start()
        except codex_cli.CodexError as error:
            raise HTTPException(502, str(error)) from None

    @app.get("/api/providers/codex/login", dependencies=[Depends(codex_operator)])
    async def codex_login_status():
        return {"status": codex_login.status}

    @app.delete("/api/providers/codex/login", dependencies=[Depends(codex_operator)])
    async def cancel_codex_login():
        await codex_login.cancel()
        return {"ok": True}

    @app.put("/api/providers")
    def configure(body: m.Provider, actor=Depends(authenticated)):
        store.provider(actor, body)
        return {"ok": True}

    @app.put("/api/projects/{pid}/budget")
    def budget(pid: m.Id, body: m.Budget, actor=Depends(authenticated)):
        store.budget(actor, pid, body)
        return {"ok": True}

    @app.post("/api/projects/{pid}/quotes")
    def quote(pid: m.Id, body: m.QuoteInput, actor=Depends(authenticated)):
        return store.quote(actor, pid, body.jobs)

    @app.post("/api/projects/{pid}/quotes/{qid}/authorize", status_code=202)
    def authorize(pid: m.Id, qid: m.Id, body: m.Confirm, actor=Depends(authenticated)):
        return {"jobs": store.authorize(actor, pid, qid, body.maximum)}

    @app.get("/api/projects/{pid}/quotes/{qid}")
    def authorization(pid: m.Id, qid: m.Id, actor=Depends(authenticated)):
        return store.authorization(actor, pid, qid)

    @app.post("/api/projects/{pid}/jobs/{jid}/cancel")
    def cancel(pid: m.Id, jid: m.Id, actor=Depends(authenticated)):
        store.cancel(actor, pid, jid)
        return {"ok": True}

    @app.post("/api/projects/{pid}/jobs/{jid}/settle")
    def settle(pid: m.Id, jid: m.Id, body: m.Settlement, actor=Depends(authenticated)):
        store.settle(actor, pid, jid, body)
        return {"ok": True}

    @app.post("/api/projects/{pid}/jobs/{jid}/reconcile")
    def reconcile(pid: m.Id, jid: m.Id, actor=Depends(authenticated)):
        store.reconcile(actor, pid, jid)
        return {"ok": True}

    @app.post("/api/projects/{pid}/uploads", status_code=201)
    async def upload(
        pid: m.Id,
        request: Request,
        kind: Literal["reference", "image", "audio", "video", "voice"],
        title: m.Short,
        scene: m.Id | None = None,
        language: m.Language | None = None,
        actor=Depends(authenticated),
    ):
        def import_source(db):
            project = store.project(db, actor, pid)["data"]
            if kind not in {"image", "video", "voice"}:
                return None, [], {"title": title}
            if not scene or scene not in store.order(db, pid):
                raise RuleError("Choose an active storyboard scene")
            storyboard = store.require(db, pid, f"scene/{scene}")
            if kind == "image":
                return (
                    f"image/{scene}",
                    [{"id": storyboard["id"]}],
                    {
                        "title": title,
                        "prompt": storyboard["data"]["image_prompt"],
                        "provider": "local",
                        "model": "import",
                    },
                )
            if kind == "video":
                image = store.require(db, pid, f"image/{scene}")
                return (
                    f"video/{scene}",
                    [{"id": storyboard["id"]}, {"id": image["id"]}],
                    {"title": title},
                )
            if language not in project["languages"]:
                raise RuleError("Choose a supported narration language")
            narration = storyboard["data"]["narration"]
            deps = [{"speech": [scene, narration]}]
            if language != project["languages"][0]:
                translated = store.require(db, pid, f"translation/{scene}/{language}")
                deps.append({"id": translated["id"]})
                narration = translated["data"]["narration"]
            return (
                f"voice/{scene}/{language}",
                deps,
                {
                    "title": title,
                    "narration": narration,
                    "language": language,
                    "voice": title,
                    "provider": "local",
                    "model": "import",
                },
            )

        with store.transaction(write=False) as db:
            inputs = import_source(db)
        if not limiter.hit(("upload", actor), 8, 60):
            raise HTTPException(429, "Upload rate exceeded")
        limit = {
            "reference": media.MAX_IMAGE,
            "image": media.MAX_IMAGE,
            "audio": media.MAX_AUDIO,
            "voice": media.MAX_AUDIO,
            "video": media.MAX_VIDEO,
        }[kind]
        length = request.headers.get("content-length")
        if length and (
            not length.isdecimal() or len(length) > 10 or int(length) > limit
        ):
            raise HTTPException(413, "Upload exceeds byte limit")
        with TemporaryDirectory(prefix="upload-", dir=store.directory) as directory:
            source = Path(directory) / "upload"
            size = 0
            with source.open("wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, "Upload exceeds byte limit")
                    out.write(chunk)
            target = (
                Path(directory)
                / {
                    "reference": "asset.png",
                    "image": "asset.png",
                    "video": "asset.mp4",
                    "audio": "asset.wav",
                    "voice": "asset.wav",
                }[kind]
            )
            slot, deps, data = inputs
            try:
                if kind in {"reference", "image"}:
                    alpha = await run_in_threadpool(
                        media.sanitize_image,
                        source,
                        target,
                        preserve_alpha=kind == "reference",
                    )
                    if kind == "reference":
                        data = dict(data, transparent=alpha)
                elif kind == "video":
                    data = dict(
                        data,
                        **await run_in_threadpool(
                            media.sanitize_video, source, target, lambda: False, 150
                        ),
                    )
                else:
                    await run_in_threadpool(
                        media.sanitize_audio, source, target, lambda: False, 30
                    )
                    if kind == "voice":
                        data = dict(data, seconds=media.audio_duration(target))
                name = media.publish(worker.root, target)
            except media.MediaError as exc:
                raise HTTPException(422, str(exc)) from None
            try:
                with store.transaction() as db:
                    if import_source(db) != inputs:
                        raise RuleError("Asset inputs changed during upload")
                    return store.add(
                        db,
                        pid,
                        slot or f"{kind}/{name[:32]}",
                        kind,
                        data,
                        deps,
                        media=name,
                    )
            except BaseException:
                media.asset_path(worker.root, name).unlink(missing_ok=True)
                raise

    def asset_response(asset, version, attachment=False, webvtt=False):
        if not asset["media"]:
            raise RuleError("Media not found", 404)
        path = media.asset_path(worker.root, asset["media"])
        if not path.is_file():
            raise RuleError("Media not found", 404)
        if webvtt:
            if asset["kind"] != "captions" or path.suffix != ".srt":
                raise RuleError("Subtitle track not found", 404)
            return Response(media.webvtt(path), media_type="text/vtt")
        return FileResponse(
            path,
            media_type=media.FORMATS[path.suffix[1:]],
            filename=f"{asset['kind']}-{version[:8]}{path.suffix}"
            if attachment
            else None,
        )

    @app.api_route("/api/projects/{pid}/media/{version}", methods=["GET", "HEAD"])
    def download(
        pid: m.Id,
        version: m.Id,
        attachment: bool = False,
        webvtt: bool = False,
        actor=Depends(authenticated),
    ):
        with store.transaction(write=False) as db:
            store.project(db, actor, pid)
            asset = store.asset(db, pid, version)
        return asset_response(asset, version, attachment, webvtt)

    @app.api_route(
        "/api/agent/v1/projects/{pid}/media/{version}", methods=["GET", "HEAD"]
    )
    def agent_media(pid: m.Id, version: m.Id, digest=Depends(agent_credential)):
        return asset_response(project_agents.media(pid, digest, version), version)

    return app
