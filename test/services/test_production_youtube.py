"""OAuth, resumed upload, visibility reconciliation and analytics with mocked Google APIs."""

import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx

from app.production import models as m, youtube
from app.production.api import create_app


CHANNEL = "UC" + "a" * 22
FOREIGN = "UC" + "b" * 22
VIDEO = "QA000000001"
ORIGIN = "http://testserver"
LINK = youtube.UPLOAD + "/videos?upload_id=qa-private-session"
TOKENS = {"a" * 32: "alice", "b" * 32: "bob"}


class Google:
    def __init__(self):
        self.channel = CHANNEL
        self.sent = []
        self.visibility = "private"
        self.scope = youtube.READ_SCOPES | {youtube.WRITE_SCOPE}
        self.client_secret = "local-test-secret"
        self.origin = ORIGIN
        self.fail_upload = True
        self.fail_publish = True
        self.job = False
        self.caption = False

    def video(self):
        return {
            "id": VIDEO,
            "snippet": {
                "channelId": self.channel,
                "title": "Private documentary",
                "publishedAt": "2026-10-15T23:00:00Z",
            },
            "status": {
                "privacyStatus": self.visibility,
                "uploadStatus": "processed",
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": True,
            },
        }

    def __call__(self, request):
        method, path = request.method, request.url.path
        self.sent.append((method, path, request.headers.get("content-range")))
        if str(request.url) == youtube.TOKEN:
            payload = parse_qs(request.content.decode())
            if payload["client_secret"] != [self.client_secret]:
                return httpx.Response(401, json={"error": "invalid_client"})
            if payload["grant_type"] == ["authorization_code"]:
                assert payload["code_verifier"] and payload["redirect_uri"] == [
                    self.origin + youtube.CALLBACK
                ]
                return httpx.Response(
                    200,
                    json={
                        "access_token": "mock-access",
                        "refresh_token": "mock-refresh",
                        "scope": " ".join(self.scope),
                    },
                )
            assert payload["refresh_token"] == ["mock-refresh"]
            return httpx.Response(200, json={"access_token": "mock-access"})
        if str(request.url) == youtube.REVOKE:
            assert parse_qs(request.content.decode())["token"] == ["mock-refresh"]
            return httpx.Response(200)
        assert request.headers["authorization"] == "Bearer mock-access"
        if path == "/youtube/v3/channels":
            assert request.url.params["mine"] == "true"
            return httpx.Response(
                200,
                json={
                    "items": [{"id": self.channel, "snippet": {"title": "HYPHOTENIC"}}]
                },
            )
        if path == "/upload/youtube/v3/videos":
            if method == "POST":
                assert request.url.params["uploadType"] == "resumable"
                assert json.loads(request.content)["status"] == {
                    "privacyStatus": "private",
                    "selfDeclaredMadeForKids": False,
                    "containsSyntheticMedia": True,
                }
                return httpx.Response(200, headers={"location": LINK})
            if request.headers["content-range"] == "bytes */9":
                return httpx.Response(308, headers={"Range": "bytes=0-3"})
            if self.fail_upload:
                self.fail_upload = False
                assert request.content == b"test-file"
                raise httpx.ReadTimeout("simulated interruption", request=request)
            assert request.headers["content-range"] == "bytes 4-8/9"
            assert request.content == b"-file"
            return httpx.Response(201, json=self.video())
        if path == "/youtube/v3/videos":
            if method == "GET":
                return httpx.Response(200, json={"items": [self.video()]})
            assert json.loads(request.content)["status"] == {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": True,
            }
            self.visibility = "public"
            if self.fail_publish:
                self.fail_publish = False
                raise httpx.ReadTimeout("simulated response loss", request=request)
            return httpx.Response(200, json=self.video())
        if path == "/upload/youtube/v3/thumbnails/set":
            return httpx.Response(
                200,
                json={"items": [{"default": {"url": "https://example.test/image"}}]},
            )
        if path == "/youtube/v3/captions":
            tracks = (
                [{"snippet": {"language": "en-US", "name": self.caption}}]
                if self.caption
                else []
            )
            return httpx.Response(200, json={"items": tracks})
        if path == "/upload/youtube/v3/captions":
            self.caption = (
                request.content.decode().split('"name": "', 1)[1].split('"', 1)[0]
            )
            return httpx.Response(200, json={"id": "caption-test"})
        if path == "/v2/reports":
            assert request.url.params["ids"] == "channel==" + CHANNEL
            assert request.url.params["filters"] == "video==" + VIDEO
            assert request.url.params["startDate"] == "2026-10-15"
            assert request.url.params["endDate"] == "2026-10-21"
            metrics = request.url.params["metrics"].split(",")
            dimension = request.url.params.get("dimensions")
            row = [100, 80, 900, 600, 60]
            if dimension == "day":
                row = ["2026-10-21", *row]
            elif dimension == "insightTrafficSourceType":
                row = ["RELATED_VIDEO", 80, 65, 750]
            elif dimension == "elapsedVideoTimeRatio":
                row = [0.05, 1.12, 0.63]
            columns = ([dimension] if dimension else []) + metrics
            return httpx.Response(
                200,
                json={"columnHeaders": [{"name": n} for n in columns], "rows": [row]},
            )
        if path == "/v1/jobs":
            job = {
                "name": youtube.REACH_NAME,
                "reportTypeId": youtube.REACH_TYPE,
                "id": "reach-1",
            }
            if method == "POST":
                assert not self.job
                self.job = True
                return httpx.Response(200, json=job)
            return httpx.Response(200, json={"jobs": [job] if self.job else []})
        if path == "/v1/jobs/reach-1/reports":
            return httpx.Response(
                200,
                json={
                    "reports": [
                        {
                            "startTime": "2026-10-20T07:00:00Z",
                            "createTime": "2026-10-22T07:00:00Z",
                            "downloadUrl": youtube.REPORTING + "/v1/media/report",
                        }
                    ]
                },
            )
        if path == "/v1/media/report":
            return httpx.Response(
                200,
                text="date,channel_id,video_id,traffic_source_type,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
                + f"20261020,{CHANNEL},{VIDEO},5,300,0.05\n"
                + f"20261020,{FOREIGN},{VIDEO},5,99999,0.25\n",
            )
        raise AssertionError((method, path))


