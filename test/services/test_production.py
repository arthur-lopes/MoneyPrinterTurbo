"""Production contracts exercised without credentials or paid requests."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
from threading import Event
from uuid import uuid4
import wave
from zipfile import ZipFile

import av
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from app.production import adapters, credentials, media, models as m, scene_motion
from app.production.api import PASSWORD_ITERATIONS, PublicLogin, create_app
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


def test_editorial_files_are_private_and_create_idempotent_draft_projects(tmp_path):
    app = create_app(
        tmp_path,
        {"a" * 32: "alice", "b" * 32: "bob"},
        worker_enabled=False,
        allowed_hosts=("testserver",),
    )
    alice = {"Authorization": "Bearer " + "a" * 32}
    bob = {"Authorization": "Bearer " + "b" * 32}
    episode = {
        "id": "W01",
        "series": "What if?",
        "round": 1,
        "order_in_round": 1,
        "pilot": True,
        "status": "draft",
        "language": "en",
        "aspect_ratio": "9:16",
        "target_duration_seconds": 90,
        "title": "A quiet network",
        "opening_hook": "Look outside.",
        "premise": "Connectivity fades.",
        "story_beats": ["Start", "End"],
        "visual_plan": "Show a chart.",
        "thumbnail_text": "OFFLINE",
        "fact_check": "Verify the premise.",
        "draft_prompt": "Text for editorial review.",
        "research_start": {"label": "Reference", "url": "https://example.org/research"},
    }
    catalog = {
        "schema_version": 1,
        "kind": "editorial_catalog",
        "channel": "Hyphotenic",
        "slogan": "One change.",
        "default_narration_language": "en",
        "optional_localization": "pt-BR",
        "publishing_rounds": [["W01"]],
        "episodes": [episode],
    }
    files = {
        "catalog": json.dumps(catalog, indent=2),
        "plan": "# Editorial plan\r\n",
        "prompts": "Prompt\r\n",
    }
    with TestClient(app) as client:
        assert client.put("/api/catalog", json=files).status_code == 401
        assert client.put("/api/catalog", json=files, headers=alice).status_code == 200
        assert client.get("/api/catalog", headers=bob).json()["catalog"] is None
        assert client.get("/api/catalog/files/plan", headers=bob).status_code == 404
        for name, contents in files.items():
            assert (
                client.get("/api/catalog/files/" + name, headers=alice).content
                == contents.encode()
            )
        assert client.get("/api/projects", headers=alice).json() == []
        route = "/api/catalog/episodes/W01/project"
        assert client.post(route, headers=bob).status_code == 404
        pid = client.post(route, headers=alice).json()["id"]
        assert client.post(route, headers=alice).json()["id"] == pid
        project = client.get("/api/projects/" + pid, headers=alice).json()
        assert project["jobs"] == []
        assert project["data"]["editorial"]["id"] == "W01"
        assert project["data"]["style"]["aspect"] == "9:16"
        assert project["data"]["defaults"]["translation"] == "codex"

        source = {
            "title": "Experiment",
            "url": "https://example.org/study",
            "excerpt": "Two readings matched.",
        }
        referenced = dict(
            episode,
            id="E02",
            order_in_round=2,
            title="The early signal",
            evidence={
                "question": "Why was the signal early?",
                "finding": "The readings matched.",
                "angle": "Follow the two instruments.",
                "uncertainty": "The mechanism was modeled.",
                "sources": [source],
                "claims": [
                    {
                        "category": "verified",
                        "text": "Two readings matched.",
                        "sources": [1],
                    }
                ],
            },
        )
        revised = dict(
            catalog, publishing_rounds=[["W01", "E02"]], episodes=[episode, referenced]
        )
        invalid = dict(files, catalog=json.dumps(dict(revised, featured=["W01"])))
        assert (
            client.put("/api/catalog", json=invalid, headers=alice).status_code == 422
        )
        updated = dict(files, catalog=json.dumps(dict(revised, featured=["E02"])))
        assert (
            client.put("/api/catalog", json=updated, headers=alice).status_code == 200
        )
        assert client.post(route, headers=alice).json()["id"] == pid
        route = "/api/catalog/episodes/E02/project"
        pilot = client.post(route, json={"localize": True}, headers=alice).json()["id"]
        assert client.post(route, headers=alice).json()["id"] == pilot
        detail = client.get("/api/projects/" + pilot, headers=alice).json()
        assert detail["data"]["languages"] == ["en-US", "pt-BR"]
        assert (
            detail["data"]["editorial"]["evidence"]["question"]
            == "Why was the signal early?"
        )
        assert "sources" not in detail["data"]["editorial"]["evidence"]
        (research,) = detail["versions"]
        assert (
            research["kind"] == "research" and research["status"] == "AWAITING_REVIEW"
        )
        assert research["data"]["sources"] == [source]
        assert detail["jobs"] == []


def test_thumbnail_drafts_bundle_and_owner_access(tmp_path):
    token, other_token = "a" * 32, "b" * 32
    app = create_app(
        tmp_path,
        {token: "alice", other_token: "bob"},
        worker_enabled=False,
        allowed_hosts=("testserver",),
    )
    alice = {"Authorization": f"Bearer {token}"}
    bob = {"Authorization": f"Bearer {other_token}"}
    project = {
        "title": "A quiet network",
        "topic": "A network outage",
        "audience": "General",
    }
    with TestClient(app) as client:
        pid = client.post("/api/projects", headers=alice, json=project).json()["id"]
        base = f"/api/projects/{pid}"
        canvas = BytesIO()
        Image.new("RGB", (320, 180), "#3d6d8f").save(canvas, format="PNG")
        asset = client.post(
            base + "/uploads?kind=reference&title=Network",
            headers=alice,
            content=canvas.getvalue(),
        ).json()
        options = {
            "variants": [
                {"concept": "impact", "headline": "NO REPLY", "source": asset["id"]},
                {"concept": "contrast", "headline": "THEN OR NOW?"},
                {"concept": "detail", "source": asset["id"]},
            ]
        }
        endpoint = base + "/thumbnails"
        assert client.post(endpoint, headers=bob, json=options).status_code == 404
        assert client.post(endpoint, headers=alice, json=options).status_code == 422
        response = client.post(
            base + "/reviews",
            headers=alice,
            json={"versions": [asset["id"]], "status": "APPROVED"},
        )
        assert response.status_code == 200
        generated = client.post(endpoint, headers=alice, json=options)
        assert generated.status_code == 201, generated.text
        results = generated.json()["variants"]
        assert [v["status"] for v in results] == ["AWAITING_REVIEW"] * 3
        assert (
            client.post(endpoint, headers=alice, json=options).json()["variants"]
            == results
        )
        download = client.get(endpoint + "/bundle", headers=alice)
        assert download.headers["content-type"] == "application/zip"
        assert client.get(endpoint + "/bundle", headers=bob).status_code == 404
        with ZipFile(BytesIO(download.content)) as bundle:
            pictures = [
                bundle.read(name) for name in bundle.namelist() if name.endswith(".jpg")
            ]
            assert len(set(pictures)) == 3
            for picture in pictures:
                with Image.open(BytesIO(picture)) as image:
                    assert image.format == "JPEG" and image.size == (3840, 2160)
                assert len(picture) < 2_000_000
        result = results[0]
        assert (
            client.get(result["url"], headers=alice).headers["content-type"]
            == "image/jpeg"
        )
        assert (
            client.put(
                base + "/style", headers=alice, json={"aspect": "9:16"}
            ).status_code
            == 200
        )
        assert client.get(endpoint + "/bundle", headers=alice).status_code == 404
        replacement = client.post(endpoint, headers=alice, json=options).json()[
            "variants"
        ][0]
        assert replacement["id"] != result["id"] and replacement["seq"] == 2
        assert (replacement["data"]["width"], replacement["data"]["height"]) == (
            2160,
            3840,
        )
        assert client.get(result["url"], headers=alice).status_code == 200


def test_graphic_quote_uses_reviewed_scene_and_fixed_template(sandbox, monkeypatch):
    store, actor, pid = sandbox
    [sid, _] = prepare(store, actor, pid)
    monkeypatch.setattr(scene_motion, "executable", lambda: "mock-hyperframes")
    animation = {"mode": "bar_chart", "entries": [{"label": "<test>", "value": 200}]}
    request = m.RequestJob(operation="video", scene=sid, animation=animation)
    with pytest.raises(RuleError, match="scene approval"):
        store.quote(actor, pid, [request])
    review(store, actor, pid, head(store, pid, f"scene/{sid}"))
    quote = store.quote(actor, pid, [request])
    assert (quote["total"], quote["items"][0]["model"]) == (0, "bar_chart")

    def render(args, **kwargs):
        source = (kwargs["cwd"] / "index.html").read_text(encoding="utf-8")
        assert "&lt;test&gt;" in source and "<test>" not in source
        assert "data-no-timeline" in source
        (kwargs["cwd"] / "video.mp4").write_bytes(b"fixture")

    monkeypatch.setattr(media, "run", render)
    worker = Worker(store)
    assert not worker.step()
    store.authorize(actor, pid, quote["id"], 0)
    assert worker.step()
    result = head(store, pid, f"video/{sid}")
    assert result["status"] == "AWAITING_REVIEW"
    assert result["data"]["animation"]["entries"][0]["value"] == 200


def test_editorial_overlay_uses_approved_frame_and_tracks_trimmed_cue(
    sandbox, monkeypatch
):
    store, actor, pid = sandbox
    sid = prepare(store, actor, pid)[0]
    scene = review(store, actor, pid, head(store, pid, f"scene/{sid}"))
    monkeypatch.setattr(scene_motion, "executable", lambda: "mock-hyperframes")
    cue = {
        "kind": "reveal",
        "start": 0.1,
        "duration": 1.0,
        "x": 0.2,
        "y": 0.5,
        "label": "<detail>",
        "purpose": "Show where service returns",
    }
    request = {
        "operation": "video",
        "scene": sid,
        "animation": {"mode": "editorial_overlay", "cues": [cue]},
    }
    with pytest.raises(RuleError, match="image approval"):
        authorize(store, actor, pid, request)
    worker = Worker(store)
    authorize(store, actor, pid, {"operation": "image", "scene": sid})
    assert worker.step()
    review(store, actor, pid, head(store, pid, f"image/{sid}"))
    too_late = dict(
        request, animation={"mode": "editorial_overlay", "cues": [dict(cue, start=2.8)]}
    )
    with pytest.raises(RuleError, match="fit within"):
        authorize(store, actor, pid, too_late)

    def render(args, **kwargs):
        directory = kwargs["cwd"]
        html = (directory / "index.html").read_text(encoding="utf-8")
        assert "&lt;detail&gt;" in html and "<detail>" not in html
        assert (directory / "anime.js").is_file()
        assert (directory / "editorial-frame.png").is_file()
        (directory / "video.mp4").write_bytes(b"fixture")

    monkeypatch.setattr(media, "run", render)
    authorize(store, actor, pid, request)
    assert worker.step()
    video = review(store, actor, pid, head(store, pid, f"video/{sid}"))
    with store.transaction() as db:
        db.execute("UPDATE scenes SET active=0 WHERE project=? AND id<>?", (pid, sid))
        voice = store.add(
            db,
            pid,
            f"voice/{sid}/en-US",
            "voice",
            {"seconds": 0.6, "narration": scene["data"]["narration"]},
            [],
            media="voice.wav",
        )
    review(store, actor, pid, voice)
    with store.transaction(write=False) as db:
        timeline = store._timeline(
            db,
            pid,
            store.project(db, actor, pid)["data"],
            m.Timeline(
                clips=[m.Clip(scene=sid, trim=0.25, seconds=1.5)], languages=["en-US"]
            ),
            [],
        )
    assert timeline["retention"] == [
        {
            "scene": scene["data"]["title"],
            "video": video["id"],
            "start": 0.0,
            "end": 1.5,
            "cues": [
                {"start": 0.0, "end": 0.85, "kind": "reveal", "purpose": cue["purpose"]}
            ],
        }
    ]


def test_elevenlabs_uses_approved_text_and_single_paid_attempt(sandbox, monkeypatch):
    store, actor, pid = sandbox
    sid = prepare(store, actor, pid)[0]
    scene = review(store, actor, pid, head(store, pid, f"scene/{sid}"))
    key = "mock-elevenlabs-test-value"
    credentials.save(store.directory, "elevenlabs", key)
    store.provider(
        actor,
        m.Provider(
            provider="elevenlabs",
            operation="voice",
            model="eleven_flash_v2_5",
            enabled=True,
            ceiling=100,
            confirmed=True,
        ),
    )
    store.budget(
        actor,
        pid,
        m.Budget(scope="provider", target="elevenlabs", limit=1000, confirmed=True),
    )
    store.budget(actor, pid, m.Budget(scope="project", limit=1000, confirmed=True))
    request = m.RequestJob(
        operation="voice",
        scene=sid,
        language="en-US",
        provider="elevenlabs",
        voice="voicefixture123",
    )
    calls = []

    def provider(req):
        calls.append(req.method)
        assert req.url.path == "/v1/text-to-speech/voicefixture123"
        assert req.headers["xi-api-key"] == key
        assert json.loads(req.content) == {
            "model_id": "eleven_flash_v2_5",
            "text": scene["data"]["narration"],
            "language_code": "en",
        }
        if len(calls) == 2:
            raise httpx.ReadTimeout("Mock interruption")
        return httpx.Response(
            200, content=b"fixture", headers={"history-item-id": "receipt-test"}
        )

    monkeypatch.setattr(
        adapters,
        "_client",
        lambda _: httpx.Client(transport=httpx.MockTransport(provider)),
    )
    monkeypatch.setattr(
        media, "sanitize_audio", lambda _src, output, *_: write_voice(output)
    )
    worker = Worker(store)
    quote = store.quote(actor, pid, [request])
    assert quote["items"][0]["characters"] == len(scene["data"]["narration"])
    assert quote["total"] == (100 * len(scene["data"]["narration"]) + 999) // 1000
    assert not worker.step()
    [job] = store.authorize(actor, pid, quote["id"], quote["total"])
    with pytest.raises(RuleError, match="active jobs"):
        store.save_provider_key("elevenlabs", "replacement-key")
    assert worker.step()
    voice = head(store, pid, f"voice/{sid}/en-US")
    assert voice["data"]["history_item_id"] == "receipt-test"
    assert voice["status"] == "AWAITING_REVIEW"
    with store.transaction() as db:
        db.execute("UPDATE jobs SET submitted=0 WHERE id=?", (job,))
    quote = store.quote(actor, pid, [request])
    [retry] = store.authorize(actor, pid, quote["id"], quote["total"])
    assert worker.step() and not worker.step()
    jobs = {work["id"]: work for work in store.snapshot(actor, pid)["jobs"]}
    assert jobs[job]["status"] == "COMPLETED"
    assert (
        jobs[retry]["status"] == "FAILED" and jobs[retry]["uncertain"] == quote["total"]
    )
    another = store.quote(actor, pid, [request])
    with pytest.raises(RuleError, match="uncertain charge"):
        store.authorize(actor, pid, another["id"], another["total"])
    assert calls == ["POST", "POST"]


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


def test_public_login_and_loopback_access(tmp_path):
    origin = "https://studio.example.test"
    password = "A sample password for the public test account"
    salt = bytes(range(16))
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    public_login = PublicLogin(
        origin,
        "arthur",
        "operator",
        f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}",
    )
    token = "a" * 32
    app = create_app(
        tmp_path, {token: "operator"}, worker_enabled=False, public_login=public_login
    )
    public = TestClient(app, base_url=origin)
    local = TestClient(app, base_url="http://127.0.0.1")
    payload = {"username": "arthur", "password": password}
    headers = {"Origin": origin}
    authorization = {"Authorization": "Bearer " + token}
    assert local.get("/api/health").status_code == 200
    assert local.get("/api/projects", headers=authorization).status_code == 200
    assert public.get("/api/health").status_code == 404
    assert public.get("/api/projects", headers=authorization).status_code == 401
    assert public.get("/api/session").json() == {
        "authenticated": False,
        "login_method": "password",
    }
    assert public.get("http://studio.example.test/api/session").status_code == 403
    assert public.post("/api/session", json=payload).status_code == 403
    assert (
        public.post(
            "/api/session", json=payload, headers={"Origin": "https://elsewhere.test"}
        ).status_code
        == 403
    )
    assert (
        public.post(
            "/api/session", json={**payload, "password": "wrong"}, headers=headers
        ).status_code
        == 401
    )
    assert (
        public.post(
            "/api/session", json={**payload, "password": "x" * 257}, headers=headers
        ).status_code
        == 422
    )
    assert (
        public.post("/api/session", json={"token": token}, headers=headers).status_code
        == 401
    )
    login = public.post("/api/session", json=payload, headers=headers)
    assert login.json() == {"actor": "operator"}
    cookie = login.headers["set-cookie"]
    assert all(
        part in cookie.lower() for part in ("httponly", "secure", "samesite=strict")
    )
    assert public.get("/api/projects").status_code == 200
    assert (
        local.get("/api/projects", headers={"Cookie": cookie.split(";")[0]}).status_code
        == 401
    )
    assert public.delete("/api/session", headers=headers).status_code == 200
    assert public.get("/api/projects").status_code == 401


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
    store = Workbench(tmp_path)
    with store.transaction() as db:
        asset = store.add(
            db,
            pid,
            "export",
            "export",
            {
                "retention": [
                    {
                        "scene": "=formula",
                        "video": uuid4().hex,
                        "start": 0.0,
                        "end": 1.5,
                        "cues": [],
                    },
                ]
            },
        )
    sheet = p + f"/retention/{asset['id']}.csv"
    assert client.get(sheet, headers=other).status_code == 404
    result = client.get(sheet, headers=auth)
    assert result.status_code == 200 and "'=formula" in result.text
    assert result.headers["content-disposition"].startswith("attachment")


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
    client = TestClient(
        create_app(
            store.directory,
            {"a" * 32: actor, "b" * 32: "another-operator"},
            worker_enabled=False,
            allowed_hosts=("testserver",),
        )
    )
    auth = {"Authorization": "Bearer " + "a" * 32}
    other = {"Authorization": "Bearer " + "b" * 32}
    path = f"/api/projects/{pid}"
    timeline_url = path + "/timeline"
    creation = {**auth, "If-None-Match": "*"}
    first = client.put(timeline_url, headers=creation, json=timeline)
    assert first.status_code == 200
    assert (
        client.put(timeline_url, headers=creation, json=timeline).json()["id"]
        == first.json()["id"]
    )
    assert client.get(timeline_url, headers=other).status_code == 404
    old_quote = store.quote(
        actor,
        pid,
        [
            m.RequestJob(operation="assembly", version=first.json()["id"]),
        ],
    )
    corrected = dict(
        timeline,
        clips=[
            dict(timeline["clips"][0], captions={"pt-BR": "Hipótese revisada."}),
        ],
    )
    original = {**auth, "If-Match": first.headers["ETag"]}
    saved = client.put(timeline_url, headers=original, json=corrected)
    assert saved.status_code == 200
    revision = saved.json()["id"]
    assert client.put(timeline_url, headers=original, json=timeline).status_code == 412
    assert (
        client.get(timeline_url, headers=auth).headers["ETag"] == saved.headers["ETag"]
    )
    historical = client.get(
        timeline_url, headers=auth, params={"revision": first.json()["id"]}
    )
    assert "captions" not in historical.json()["data"]["clips"][0]
    with pytest.raises(RuleError, match="Timeline changed"):
        store.authorize(actor, pid, old_quote["id"], 0)
    quote = store.quote(
        actor, pid, [m.RequestJob(operation="assembly", version=revision)]
    )
    assert quote["total"] == 0
    jobs = store.authorize(actor, pid, quote["id"], 0)
    assert store.authorize(actor, pid, quote["id"], 0) == jobs
    assert worker.step()
    assembly = head(store, pid, "assembly")
    assert assembly["data"]["timeline_revision"] == revision
    with pytest.raises(RuleError, match="assembly approval"):
        authorize(store, actor, pid, {"operation": "export", "version": assembly["id"]})
    review(store, actor, pid, assembly)
    authorize(store, actor, pid, {"operation": "export", "version": assembly["id"]})
    assert worker.step()
    output = head(store, pid, "export")
    assert output["data"]["timeline_revision"] == revision
    assert output["data"]["retention"] == [
        {
            "scene": story["data"]["title"],
            "video": clip["id"],
            "start": 0.0,
            "end": 2.0,
            "cues": [],
        }
    ]
    with store.transaction(write=False) as db:
        assert set(output["data"]["captions"]) == {"en-US", "pt-BR"}
        subtitles = store.asset(db, pid, output["data"]["captions"]["pt-BR"])
        assert (
            media.asset_path(worker.root, subtitles["media"])
            .read_text(encoding="utf-8")
            .endswith("Hipótese revisada.\n")
        )
    assert (
        head(store, pid, f"voice/{sid}/pt-BR")["data"]["narration"] == "Uma hipótese."
    )
    track_url = path + f"/media/{subtitles['id']}?webvtt=true"
    track = client.get(track_url, headers=auth)
    assert track.status_code == 200
    assert track.headers["Content-Type"].startswith("text/vtt")
    assert track.text.startswith("WEBVTT\n\n1\n00:00:00.000 --> ")
    assert track.text.endswith("Hipótese revisada.\n")
    assert client.get(track_url).status_code == 401
    assert client.get(track_url, headers=other).status_code == 404
    assert (
        client.get(
            f"/api/projects/{pid}/media/{output['id']}?webvtt=true", headers=auth
        ).status_code
        == 404
    )
    file = media.asset_path(worker.root, output["media"])
    assert (
        file.read_bytes()
        == media.asset_path(worker.root, assembly["media"]).read_bytes()
    )
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
    [queued] = authorize(
        store, actor, pid, {"operation": "assembly", "version": revision}
    )
    assert (
        client.put(
            timeline_url,
            headers={**auth, "If-Match": saved.headers["ETag"]},
            json=timeline,
        ).status_code
        == 200
    )
    assert not worker.step()
    assert (
        next(job for job in store.snapshot(actor, pid)["jobs"] if job["id"] == queued)[
            "status"
        ]
        == "CANCELLED"
    )
    assert (
        client.get(path + f"/media/{output['id']}", headers=auth).content
        == file.read_bytes()
    )


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
