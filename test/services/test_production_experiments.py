"""Editorial experiment contracts with a mocked Codex submission."""

import json

from fastapi.testclient import TestClient

from app.production import codex_cli, project_profiles
from app.production.api import create_app
from app.production.worker import Worker


TOKENS = {"a" * 32: "alice", "b" * 32: "bob"}
HEADERS = {
    actor: {"Authorization": "Bearer " + token} for token, actor in TOKENS.items()
}
PLAN = {
    "hypothesis": "An early demonstration brings more viewers to the first answer.",
    "variable": "When the demonstration appears",
    "control": "Same title, spoken question and location.",
    "primary_metric": "retention_30",
}


def content(
    store, title, *, actor="alice", series="what-if", duration=600, language="en-US"
):
    pid = project_profiles.create_hyphotenic(
        store,
        actor,
        project_profiles.HyphotenicProject(
            channel="hyphotenic",
            series=series,
            public_title=title,
            topic="Would a glass bridge change what a visitor notices?",
            languages=[language],
        ),
    )
    with store.transaction() as db:
        scene = store.add(
            db,
            pid,
            "scene/example",
            "scene",
            {
                "title": "First answer",
                "description": "A viewer looks down through a glass panel.",
            },
        )
        voice = store.add(
            db,
            pid,
            "voice/example/" + language,
            "voice",
            {"language": language, "narration": f"Exact narration for {title}."},
        )
        cut = store.add(
            db,
            pid,
            "assembly",
            "assembly",
            {
                "aspect": "16:9",
                "seconds": duration,
                "languages": [language],
                "watermark": False,
                "retention": [{"scene": "First answer", "start": 0, "end": 30}],
                "metadata": {
                    language: {"title": title, "description": "Published description"}
                },
            },
            [{"id": scene["id"]}, {"id": voice["id"]}],
            "mock-cut.mp4",
        )
        export = store.add(
            db,
            pid,
            "export",
            "export",
            cut["data"],
            [{"id": cut["id"]}],
            "mock-export.mp4",
        )
    return pid, export["id"], cut["id"]


def test_experiments_compare_matching_samples_and_keep_version_history(tmp_path):
    app = create_app(
        tmp_path, TOKENS, worker_enabled=False, allowed_hosts=("testserver",)
    )
    store = app.state.store
    with TestClient(app) as client:

        def seed(label, index, *, days=7, traffic="total", retention=62, **kwargs):
            pid, export, cut = content(store, label, **kwargs)
            headers = HEADERS[kwargs.get("actor", "alice")]
            root = f"/api/projects/{pid}/experiments/"
            route = root + export
            plan = client.put(route, json=PLAN, headers=headers)
            assert plan.status_code == 200
            assert (
                client.put(route, json=PLAN, headers=headers).json()["id"]
                == plan.json()["id"]
            )
            sample = {
                "youtube_id": f"QA{index:09d}",
                "days": days,
                "traffic": traffic,
                "views": 1000,
                "retention_30": retention,
            }
            assert (
                client.post(
                    root + cut + "/observations", json=sample, headers=headers
                ).status_code
                == 409
            )
            response = client.post(
                route + "/observations", json=sample, headers=headers
            )
            assert response.status_code == 200
            return route, response.json()["id"], sample

        route, observation, sample = seed("Target", 1)
        seed("Earlier episode", 2, retention=50)
        seed("Second episode", 3, retention=60)
        seed("Different window", 4, days=2)
        seed("Different traffic", 5, traffic="browse")
        seed("Different length", 6, duration=200)
        seed("Different owner", 7, actor="bob")
        seed("Different series", 8, series="alternate-history")
        seed("Different language", 9, language="pt-BR")
        alice, bob = HEADERS["alice"], HEADERS["bob"]
        record = client.get(route, headers=alice).json()
        comparison = record["comparisons"][observation]
        assert {entry["title"] for entry in comparison["peers"]} == {
            "Hyphotenic | Earlier episode",
            "Hyphotenic | Second episode",
        }
        assert (comparison["median"], comparison["delta"], comparison["method"]) == (
            55,
            7,
            "observational",
        )
        assert client.get(route, headers=bob).status_code == 404
        assert client.put(route, json=PLAN, headers=bob).status_code == 404
        assert (
            client.put(
                route, json=dict(PLAN, variable="New variable"), headers=alice
            ).status_code
            == 409
        )
        assert (
            client.post(
                route + "/observations",
                json=dict(sample, youtube_id="other000000"),
                headers=alice,
            ).status_code
            == 409
        )
        assert (
            client.post(route + "/observations", json=sample, headers=alice).json()[
                "id"
            ]
            == observation
        )
        newer = client.post(
            route + "/observations", json=dict(sample, retention_30=0), headers=alice
        ).json()
        assert newer["id"] != observation and newer["seq"] == 2
        assert (
            client.get(route, headers=alice).json()["comparisons"][newer["id"]]["delta"]
            == -55
        )


