"""Subscription and device-login contracts with mocked providers."""

import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path
from subprocess import CompletedProcess

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.production import codex_cli
from app.production.api import PASSWORD_ITERATIONS, PublicLogin, create_app
from app.production.codex_login import VERIFY_URL
from app.production.store import RuleError
from app.production.workbench import Workbench
from app.production.worker import Worker
from app.production import models as m


def test_public_operator_can_authorize_codex_with_device_code(tmp_path, monkeypatch):
    processes = []
    signed_in = False

    class AppServer:
        def __init__(self):
            self.stdin = self
            self.stdout = asyncio.StreamReader()
            self.returncode = None

        def reply(self, message):
            self.stdout.feed_data(json.dumps(message).encode() + b"\n")

        def write(self, line):
            request = json.loads(line)
            if request["method"] == "initialize":
                self.reply({"id": request["id"], "result": {}})
            if request["method"] == "account/login/start":
                assert request["params"] == {"type": "chatgptDeviceCode"}
                self.reply(
                    {
                        "id": request["id"],
                        "result": {
                            "type": "chatgptDeviceCode",
                            "loginId": "device-login-test",
                            "verificationUrl": VERIFY_URL,
                            "userCode": "ABCD-1234",
                        },
                    }
                )

        async def drain(self):
            pass

        def terminate(self):
            self.returncode = 0
            self.stdout.feed_eof()

        async def wait(self):
            return self.returncode

    async def spawn(*args, **kwargs):
        assert args[-3:] == ("app-server", "--listen", "stdio://")
        assert Path(kwargs["cwd"]).is_dir()
        process = AppServer()
        processes.append(process)
        return process

    async def authorize_device():
        nonlocal signed_in
        signed_in = True
        processes[-1].reply(
            {
                "method": "account/login/completed",
                "params": {"loginId": "device-login-test", "success": True},
            }
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(codex_cli, "executable", lambda: "fixture-codex")
    monkeypatch.setattr(
        codex_cli, "connection_status", lambda: "chatgpt" if signed_in else "login_required"
    )
    origin = "https://studio.example.test"
    password = "operator password for testing only"
    salt = bytes(range(16))
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    login = PublicLogin(
        origin,
        "operator",
        "operator",
        f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}",
    )
    token = "a" * 32
    app = create_app(
        tmp_path, {token: "operator"}, worker_enabled=False, public_login=login
    )
    path = "/api/providers/codex/login"
    headers = {"Origin": origin}
    with TestClient(app, base_url=origin) as client:
        assert client.post(path, headers=headers).status_code == 401
        assert client.get(path).status_code == 401
        assert client.post(
            "http://127.0.0.1" + path, headers={"Authorization": "Bearer " + token}
        ).status_code == 404
        assert client.post(
            "/api/session", json={"username": "operator", "password": password},
            headers=headers,
        ).status_code == 200
        assert client.get("/api/providers").json()[0]["device_login"] is True
        assert client.post(path).status_code == 403
        assert client.post(path, headers=headers).json() == {
            "status": "pending", "url": VERIFY_URL, "code": "ABCD-1234"
        }
        assert client.post(path, headers=headers).json()["status"] == "pending"
        assert len(processes) == 1
        assert client.get(path).json() == {"status": "pending"}
        assert client.delete(path, headers=headers).status_code == 200
        assert processes[0].returncode == 0
        assert client.get(path).json() == {"status": "idle"}
        assert client.post(path, headers=headers).json()["status"] == "pending"
        client.portal.call(authorize_device)
        for _ in range(10):
            if client.get(path).json()["status"] == "connected":
                break
        assert client.get(path).json() == {"status": "connected"}
        assert processes[1].returncode == 0
        assert client.get("/api/providers").json()[0]["credential_present"] is True
        assert client.post(path, headers=headers).json() == {"status": "connected"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_cli, "executable", lambda: "fixture-codex")
    store = Workbench(tmp_path)
    pid = store.create(
        "operator",
        m.Project(title="Test", topic="Communication", audience="General").model_dump(),
    )
    return store, pid