def fixture(tmp_path, google):
    settings = youtube.Settings(
        "local-test-client",
        "local-test-secret",
        ORIGIN + youtube.CALLBACK,
        Fernet(Fernet.generate_key()),
        CHANNEL,
    )
    return create_app(
        tmp_path,
        TOKENS,
        allowed_hosts=("testserver",),
        worker_enabled=False,
        youtube_settings=settings,
        youtube_transport=httpx.MockTransport(google),
    )


def content(store, *, watermark=False):
    data = m.Project(
        title="Private documentary", topic="A QA export", audience="General"
    ).model_dump()
    pid = store.create("alice", data)

    def media_file(extension, data):
        name = uuid4().hex + "." + extension
        (store.directory / "assets" / name).write_bytes(data)
        return name

    with store.transaction() as db:
        asset = {
            "watermark": watermark,
            "languages": ["en-US"],
            "seconds": 600,
            "aspect": "16:9",
        }
        caption = store.add(
            db,
            pid,
            "captions/test/en-US",
            "captions",
            {"language": "en-US"},
            media=media_file("srt", b"1\n00:00:00,000 --> 00:00:01,000\nDemo\n"),
        )
        assembly = store.add(db, pid, "assembly", "assembly", asset)
        thumb = store.add(
            db,
            pid,
            "thumbnail/impact",
            "thumbnail",
            {"headline": "QA"},
            media=media_file("jpg", b"mock-jpeg"),
        )
    store.review(
        "alice", pid, [assembly["id"], thumb["id"]], m.ReviewInput(status="APPROVED")
    )
    with store.transaction() as db:
        export = store.add(
            db,
            pid,
            "export",
            "export",
            asset | {"captions": {"en-US": caption["id"]}},
            [{"id": assembly["id"]}],
            media_file("mp4", b"test-file"),
        )
    return "/api/projects/" + pid + "/youtube/" + export["id"], thumb["id"]


