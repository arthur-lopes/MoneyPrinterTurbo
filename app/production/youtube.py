"""Owner-authorized YouTube connection and publication receipts."""

from base64 import urlsafe_b64encode
import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time as civil_time, timedelta, timezone
from hashlib import sha256
from io import StringIO
import json
import os
import re
from secrets import token_urlsafe
from tempfile import NamedTemporaryFile
from threading import Event, Lock, Thread
import time
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from filelock import FileLock, Timeout
import httpx
from pydantic import Field, SecretStr, StringConstraints, field_validator

from . import media, models as m
from .store import RuleError, encode, unpack


AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
REVOKE = "https://oauth2.googleapis.com/revoke"
DATA = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
REPORTING = "https://youtubereporting.googleapis.com"
REACH_TYPE = "channel_reach_combined_a1"
REACH_NAME = "MoneyPrinterTurbo video reach"
READ_SCOPES = frozenset(
    "https://www.googleapis.com/auth/" + scope
    for scope in ("youtube.readonly", "yt-analytics.readonly")
)
WRITE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
VIDEO_PATTERN = r"^[A-Za-z0-9_-]{11}$"
CHANNEL_PATTERN = r"^UC[A-Za-z0-9_-]{22}$"
CALLBACK = "/api/youtube/callback"
CHUNK = 8 * 1024 * 1024
WINDOW = Literal[2, 7, 28]
VERIFICATION_PERIOD = 30 * 86400
VideoId = Annotated[str, StringConstraints(pattern=VIDEO_PATTERN)]


@dataclass(frozen=True)
class Settings:
    client_id: str
    secret: str = field(repr=False)
    redirect: str
    cipher: Fernet = field(repr=False)
    channel: str = ""

    @classmethod
    def from_env(cls):
        names = ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI", "TOKEN_KEY")
        values = [os.getenv("MPT_YOUTUBE_" + key, "").strip() for key in names]
        if not any(values):
            return None
        client_id, secret, redirect, key = values
        return cls.build(
            client_id,
            secret,
            redirect,
            key,
            os.getenv("MPT_YOUTUBE_CHANNEL_ID", "").strip(),
        )

    @classmethod
    def build(cls, client_id, secret, redirect, key, channel):
        parts = urlsplit(redirect)
        loopback = parts.scheme == "http" and parts.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if (
            not all((client_id, secret, redirect, key))
            or not (parts.scheme == "https" or loopback)
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path != CALLBACK
            or (channel and not re.fullmatch(CHANNEL_PATTERN, channel))
        ):
            raise ValueError("Invalid MPT_YOUTUBE OAuth settings")
        try:
            cipher = Fernet(key.encode("ascii"))
        except (UnicodeError, ValueError):
            raise ValueError("Invalid MPT_YOUTUBE_TOKEN_KEY") from None
        return cls(client_id, secret, redirect, cipher, channel)


class Connect(m.Input):
    mode: Literal["read", "publish"]


class Setup(m.Input):
    client_id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            pattern=r"^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$",
            max_length=240,
        ),
    ]
    client_secret: SecretStr = Field(min_length=10, max_length=240)
    channel: Annotated[str, StringConstraints(pattern=CHANNEL_PATTERN)] | None = None
    confirmed: m.Confirmed


class Upload(m.Input):
    channel: Annotated[str, StringConstraints(pattern=CHANNEL_PATTERN)]
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    description: Annotated[str, StringConstraints(max_length=5000)] = ""
    category: Annotated[str, StringConstraints(pattern=r"^[0-9]{1,3}$")] = "27"
    made_for_kids: bool = Field(strict=True)
    synthetic: bool = Field(strict=True)
    confirmed: m.Confirmed

    @field_validator("title", "description")
    @classmethod
    def valid_text(cls, value):
        if "<" in value or ">" in value:
            raise ValueError("Remove angle brackets from the video metadata")
        return value


