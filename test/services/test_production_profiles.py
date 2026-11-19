"""Profile inheritance and paid admission without provider calls."""

from fastapi.testclient import TestClient
import pytest

from app.production import models as m, project_profiles
from app.production.adapters import _context_prompt
from app.production.api import create_app
from app.production.store import RuleError
from app.production.workbench import Workbench
from app.production.worker import Worker


def test_profiles_create_independent_drafts_and_survive_reopening(tmp_path):
    tokens = {"a" * 32: "alice", "b" * 32: "bob"}
    alice = {"Authorization": "Bearer " + "a" * 32}
    bob = {"Authorization": "Bearer " + "b" * 32}

    def app():
        return create_app(
            tmp_path, tokens, worker_enabled=False, allowed_hosts=("testserver",)
        )

    cases = [
        ("what-if", "What if tides stopped?", 840, "16:9", 8),
        ("alternate-history", "What if railroads arrived earlier?", 780, "16:9", 8),
        ("first-24-hours", "What happens when a canal closes?", 600, "16:9", 8),
        ("shorts", "Why do echoes fade?", 40, "9:16", 4),
    ]
    ids = []
    with TestClient(app()) as client:
        assert client.get("/api/project-profiles/hyphotenic").status_code == 401
        profile = client.get("/api/project-profiles/hyphotenic", headers=alice).json()
        assert list(profile["series"]) == [series for series, *_ in cases]
        assert client.get(
            "/api/project-profiles/hyphotenic/logo", headers=alice
        ).content.startswith(b"\x89PNG\r\n\x1a\n")
        for series, topic, *_ in cases:
            response = client.post(
                "/api/projects",
                headers=alice,
                json={
                    "channel": "hyphotenic",
                    "series": series,
                    "public_title": topic,
                    "topic": topic,
                    "changed_condition": "One variable changes.",
                },
            )
            assert response.status_code == 201
            ids.append(response.json()["id"])
        custom = client.post(
            "/api/projects",
            headers=alice,
            json={
                "channel": "hyphotenic",
                "series": "shorts",
                "public_title": "Echo timing",
                "topic": "How long does the sound last?",
                "duration": 47,
                "structure": ["Show the delay", "Explain it"],
                "style": {"aspect": "16:9"},
                "languages": ["en-US"],
                "sources": [
                    {
                        "title": "Lab log",
                        "url": "https://example.org/notes",
                        "excerpt": "Unreviewed notes.",
                    }
                ],
            },
        ).json()["id"]
        general = client.post(
            "/api/projects",
            headers=alice,
            json={"title": "General", "topic": "Example", "audience": "General"},
        ).json()["id"]

    with TestClient(app()) as client:
        for pid, (series, topic, duration, aspect, beats) in zip(
            ids, cases, strict=True
        ):
            route = "/api/projects/" + pid
            detail = client.get(route, headers=alice).json()
            data = detail["data"]
            assert client.get(route, headers=bob).status_code == 404
            assert (data["title"], data["topic"]) == ("Hyphotenic | " + topic, topic)
            assert data["duration"] == duration and data["style"]["aspect"] == aspect
            assert data["structure"] == profile["series"][series]["structure"]
            assert len(data["structure"]) == beats
            assert (data["currency"], data["languages"]) == ("BRL", ["en-US", "pt-BR"])
            assert data["style"]["palette"] == profile["base"]["style"]["palette"]
            assert data["audience"] == profile["base"]["audience"]
            assert data["tone"] == profile["base"]["tone"]
            assert (
                data["profile"]["brief"]["changed_condition"] == "One variable changes."
            )
            assert (
                data["profile"]["script_instructions"] == profile["script_instructions"]
            )
            assert (
                data["profile"]["research_checklist"] == profile["research_checklist"]
            )
            assert data["profile"]["approvals"] == profile["approvals"]
            assert detail["jobs"] == detail["costs"]["limits"] == []
            for kind, key, value in [
                ("script", "script_instructions", profile["script_instructions"]),
                ("research", "research_checklist", profile["research_checklist"][0]),
            ]:
                assert value in _context_prompt(kind, {"project": data})[0]["content"]
                assert data["profile"][key]
        detail = client.get("/api/projects/" + custom, headers=alice).json()
        data = detail["data"]
        assert data["duration"] == 47 and data["style"]["aspect"] == "16:9"
        assert data["structure"] == ["Show the delay", "Explain it"]
        assert data["languages"] == ["en-US"]
        assert data["style"]["palette"] == profile["base"]["style"]["palette"]
        assert detail["versions"][0]["status"] == "AWAITING_REVIEW"
        assert detail["versions"][0]["data"]["sources"][0]["title"] == "Lab log"
        assert detail["versions"][0]["data"]["claims"] == []
        assert (
            "profile"
            not in client.get("/api/projects/" + general, headers=alice).json()["data"]
        )


