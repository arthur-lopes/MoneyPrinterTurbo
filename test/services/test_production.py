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


def test_scene_evidence_preserves_original_research_and_detects_citation_changes(
    sandbox,
):
    store, actor, pid = sandbox
    sources = [
        {
            "title": "Study A",
            "url": "https://example.test/a",
            "excerpt": "Initial finding.",
        },
        {
            "title": "Study B",
            "url": "https://example.test/b",
            "excerpt": "Independent finding.",
        },
    ]
    sections = [
        m.Section(
            key=f"section{i}",
            title=f"Section {i}",
            narration="A measured observation.",
            seconds=3,
            category="verified",
            sources=[i],
        ).model_dump(exclude_none=True)
        for i in (1, 2)
    ]

    def approve_versions(catalog):
        with store.transaction() as db:
            research = store.add(
                db, pid, "research", "research", {"sources": catalog, "claims": []}
            )
        review(store, actor, pid, research)
        with store.transaction() as db:
            script = store.script(db, pid, {"sections": sections}, research)
        review(store, actor, pid, script)
        return research, script

    original_research, original_script = approve_versions(sources)
    first, second = store.storyboard(actor, pid)
    original = review(store, actor, pid, head(store, pid, f"scene/{first}"))
    review(store, actor, pid, head(store, pid, f"scene/{second}"))
    legacy = uuid4().hex
    with store.transaction() as db:
        db.execute(
            "INSERT INTO scenes(id,project,position) VALUES(?,?,2)", (legacy, pid)
        )
        store.add(
            db,
            pid,
            f"scene/{legacy}",
            "scene",
            original["data"],
            [
                {"sections": original["dependencies"][0]["sections"]},
                original["dependencies"][1],
            ],
        )
    app = create_app(
        store.directory, {"a" * 32: actor, "b" * 32: "other"}, worker_enabled=False
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    owner = {"Authorization": "Bearer " + "a" * 32}
    other = {"Authorization": "Bearer " + "b" * 32}
    project_url = f"/api/projects/{pid}"
    agent_url = f"/api/agent/v1/projects/{pid}"
    suffix = f"/scenes/{first}/evidence"
    legacy_url = project_url + f"/scenes/{legacy}/evidence"
    grant = (
        client.post(
            project_url + "/agent-access",
            headers=owner,
            json={"label": "Evidence reader", "scope": "read"},
        )
        .raise_for_status()
        .json()
    )
    agent = {"Authorization": "Bearer " + grant["token"]}
    current = client.get(project_url + suffix, headers=owner).raise_for_status().json()
    assert current == {
        "scene": first,
        "scene_version": original["id"],
        "script_version": original_script["id"],
        "research_version": original_research["id"],
        "binding": "recorded",
        "fresh": True,
        "sections": [
            {
                "key": "section1",
                "title": "Section 1",
                "category": "verified",
                "sources": [{"number": 1, **sources[0]}],
            }
        ],
    }
    assert (
        client.get(agent_url + suffix, headers=agent).raise_for_status().json()
        == current
    )
    assert client.get(project_url + suffix, headers=agent).status_code == 401
    assert client.get(project_url + suffix, headers=other).status_code == 404
    assert client.get(project_url + suffix).status_code == 401
    foreign = store.create(actor, store.snapshot(actor, pid)["data"])
    assert (
        client.get(
            f"/api/agent/v1/projects/{foreign}" + suffix, headers=agent
        ).status_code
        == 404
    )
    assert (
        client.get(
            agent_url + f"/scenes/{uuid4().hex}/evidence", headers=agent
        ).status_code
        == 404
    )

    extra = {
        "title": "Uncited",
        "url": "https://example.test/c",
        "excerpt": "Unrelated research.",
    }
    approve_versions([*sources, extra])
    assert client.get(project_url + suffix, headers=owner).json() == current
    historic = client.get(legacy_url, headers=owner).raise_for_status().json()
    assert historic["binding"] == "historical" and historic["fresh"]
    assert historic["research_version"] == original_research["id"]
    revised = dict(sources[0], excerpt="Correction from the source.")
    latest_research, latest_script = approve_versions([revised, sources[1], extra])
    assert client.get(project_url + suffix, headers=owner).json() == dict(
        current, fresh=False
    )
    assert client.get(legacy_url, headers=owner).json() == dict(historic, fresh=False)
    assert client.get(project_url + f"/scenes/{second}/evidence", headers=owner).json()[
        "fresh"
    ]
    assert store.storyboard(actor, pid) == [first, second, legacy]
    replacement = (
        client.get(project_url + suffix, headers=owner).raise_for_status().json()
    )
    assert replacement["scene_version"] != original["id"] and replacement["fresh"]
    assert replacement["script_version"] == latest_script["id"]
    assert replacement["research_version"] == latest_research["id"]
    assert replacement["sections"][0]["sources"] == [{"number": 1, **revised}]
    assert (
        client.delete(
            project_url + "/agent-access/" + grant["id"], headers=owner
        ).status_code
        == 200
    )
    assert client.get(agent_url + suffix, headers=agent).status_code == 401


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


def test_google_flow_scene_uses_reviewed_import_in_final_assembly(sandbox):
    store, actor, pid = sandbox
    with store.transaction() as db:
        db.execute(
            "UPDATE projects SET data=json_set(data,'$.structure',json('[\"Opening\"]')) WHERE id=?",
            (pid,),
        )
    base = f"/api/projects/{pid}"
    app = create_app(
        store.directory,
        {"a" * 32: actor, "b" * 32: "other"},
        worker_enabled=False,
        allowed_hosts=("testserver",),
    )
    client = TestClient(app, headers={"Authorization": "Bearer " + "a" * 32})
    defaults = client.get(base).json()["data"]["defaults"]
    defaults["video"] = "google_flow"
    assert client.put(base + "/defaults", json=defaults).json()["defaults"] == defaults
    [sid] = prepare(store, actor, pid)
    scene = review(store, actor, pid, head(store, pid, f"scene/{sid}"))
    assert (scene["data"]["provider"], scene["data"]["model"]) == ("google_flow", "import")
    png = BytesIO()
    Image.new("RGB", (32, 24)).save(png, format="PNG")

    def approve_frame():
        response = client.post(
            base + "/uploads",
            params={"kind": "image", "scene": sid, "title": "Approved frame"},
            content=png.getvalue(),
        )
        assert response.status_code == 201
        return review(store, actor, pid, response.json())

    frame = approve_frame()
    for provider in ({}, {"provider": "runway"}):
        denied = client.post(
            base + "/quotes",
            json={"jobs": [{"operation": "video", "scene": sid, **provider}]},
        )
        assert denied.status_code == 409
        assert "Google Flow" in denied.json()["detail"]
    source = store.directory / "flow-fixture.mp4"
    media.ffmpeg(
        ["-f", "lavfi", "-i", "color=size=32x24:duration=1", "-c:v", "libx264", source],
        cwd=store.directory,
        stop=lambda: False,
        timeout=15,
    )
    clip = source.read_bytes()
    params = {"kind": "video", "scene": sid, "title": "Reviewed footage"}
    response = client.post(base + "/uploads", params=params, content=clip)
    assert response.status_code == 201
    earlier = review(store, actor, pid, response.json())
    assert "source_provider" not in earlier["data"]

    params.update(source_provider="google_flow", scene_version=scene["id"])
    assert (
        client.post(base + "/uploads", params=params, content=clip).status_code == 422
    )
    current_frame = approve_frame()
    params["image_version"] = frame["id"]
    assert (
        client.post(base + "/uploads", params=params, content=clip).status_code == 409
    )
    params["image_version"] = current_frame["id"]
    assert (
        client.post(
            base + "/uploads",
            params=params,
            content=clip,
            headers={"Authorization": "Bearer " + "b" * 32},
        ).status_code
        == 404
    )
    before = client.get(base).json()
    response = client.post(base + "/uploads", params=params, content=clip)
    assert response.status_code == 201
    imported = response.json()
    assert imported["slot"] == f"video/{sid}"
    assert imported["status"] == "AWAITING_REVIEW"
    assert imported["seq"] == 2
    assert imported["data"]["source_provider"] == "google_flow"
    assert imported["dependencies"] == [
        {"id": scene["id"]},
        {"id": current_frame["id"]},
    ]
    assert client.get(imported["url"]).headers["content-type"] == "video/mp4"
    after = client.get(base).json()
    assert before["jobs"] == after["jobs"]
    assert before["costs"] == after["costs"]
    assert client.get(earlier["url"]).status_code == 200

    voice = store.directory / "flow-voice.wav"
    write_voice(voice)
    narration = client.post(
        base + "/uploads",
        params={"kind": "voice", "scene": sid, "title": "Narration", "language": "en-US"},
        content=voice.read_bytes(),
    )
    assert narration.status_code == 201
    review(store, actor, pid, narration.json())
    assembly = {
        "operation": "assembly",
        "timeline": {
            "clips": [{"scene": sid, "seconds": 1}],
            "languages": ["en-US"],
            "resolution": 720,
            "watermark": False,
        },
    }
    assert client.post(base + "/quotes", json={"jobs": [assembly]}).status_code == 409
    assert review(store, actor, pid, imported)["status"] == "APPROVED"
    authorize(store, actor, pid, assembly)
    worker = Worker(store)
    assert worker.step()
    rendered = head(store, pid, "assembly")
    assert rendered["data"]["retention"][0]["video"] == imported["id"]
    review(store, actor, pid, rendered)
    authorize(store, actor, pid, {"operation": "export", "version": rendered["id"]})
    assert worker.step()
    exported = head(store, pid, "export")
    assert client.get(exported["url"]).headers["content-type"] == "video/mp4"
    with av.open(str(media.asset_path(worker.root, exported["media"]))) as result:
        assert next(result.decode(video=0)).height == 720


def test_project_agent_proposes_an_idempotent_edit_for_human_review(sandbox):
    store, actor, pid = sandbox
    scenes = prepare(store, actor, pid)
    for sid in scenes:
        review(store, actor, pid, head(store, pid, f"scene/{sid}"))
        with store.transaction() as db:
            take = store.add(
                db,
                pid,
                f"video/{sid}",
                "video",
                {"seconds": 4},
                media=uuid4().hex + ".mp4",
            )
            voice = store.add(
                db,
                pid,
                f"voice/{sid}/en-US",
                "voice",
                {"seconds": 0.5, "narration": "The verified track."},
                media=uuid4().hex + ".wav",
            )
        review(store, actor, pid, take)
        review(store, actor, pid, voice)
    previous = store.snapshot(actor, pid)
    second_project = store.create(actor, previous["data"])
    with store.transaction() as db:
        timeline = store.add(
            db,
            pid,
            "timeline",
            "timeline",
            m.Timeline(
                clips=[m.Clip(scene=sid, seconds=3) for sid in scenes],
                languages=["en-US"],
            ).model_dump(),
        )
        alternate = store.add(
            db,
            pid,
            f"video/{scenes[0]}",
            "video",
            {"seconds": 4},
            media=uuid4().hex + ".mp4",
        )
    review(store, actor, pid, alternate)
    origin = "https://studio.example.test"
    app = create_app(
        store.directory,
        {"a" * 32: actor, "b" * 32: "intruder"},
        worker_enabled=False,
        public_login=PublicLogin(
            origin,
            "operator",
            actor,
            f"pbkdf2_sha256${PASSWORD_ITERATIONS}${'ab' * 16}${'ab' * 32}",
        ),
    )
    owner = TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer " + "a" * 32},
    )
    studio = TestClient(app, base_url=origin)
    p = f"/api/projects/{pid}"
    agent_url = f"/api/agent/v1/projects/{pid}"
    grant = owner.post(p + "/agent-access", json={"label": "Timeline assistant"}).json()
    reader = owner.post(
        p + "/agent-access", json={"label": "Reader", "scope": "read"}
    ).json()
    bearer = {"Authorization": "Bearer " + grant["token"]}
    read_only = {"Authorization": "Bearer " + reader["token"]}
    assert all(
        "token" not in row and "digest" not in row
        for row in owner.get(p + "/agent-access").json()
    )
    assert (
        studio.get(agent_url, headers=bearer).json()["timeline"]["id"] == timeline["id"]
    )
    assert (
        studio.get(
            f"/api/agent/v1/projects/{second_project}", headers=bearer
        ).status_code
        == 404
    )
    assert studio.get(p, headers=bearer).status_code == 401
    assert owner.get(agent_url).status_code == 401
    assert (
        owner.get(
            p + "/agent-access", headers={"Authorization": "Bearer " + "b" * 32}
        ).status_code
        == 404
    )
    assert studio.get(agent_url, headers=read_only).status_code == 200
    path = media.asset_path(store.directory / "assets", alternate["media"])
    path.write_bytes(b"test-media")
    assert (
        studio.get(agent_url + "/media/" + alternate["id"], headers=bearer).content
        == b"test-media"
    )

    with store.transaction() as db:
        rendered = {
            "timeline_revision": timeline["id"],
            "seconds": 3 * len(scenes),
            "languages": ["en-US"],
            "retention": [
                {
                    "scene": f"Scene {i + 1}",
                    "video": store.head(db, pid, f"video/{sid}")["id"],
                    "start": 3 * i,
                    "end": 3 * (i + 1),
                }
                for i, sid in enumerate(scenes)
            ],
        }
        assembly = store.add(
            db,
            pid,
            "assembly",
            "assembly",
            rendered,
            [{"id": store.head(db, pid, f"voice/{scenes[0]}/en-US")["id"]}],
            media=uuid4().hex + ".mp4",
        )
        earlier = store.add(
            db,
            pid,
            "assembly",
            "assembly",
            dict(rendered, timeline_revision=uuid4().hex),
            media=uuid4().hex + ".mp4",
        )

    def note_on(item, position):
        return (
            owner.post(
                p + f"/assemblies/{item['id']}/notes",
                json={
                    "request_id": uuid4().hex,
                    "position_ms": position,
                    "body": "Check the wording.",
                },
            )
            .raise_for_status()
            .json()
        )

    note = note_on(assembly, 1250)
    boundary = note_on(assembly, 3000)
    ending = note_on(assembly, 3000 * len(scenes))
    old_note = note_on(earlier, 1250)
    note_listing = (
        studio.get(agent_url + f"/assemblies/{assembly['id']}/notes", headers=read_only)
        .raise_for_status()
        .json()
    )
    anchored = {item["id"]: item["scene"] for item in note_listing["notes"]}
    assert anchored[note["id"]] == {
        "id": scenes[0],
        "title": "Scene 1",
        "start_ms": 0,
        "end_ms": 3000,
        "offset_ms": 1250,
    }
    assert anchored[boundary["id"]]["id"] == scenes[1]
    assert anchored[boundary["id"]]["offset_ms"] == 0
    assert anchored[ending["id"]]["id"] == scenes[-1]
    assert anchored[ending["id"]]["offset_ms"] == 3000

    plan = {
        "base_revision": timeline["id"],
        "rationale": "Fix the caption and align the cut.",
        "review_note": note["id"],
        "changes": [
            {
                "scene": scenes[0],
                "video": alternate["id"],
                "trim": 0.1,
                "captions": {"en-US": "The revised subtitle."},
            }
        ],
    }
    assert (
        studio.post(
            agent_url + "/proposals/validate", json=plan, headers=read_only
        ).status_code
        == 403
    )
    assert studio.post(agent_url + "/proposals/validate", json=plan).status_code == 403
    assert (
        studio.post(
            agent_url + "/proposals/validate",
            json=plan,
            headers={**bearer, "Origin": "https://other.example.test"},
        ).status_code
        == 403
    )
    validate_url = agent_url + "/proposals/validate"
    for ident, expected in [
        (old_note["id"], 409),
        (boundary["id"], 422),
        (uuid4().hex, 404),
    ]:
        rejected = studio.post(
            validate_url, json=dict(plan, review_note=ident), headers=bearer
        )
        assert rejected.status_code == expected
    unlinked_plan = {key: value for key, value in plan.items() if key != "review_note"}
    unlinked = (
        studio.post(validate_url, json=unlinked_plan, headers=bearer)
        .raise_for_status()
        .json()
    )
    assert "review_note" not in unlinked
    check = studio.post(agent_url + "/proposals/validate", json=plan, headers=bearer)
    assert check.status_code == 200
    assert check.json()["diff"][0]["changes"]["trim"] == {"before": 0, "after": 0.1}
    assert check.json()["review_note"] == {
        key: note[key] for key in ("id", "assembly", "position_ms", "url")
    } | {"scene": anchored[note["id"]]}
    assert owner.get(p + "/proposals").json() == []
    request = dict(
        plan, request_id=uuid4().hex, expected_fingerprint=check.json()["fingerprint"]
    )
    proposal = studio.post(
        agent_url + "/proposals", json=request, headers=bearer
    ).json()
    assert proposal["status"] == "PENDING" and proposal["agent_id"] == grant["id"]
    assert proposal["plan"]["review_note"]["url"] == note["url"]
    assert (
        studio.post(agent_url + "/proposals", json=request, headers=bearer).json()["id"]
        == proposal["id"]
    )
    assert (
        studio.post(
            agent_url + "/proposals",
            json=dict(request, rationale="Changed"),
            headers=bearer,
        ).status_code
        == 409
    )
    assert (
        studio.get(
            agent_url + "/proposals/" + proposal["id"], headers=read_only
        ).status_code
        == 404
    )
    assert owner.get(p + "/timeline").json()["id"] == timeline["id"]
    decision = p + f"/proposals/{proposal['id']}/decision"
    assert (
        studio.post(
            decision, json={"decision": "apply"}, headers={**bearer, "Origin": origin}
        ).status_code
        == 401
    )
    assert (
        studio.post(
            p + "/quotes", json={"jobs": []}, headers={**bearer, "Origin": origin}
        ).status_code
        == 401
    )

    # An approval with the same cut ID still checks the inputs the agent saw.
    with store.transaction() as db:
        newer_voice = store.add(
            db,
            pid,
            f"voice/{scenes[0]}/en-US",
            "voice",
            {"seconds": 0.5, "narration": "A new track."},
            media=uuid4().hex + ".wav",
        )
    review(store, actor, pid, newer_voice)
    outdated = studio.post(validate_url, json=plan, headers=bearer)
    assert outdated.status_code == 409
    assert "assembly's inputs changed" in outdated.json()["detail"]
    stale = dict(request, request_id=uuid4().hex)
    assert (
        studio.post(agent_url + "/proposals", json=stale, headers=bearer).status_code
        == 409
    )
    assert owner.post(decision, json={"decision": "apply"}).status_code == 409
    assert (
        owner.post(
            decision, json={"decision": "reject", "comment": "Voice changed."}
        ).json()["status"]
        == "REJECTED"
    )
    proposal = studio.post(
        agent_url + "/proposals",
        json=dict(unlinked_plan, request_id=uuid4().hex),
        headers=bearer,
    ).json()
    decision = p + f"/proposals/{proposal['id']}/decision"
    applied = owner.post(
        decision, json={"decision": "apply", "comment": "Reviewed."}
    ).json()
    assert applied["decided_by"] == actor
    assert (
        owner.post(decision, json={"decision": "apply"}).json()["applied_revision"]
        == applied["applied_revision"]
    )
    assert owner.post(decision, json={"decision": "reject"}).status_code == 409
    updated = owner.get(p + "/timeline").json()
    assert updated["id"] == applied["applied_revision"]
    assert updated["data"] == check.json()["timeline"]
    assert len(store.snapshot(actor, pid)["jobs"]) == len(previous["jobs"])
    assert (
        studio.get(agent_url + "/proposals/" + proposal["id"], headers=bearer).json()[
            "status"
        ]
        == "APPLIED"
    )
    with store.transaction() as db:
        db.execute("UPDATE agent_keys SET expires=0 WHERE id=?", (reader["id"],))
    assert studio.get(agent_url, headers=read_only).status_code == 401
    assert owner.delete(p + "/agent-access/" + grant["id"]).status_code == 200
    assert studio.get(agent_url, headers=bearer).status_code == 401
    assert (
        studio.post(agent_url + "/proposals", json=request, headers=bearer).status_code
        == 401
    )
    assert owner.get(p + "/proposals/" + proposal["id"]).json()["status"] == "APPLIED"