class Visibility(m.Input):
    video: VideoId
    publish_at: datetime | None = None
    confirmed: m.Confirmed

    @field_validator("publish_at")
    @classmethod
    def aware_time(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Use a schedule with a timezone")
        return value


class Link(m.Input):
    video: VideoId
    confirmed: m.Confirmed


class Assets(m.Input):
    video: VideoId
    thumbnail: m.Id | None = None
    captions: list[m.Language] = Field(default_factory=list, max_length=2)
    confirmed: m.Confirmed


class Consent(m.Input):
    confirmed: m.Confirmed


class GoogleError(Exception):
    def __init__(self, response):
        self.status = response.status_code
        try:
            data = response.json().get("error")
            self.reason = (
                data
                if isinstance(data, str)
                else (data or {}).get("errors", [{}])[0].get("reason", "")
            )
        except (ValueError, IndexError, AttributeError, TypeError):
            self.reason = ""


def seconds_offset(response, size):
    value = response.headers.get("range")
    match = re.fullmatch(r"bytes=0-([0-9]+)", value or "")
    offset = int(match[1]) + 1 if match else 0
    if (value and not match) or offset >= size:
        raise RuleError("YouTube returned an invalid resumable upload position", 502)
    return offset


class YouTube:
    def __init__(self, store, settings, *, transport=None, setup_key=None):
        self.store = store
        self.settings = settings
        self.from_env = settings is not None
        self.setup_cipher = (
            Fernet(
                urlsafe_b64encode(
                    sha256(
                        b"mpt-youtube-setup-v1\0" + setup_key.encode("ascii")
                    ).digest()
                )
            )
            if setup_key
            else None
        )
        self.setup_path = store.directory / ".youtube-oauth"
        self.setup_error = False
        if not settings and self.setup_cipher and self.setup_path.exists():
            try:
                self.settings = Settings.build(
                    **json.loads(
                        self.setup_cipher.decrypt(self.setup_path.read_bytes())
                    )
                )
            except (ValueError, InvalidToken, OSError, TypeError):
                self.setup_error = True
        self.transport = transport
        self.pending = {}
        self.states_lock = Lock()
        self.stop = Event()
        self.worker = Thread(target=self.loop, daemon=True, name="mpt-youtube")

    def configure(self, request, origin):
        if self.from_env or not self.setup_cipher:
            raise RuleError(
                "This YouTube client is managed by the server administrator", 403
            )
        with self.states_lock, self.store.transaction() as db:
            if db.execute("SELECT 1 FROM youtube_connections LIMIT 1").fetchone():
                raise RuleError(
                    "Disconnect the channel before replacing the OAuth client"
                )
            private = {
                "client_id": request.client_id,
                "secret": request.client_secret.get_secret_value(),
                "redirect": origin + CALLBACK,
                "key": Fernet.generate_key().decode("ascii"),
                "channel": request.channel or "",
            }
            try:
                settings = Settings.build(**private)
            except ValueError:
                raise RuleError(
                    "Use the workspace HTTPS address or loopback for YouTube OAuth", 422
                ) from None
            with NamedTemporaryFile(dir=self.store.directory, delete=False) as file:
                temporary = self.setup_path.with_name(os.path.basename(file.name))
                try:
                    file.write(
                        self.setup_cipher.encrypt(encode(private).encode("utf-8"))
                    )
                    file.close()
                    temporary.replace(self.setup_path)
                finally:
                    temporary.unlink(missing_ok=True)
            self.settings = settings
            self.setup_error = False
            self.pending.clear()

    def _configured(self):
        if self.settings is None:
            raise RuleError("Configure the server's YouTube OAuth client", 503)
        return self.settings

    def _client(self):
        return httpx.Client(
            transport=self.transport,
            follow_redirects=False,
            timeout=httpx.Timeout(30, write=90),
            trust_env=False,
        )

    def _request(self, method, url, token=None, *, accepted=(), **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            with self._client() as client:
                response = client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise RuleError(
                "Connection to YouTube interrupted. Verify the remote state before retrying.",
                502,
            ) from None
        if not response.is_success and response.status_code not in accepted:
            raise GoogleError(response)
        return response

    def _json(self, *args, **kwargs):
        try:
            data = self._request(*args, **kwargs).json()
        except ValueError:
            raise RuleError("Unexpected response from YouTube", 502) from None
        if not isinstance(data, dict):
            raise RuleError("Unexpected response from YouTube", 502)
        return data

    def _seal(self, data):
        return self._configured().cipher.encrypt(data.encode("utf-8"))

    def _unseal(self, data):
        try:
            return self._configured().cipher.decrypt(data).decode("utf-8")
        except InvalidToken:
            raise RuleError(
                "The stored YouTube connection could not be opened", 409
            ) from None

    def begin(self, actor, browser_session, mode, origin):
        settings = self._configured()
        if origin + CALLBACK != settings.redirect:
            raise RuleError(
                "Open the workspace on the configured YouTube OAuth origin", 409
            )
        state, verifier = token_urlsafe(32), token_urlsafe(64)
        scopes = READ_SCOPES | ({WRITE_SCOPE} if mode == "publish" else set())
        with self.states_lock:
            now = time.monotonic()
            self.pending = {
                key: value
                for key, value in self.pending.items()
                if value[0] > now and value[1] != browser_session
            }
            if len(self.pending) >= 100:
                raise RuleError("Too many pending YouTube connections", 429)
            self.pending[state] = (now + 600, browser_session, actor, verifier, scopes)
        challenge = (
            urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip("=")
        )
        return (
            AUTHORIZE
            + "?"
            + urlencode(
                {
                    "client_id": settings.client_id,
                    "redirect_uri": settings.redirect,
                    "response_type": "code",
                    "access_type": "offline",
                    "prompt": "consent select_account",
                    "scope": " ".join(sorted(scopes)),
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    def consume_state(self, state):
        with self.states_lock:
            value = self.pending.pop(state, None)
        if not value or value[0] <= time.monotonic():
            raise RuleError("The YouTube connection request expired", 400)
        return value

    def connect(self, actor, verifier, scopes, code):
        settings = self._configured()
        result = self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": settings.redirect,
            }
        )
        granted = set(result.get("scope", "").split())
        refresh = result.get("refresh_token")
        if not scopes <= granted or not refresh:
            raise RuleError("Google did not grant the requested YouTube access", 403)
        channels = self._json(
            "GET",
            DATA + "/channels",
            result["access_token"],
            params={"part": "snippet", "mine": "true", "maxResults": 2},
        )
        items = channels.get("items", [])
        if len(items) != 1 or not re.fullmatch(CHANNEL_PATTERN, items[0].get("id", "")):
            raise RuleError(
                "Select a Google account with exactly one active YouTube channel", 409
            )
        channel = items[0]["id"]
        if settings.channel and settings.channel != channel:
            raise RuleError(
                "The selected Google account belongs to a different channel", 409
            )
        with self.states_lock, self.store.transaction() as db:
            if self.settings is not settings:
                raise RuleError("The OAuth client changed during the connection", 409)
            existing = db.execute(
                "SELECT channel FROM youtube_connections WHERE owner=?", (actor,)
            ).fetchone()
            if existing and existing["channel"] != channel:
                raise RuleError(
                    "Disconnect the previous channel before connecting another", 409
                )
            db.execute(
                """INSERT OR REPLACE INTO youtube_connections
                VALUES(?,?,?,?,?,?,?)""",
                (
                    actor,
                    channel,
                    items[0]["snippet"]["title"],
                    self._seal(refresh),
                    int(WRITE_SCOPE in granted),
                    token_urlsafe(16),
                    time.time(),
                ),
            )

    def _token(self, data):
        settings = self._configured()
        return self._json(
            "POST",
            TOKEN,
            data=dict(
                data, client_id=settings.client_id, client_secret=settings.secret
            ),
        )

    def _connection(self, db, actor, *, write=False):
        self._configured()
        row = db.execute(
            "SELECT * FROM youtube_connections WHERE owner=?", (actor,)
        ).fetchone()
        if not row or (write and not row["writable"]):
            raise RuleError(
                "Connect YouTube with "
                + ("publishing" if write else "read")
                + " access"
            )
        if self.settings.channel and row["channel"] != self.settings.channel:
            raise RuleError("The connected channel differs from the configured channel")
        return dict(row)

    def _access(self, connection):
        try:
            result = self._token(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self._unseal(connection["refresh_token"]),
                }
            )
        except GoogleError as error:
            if error.reason == "invalid_grant":
                self._clear(connection)
                raise RuleError(
                    "Google revoked this connection. Connect the channel again", 409
                ) from None
            raise
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuleError("Google did not return an access token", 502)
        with self.store.transaction() as db:
            updated = db.execute(
                "UPDATE youtube_connections SET verified=? WHERE owner=? AND revision=?",
                (time.time(), connection["owner"], connection["revision"]),
            ).rowcount
        if not updated:
            raise RuleError("The YouTube connection changed")
        return token

    def status(self, actor):
        with self.store.transaction(write=False) as db:
            row = db.execute(
                "SELECT channel,title,writable FROM youtube_connections WHERE owner=?",
                (actor,),
            ).fetchone()
        return {
            "configured": self.settings is not None,
            "connection": dict(row) if row else None,
            "setup_error": self.setup_error,
        }

    def _clear(self, connection):
        actor = connection["owner"]
        with self.store.transaction() as db:
            removed = db.execute(
                "DELETE FROM youtube_connections WHERE owner=? AND revision=?",
                (actor, connection["revision"]),
            ).rowcount
            if removed:
                db.execute(
                    """DELETE FROM youtube_metrics WHERE project IN
                    (SELECT id FROM projects WHERE owner=?) AND export IN
                    (SELECT export FROM youtube_publications WHERE channel=?)""",
                    (actor, connection["channel"]),
                )
                db.execute(
                    """UPDATE youtube_publications SET state='DETACHED',cancelled=1,
                    video=NULL,upload_url=NULL,visibility=NULL,processing=NULL,scheduled=NULL,pending=NULL,
                    last_error='Channel disconnected. Relink the existing video before retrying.'
                    WHERE channel=? AND project IN (SELECT id FROM projects WHERE owner=?)""",
                    (connection["channel"], actor),
                )

    def disconnect(self, actor):
        with self.store.transaction(write=False) as db:
            connection = self._connection(db, actor)
        try:
            self._request(
                "POST",
                REVOKE,
                data={"token": self._unseal(connection["refresh_token"])},
            )
        except GoogleError as error:
            if error.reason not in {"invalid_token", "invalid_grant"}:
                raise
        self._clear(connection)
        return {"ok": True}

    def _publication(self, db, pid, export):
        row = db.execute(
            "SELECT * FROM youtube_publications WHERE project=? AND export=?",
            (pid, export),
        ).fetchone()
        return unpack(row, "request") if row else None

    def _event(self, db, pid, export, actor, event):
        db.execute(
            "INSERT INTO youtube_events(project,export,actor,event,created) VALUES(?,?,?,?,?)",
            (pid, export, actor, event, time.time()),
        )

    def _export(self, db, pid, export, *, current=False):
        asset = self.store.asset(db, pid, export)
        deps = asset["dependencies"]
        if (
            asset["kind"] != "export"
            or not asset["media"]
            or not deps
            or "id" not in deps[0]
        ):
            raise RuleError("Choose an exported, approved cut", 422)
        assembly = self.store.asset(db, pid, deps[0]["id"])
        if (
            assembly["kind"] != "assembly"
            or assembly["status"] != "APPROVED"
            or assembly["data"].get("watermark", True)
        ):
            raise RuleError(
                "Approve the unwatermarked cut before sending it to YouTube"
            )
        if current and (
            self.store.head(db, pid, "export")["id"] != export
            or not self.store.fresh(db, asset)
        ):
            raise RuleError(
                "Export the current approved cut before sending it to YouTube"
            )
        return asset

    def _file(self, asset):
        try:
            path = media.asset_path(self.store.directory / "assets", asset["media"])
            if path.is_file() and path.stat().st_size:
                return path
        except (OSError, media.MediaError):
            pass
        raise RuleError("The selected export or attachment is missing", 404)

    def snapshot(self, actor, pid, export):
        with self.store.transaction(write=False) as db:
            self.store.project(db, actor, pid)
            self._export(db, pid, export)
            row = self._publication(db, pid, export)
            if row:
                row.pop("upload_url")
                row["events"] = [
                    dict(r)
                    for r in db.execute(
                        """SELECT actor,event,created FROM youtube_events
                    WHERE project=? AND export=? ORDER BY id DESC LIMIT 30""",
                        (pid, export),
                    )
                ]
            metrics = [
                unpack(r, "data")
                for r in db.execute(
                    """SELECT y.days,y.data,y.retrieved FROM youtube_metrics y
                    JOIN youtube_publications p ON p.project=y.project AND p.export=y.export
                    JOIN youtube_connections c ON c.channel=p.channel AND c.owner=?
                    WHERE y.project=? AND y.export=? AND c.verified>=? ORDER BY y.days""",
                    (actor, pid, export, time.time() - VERIFICATION_PERIOD),
                )
            ]
        return self.status(actor) | {"publication": row, "metrics": metrics}

    def queue(self, actor, pid, export, request):
        data = request.model_dump(exclude={"confirmed"})
        with self.store.transaction() as db:
            self.store.project(db, actor, pid)
            connection = self._connection(db, actor, write=True)
            if request.channel != connection["channel"]:
                raise RuleError("The selected YouTube channel changed")
            previous = self._publication(db, pid, export)
            if previous and previous["request"] != data:
                raise RuleError("This export already has a YouTube receipt")
            if not previous:
                size = (
                    self._file(self._export(db, pid, export, current=True))
                    .stat()
                    .st_size
                )
                db.execute(
                    """INSERT INTO youtube_publications(project,export,channel,state,request,bytes,created)
                    VALUES(?,?,?,'QUEUED',?,?,?)""",
                    (
                        pid,
                        export,
                        connection["channel"],
                        encode(data),
                        size,
                        time.time(),
                    ),
                )
                self._event(db, pid, export, actor, "private_upload_authorized")
        return self.snapshot(actor, pid, export)

    def _bound(self, db, actor, pid, export, *, write=False):
        self.store.project(db, actor, pid)
        connection = self._connection(db, actor, write=write)
        publication = self._publication(db, pid, export)
        if not publication or publication["channel"] != connection["channel"]:
            raise RuleError("The export is not linked to this YouTube channel", 404)
        return publication, connection

    def resume(self, actor, pid, export):
        with self.store.transaction() as db:
            row, _ = self._bound(db, actor, pid, export, write=True)
            if row["state"] != "PAUSED" or row["video"]:
                raise RuleError("Only a paused upload can be resumed")
            self._export(db, pid, export, current=True)
            db.execute(
                """UPDATE youtube_publications SET state='QUEUED',cancelled=0,last_error=NULL
                       WHERE project=? AND export=?""",
                (pid, export),
            )
            self._event(db, pid, export, actor, "upload_resume_authorized")
        return self.snapshot(actor, pid, export)

    def cancel(self, actor, pid, export):
        with self.store.transaction() as db:
            row, _ = self._bound(db, actor, pid, export)
            if row["state"] not in {"QUEUED", "UPLOADING", "PAUSED"}:
                raise RuleError("There is no upload to pause")
            db.execute(
                """UPDATE youtube_publications SET cancelled=1,
                       state=CASE WHEN state='QUEUED' THEN 'PAUSED' ELSE state END
                       WHERE project=? AND export=?""",
                (pid, export),
            )
            self._event(db, pid, export, actor, "upload_pause_requested")
        return self.snapshot(actor, pid, export)

    def loop(self, draining=lambda: False):
        lock = FileLock(self.store.directory / "youtube-worker.lock")
        while not self.stop.is_set():
            try:
                with lock.acquire(timeout=0):
                    with self.store.transaction() as db:
                        db.execute("""UPDATE youtube_publications SET state='PAUSED',
                                   last_error='Upload interrupted. Resume the existing session.'
                                   WHERE state='UPLOADING'""")
                    verification = 0
                    while not self.stop.is_set():
                        if self.settings is None or draining():
                            self.stop.wait(0.5)
                            continue
                        if time.monotonic() >= verification:
                            self._verify_connections()
                            verification = time.monotonic() + 3600
                        if not self.step():
                            self.stop.wait(0.5)
            except Timeout:
                self.stop.wait(1)
            except (OSError, RuleError):
                self.stop.wait(1)

    def _verify_connections(self):
        with self.store.transaction(write=False) as db:
            due = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM youtube_connections WHERE verified<?",
                    (time.time() - VERIFICATION_PERIOD + 86400,),
                )
            ]
        for connection in due:
            if self.stop.is_set():
                return
            try:
                self._access(connection)
            except (GoogleError, RuleError):
                continue

    def step(self):
        with self.store.transaction() as db:
            row = db.execute(
                """SELECT y.*,p.owner FROM youtube_publications y JOIN projects p ON p.id=y.project
                WHERE y.state='QUEUED' ORDER BY y.created LIMIT 1"""
            ).fetchone()
            if not row:
                return False
            row = unpack(row, "request")
            db.execute(
                "UPDATE youtube_publications SET state='UPLOADING' WHERE project=? AND export=?",
                (row["project"], row["export"]),
            )
        try:
            self._upload(row)
        except (
            GoogleError,
            RuleError,
            httpx.HTTPError,
            OSError,
            ValueError,
            KeyError,
        ) as error:
            message = (
                str(error)
                if isinstance(error, RuleError)
                else "Upload paused. Check or resume the existing session."
            )
            if (
                isinstance(error, GoogleError)
                and error.status in {404, 410}
                and row["upload_url"]
            ):
                message = "Upload session expired. Check YouTube Studio and link the existing video before retrying."
            with self.store.transaction() as db:
                db.execute(
                    """UPDATE youtube_publications SET state='PAUSED',last_error=?
                              WHERE project=? AND export=? AND state='UPLOADING'""",
                    (message, row["project"], row["export"]),
                )
                self._event(
                    db, row["project"], row["export"], row["owner"], "upload_paused"
                )
        return True

    def _upload_session(self, url):
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc not in {"www.googleapis.com", "youtube.googleapis.com"}
            or parts.path
            not in {"/upload/youtube/v3/videos", "/resumable/upload/youtube/v3/videos"}
            or not parts.query
            or parts.fragment
        ):
            raise RuleError("Unexpected resumable upload URL", 502)
        return url

    def _check_upload(self, actor, pid, export, channel):
        with self.store.transaction(write=False) as db:
            row, connection = self._bound(db, actor, pid, export, write=True)
            if (
                row["cancelled"]
                or self.stop.is_set()
                or row["state"] != "UPLOADING"
                or connection["channel"] != channel
            ):
                raise RuleError("Upload paused by the operator")
            self._export(db, pid, export, current=True)

    def _upload(self, row):
        actor, pid, export, channel = (
            row[key] for key in ("owner", "project", "export", "channel")
        )
        with self.store.transaction(write=False) as db:
            _, connection = self._bound(db, actor, pid, export, write=True)
            path = self._file(self._export(db, pid, export, current=True))
        self._check_upload(actor, pid, export, channel)
        token = self._access(connection)
        size = row["bytes"]
        if path.stat().st_size != size:
            raise RuleError("The exported file changed since authorization")
        offset = 0
        if row["upload_url"]:
            url = self._upload_session(self._unseal(row["upload_url"]))
            response = self._request(
                "PUT",
                url,
                token,
                accepted=(308,),
                headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"},
            )
            if response.is_success:
                return self._upload_receipt(row, response.json())
            offset = seconds_offset(response, size)
        else:
            data = row["request"]
            response = self._request(
                "POST",
                UPLOAD + "/videos",
                token,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    "X-Upload-Content-Length": str(size),
                    "X-Upload-Content-Type": "video/mp4",
                },
                json={
                    "snippet": {
                        "title": data["title"],
                        "description": data["description"],
                        "categoryId": data["category"],
                    },
                    "status": {
                        "privacyStatus": "private",
                        "selfDeclaredMadeForKids": data["made_for_kids"],
                        "containsSyntheticMedia": data["synthetic"],
                    },
                },
            )
            url = self._upload_session(response.headers.get("location", ""))
            encrypted = self._seal(url)
            with self.store.transaction() as db:
                db.execute(
                    """UPDATE youtube_publications SET upload_url=?
                              WHERE project=? AND export=? AND state='UPLOADING'""",
                    (encrypted, pid, export),
                )
            row["upload_url"] = encrypted
        refreshed = time.monotonic()
        with path.open("rb") as source:
            while offset < size:
                self._check_upload(actor, pid, export, channel)
                if time.monotonic() - refreshed > 45 * 60:
                    token = self._access(connection)
                    refreshed = time.monotonic()
                source.seek(offset)
                block = source.read(min(CHUNK, size - offset))
                if not block:
                    raise RuleError("The exported file became unavailable")
                end = offset + len(block) - 1
                response = self._request(
                    "PUT",
                    url,
                    token,
                    accepted=(308,),
                    content=block,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {offset}-{end}/{size}",
                    },
                )
                if response.is_success:
                    return self._upload_receipt(row, response.json())
                position = seconds_offset(response, size)
                if not offset < position <= end + 1:
                    raise RuleError(
                        "YouTube did not acknowledge the uploaded chunk. Resume to reconcile."
                    )
                offset = position
                with self.store.transaction() as db:
                    db.execute(
                        "UPDATE youtube_publications SET received=? WHERE project=? AND export=?",
                        (offset, pid, export),
                    )

    def _upload_receipt(self, row, item):
        pid, export, actor = (row[key] for key in ("project", "export", "owner"))
        video = item.get("id", "")
        if (
            not re.fullmatch(VIDEO_PATTERN, video)
            or item.get("snippet", {}).get("channelId") != row["channel"]
        ):
            raise RuleError(
                "YouTube returned an unverified video receipt. Check Studio."
            )
        status = item.get("status", {})
        with self.store.transaction() as db:
            if self._publication(db, pid, export)["state"] != "UPLOADING":
                return
            db.execute(
                """UPDATE youtube_publications SET state='UPLOADED',video=?,received=bytes,
                          upload_url=NULL,visibility=?,processing=?,last_error=NULL WHERE project=? AND export=?""",
                (
                    video,
                    status.get("privacyStatus"),
                    status.get("uploadStatus"),
                    pid,
                    export,
                ),
            )
            self._event(db, pid, export, actor, "upload_receipt_recorded")

    def _video(self, token, channel, video):
        result = self._json(
            "GET",
            DATA + "/videos",
            token,
            params={"part": "snippet,status", "id": video},
        )
        items = result.get("items", [])
        if (
            len(items) != 1
            or items[0].get("id") != video
            or items[0].get("snippet", {}).get("channelId") != channel
        ):
            raise RuleError("This video does not belong to the connected channel", 404)
        return items[0]

    def _save_status(self, db, pid, export, item):
        status = item["status"]
        db.execute(
            """UPDATE youtube_publications SET visibility=?,processing=?,scheduled=?,
                      state=CASE WHEN state IN ('VISIBILITY_UNKNOWN','VISIBILITY_PENDING') THEN 'UPLOADED' ELSE state END,
                      pending=NULL,last_error=NULL WHERE project=? AND export=?""",
            (
                status.get("privacyStatus"),
                status.get("uploadStatus"),
                status.get("publishAt"),
                pid,
                export,
            ),
        )

    def link(self, actor, pid, export, request):
        with self.store.transaction(write=False) as db:
            self.store.project(db, actor, pid)
            self._export(db, pid, export)
            channel = self._connection(db, actor)
        item = self._video(self._access(channel), channel["channel"], request.video)
        with self.store.transaction() as db:
            current = self._connection(db, actor)
            if current["revision"] != channel["revision"]:
                raise RuleError("The YouTube connection changed")
            row = self._publication(db, pid, export)
            if row:
                if (
                    row["state"] not in {"PAUSED", "DETACHED"}
                    or row["video"]
                    or row["channel"] != channel["channel"]
                ):
                    raise RuleError(
                        "This export already has a publication or an active upload"
                    )
                if (
                    row["request"].get("title")
                    and row["request"]["title"] != item["snippet"]["title"]
                ):
                    raise RuleError(
                        "The selected video title differs from the upload receipt"
                    )
            if db.execute(
                "SELECT 1 FROM youtube_publications WHERE channel=? AND video=?",
                (channel["channel"], request.video),
            ).fetchone():
                raise RuleError(
                    "This YouTube video is already linked to another export"
                )
            if row:
                db.execute(
                    """UPDATE youtube_publications SET video=?,state='LINKED',upload_url=NULL
                              WHERE project=? AND export=?""",
                    (request.video, pid, export),
                )
            else:
                db.execute(
                    """INSERT INTO youtube_publications(project,export,channel,video,state,request,created)
                              VALUES(?,?,?,?,'LINKED','{}',?)""",
                    (pid, export, channel["channel"], request.video, time.time()),
                )
            self._save_status(db, pid, export, item)
            self._event(db, pid, export, actor, "existing_video_linked")
        return self.snapshot(actor, pid, export)

    def refresh(self, actor, pid, export):
        with self.store.transaction(write=False) as db:
            row, channel = self._bound(db, actor, pid, export)
            if not row["video"]:
                raise RuleError("Wait for the video or the current visibility request")
            if row["state"] == "VISIBILITY_PENDING":
                last = db.execute(
                    """SELECT MAX(created) FROM youtube_events WHERE project=? AND export=?
                    AND event IN ('public_visibility_authorized','scheduled_visibility_authorized')""",
                    (pid, export),
                ).fetchone()[0]
                if last is not None and time.time() - last < 180:
                    raise RuleError(
                        "Wait for the current visibility request before checking"
                    )
        item = self._video(self._access(channel), row["channel"], row["video"])
        with self.store.transaction() as db:
            current, connection = self._bound(db, actor, pid, export)
            if (
                current["state"] != row["state"]
                or connection["revision"] != channel["revision"]
            ):
                raise RuleError("Publication changed while checking YouTube")
            self._save_status(db, pid, export, item)
            self._event(db, pid, export, actor, "remote_status_verified")
        return self.snapshot(actor, pid, export)

    def publish(self, actor, pid, export, request):
        lock = FileLock(self.store.directory / f"youtube-write-{export}.lock")
        try:
            with lock.acquire(timeout=0):
                return self._publish(actor, pid, export, request)
        except Timeout:
            raise RuleError(
                "Another YouTube write is in progress for this export"
            ) from None

    def _publish(self, actor, pid, export, request):
        schedule = request.publish_at
        if schedule and schedule <= datetime.now(timezone.utc) + timedelta(minutes=1):
            raise RuleError("Choose a schedule at least one minute in the future", 422)
        scheduled = schedule.astimezone(timezone.utc).isoformat() if schedule else None
        with self.store.transaction(write=False) as db:
            row, channel = self._bound(db, actor, pid, export, write=True)
            if (
                row["state"] not in {"UPLOADED", "LINKED"}
                or row["video"] != request.video
            ):
                raise RuleError(
                    "Verify the connected video and reconcile any pending visibility change"
                )
            self._export(db, pid, export, current=True)
        token = self._access(channel)
        remote = self._video(token, row["channel"], row["video"])
        status = remote["status"]
        if (
            status.get("privacyStatus") != "private"
            or status.get("publishAt")
            or status.get("uploadStatus") != "processed"
        ):
            raise RuleError(
                "YouTube must report a processed, private, unscheduled video"
            )
        with self.store.transaction() as db:
            current, connection = self._bound(db, actor, pid, export, write=True)
            if (
                current["state"] != row["state"]
                or connection["revision"] != channel["revision"]
            ):
                raise RuleError("Publication or channel changed")
            self._export(db, pid, export, current=True)
            db.execute(
                """UPDATE youtube_publications SET state='VISIBILITY_PENDING',pending=?
                          WHERE project=? AND export=?""",
                (encode(request.model_dump(mode="json")), pid, export),
            )
            self._event(
                db,
                pid,
                export,
                actor,
                "public_visibility_authorized"
                if not scheduled
                else "scheduled_visibility_authorized",
            )
        try:
            change = {
                key: status[key]
                for key in (
                    "selfDeclaredMadeForKids",
                    "containsSyntheticMedia",
                    "license",
                    "embeddable",
                    "publicStatsViewable",
                )
                if key in status
            }
            change["privacyStatus"] = "private" if scheduled else "public"
            if scheduled:
                change["publishAt"] = scheduled
            self._json(
                "PUT",
                DATA + "/videos",
                token,
                params={"part": "status"},
                json={"id": request.video, "status": change},
            )
            verified = self._video(token, row["channel"], row["video"])
            actual = verified["status"]
            if actual.get("privacyStatus") != change["privacyStatus"] or (
                scheduled
                and datetime.fromisoformat(actual.get("publishAt", "")) != schedule
            ):
                raise RuleError("YouTube has not confirmed the requested visibility")
        except (httpx.HTTPError, GoogleError, RuleError, KeyError, ValueError):
            with self.store.transaction() as db:
                db.execute(
                    """UPDATE youtube_publications SET state='VISIBILITY_UNKNOWN',
                              last_error='Check YouTube before trying to publish again.'
                              WHERE project=? AND export=? AND state='VISIBILITY_PENDING'""",
                    (pid, export),
                )
            return self.snapshot(actor, pid, export)
        with self.store.transaction() as db:
            current, connection = self._bound(db, actor, pid, export, write=True)
            if (
                current["state"] != "VISIBILITY_PENDING"
                or connection["revision"] != channel["revision"]
            ):
                raise RuleError("YouTube visibility needs reconciliation")
            db.execute(
                "UPDATE youtube_publications SET state='UPLOADED' WHERE project=? AND export=?",
                (pid, export),
            )
            self._save_status(db, pid, export, verified)
            self._event(db, pid, export, actor, "visibility_verified")
        return self.snapshot(actor, pid, export)

    def upload_assets(self, actor, pid, export, request):
        if not request.thumbnail and not request.captions:
            raise RuleError("Choose a thumbnail or caption track", 422)
        if len(set(request.captions)) != len(request.captions):
            raise RuleError("Choose each caption language once", 422)
        lock = FileLock(self.store.directory / f"youtube-write-{export}.lock")
        try:
            with lock.acquire(timeout=0):
                self._upload_assets(actor, pid, export, request)
        except Timeout:
            raise RuleError(
                "Another attachment request is already in progress"
            ) from None
        return self.snapshot(actor, pid, export)

    def _upload_assets(self, actor, pid, export, request):
        with self.store.transaction() as db:
            row, channel = self._bound(db, actor, pid, export, write=True)
            item = self._export(db, pid, export, current=True)
            if row["video"] != request.video or row["state"] not in {
                "UPLOADED",
                "LINKED",
            }:
                raise RuleError("Choose the confirmed YouTube video")
            thumbnail = None
            if request.thumbnail:
                asset = self.store.asset(db, pid, request.thumbnail)
                if (
                    asset["kind"] != "thumbnail"
                    or asset["status"] != "APPROVED"
                    or not self.store.fresh(db, asset)
                ):
                    raise RuleError("Approve the selected thumbnail first")
                thumbnail = self._file(asset)
            captions = {}
            for language in request.captions:
                vid = (item["data"].get("captions") or {}).get(language)
                if not vid:
                    raise RuleError(
                        "The selected export has no caption for " + language, 422
                    )
                asset = self.store.asset(db, pid, vid)
                if asset["kind"] != "captions":
                    raise RuleError("The selected caption version is invalid", 422)
                captions[language] = self._file(asset)
            self._event(db, pid, export, actor, "attachments_authorized")
        token = self._access(channel)
        item = self._video(token, row["channel"], row["video"])
        if item["status"].get("uploadStatus") != "processed":
            raise RuleError("Wait until YouTube finishes processing the video")
        if thumbnail:
            if thumbnail.stat().st_size > 2 * 1024 * 1024:
                raise RuleError(
                    "The approved thumbnail exceeds the API size limit", 422
                )
            self._request(
                "POST",
                UPLOAD + "/thumbnails/set",
                token,
                params={"uploadType": "media", "videoId": request.video},
                headers={"Content-Type": "image/jpeg"},
                content=thumbnail.read_bytes(),
            )
            with self.store.transaction() as db:
                self._event(
                    db, pid, export, actor, "thumbnail_uploaded:" + request.thumbnail
                )
        if not captions:
            return
        tracks = self._json(
            "GET",
            DATA + "/captions",
            token,
            params={"part": "snippet", "videoId": request.video},
        ).get("items", [])
        existing = {
            (track["snippet"]["language"], track["snippet"]["name"]) for track in tracks
        }
        for language, path in captions.items():
            name = f"{language} · MPT {export[:12]}"
            if (language, name) not in existing:
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise RuleError(
                        "The caption file exceeds the workspace size limit", 422
                    )
                boundary = token_urlsafe(24)
                metadata = encode(
                    {
                        "snippet": {
                            "videoId": request.video,
                            "language": language,
                            "name": name,
                        }
                    }
                )
                payload = (
                    (
                        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{metadata}\r\n"
                        f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n"
                    ).encode("utf-8")
                    + path.read_bytes()
                    + f"\r\n--{boundary}--\r\n".encode()
                )
                self._request(
                    "POST",
                    UPLOAD + "/captions",
                    token,
                    params={"uploadType": "multipart", "part": "snippet"},
                    headers={"Content-Type": f"multipart/related; boundary={boundary}"},
                    content=payload,
                )
            with self.store.transaction() as db:
                self._event(db, pid, export, actor, "caption_verified:" + language)

    def sync(self, actor, pid, export, days):
        with self.store.transaction(write=False) as db:
            row, channel = self._bound(db, actor, pid, export)
            if not row["video"]:
                raise RuleError("Link a published YouTube video first")
        token = self._access(channel)
        video = self._video(token, row["channel"], row["video"])
        if video["status"].get("privacyStatus") not in {"public", "unlisted"}:
            raise RuleError("Metrics windows start when the video is published")
        pacific = ZoneInfo("America/Los_Angeles")
        start = (
            datetime.fromisoformat(video["snippet"]["publishedAt"])
            .astimezone(pacific)
            .date()
        )
        end = start + timedelta(days=days - 1)
        if end >= datetime.now(pacific).date():
            raise RuleError("This observation window is still open")
        query = {
            "ids": "channel==" + channel["channel"],
            "filters": "video==" + row["video"],
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
        }

        def report(metrics, dimension=None):
            params = query | {"metrics": metrics}
            if dimension:
                params["dimensions"] = dimension
            result = self._json("GET", ANALYTICS, token, params=params)
            names = [header["name"] for header in result.get("columnHeaders", [])]
            return [
                dict(zip(names, values, strict=True))
                for values in result.get("rows", [])
            ]

        measures = "views,engagedViews,estimatedMinutesWatched,averageViewDuration,averageViewPercentage"
        daily = report(measures, "day")
        covered_through = max(
            (date.fromisoformat(point["day"]) for point in daily), default=None
        )
        complete = covered_through is not None and covered_through >= end
        data = {
            "source": "youtube_analytics",
            "video": row["video"],
            "timezone": "America/Los_Angeles",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "complete": complete,
            "covered_through": covered_through.isoformat() if covered_through else None,
            "totals": (report(measures) or [None])[0] if daily else None,
            "daily": daily,
            "traffic": report(
                "views,engagedViews,estimatedMinutesWatched", "insightTrafficSourceType"
            )
            if daily
            else [],
            "retention": report(
                "audienceWatchRatio,relativeRetentionPerformance",
                "elapsedVideoTimeRatio",
            )
            if daily
            else [],
        }
        with self.store.transaction() as db:
            current, active = self._bound(db, actor, pid, export)
            if (
                active["revision"] != channel["revision"]
                or current["video"] != row["video"]
            ):
                raise RuleError("YouTube connection changed during import")
            previous = db.execute(
                "SELECT data FROM youtube_metrics WHERE project=? AND export=? AND days=?",
                (pid, export, days),
            ).fetchone()
            if previous:
                prior = unpack(previous, "data")["data"]
                if prior.get("start") == data["start"] and "reach" in prior:
                    data["reach"] = prior["reach"]
            db.execute(
                """INSERT INTO youtube_metrics VALUES(?,?,?,?,?)
                          ON CONFLICT(project,export,days) DO UPDATE SET data=excluded.data,retrieved=excluded.retrieved""",
                (pid, export, days, encode(data), time.time()),
            )
        return self.snapshot(actor, pid, export)

    def _pages(self, url, token, key, params=None):
        params = dict(params or {})
        for _ in range(12):
            page = self._json("GET", url, token, params=params)
            yield from page.get(key, [])
            if not page.get("nextPageToken"):
                return
            params["pageToken"] = page["nextPageToken"]
        raise RuleError("YouTube returned too many report pages", 502)

    def _reach_job(self, token):
        for item in self._pages(REPORTING + "/v1/jobs", token, "jobs"):
            if (
                item.get("reportTypeId") == REACH_TYPE
                and item.get("name") == REACH_NAME
                and not item.get("expireTime")
            ):
                job = item.get("id", "")
                if re.fullmatch(r"[A-Za-z0-9_-]{1,180}", job):
                    return job
        return None

    def enable_reach(self, actor):
        lock = FileLock(self.store.directory / "youtube-reporting.lock")
        try:
            with lock.acquire(timeout=0):
                with self.store.transaction(write=False) as db:
                    channel = self._connection(db, actor)
                token = self._access(channel)
                if not self._reach_job(token):
                    self._json(
                        "POST",
                        REPORTING + "/v1/jobs",
                        token,
                        json={"name": REACH_NAME, "reportTypeId": REACH_TYPE},
                    )
        except Timeout:
            raise RuleError("A YouTube reporting request is in progress") from None
        return {"enabled": True}

    def _download_report(self, token, url):
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != "youtubereporting.googleapis.com"
            or not parts.path.startswith(("/v1/media/", "/download/v1/media/"))
            or parts.fragment
        ):
            raise RuleError("Unexpected YouTube report download address", 502)
        result = bytearray()
        try:
            with (
                self._client() as client,
                client.stream(
                    "GET", url, headers={"Authorization": "Bearer " + token}
                ) as response,
            ):
                if not response.is_success:
                    raise RuleError("YouTube report download is unavailable", 502)
                for block in response.iter_bytes(64 * 1024):
                    result.extend(block)
                    if len(result) > 32 * 1024 * 1024:
                        raise RuleError(
                            "This YouTube report exceeds the workspace download limit",
                            502,
                        )
        except httpx.HTTPError:
            raise RuleError("YouTube report download interrupted", 502) from None
        try:
            return csv.DictReader(StringIO(result.decode("utf-8-sig")))
        except UnicodeDecodeError:
            raise RuleError("Invalid YouTube report encoding", 502) from None

    def sync_reach(self, actor, pid, export, days):
        with self.store.transaction(write=False) as db:
            row, channel = self._bound(db, actor, pid, export)
            record = db.execute(
                "SELECT data FROM youtube_metrics WHERE project=? AND export=? AND days=?",
                (pid, export, days),
            ).fetchone()
            if not row["video"] or not record:
                raise RuleError("Import the Analytics window before importing reach")
            observation = unpack(record, "data")["data"]
        token = self._access(channel)
        self._video(token, row["channel"], row["video"])
        job = self._reach_job(token)
        if not job:
            raise RuleError("Enable reach reporting in YouTube channel settings")
        pacific = ZoneInfo(observation["timezone"])
        start, end = (date.fromisoformat(observation[key]) for key in ("start", "end"))

        def utc(day):
            instant = datetime.combine(day, civil_time.min, pacific)
            return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        params = {
            "startTimeAtOrAfter": utc(start),
            "startTimeBefore": utc(end + timedelta(days=1)),
        }
        reports = {}
        for item in self._pages(
            f"{REPORTING}/v1/jobs/{job}/reports", token, "reports", params
        ):
            day = datetime.fromisoformat(item["startTime"]).astimezone(pacific).date()
            if start <= day <= end and (
                day not in reports or item["createTime"] > reports[day]["createTime"]
            ):
                reports[day] = item
        fields = (
            "date",
            "traffic_source_type",
            "traffic_source_detail",
            "device_type",
            "video_thumbnail_impressions",
            "video_thumbnail_impressions_ctr",
        )
        rows = []
        for item in reports.values():
            for entry in self._download_report(token, item["downloadUrl"]):
                if (
                    entry.get("video_id") == row["video"]
                    and entry.get("channel_id") == row["channel"]
                ):
                    rows.append({key: entry.get(key) for key in fields})
                    if len(rows) > 10000:
                        raise RuleError(
                            "This video has too many reach rows in the selected window",
                            502,
                        )
        reach = {
            "source": "youtube_reporting",
            "days_available": len(reports),
            "rows": rows,
        }
        with self.store.transaction() as db:
            current, active = self._bound(db, actor, pid, export)
            latest = db.execute(
                "SELECT data FROM youtube_metrics WHERE project=? AND export=? AND days=?",
                (pid, export, days),
            ).fetchone()
            if (
                active["revision"] != channel["revision"]
                or current["video"] != row["video"]
                or not latest
            ):
                raise RuleError("YouTube connection changed during import")
            data = unpack(latest, "data")["data"]
            if data["start"] != observation["start"]:
                raise RuleError("The observation window changed during import")
            db.execute(
                "UPDATE youtube_metrics SET data=?,retrieved=? WHERE project=? AND export=? AND days=?",
                (encode(data | {"reach": reach}), time.time(), pid, export, days),
            )
        return self.snapshot(actor, pid, export)
