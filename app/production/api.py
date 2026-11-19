"""Loopback production API and same-origin workspace."""

from contextlib import asynccontextmanager
import hmac
from pathlib import Path
import re
import secrets
from tempfile import TemporaryDirectory
from threading import Lock
import time
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import media, models as m
from .store import RuleError, encode
from .workbench import Workbench
from .worker import Worker


WEB = Path(__file__).with_name("web")
COOKIE = "mpt_production_session"
MAX_JSON = 1024 * 1024


class Login(m.Input):
    token: str


class BulkReview(m.ReviewInput):
    versions: list[m.Id]


class RateLimit:
    def __init__(self):
        self.windows = {}
        self.lock = Lock()

    def hit(self, key, limit, window):
        with self.lock:
            now = time.monotonic()
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
):
    if not tokens or any(
        len(token) < 32 or not token.isascii() or not actor
        for token, actor in tokens.items()
    ):
        raise ValueError(
            "Configure a unique production access token with at least 32 ASCII characters"
        )
    store = Workbench(Path(directory))
    worker = Worker(store)
    sessions = {}
    sessions_lock = Lock()
    limiter = RateLimit()

    @asynccontextmanager
    async def lifespan(_app):
        if worker_enabled:
            worker.thread.start()
        try:
            yield
        finally:
            worker.stop.set()
            if worker.thread.is_alive():
                await run_in_threadpool(worker.thread.join, 2)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    @app.middleware("http")
    async def boundary(request, call_next):
        method = request.method
        if method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin != f"http://{request.headers.get('host', '')}":
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
        return session[0] if session and session[1] >= time.monotonic() else None

    def authenticated(request: Request):
        header = request.headers.get("authorization")
        if header:
            actor = (
                token_actor(header.removeprefix("Bearer "))
                if header.startswith("Bearer ")
                else None
            )
        else:
            actor = session_actor(request)
            if (
                actor
                and request.method not in {"GET", "HEAD", "OPTIONS"}
                and not request.headers.get("origin")
            ):
                raise HTTPException(403, "Same-origin request required")
        if not actor:
            raise HTTPException(401, "Production authentication required")
        limit = 240 if request.method in {"GET", "HEAD"} else 90
        if not limiter.hit((actor, request.method in {"GET", "HEAD"}), limit, 60):
            raise HTTPException(429, "Request rate exceeded")
        return actor

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get("/ui/{name}", include_in_schema=False)
    def static(name: Literal["workspace.js", "workspace.css"]):
        return FileResponse(WEB / name)

    @app.get("/api/session")
    def session(request: Request):
        return {"authenticated": bool(session_actor(request))}

    @app.post("/api/session")
    def login(payload: Login, request: Request, response: Response):
        if not limiter.hit(("login", request.client.host), 10, 60):
            raise HTTPException(429, "Try signing in again in a minute")
        actor = token_actor(payload.token)
        if not actor:
            raise HTTPException(401, "Invalid production access token")
        session = secrets.token_urlsafe(32)
        with sessions_lock:
            now = time.monotonic()
            for expired in [key for key, (_, end) in sessions.items() if end < now]:
                del sessions[expired]
            sessions[session] = (actor, now + 12 * 60 * 60)
        response.set_cookie(
            COOKIE, session, httponly=True, samesite="strict", max_age=43200
        )
        return {"actor": actor}

    @app.delete("/api/session")
    def logout(request: Request, response: Response, actor=Depends(authenticated)):
        with sessions_lock:
            sessions.pop(request.cookies.get(COOKIE), None)
        response.delete_cookie(COOKIE, httponly=True, samesite="strict")
        return {"ok": True}

    @app.get("/api/projects")
    def projects(actor=Depends(authenticated)):
        return store.snapshot(actor)

    @app.post("/api/projects", status_code=201)
    def create(body: m.Project, actor=Depends(authenticated)):
        return {"id": store.create(actor, body.model_dump())}

    @app.get("/api/projects/{pid}")
    def project(pid: m.Id, actor=Depends(authenticated)):
        return store.snapshot(actor, pid)

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

    @app.put("/api/projects/{pid}/timeline")
    def timeline(pid: m.Id, body: m.Timeline, actor=Depends(authenticated)):
        with store.transaction() as db:
            project = store.project(db, actor, pid)
            if {clip.scene for clip in body.clips} != set(
                store.order(db, pid)
            ) or not set(body.languages) <= set(project["data"]["languages"]):
                raise RuleError(
                    "Timeline must match the active scenes and project languages"
                )
            return store.add(db, pid, "timeline", "timeline", body.model_dump())

    @app.post("/api/projects/{pid}/reviews")
    def review(pid: m.Id, body: BulkReview, actor=Depends(authenticated)):
        if not 1 <= len(body.versions) <= 100:
            raise RuleError("Review 1 to 100 versions", 422)
        return store.review(actor, pid, body.versions, body)

    @app.get("/api/providers")
    def providers(actor=Depends(authenticated)):
        return store.providers(actor)

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
        kind: Literal["reference", "audio", "video", "voice"],
        title: m.Short,
        scene: m.Id | None = None,
        language: m.Language | None = None,
        actor=Depends(authenticated),
    ):
        def import_source(db):
            project = store.project(db, actor, pid)["data"]
            if kind not in {"video", "voice"}:
                return None, [], {"title": title}
            if not scene or scene not in store.order(db, pid):
                raise RuleError("Choose an active storyboard scene")
            storyboard = store.require(db, pid, f"scene/{scene}")
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
                    "video": "asset.mp4",
                    "audio": "asset.wav",
                    "voice": "asset.wav",
                }[kind]
            )
            slot, deps, data = inputs
            try:
                if kind == "reference":
                    await run_in_threadpool(media.sanitize_image, source, target)
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

    @app.api_route("/api/projects/{pid}/media/{version}", methods=["GET", "HEAD"])
    def download(
        pid: m.Id, version: m.Id, attachment: bool = False, actor=Depends(authenticated)
    ):
        with store.transaction(write=False) as db:
            store.project(db, actor, pid)
            asset = store.asset(db, pid, version)
            if not asset["media"]:
                raise RuleError("Media not found", 404)
            path = media.asset_path(worker.root, asset["media"])
            if not path.is_file():
                raise RuleError("Media not found", 404)
            mime = media.FORMATS[path.suffix[1:]]
        return FileResponse(
            path,
            media_type=mime,
            filename=f"{asset['kind']}-{version[:8]}{path.suffix}"
            if attachment
            else None,
        )

    return app