def test_review_notes_pin_assembly_time_and_audit_state_without_editing_the_cut(
    sandbox,
):
    store, actor, pid = sandbox
    with store.transaction() as db:
        first = store.add(
            db,
            pid,
            "assembly",
            "assembly",
            {"seconds": 16.5, "languages": ["en-US"]},
            media=uuid4().hex + ".mp4",
        )
        latest = store.add(
            db,
            pid,
            "assembly",
            "assembly",
            {"seconds": 20, "languages": ["en-US"]},
            media=uuid4().hex + ".mp4",
        )
    app = create_app(
        store.directory,
        {"a" * 32: actor, "b" * 32: "another-owner"},
        worker_enabled=False,
    )
    owner = {"Authorization": "Bearer " + "a" * 32}
    another = {"Authorization": "Bearer " + "b" * 32}
    client = TestClient(app, base_url="http://127.0.0.1", headers=owner)
    p = f"/api/projects/{pid}"
    url = p + f"/assemblies/{first['id']}/notes"
    latest_url = p + f"/assemblies/{latest['id']}/notes"
    before = store.snapshot(actor, pid)
    write = {
        "request_id": uuid4().hex,
        "position_ms": 13250,
        "body": "Explain the source of this observation.",
    }
    assert client.post(url, json=dict(write, position_ms=16501)).status_code == 422
    assert client.post(url, json=dict(write, position_ms=1.5)).status_code == 422
    assert client.post(url, headers=another, json=write).status_code == 404
    note = client.post(url, json=write).raise_for_status().json()
    assert note["state"] == "OPEN" and note["state_version"] == 0
    assert note["url"].endswith(f"&assembly={first['id']}&t=13250&note={note['id']}")
    assert client.post(url, json=write).json() == note
    assert client.post(url, json=dict(write, body="Different")).status_code == 409
    assert client.post(latest_url, json=write).status_code == 409
    assert client.get(latest_url).json()["notes"] == []

    state = url + f"/{note['id']}/state"
    resolved = {"state": "RESOLVED", "expected_version": 0}
    assert client.put(state, json=resolved).json()["state_version"] == 1
    assert client.put(state, json=resolved).json()["state_version"] == 1
    assert (
        client.put(state, json={"state": "OPEN", "expected_version": 1}).json()[
            "state_version"
        ]
        == 2
    )
    assert client.put(state, json=resolved).status_code == 409
    events = client.get(url + "/" + note["id"]).raise_for_status().json()["events"]
    assert [(event["seq"], event["state"], event["actor"]) for event in events] == [
        (1, "RESOLVED", actor),
        (2, "OPEN", actor),
    ]
    with store.transaction(write=False) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM review_note_events WHERE note=?", (note["id"],)
            ).fetchone()[0]
            == 2
        )
    assert client.get(url).json()["notes"][0]["actor"] == actor
    grant = client.post(
        p + "/agent-access", json={"label": "Notes reader", "scope": "read"}
    ).json()
    key = {"Authorization": "Bearer " + grant["token"]}
    api = f"/api/agent/v1/projects/{pid}/assemblies/{first['id']}/notes"
    assert (
        client.get(api, headers=key).raise_for_status().json()["notes"][0]["id"]
        == note["id"]
    )
    assert client.get(url, headers=key).status_code == 401
    assert client.post(url, headers=key, json=write).status_code == 401
    assert client.delete(p + "/agent-access/" + grant["id"]).status_code == 200
    assert client.get(api, headers=key).status_code == 401
    after = store.snapshot(actor, pid)
    assert before["versions"] == after["versions"] and before["jobs"] == after["jobs"]