def connect(client, mode="publish"):
    assert client.post("/api/session", json={"token": "a" * 32}).status_code == 200
    browser = {"Origin": ORIGIN}
    response = client.post("/api/youtube/connect", json={"mode": mode}, headers=browser)
    assert response.status_code == 200
    query = parse_qs(urlsplit(response.json()["url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [ORIGIN + youtube.CALLBACK]
    assert (youtube.WRITE_SCOPE in query["scope"][0].split()) is (mode == "publish")
    callback = client.get(
        youtube.CALLBACK,
        params={"state": query["state"][0], "code": "mock-code"},
        follow_redirects=False,
    )
    return callback, query


def test_oauth_publishing_and_analytics_with_reconciliation(tmp_path):
    google = Google()
    app = fixture(tmp_path, google)
    store = app.state.store
    route, thumbnail = content(store)
    stale_route, _ = content(store, watermark=True)
    browser = {"Origin": ORIGIN}
    raw = {
        "channel": CHANNEL,
        "title": "Private documentary",
        "description": "Proof",
        "made_for_kids": False,
        "synthetic": True,
        "confirmed": True,
    }
    with TestClient(app) as client:
        callback, query = connect(client)
        assert callback.headers["location"].endswith("connected")
        repeat = client.get(
            youtube.CALLBACK,
            params={"state": query["state"][0], "code": "again"},
            follow_redirects=False,
        )
        assert repeat.headers["location"].endswith("error")
        assert client.get("/api/youtube").json()["connection"] == {
            "channel": CHANNEL,
            "title": "HYPHOTENIC",
            "writable": 1,
        }
        with store.transaction(write=False) as db:
            assert (
                b"mock-refresh"
                not in db.execute(
                    "SELECT refresh_token FROM youtube_connections"
                ).fetchone()[0]
            )
        assert (
            client.get(
                route, headers={"Authorization": "Bearer " + "b" * 32}
            ).status_code
            == 404
        )
        assert (
            client.post(stale_route + "/upload", headers=browser, json=raw).status_code
            == 409
        )
        assert (
            client.post(
                route + "/upload", headers=browser, json=dict(raw, confirmed=False)
            ).status_code
            == 422
        )
        assert (
            client.post(
                route + "/upload", headers=browser, json=dict(raw, channel=FOREIGN)
            ).status_code
            == 409
        )
        assert (
            client.post(route + "/upload", headers=browser, json=raw).status_code == 202
        )
        assert not any(
            path == "/upload/youtube/v3/videos" for _, path, _ in google.sent
        )
        assert app.state.youtube.step()
        paused = client.get(route).json()["publication"]
        assert paused["state"] == "PAUSED" and "upload_url" not in paused
        assert client.post(route + "/cancel", headers=browser).status_code == 200
        assert client.post(route + "/resume", headers=browser).status_code == 202
        assert app.state.youtube.step()
        receipt = client.get(route).json()["publication"]
        assert (receipt["video"], receipt["visibility"], receipt["state"]) == (
            VIDEO,
            "private",
            "UPLOADED",
        )
        assert (
            sum(
                method == "POST" and path == "/upload/youtube/v3/videos"
                for method, path, _ in google.sent
            )
            == 1
        )
        assert (
            client.post(route + "/upload", headers=browser, json=raw).json()[
                "publication"
            ]["video"]
            == VIDEO
        )
        assert (
            client.post(
                route + "/assets",
                headers=browser,
                json={
                    "video": VIDEO,
                    "thumbnail": thumbnail,
                    "captions": ["en-US"],
                    "confirmed": True,
                },
            ).status_code
            == 200
        )
        assert google.caption.startswith("en-US")
        assert (
            client.post(
                route + "/assets",
                headers=browser,
                json={"video": VIDEO, "captions": ["en-US"], "confirmed": True},
            ).status_code
            == 200
        )
        assert (
            sum(path == "/upload/youtube/v3/captions" for _, path, _ in google.sent)
            == 1
        )
        unknown = client.post(
            route + "/publish",
            headers=browser,
            json={"video": VIDEO, "confirmed": True},
        ).json()
        assert unknown["publication"]["state"] == "VISIBILITY_UNKNOWN"
        assert (
            client.post(
                route + "/publish",
                headers=browser,
                json={"video": VIDEO, "confirmed": True},
            ).status_code
            == 409
        )
        assert (
            client.post(route + "/refresh", headers=browser).json()["publication"][
                "visibility"
            ]
            == "public"
        )
        assert (
            sum(
                method == "PUT" and path == "/youtube/v3/videos"
                for method, path, _ in google.sent
            )
            == 1
        )
        record = client.post(route + "/metrics/7", headers=browser).json()["metrics"][
            0
        ]["data"]
        assert record["complete"] and record["totals"]["views"] == 100
        assert record["retention"][0]["audienceWatchRatio"] == 1.12
        for _ in range(2):
            assert (
                client.post(
                    "/api/youtube/reach", headers=browser, json={"confirmed": True}
                ).status_code
                == 200
            )
        reach = client.post(route + "/reach/7", headers=browser).json()["metrics"][0][
            "data"
        ]["reach"]
        assert reach["days_available"] == 1 and len(reach["rows"]) == 1
        assert reach["rows"][0]["video_thumbnail_impressions_ctr"] == "0.05"
        assert client.delete("/api/youtube", headers=browser).status_code == 200
        cleaned = client.get(route).json()
        assert (
            cleaned["metrics"] == [] and cleaned["publication"]["state"] == "DETACHED"
        )
        assert (
            cleaned["publication"]["video"] is None and cleaned["publication"]["events"]
        )


def test_wrong_channel_and_read_only_oauth(tmp_path):
    google = Google()
    google.channel = FOREIGN
    google.scope = youtube.READ_SCOPES
    app = fixture(tmp_path, google)
    route, _ = content(app.state.store)
    with TestClient(app) as client:
        callback, _ = connect(client, "read")
        assert callback.headers["location"].endswith("mismatch")
        assert client.get("/api/youtube").json()["connection"] is None
        google.channel = CHANNEL
        callback, _ = connect(client, "read")
        assert callback.headers["location"].endswith("connected")
        response = client.post(
            route + "/upload",
            headers={"Origin": ORIGIN},
            json={
                "channel": CHANNEL,
                "title": "QA",
                "made_for_kids": False,
                "synthetic": False,
                "confirmed": True,
            },
        )
        assert response.status_code == 409


def test_owner_setup_is_encrypted_and_persists_across_restart(tmp_path):
    token = "s" * 32
    address = "http://127.0.0.1:8765"
    payload = {
        "client_id": "test-client.apps.googleusercontent.com",
        "client_secret": "local-test-secret",
        "channel": CHANNEL,
        "confirmed": True,
    }

    google = Google()
    google.origin = address

    def app():
        return create_app(
            tmp_path, {token: "operator"}, worker_enabled=False,
            youtube_transport=httpx.MockTransport(google),
        )

    with TestClient(app(), base_url=address) as client:
        assert (
            client.post(
                "/api/youtube/setup",
                json=payload,
                headers={"Authorization": "Bearer " + token},
            ).status_code
            == 403
        )
        assert client.post("/api/session", json={"token": token}).status_code == 200
        response = client.post(
            "/api/youtube/setup", json=payload, headers={"Origin": address}
        )
        assert response.status_code == 200 and response.json()["configured"] is True
        assert payload["client_secret"] not in response.text
        assert (
            payload["client_secret"].encode()
            not in (tmp_path / ".youtube-oauth").read_bytes()
        )
    with TestClient(app(), base_url=address) as client:
        assert client.post("/api/session", json={"token": token}).status_code == 200
        assert client.get("/api/youtube").json()["configured"] is True
        accept = client.post(
            "/api/youtube/connect",
            json={"mode": "publish"},
            headers={"Origin": address},
        )
        query = parse_qs(urlsplit(accept.json()["url"]).query)
        assert query["redirect_uri"] == [address + youtube.CALLBACK]
        callback = client.get(
            youtube.CALLBACK,
            params={"state": query["state"][0], "code": "mock-code"},
            follow_redirects=False,
        )
        assert callback.headers["location"].endswith("connected")
        expected = client.get("/api/youtube").json()["connection"]
        setup_file = tmp_path / ".youtube-oauth"
        saved_config = setup_file.read_bytes()
        headers = {"Origin": address}
        updated = dict(payload, client_secret="replacement-secret", channel=None)
        assert client.post("/api/youtube/setup", headers=headers, json=updated).status_code == 502
        assert setup_file.read_bytes() == saved_config
        google.client_secret = updated["client_secret"]
        wrong_client = dict(updated, client_id="other.apps.googleusercontent.com")
        assert client.post("/api/youtube/setup", headers=headers, json=wrong_client).status_code == 409
        google.channel = FOREIGN
        assert client.post("/api/youtube/setup", headers=headers, json=updated).status_code == 409
        assert setup_file.read_bytes() == saved_config
        google.channel = CHANNEL
        rotated = client.post("/api/youtube/setup", headers=headers, json=updated)
        assert rotated.status_code == 200
        assert rotated.json()["connection"] == expected
        assert updated["client_secret"].encode() not in setup_file.read_bytes()

    with TestClient(app(), base_url=address) as client:
        assert client.post("/api/session", json={"token": token}).status_code == 200
        assert client.get("/api/youtube").json()["connection"] == expected
        reach = client.post("/api/youtube/reach", headers=headers, json={"confirmed": True})
        assert reach.status_code == 200