def test_codex_requires_authorization_and_never_falls_back_to_api(project, monkeypatch):
    store, pid = project
    requests = [m.RequestJob(operation="research", provider="codex")]
    quote = store.quote("operator", pid, requests)
    assert quote["total"] == 0
    assert quote["items"][0]["billing"] == "subscription"
    calls = []

    def generate(kind, messages, model, *_):
        calls.append((kind, model))
        raise codex_cli.CodexError("Codex reported a subscription usage or rate limit")

    monkeypatch.setattr(codex_cli, "generate", generate)
    monkeypatch.setenv("MPT_PRODUCTION_OPENAI_KEY", "fixture-paid-key")
    worker = Worker(store)
    assert not worker.step()
    assert calls == []
    [job] = store.authorize("operator", pid, quote["id"], 0)
    assert store.authorize("operator", pid, quote["id"], 0) == [job]
    assert worker.step()
    assert not worker.step()
    assert calls == [("research", "cli-default")]
    state = store.snapshot("operator", pid)
    assert state["jobs"][0]["status"] == "FAILED"
    assert "subscription usage or rate limit" in state["jobs"][0]["last_error"]
    assert state["costs"]["actual"] == state["costs"]["uncertain"] == 0


def test_project_default_codex_yields_reviewable_text(project, monkeypatch):
    store, pid = project
    with store.transaction() as db:
        asset = store.add(
            db, pid, "research", "research", {"claims": [], "sources": []}
        )
    store.review("operator", pid, [asset["id"]], m.ReviewInput(status="APPROVED"))
    monkeypatch.setenv("MPT_PRODUCTION_CODEX_MODEL", "fixture-model")
    monkeypatch.setattr(
        codex_cli,
        "generate",
        lambda kind, *_: {
            "sections": [
                {
                    "key": "intro",
                    "title": "Opening",
                    "narration": "A hypothetical world.",
                    "seconds": 3,
                    "sources": [],
                    "category": "speculation",
                }
            ],
        },
    )
    token = "a" * 32
    app = create_app(
        store.directory,
        {token: "operator"},
        worker_enabled=False,
        allowed_hosts=("testserver",),
    )
    auth = {"Authorization": "Bearer " + token}
    client = TestClient(app, headers=auth)
    root = "/api/projects/" + pid
    assert (
        client.put(
            root + "/defaults", json={"script": "codex", "translation": "codex"}
        ).status_code
        == 200
    )
    quote = client.post(
        root + "/quotes", json={"jobs": [{"operation": "script"}]}
    ).json()
    assert quote["items"][0]["model"] == "fixture-model"
    assert quote["items"][0]["provider"] == "codex"
    response = client.post(
        root + "/quotes/" + quote["id"] + "/authorize",
        json={"maximum": 0, "confirmed": True},
    )
    assert response.status_code == 202
    assert Worker(store).step()
    with store.transaction(write=False) as db:
        script = store.head(db, pid, "script")
    assert script["status"] == "AWAITING_REVIEW"
    assert script["data"]["sections"][0]["narration"] == "A hypothetical world."


def test_codex_login_without_chatgpt_never_starts_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_cli, "executable", lambda: "fixture-codex")
    monkeypatch.setattr(
        codex_cli.subprocess,
        "run",
        lambda args, **_: CompletedProcess(args, 0, b"Logged in using an API key"),
    )
    monkeypatch.setattr(
        codex_cli.subprocess,
        "Popen",
        lambda *_a, **_k: pytest.fail("Codex exec started"),
    )
    with pytest.raises(codex_cli.CodexError, match="with ChatGPT"):
        codex_cli.generate("research", [], "cli-default", tmp_path, lambda: False, 10)


