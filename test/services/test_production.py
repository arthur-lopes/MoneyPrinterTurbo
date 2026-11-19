"""Production contracts exercised without credentials or paid requests."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from threading import Event
from uuid import uuid4
import wave

import av
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from app.production import adapters, media, models as m
from app.production.api import create_app
from app.production.store import RuleError
from app.production.workbench import Workbench
from app.production.worker import Worker


@pytest.fixture
def sandbox(tmp_path):
    store = Workbench(tmp_path)
    actor = "operator"
    data = m.Project(
        title="Offline documentary",
        topic="A world without communications",
        audience="General",
        duration=6,
        structure=["Setup", "Conclusion"],
    ).model_dump()
    pid = store.create(actor, data)
    return store, actor, pid


def head(store, pid, name):
    with store.transaction(write=False) as db:
        return store.head(db, pid, name)


def review(store, actor, pid, item, status="APPROVED"):
    return store.review(actor, pid, [item["id"]], m.ReviewInput(status=status))[0]


def authorize(store, actor, pid, *requests):
    q = store.quote(actor, pid, [m.RequestJob(**r) for r in requests])
    return store.authorize(actor, pid, q["id"], q["total"])


def prepare(store, actor, pid):
    review(store, actor, pid, create_research(store, pid))
    worker = Worker(store)
    authorize(store, actor, pid, {"operation": "script"})
    assert worker.step()
    review(store, actor, pid, head(store, pid, "script"))
    return store.storyboard(actor, pid)


def create_research(store, pid):
    with store.transaction() as db:
        return store.add(db, pid, "research", "research", {"sources": [], "claims": []})


def paid(
    store, actor, pid, monkeypatch, operation="research", provider="openai", budget=150
):
    monkeypatch.setenv(
        f"MPT_PRODUCTION_{provider.upper()}_KEY", "mock-provider-test-value"
    )
    store.provider(
        actor,
        m.Provider(
            provider=provider,
            operation=operation,
            model="gpt-4.1-mini" if provider == "openai" else "gen4_turbo",
            ceiling=100,
            enabled=True,
            confirmed=True,
        ),
    )
    store.budget(actor, pid, m.Budget(scope="project", limit=budget, confirmed=True))


def test_approval_order_and_immutable_versions(sandbox):
    store, actor, pid = sandbox
    with pytest.raises(RuleError, match="research approval"):
        store.quote(actor, pid, [m.RequestJob(operation="script")])
    with pytest.raises(RuleError, match="script approval"):
        store.storyboard(actor, pid)
    ids = prepare(store, actor, pid)
    sid = ids[0]
    scene = head(store, pid, f"scene/{sid}")
    with pytest.raises(RuleError, match="scene approval"):
        store.quote(actor, pid, [m.RequestJob(operation="image", scene=sid)])
    review(store, actor, pid, scene)
    with pytest.raises(RuleError, match="image approval"):
        store.quote(actor, pid, [m.RequestJob(operation="video", scene=sid)])
    with pytest.raises(RuleError, match="immutable"):
        review(store, actor, pid, scene, "REJECTED")
    previous = review(store, actor, pid, head(store, pid, f"scene/{ids[1]}"))
    old_script = head(store, pid, "script")
    sections = old_script["data"]["sections"]
    sections[0]["narration"] = "Revised scene narration."
    with store.transaction() as db:
        changed = store.script(
            db, pid, {"sections": sections}, store.require(db, pid, "research")
        )
    review(store, actor, pid, changed)
    assert store.storyboard(actor, pid) == ids
    assert head(store, pid, f"scene/{sid}")["id"] != scene["id"]
    assert head(store, pid, f"scene/{ids[1]}")["id"] == previous["id"]
    assert head(store, pid, f"scene/{ids[1]}")["status"] == "APPROVED"
    with store.transaction(write=False) as db:
        assert (
            store.asset(db, pid, old_script["id"])["data"]["sections"][0]["narration"]
            != sections[0]["narration"]
        )


def test_concurrent_duplicate_and_budget_reservation(sandbox, monkeypatch):
    store, actor, pid = sandbox
    paid(store, actor, pid, monkeypatch)
    quote = store.quote(
        actor, pid, [m.RequestJob(operation="research", provider="openai")]
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: store.authorize(actor, pid, quote["id"], 100), range(12))
        )
    assert all(value == results[0] for value in results)
    assert len(store.snapshot(actor, pid)["jobs"]) == 1
    assert store.snapshot(actor, pid)["costs"]["reserved"] == 100
    with pytest.raises(RuleError, match="different maximum"):
        store.authorize(actor, pid, quote["id"], 110)
    next_pid = store.create(actor, store.snapshot(actor, pid)["data"])
    store.budget(actor, next_pid, m.Budget(scope="project", limit=150, confirmed=True))
    store.budget(actor, pid, m.Budget(scope="day", limit=100, confirmed=True))
    with pytest.raises(RuleError, match="day budget"):
        authorize(
            store, actor, next_pid, {"operation": "research", "provider": "openai"}
        )
    assert not store.snapshot(actor, next_pid)["jobs"]


def test_parallel_distinct_projects_share_budget(sandbox, monkeypatch):
    store, actor, pid = sandbox
    paid(store, actor, pid, monkeypatch)
    pids = [pid, store.create(actor, store.snapshot(actor, pid)["data"])]
    store.budget(actor, pids[1], m.Budget(scope="project", limit=150, confirmed=True))
    store.budget(
        actor,
        pid,
        m.Budget(scope="provider", target="openai", limit=100, confirmed=True),
    )
    quotations = [
        store.quote(
            actor, entry, [m.RequestJob(operation="research", provider="openai")]
        )
        for entry in pids
    ]
    gate = Event()

    def spend(index):
        gate.wait(timeout=3)
        try:
            return store.authorize(actor, pids[index], quotations[index]["id"], 100)
        except RuleError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(spend, i) for i in range(2)]
        gate.set()
        responses = [future.result() for future in futures]
    assert sum(isinstance(value, list) for value in responses) == 1
    assert "Job exceeds the remaining provider budget" in responses


def test_cancellation_timeout_recovery_and_cost_settlement(sandbox, monkeypatch):
    store, actor, pid = sandbox
    paid(store, actor, pid, monkeypatch, budget=400)
    req = {"operation": "research", "provider": "openai"}
    [cancelled] = authorize(store, actor, pid, req)
    store.cancel(actor, pid, cancelled)
    assert store.snapshot(actor, pid)["costs"]["reserved"] == 0
    [timed_out] = authorize(store, actor, pid, req)
    called = []

    class TimeoutAdapter:
        def run(self, job, *_):
            called.append(job["id"])
            raise httpx.ReadTimeout("provider secret not for publication")

    worker = Worker(store, {"openai": TimeoutAdapter()})
    assert worker.step()
    assert not worker.step()
    assert called == [timed_out]
    snapshot = store.snapshot(actor, pid)
    assert {j["status"] for j in snapshot["jobs"]} == {"CANCELLED", "FAILED"}
    assert snapshot["costs"]["reserved"] == 0
    assert snapshot["costs"]["actual"] == 0
    assert snapshot["costs"]["uncertain"] == 100
    assert "provider secret" not in str(snapshot)
    with pytest.raises(RuleError, match="uncertain charge"):
        authorize(store, actor, pid, req)
    store.settle(
        actor,
        pid,
        timed_out,
        m.Settlement(actual=47, receipt="provider receipt 001", confirmed=True),
    )
    [interrupted] = authorize(store, actor, pid, req)
    assert store.claim() is None
    with store.transaction() as db:
        db.execute("UPDATE jobs SET submitted=submitted-10 WHERE id=?", (timed_out,))
    assert store.claim()["id"] == interrupted
    restarted = Workbench(store.directory)
    restarted.recover()
    assert restarted.claim() is None
    assert restarted.snapshot(actor, pid)["costs"]["uncertain"] == 100
    assert restarted.snapshot(actor, pid)["costs"]["actual"] == 47


def test_queued_job_cancels_after_input_change(sandbox):
    store, actor, pid = sandbox
    sid = prepare(store, actor, pid)[0]
    original = review(store, actor, pid, head(store, pid, f"scene/{sid}"))
    [job] = authorize(store, actor, pid, {"operation": "image", "scene": sid})
    store.edit_scene(
        actor, pid, sid, m.Scene(**dict(original["data"], image_prompt="Replacement"))
    )
    assert not Worker(store).step()
    assert (
        next(item for item in store.snapshot(actor, pid)["jobs"] if item["id"] == job)[
            "status"
        ]
        == "CANCELLED"
    )


def test_reduced_budget_blocks_reserved_job_before_submission(sandbox, monkeypatch):
    store, actor, pid = sandbox
    paid(store, actor, pid, monkeypatch)
    [jid] = authorize(
        store, actor, pid, {"operation": "research", "provider": "openai"}
    )
    store.budget(actor, pid, m.Budget(scope="project", limit=0, confirmed=True))
    assert not Worker(store).step()
    item = next(job for job in store.snapshot(actor, pid)["jobs"] if job["id"] == jid)
    assert item["status"] == "CANCELLED"
    assert item["attempts"] == 0
    assert store.snapshot(actor, pid)["costs"]["reserved"] == 0


def test_approving_existing_clip_cancels_queued_replacement(sandbox):
    store, actor, pid = sandbox
    sid = prepare(store, actor, pid)[0]
    scene = review(store, actor, pid, head(store, pid, f"scene/{sid}"))
    with store.transaction() as db:
        preview = store.add(db, pid, f"image/{sid}", "image", {}, [{"id": scene["id"]}])
    review(store, actor, pid, preview)
    with store.transaction() as db:
        clip = store.add(db, pid, f"video/{sid}", "video", {}, [{"id": preview["id"]}])
    review(store, actor, pid, clip, "REJECTED")
    [jid] = authorize(store, actor, pid, {"operation": "video", "scene": sid})
    review(store, actor, pid, clip)
    assert not Worker(store).step()
    status = next(
        job["status"] for job in store.snapshot(actor, pid)["jobs"] if job["id"] == jid
    )
    assert status == "CANCELLED"
    assert head(store, pid, f"video/{sid}")["id"] == clip["id"]


def test_api_auth_owner_and_cross_site(tmp_path):
    token = "a" * 32
    app = create_app(
        tmp_path,
        {token: "alice", "b" * 32: "bob"},
        worker_enabled=False,
        allowed_hosts=("testserver",),
    )
    client = TestClient(app)
    auth = {"Authorization": "Bearer " + token}
    assert client.get("/").status_code == 200
    assert client.get("/api/projects").status_code == 401
    assert (
        client.get("/api/projects", headers={"Host": "example.org", **auth}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/session",
            headers={"Origin": "https://outside.test"},
            json={"token": token},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/session",
            headers={"Origin": "http://testserver"},
            json={"token": token},
        ).status_code
        == 200
    )
    assert client.post("/api/projects", json={}).status_code == 403
    body = {"title": "Test", "topic": "A topic", "audience": "People"}
    pid = client.post(
        "/api/projects", json=body, headers={"Origin": "http://testserver"}
    ).json()["id"]
    p = "/api/projects/" + pid
    other = {"Authorization": "Bearer " + "b" * 32}
    assert client.get(p, headers=other).status_code == 404
    assert client.get(p + "/media/" + uuid4().hex, headers=other).status_code == 404
    assert client.get(p, headers={**auth, "Range": "bytes=0-4,6-9"}).status_code == 416
    assert (
        client.post(
            p + "/quotes/" + uuid4().hex + "/authorize",
            headers=auth,
            json={"confirmed": False, "maximum": 0},
        ).status_code
        == 422
    )
    assert (
        client.post(
            p + "/quotes/" + uuid4().hex + "/authorize",
            headers=auth,
            json={"confirmed": 1, "maximum": 0},
        ).status_code
        == 422
    )
    assert (
        client.post("/api/projects", headers=auth, content=b"1" * 1100000).status_code
        == 413
    )
    assert (
        client.post(
            p + "/uploads?kind=reference&title=bad",
            headers=auth,
            content=b"<svg onload='no'></svg>",
        ).status_code
        == 422
    )
    image = BytesIO()
    Image.new("RGB", (30, 40)).save(image, format="PNG")
    new = client.post(
        p + "/uploads?kind=reference&title=Reference",
        headers=auth,
        content=image.getvalue(),
    )
    assert new.status_code == 201
    assert (
        client.get(new.json()["url"], headers=auth)
        .headers["Content-Type"]
        .startswith("image/png")
    )
    assert client.get(new.json()["url"], headers=other).status_code == 404


def write_voice(destination):
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(bytes(24000))


def test_multilingual_render_and_approval_gate(sandbox, monkeypatch):
    store, actor, pid = sandbox
    with store.transaction() as db:
        project = store.project(db, actor, pid)
        db.execute(
            "UPDATE projects SET data=json_set(data,'$.structure',json('[\"Opening\"]'),'$.duration',2) WHERE id=?",
            (project["id"],),
        )
    [sid] = prepare(store, actor, pid)
    story = review(store, actor, pid, head(store, pid, f"scene/{sid}"))
    worker = Worker(store)
    authorize(store, actor, pid, {"operation": "image", "scene": sid})
    assert worker.step()
    review(store, actor, pid, head(store, pid, f"image/{sid}"))
    authorize(store, actor, pid, {"operation": "video", "scene": sid})
    assert worker.step()
    candidate = head(store, pid, f"video/{sid}")
    review(store, actor, pid, candidate, "REJECTED")
    authorize(store, actor, pid, {"operation": "video", "scene": sid})
    assert worker.step()
    clip = review(store, actor, pid, head(store, pid, f"video/{sid}"))
    assert clip["seq"] == 2
    with pytest.raises(RuleError, match="already has an approved video"):
        store.quote(actor, pid, [m.RequestJob(operation="video", scene=sid)])
    with store.transaction() as db:
        translated = store.add(
            db,
            pid,
            f"translation/{sid}/pt-BR",
            "translation",
            {
                "title": "Título",
                "description": "Descrição",
                "narration": "Uma hipótese.",
            },
            [{"speech": [sid, story["data"]["narration"]]}],
        )
    review(store, actor, pid, translated)
    monkeypatch.setattr(
        media, "speech", lambda _text, _lang, _voice, output, *_: write_voice(output)
    )
    authorize(
        store,
        actor,
        pid,
        *(
            {"operation": "voice", "scene": sid, "language": lang}
            for lang in ("en-US", "pt-BR")
        ),
    )
    for lang in ("en-US", "pt-BR"):
        assert worker.step()
        review(store, actor, pid, head(store, pid, f"voice/{sid}/{lang}"))
    assert head(store, pid, f"video/{sid}")["id"] == clip["id"]
    voice = head(store, pid, f"voice/{sid}/en-US")
    with store.transaction() as db:
        soundtrack = store.add(
            db, pid, "audio/bgm", "audio", {"title": "Test sound"}, media=voice["media"]
        )
    review(store, actor, pid, soundtrack)
    timeline = {
        "clips": [{"scene": sid, "transition": "fade", "effect": soundtrack["id"]}],
        "languages": ["en-US", "pt-BR"],
        "subtitles": True,
        "music": soundtrack["id"],
    }
    authorize(
        store,
        actor,
        pid,
        {"operation": "assembly", "timeline": dict(timeline, watermark=True)},
    )
    assert worker.step()
    draft = review(store, actor, pid, head(store, pid, "assembly"))
    with pytest.raises(RuleError, match="watermarked"):
        authorize(store, actor, pid, {"operation": "export", "version": draft["id"]})
    authorize(store, actor, pid, {"operation": "assembly", "timeline": timeline})
    assert worker.step()
    assembly = head(store, pid, "assembly")
    with pytest.raises(RuleError, match="assembly approval"):
        authorize(store, actor, pid, {"operation": "export", "version": assembly["id"]})
    review(store, actor, pid, assembly)
    authorize(store, actor, pid, {"operation": "export", "version": assembly["id"]})
    assert worker.step()
    output = head(store, pid, "export")
    with store.transaction(write=False) as db:
        assert set(output["data"]["captions"]) == {"en-US", "pt-BR"}
        subtitles = store.asset(db, pid, output["data"]["captions"]["pt-BR"])
        assert (
            media.asset_path(worker.root, subtitles["media"])
            .read_text(encoding="utf-8")
            .endswith("Uma hipótese.\n")
        )
    file = media.asset_path(worker.root, output["media"])
    with av.open(str(file)) as container:
        assert [stream.metadata["language"] for stream in container.streams.audio] == [
            "eng",
            "por",
        ]
        assert [
            stream.metadata["language"] for stream in container.streams.subtitles
        ] == ["eng", "por"]
        assert [
            (stream.width, stream.height) for stream in container.streams.video
        ] == [(1920, 1080)]
        assert next(container.decode(video=0)).width == 1920
    restarted = Workbench(store.directory)
    assert restarted.snapshot(actor, pid)["export_status"] == "COMPLETED"


def test_runway_recovery_polls_saved_task_without_resubmission(sandbox, monkeypatch):
    store, actor, pid = sandbox
    paid(
        store, actor, pid, monkeypatch, operation="video", provider="runway", budget=600
    )
    sid = prepare(store, actor, pid)[0]
    latest = head(store, pid, f"scene/{sid}")
    scene = store.edit_scene(
        actor,
        pid,
        sid,
        m.Scene(**dict(latest["data"], provider="runway", model="gen4_turbo")),
    )
    review(store, actor, pid, scene)
    authorize(store, actor, pid, {"operation": "image", "scene": sid})
    assert Worker(store).step()
    review(store, actor, pid, head(store, pid, f"image/{sid}"))
    [job_id] = authorize(store, actor, pid, {"operation": "video", "scene": sid})
    remote = str(uuid4())
    calls = []

    def intercept(request):
        calls.append((request.method, str(request.url)))
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["model"] == "gen4_turbo"
            assert body["duration"] == 3 and body["ratio"] == "1280:720"
            assert body["promptImage"].startswith("data:image/png;base64,")
            assert request.headers["X-Runway-Version"] == adapters.RUNWAY_VERSION
            return httpx.Response(200, json={"id": remote})
        return httpx.Response(200, json={"status": "FAILED"})

    monkeypatch.setattr(
        adapters,
        "_client",
        lambda _: httpx.Client(transport=httpx.MockTransport(intercept)),
    )
    assert Worker(store).step()
    with store.transaction() as db:
        db.execute("UPDATE jobs SET available=0 WHERE id=?", (job_id,))
    assert store.claim()["remote"] == remote
    reopened = Workbench(store.directory)
    reopened.recover()
    assert Worker(reopened).step()
    assert calls == [
        ("POST", adapters.RUNWAY + "/image_to_video"),
        ("GET", adapters.RUNWAY + "/tasks/" + remote),
    ]
    job = next(
        item for item in reopened.snapshot(actor, pid)["jobs"] if item["id"] == job_id
    )
    assert job["status"] == "FAILED"
    assert job["attempts"] == 1
    assert job["actual"] == 0
    assert job["uncertain"] == 300


def test_openai_research_uses_supplied_source_catalog(sandbox, monkeypatch):
    store, actor, pid = sandbox
    paid(store, actor, pid, monkeypatch)
    source = {
        "title": "An authorized reference",
        "url": "https://example.org/source",
        "excerpt": "The supplied excerpt.",
    }
    [jid] = authorize(
        store,
        actor,
        pid,
        {"operation": "research", "provider": "openai", "sources": [source]},
    )
    calls = []

    def intercept(request):
        calls.append(str(request.url))
        content = json.loads(request.content)
        assert content["model"] == "gpt-4.1-mini"
        assert content["response_format"]["type"] == "json_object"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "claims": [
                                        {
                                            "text": "The supplied excerpt.",
                                            "category": "verified",
                                            "sources": [1],
                                        }
                                    ],
                                    "sources": [
                                        {
                                            "title": "Untrusted suggestion",
                                            "url": "https://example.org/elsewhere",
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(
        adapters,
        "_client",
        lambda _: httpx.Client(transport=httpx.MockTransport(intercept)),
    )
    assert Worker(store).step()
    research = head(store, pid, "research")
    assert research["status"] == "AWAITING_REVIEW"
    assert research["data"]["sources"] == [source]
    assert research["data"]["claims"][0]["sources"] == [1]
    assert calls == [adapters.OPENAI + "/chat/completions"]
    snapshot = store.snapshot(actor, pid)
    assert (
        next(job["status"] for job in snapshot["jobs"] if job["id"] == jid)
        == "COMPLETED"
    )
    assert snapshot["costs"]["actual"] == 0
    assert snapshot["costs"]["uncertain"] == 100