def test_paid_profile_jobs_require_current_script_storyboard_and_budget(
    tmp_path, monkeypatch
):
    store = Workbench(tmp_path)
    actor = "alice"
    pid = project_profiles.create_hyphotenic(
        store,
        actor,
        project_profiles.HyphotenicProject(
            channel="hyphotenic",
            series="shorts",
            public_title="A test",
            topic="How does a shadow move?",
            languages=["en-US"],
            structure=["Setup", "Answer"],
        ),
    )
    approve = m.ReviewInput(status="APPROVED")
    with store.transaction() as db:
        research = store.add(
            db, pid, "research", "research", {"sources": [], "claims": []}
        )
    local = m.RequestJob(operation="script", provider="local")
    with pytest.raises(RuleError, match="research approval"):
        store.quote(actor, pid, [local])
    store.review(actor, pid, [research["id"]], approve)
    quote = store.quote(actor, pid, [local])
    assert quote["total"] == 0
    store.authorize(actor, pid, quote["id"], 0)
    assert Worker(store).step()
    with store.transaction(write=False) as db:
        script = store.head(db, pid, "script")
    store.review(actor, pid, [script["id"]], approve)
    first, second = store.storyboard(actor, pid)
    with store.transaction(write=False) as db:
        target = store.head(db, pid, f"scene/{first}")
        other = store.head(db, pid, f"scene/{second}")
    store.review(actor, pid, [target["id"]], approve)
    monkeypatch.setenv("MPT_PRODUCTION_OPENAI_KEY", "mock-provider-test-value")
    store.provider(
        actor,
        m.Provider(
            provider="openai",
            operation="image",
            model="gpt-image-1.5",
            currency="BRL",
            ceiling=100,
            enabled=True,
            confirmed=True,
        ),
    )
    paid = m.RequestJob(operation="image", provider="openai", scene=first)
    with pytest.raises(RuleError, match="scene approval"):
        store.quote(actor, pid, [paid])
    store.review(actor, pid, [other["id"]], approve)
    quote = store.quote(actor, pid, [paid])
    assert (quote["total"], quote["currency"]) == (100, "BRL")
    with pytest.raises(RuleError, match="Project not found"):
        store.authorize("bob", pid, quote["id"], 100)
    with pytest.raises(RuleError, match="below the estimate"):
        store.authorize(actor, pid, quote["id"], 99)
    with pytest.raises(RuleError, match="Set a project budget"):
        store.authorize(actor, pid, quote["id"], 100)
    store.budget(actor, pid, m.Budget(scope="project", limit=100, confirmed=True))
    (job,) = store.authorize(actor, pid, quote["id"], 100)
    store.edit_scene(
        actor,
        pid,
        second,
        m.Scene(**dict(other["data"], description="Relight the subject.")),
    )
    assert store.claim() is None
    detail = store.snapshot(actor, pid)
    assert (
        next(item for item in detail["jobs"] if item["id"] == job)["status"]
        == "CANCELLED"
    )
    assert detail["costs"]["reserved"] == detail["costs"]["actual"] == 0