def test_codex_cli_uses_bounded_structured_job_and_no_key_or_agent_tools(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(codex_cli, "executable", lambda: "fixture-codex")
    monkeypatch.setattr(
        codex_cli.subprocess,
        "run",
        lambda args, **_: CompletedProcess(args, 0, b"Logged in using ChatGPT\n"),
    )
    for key in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "MPT_PRODUCTION_OPENAI_KEY",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.setenv(key, "test-value")
    observed = []

    class Child:
        returncode = 0

        def __init__(self, args, *, stdin, env, cwd, **_):
            observed.append(args)
            assert not (
                {
                    "OPENAI_API_KEY",
                    "CODEX_API_KEY",
                    "MPT_PRODUCTION_OPENAI_KEY",
                    "OPENAI_BASE_URL",
                }
                & env.keys()
            )
            assert cwd == tmp_path
            assert 'forced_login_method="chatgpt"' in args
            assert 'model_provider="openai"' in args
            assert 'web_search="disabled"' in args
            assert all(
                flag in args
                for flag in (
                    "--no-daemon",
                    "--ignore-user-config",
                    "--ephemeral",
                    "read-only",
                    "shell_tool",
                )
            )
            assert args[-1] == "-"
            assert "source text" in stdin.read().decode("utf-8")
            shape = json.loads(
                Path(args[args.index("--output-schema") + 1]).read_text()
            )
            assert shape["additionalProperties"] is False
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text('{"claims":[]}', encoding="utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def poll(self):
            return 0

    monkeypatch.setattr(codex_cli.subprocess, "Popen", Child)
    result = codex_cli.generate(
        "research",
        [{"content": "Create claims"}, {"content": "source text"}],
        "fixture-model",
        tmp_path,
        lambda: False,
        10,
    )
    assert result == {"claims": []}
    assert observed[0][observed[0].index("--model") + 1] == "fixture-model"


def test_codex_interrupted_submission_needs_new_confirmation(project):
    store, pid = project
    quote = store.quote(
        "operator", pid, [m.RequestJob(operation="research", provider="codex")]
    )
    [jid] = store.authorize("operator", pid, quote["id"], 0)
    assert store.claim()["id"] == jid
    reopened = Workbench(store.directory)
    reopened.recover()
    assert reopened.claim() is None
    assert reopened.snapshot("operator", pid)["jobs"][0]["status"] == "FAILED"
    with pytest.raises(RuleError, match="supports research"):
        reopened.quote(
            "operator", pid, [m.RequestJob(operation="assembly", provider="codex")]
        )


def test_imported_storyboard_frame_requires_its_own_review(project):
    store, pid = project
    actor = "operator"
    decision = m.ReviewInput(status="APPROVED")
    with store.transaction() as db:
        research = store.add(
            db, pid, "research", "research", {"claims": [], "sources": []}
        )
    store.review(actor, pid, [research["id"]], decision)
    with store.transaction() as db:
        script = store.script(
            db,
            pid,
            {
                "sections": [
                    {
                        "key": "intro",
                        "title": "Opening",
                        "narration": "A hypothetical world.",
                        "seconds": 3,
                    }
                ]
            },
            store.require(db, pid, "research"),
        )
    store.review(actor, pid, [script["id"]], decision)
    [sid] = store.storyboard(actor, pid)
    token = "a" * 32
    app = create_app(
        store.directory,
        {token: actor},
        worker_enabled=False,
        allowed_hosts=("testserver",),
    )
    client = TestClient(app, headers={"Authorization": "Bearer " + token})
    image = BytesIO()
    Image.new("RGB", (120, 80), "navy").save(image, format="PNG")
    url = f"/api/projects/{pid}/uploads?kind=image&scene={sid}&title=Reference"
    assert client.post(url, content=image.getvalue()).status_code == 409
    with store.transaction(write=False) as db:
        scene = store.head(db, pid, f"scene/{sid}")
    store.review(actor, pid, [scene["id"]], decision)
    uploaded = client.post(url, content=image.getvalue())
    assert uploaded.status_code == 201
    frame = uploaded.json()
    assert frame["slot"] == f"image/{sid}"
    assert frame["status"] == "AWAITING_REVIEW"
    video = [m.RequestJob(operation="video", scene=sid)]
    with pytest.raises(RuleError, match="image approval"):
        store.quote(actor, pid, video)
    store.review(actor, pid, [frame["id"]], decision)
    assert store.quote(actor, pid, video)["total"] == 0
    assert not store.snapshot(actor, pid)["jobs"]


def test_health_identifies_the_current_process_revision(project, monkeypatch):
    store, _ = project
    revision = "a" * 40
    monkeypatch.setenv("MPT_PRODUCTION_RELEASE", revision)
    app = create_app(
        store.directory,
        {"a" * 32: "operator"},
        worker_enabled=False,
        allowed_hosts=("testserver",),
    )
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "revision": revision,
        "active_jobs": 0,
    }


def test_deploy_drain_pauses_claims_until_released(project, monkeypatch):
    fcntl = pytest.importorskip("fcntl")
    store, pid = project
    drain = store.directory / "drain.lock"
    drain.touch()
    monkeypatch.setenv("MPT_PRODUCTION_DRAIN_LOCK", str(drain))
    quote = store.quote("operator", pid, [m.RequestJob(operation="research")])
    [jid] = store.authorize("operator", pid, quote["id"], 0)
    with drain.open("rb+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        assert store.claim() is None
    assert store.claim()["id"] == jid