def test_first_cut_resumes_and_commits_an_editable_watermarked_revision(
    sandbox, monkeypatch
):
    store, actor, pid = sandbox
    with store.transaction() as db:
        research = store.add(
            db,
            pid,
            "research",
            "research",
            {
                "sources": [
                    {
                        "url": "https://example.test/observations",
                        "title": "Observation report",
                        "excerpt": "The first signal preceded the estimate.",
                    }
                ],
                "claims": [],
            },
        )
    research = review(store, actor, pid, research)
    diagram = {
        "layout": "sequence",
        "labels": ["Observed", "Estimated"],
        "annotation": "Observed first",
        "reveal_at": 0.5,
    }
    with pytest.raises(ValueError, match="needs a source"):
        m.Section(
            key="opening",
            title="First signal",
            seconds=1.5,
            narration="The signal.",
            evidence_graphic=diagram,
        )
    with store.transaction() as db:
        script = store.script(
            db,
            pid,
            {
                "sections": [
                    {
                        "key": "opening",
                        "title": "First signal",
                        "seconds": 1.5,
                        "narration": "The first signal arrived early.",
                        "sources": [1],
                        "evidence_graphic": diagram,
                    }
                ]
            },
            research,
        )
    request = m.RequestJob(operation="rough_cut", version=script["id"])
    with pytest.raises(RuleError, match="script approval"):
        store.quote(actor, pid, [request])
    review(store, actor, pid, script)
    with pytest.raises(RuleError, match="local renderer"):
        store.quote(actor, pid, [request.model_copy(update={"provider": "openai"})])
    with pytest.raises(RuleError, match="Project not found"):
        store.quote("another-operator", pid, [request])

    quote = store.quote(actor, pid, [request])
    assert quote["total"] == 0 and quote["scene_count"] == 1
    [jid] = store.authorize(actor, pid, quote["id"], 0)
    assert store.authorize(actor, pid, quote["id"], 0) == [jid]
    assert store.claim()["id"] == jid
    restarted = Workbench(store.directory)
    restarted.recover()
    assert restarted.snapshot(actor, pid)["jobs"][0]["status"] == "DRAFT"
    monkeypatch.setattr(
        media, "speech", lambda _text, _lang, _voice, output, *_: write_voice(output)
    )
    worker = Worker(restarted)
    assert worker.step()
    snapshot = restarted.snapshot(actor, pid)
    assert snapshot["jobs"][0]["status"] == "COMPLETED"
    assert snapshot["jobs"][0]["actual"] == 0
    [sid] = snapshot["scenes"]
    assembly = head(store, pid, "assembly")
    timeline = head(store, pid, "timeline")
    assert assembly["data"]["timeline_revision"] == timeline["id"]
    assert assembly["data"]["watermark"]
    assert assembly["data"]["languages"] == ["en-US"]
    assert assembly["data"]["retention"][0]["scene"] == "First signal"
    with store.transaction(write=False) as db:
        cover = store.asset(db, pid, assembly["data"]["poster"])
    assert cover["kind"] == "poster"
    with Image.open(media.asset_path(worker.root, cover["media"])) as poster:
        assert poster.format == "JPEG" and poster.width == 720 and poster.height < 720
    with av.open(str(media.asset_path(worker.root, assembly["media"]))) as container:
        assert next(container.decode(video=0)).height == 720
        assert len(container.streams.audio) == len(container.streams.subtitles) == 1
    drafts = [v for v in snapshot["versions"] if v.get("draft_preview")]
    assert {v["kind"] for v in drafts} == {"scene", "image", "video", "voice"}
    assert all(v["status"] == "AWAITING_REVIEW" and v["fresh"] for v in drafts)
    video = next(v for v in drafts if v["kind"] == "video")
    assert video["data"]["evidence_graphic"] == diagram
    with av.open(str(media.asset_path(worker.root, video["media"]))) as container:
        frames = container.decode(video=0)
        first = next(frames).to_image().getpixel((353, 359))
        for frame in frames:
            last = frame
        assert last.to_image().getpixel((353, 359))[1] > first[1] + 80
    with pytest.raises(RuleError, match="source image first"):
        review(store, actor, pid, head(store, pid, f"video/{sid}"))
    with pytest.raises(RuleError, match="scene approval"):
        store.quote(
            actor,
            pid,
            [
                m.RequestJob(
                    operation="assembly",
                    timeline=m.Timeline(
                        **dict(timeline["data"], watermark=False),
                    ),
                )
            ],
        )
    review(store, actor, pid, assembly)
    with pytest.raises(RuleError, match="watermarked"):
        authorize(store, actor, pid, {"operation": "export", "version": assembly["id"]})
    with pytest.raises(RuleError, match="existing storyboard"):
        store.quote(actor, pid, [request])

    edited = dict(
        timeline["data"],
        clips=[
            dict(
                timeline["data"]["clips"][0], captions={"en-US": "The initial signal."}
            )
        ],
    )
    with store.transaction() as db:
        revision = store.add(db, pid, "timeline", "timeline", edited)
    authorize(store, actor, pid, {"operation": "assembly", "version": revision["id"]})
    assert worker.step()
    updated = head(store, pid, "assembly")
    assert updated["data"]["timeline_revision"] == revision["id"]
    with store.transaction(write=False) as db:
        caption = store.asset(db, pid, updated["data"]["captions"]["en-US"])
    assert (
        media.asset_path(worker.root, caption["media"])
        .read_text(encoding="utf-8")
        .endswith("The initial signal.\n")
    )

    app = create_app(
        store.directory,
        {"a" * 32: actor, "b" * 32: "another-owner"},
        worker_enabled=False,
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer " + "a" * 32},
    )
    url = f"/api/projects/{pid}/delivery"
    plan = client.get(url).json()
    assert plan["revision"] == revision["id"]
    [scene] = plan["scenes"]
    assert [item["kind"] for item in scene["materials"]] == [
        "scene",
        "image",
        "video",
        "voice",
    ]
    preparation = {
        "revision": plan["revision"],
        "confirmed": True,
        "versions": [item["id"] for item in scene["materials"]],
    }
    assert client.post(url + "/prepare", json=preparation).status_code == 409
    assert (
        client.get(url, headers={"Authorization": "Bearer " + "b" * 32}).status_code
        == 404
    )
    selection = dict(preparation, scenes=[sid])
    assert (
        client.post(
            url + "/materials",
            json=dict(selection, versions=preparation["versions"][:-1]),
        ).status_code
        == 409
    )
    assert (
        client.post(
            url + "/materials", json=dict(selection, revision=timeline["id"])
        ).status_code
        == 409
    )
    assert (
        client.post(
            url + "/materials", json=dict(selection, confirmed=False)
        ).status_code
        == 422
    )
    assert (
        client.post(url + "/materials", json=selection).json()["approved"]
        == preparation["versions"]
    )
    assert client.post(url + "/materials", json=selection).json()["approved"] == []
    approved = [
        entry
        for entry in store.snapshot(actor, pid)["reviews"]
        if entry["version"] in preparation["versions"]
    ]
    assert len(approved) == 4 and all(
        entry["actor"] == actor and revision["id"] in entry["comment"]
        for entry in approved
    )

    response = client.post(url + "/prepare", json=preparation)
    assert response.status_code == 200
    final_timeline = response.json()
    assert (
        final_timeline["id"] != revision["id"]
        and final_timeline["data"]["watermark"] is False
    )
    assert (
        client.post(url + "/prepare", json=preparation).json()["id"]
        == final_timeline["id"]
    )
    authorize(
        store, actor, pid, {"operation": "assembly", "version": final_timeline["id"]}
    )
    assert worker.step()
    final = head(store, pid, "assembly")
    assert final["data"]["timeline_revision"] == final_timeline["id"]
    with pytest.raises(RuleError, match="assembly approval"):
        authorize(store, actor, pid, {"operation": "export", "version": final["id"]})
    review(store, actor, pid, final)
    authorize(store, actor, pid, {"operation": "export", "version": final["id"]})
    assert worker.step()
    exported = head(store, pid, "export")
    assert media.asset_path(worker.root, exported["media"]).read_bytes() == (
        media.asset_path(worker.root, final["media"]).read_bytes()
    )
    with store.transaction() as db:
        previous_voice = store.head(db, pid, f"voice/{sid}/en-US")
        store.add(
            db,
            pid,
            previous_voice["slot"],
            "voice",
            previous_voice["data"],
            media=previous_voice["media"],
        )
    with pytest.raises(RuleError, match="Timeline changed"):
        authorize(
            store,
            actor,
            pid,
            {"operation": "assembly", "version": final_timeline["id"]},
        )