def test_analysis_is_supervised_and_uses_exact_content_and_measurement(
    tmp_path, monkeypatch
):
    app = create_app(
        tmp_path, TOKENS, worker_enabled=False, allowed_hosts=("testserver",)
    )
    store = app.state.store
    pid, export, cut = content(store, "Test cut")
    alice, bob = HEADERS["alice"], HEADERS["bob"]
    monkeypatch.setattr(codex_cli, "executable", lambda: "mock-codex")
    calls = []

    def mock_generate(kind, messages, model, scratch, stop, timeout):
        assert kind == "analysis"
        assert not stop()
        assert "observational" in messages[0]["content"]
        evidence = json.loads(messages[1]["content"])["experiment"]
        assert (
            evidence["content_version"] == export
            and evidence["assembly_version"] == cut
        )
        assert evidence["title"] == "Test cut"
        assert evidence["material"][1]["narration"] == "Exact narration for Test cut."
        assert (
            evidence["experiment"] == PLAN
            and evidence["observation"]["retention_30"] == 61
        )
        calls.append(evidence)
        return {
            "content_findings": "A demonstração explica a situação.",
            "hypothesis_readout": "Leitura observacional da amostra.",
            "limitations": "Sem controle de tráfego comparável.",
            "next_test": "Mover a resposta para antes da definição e observar R30.",
        }

    monkeypatch.setattr(codex_cli, "generate", mock_generate)
    with TestClient(app) as client:
        root = f"/api/projects/{pid}"
        route = f"{root}/experiments/{export}"
        job = {"operation": "analysis", "provider": "codex", "version": export}
        assert (
            client.post(
                root + "/quotes", json={"jobs": [job]}, headers=alice
            ).status_code
            == 409
        )
        plan = client.put(route, json=PLAN, headers=alice).json()["id"]
        sample = {
            "youtube_id": "QA000000001",
            "days": 7,
            "traffic": "total",
            "views": 100,
            "retention_30": 60,
        }
        observation_url = route + "/observations"
        assert client.post(observation_url, json=sample, headers=bob).status_code == 404
        observation = client.post(observation_url, json=sample, headers=alice).json()[
            "id"
        ]
        job["observation"] = observation
        assert (
            client.post(
                root + "/quotes",
                json={"jobs": [dict(job, provider="openai")]},
                headers=alice,
            ).status_code
            == 422
        )
        quote = client.post(
            root + "/quotes", json={"jobs": [job]}, headers=alice
        ).json()
        assert quote["items"][0]["billing"] == "subscription" and quote["total"] == 0
        assert not Worker(store).step() and calls == []
        auth = root + "/quotes/" + quote["id"] + "/authorize"
        confirmation = {"confirmed": True, "maximum": 0}
        assert client.post(auth, json=confirmation, headers=bob).status_code == 404
        assert (
            client.post(
                auth, json=dict(confirmation, confirmed=False), headers=alice
            ).status_code
            == 422
        )
        active = client.post(auth, json=confirmation, headers=alice).json()["jobs"][0]
        observation = client.post(
            observation_url, json=dict(sample, retention_30=61), headers=alice
        ).json()["id"]
        assert not Worker(store).step() and calls == []
        assert store.snapshot("alice", pid)["jobs"][0]["id"] == active
        assert store.snapshot("alice", pid)["jobs"][0]["status"] == "CANCELLED"
        assert (
            client.post(
                root + "/quotes", json={"jobs": [job]}, headers=alice
            ).status_code
            == 404
        )
        quote = client.post(
            root + "/quotes",
            json={"jobs": [dict(job, observation=observation)]},
            headers=alice,
        ).json()
        response = client.post(
            root + "/quotes/" + quote["id"] + "/authorize",
            json=confirmation,
            headers=alice,
        )
        assert response.status_code == 202
        assert Worker(store).step()
        assert len(calls) == 1
        report = client.get(route, headers=alice).json()["report"]
        assert report["data"]["evidence"]["hypothesis_version"] == plan
        assert report["data"]["evidence"]["observation_version"] == observation
        assert report["data"]["next_test"].startswith("Mover")