def test_render_reuses_unchanged_segments_and_rebuilds_corrupt_cache(
    tmp_path, monkeypatch
):
    assets = tmp_path / "assets"
    assets.mkdir()
    image = tmp_path / "image.png"
    Image.new("RGB", (96, 128), "navy").save(image)
    video = assets / ("a" * 32 + ".mp4")
    voice = assets / ("b" * 32 + ".wav")
    stop = Event()
    media.motion(image, video, 1.25, "9:16", stop.is_set, 15)
    write_voice(voice)
    clip = {
        "video": video.name,
        "trim": 0.0,
        "seconds": 0.75,
        "narration_offset": 0.0,
        "transition": "cut",
        "effect": None,
        "audio": {
            "en-US": {"media": voice.name, "seconds": 0.5, "narration": "Original"}
        },
    }
    timeline = {
        "clips": [clip, dict(clip, trim=0.25)],
        "aspect": "9:16",
        "resolution": 720,
        "languages": ["en-US"],
        "watermark": False,
        "subtitles": True,
        "music": None,
    }
    encodes = []
    original = media.ffmpeg

    def track(args, **kwargs):
        if str(args[-1]).startswith("clip-"):
            encodes.append(args[-1])
        original(args, **kwargs)

    def render(name, scope="project"):
        scratch = tmp_path / name
        scratch.mkdir()
        output = scratch / "assembly.mp4"
        encodes.clear()
        media.render(
            assets,
            timeline,
            output,
            stop.is_set,
            15,
            lambda _value: None,
            cache_scope=scope,
        )
        return output

    monkeypatch.setattr(media, "ffmpeg", track)
    initial = render("initial")
    assert encodes == ["clip-0.mp4", "clip-1.mp4"]
    assert render("warm").read_bytes() == initial.read_bytes()
    assert encodes == []

    timeline["clips"][0] = dict(clip, captions={"en-US": "Corrected"})
    corrected = render("corrected")
    assert encodes == []
    assert (corrected.parent / "captions-0.srt").read_text().count("Corrected") == 1
    assert corrected.read_bytes() == render("uncached", None).read_bytes()

    timeline["clips"][1]["trim"] = 0.375
    trimmed = render("trimmed")
    assert encodes == ["clip-1.mp4"]
    assert trimmed.read_bytes() == render("isolated", "different-project").read_bytes()
    assert encodes == ["clip-0.mp4", "clip-1.mp4"]

    # Replace rather than edit in place because cached files are hard links.
    for entry in (tmp_path / "render-cache").glob("*.mp4"):
        entry.unlink()
        entry.write_bytes(b"unreadable")
    assert render("recovered").read_bytes() == trimmed.read_bytes()
    assert encodes == ["clip-0.mp4", "clip-1.mp4"]
    stop.set()
    with pytest.raises(media.Stopped):
        render("cancelled")
    assert encodes == []


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
    preview_url = timeline_url + "/scene-preview"
    request = {
        "clip": {
            "scene": sid,
            "video": clip["id"],
            "trim": 0.05,
            "seconds": 1.75,
            "narration_offset": 0.125,
            "captions": {"pt-BR": "Legenda corrigida."},
        },
        "language": "pt-BR",
    }
    jobs_before = store.snapshot(actor, pid)["jobs"]
    preview = client.post(preview_url, headers=auth, json=request).json()
    pt_voice = head(store, pid, f"voice/{sid}/pt-BR")
    assert preview == {
        "take": clip["url"],
        "voice": pt_voice["url"],
        "trim": 0.05,
        "seconds": 1.75,
        "narration_offset": 0.125,
        "aspect": "16:9",
        "cues": [{"start": 0.125, "end": 0.625, "text": "Legenda corrigida."}],
    }
    assert client.post(preview_url, headers=other, json=request).status_code == 404
    assert client.post(preview_url, json=request).status_code == 401
    invalid = dict(request, clip=dict(request["clip"], trim=1.0))
    assert client.post(preview_url, headers=auth, json=invalid).status_code == 409
    no_text = client.post(
        preview_url, headers=auth, json=dict(request, subtitles=False)
    )
    assert no_text.json()["cues"] == []
    cut_url = timeline_url + "/cut-preview"
    cut_request = {
        "timeline": dict(
            timeline,
            clips=[dict(request["clip"], transition="fade", effect=soundtrack["id"])],
            watermark=True,
        ),
        "language": "pt-BR",
    }
    cut = client.post(cut_url, headers=auth, json=cut_request).raise_for_status().json()
    music_url = path + "/media/" + soundtrack["id"]
    assert cut == {
        "clips": [
            dict(
                {k: v for k, v in preview.items() if k != "aspect"},
                scene=sid,
                title=story["data"]["title"],
                start=0,
                effect=music_url,
                fade=0.4,
            )
        ],
        "seconds": 1.75,
        "aspect": "16:9",
        "music": music_url,
        "music_volume": 0.15,
        "watermark": True,
    }
    assert client.post(cut_url, headers=other, json=cut_request).status_code == 404
    assert client.post(cut_url, json=cut_request).status_code == 401
    wrong_scene = dict(cut_request["timeline"], clips=[{"scene": uuid4().hex}])
    assert (
        client.post(
            cut_url, headers=auth, json=dict(cut_request, timeline=wrong_scene)
        ).status_code
        == 409
    )
    wrong_language = dict(cut_request["timeline"], languages=["en-US"])
    assert (
        client.post(
            cut_url, headers=auth, json=dict(cut_request, timeline=wrong_language)
        ).status_code
        == 422
    )
    assert store.snapshot(actor, pid)["jobs"] == jobs_before
    assert client.get(timeline_url, headers=auth).status_code == 404
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
