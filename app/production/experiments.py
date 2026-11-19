"""Versioned editorial hypotheses, Studio observations and analysis context."""

from statistics import median
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from . import models as m
from .store import RuleError, unpack


Metric = Literal["views", "ctr", "retention_30", "average_viewed", "stayed"]
Traffic = Literal["total", "browse", "suggested", "search", "shorts_feed", "external"]
Percent = Annotated[float, Field(ge=0, le=100)]
Count = Annotated[int, Field(strict=True, ge=0, le=10_000_000_000)]


class Plan(m.Input):
    hypothesis: m.Text
    variable: m.Short
    control: m.Text
    primary_metric: Metric


class Observation(m.Input):
    youtube_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{11}$")]
    days: Literal[2, 7, 28]
    traffic: Traffic
    views: Count
    ctr: Percent | None = None
    impressions: Count | None = None
    retention_30: Percent | None = None
    average_viewed: Annotated[float, Field(ge=0, le=10000)] | None = None
    stayed: Percent | None = None
    note: m.Text | None = None


class Report(m.Input):
    content_findings: m.Text
    hypothesis_readout: m.Text
    limitations: m.Text
    next_test: m.Text


def _asset(store, db, pid, version):
    item = store.asset(db, pid, version)
    if item["kind"] not in {"assembly", "export"} or not item["media"]:
        raise RuleError("Choose a rendered cut or final export", 422)
    return item


def _format(project, item):
    data = project["data"]
    profile = data.get("profile") or {}
    series = profile.get("series") or (data.get("editorial") or {}).get("series")
    return {
        "series": series.casefold() if series else project["id"],
        "channel": profile.get("channel") or "",
        "aspect": item["data"]["aspect"],
        "language": item["data"]["languages"][0],
        "seconds": item["data"]["seconds"],
        "short": profile.get("series") == "shorts"
        if profile.get("series")
        else item["data"]["aspect"] == "9:16",
    }


def _observations(store, db, pid, version):
    return [
        store.asset(db, pid, row["id"])
        for row in db.execute(
            """SELECT v.id FROM versions v WHERE v.project=? AND v.kind='observation'
            AND v.slot LIKE ? AND v.seq=(SELECT MAX(h.seq) FROM versions h
            WHERE h.project=v.project AND h.slot=v.slot) ORDER BY v.created DESC""",
            (pid, f"observation/{version}/%"),
        )
    ]


def save_plan(store, actor, pid, version, request):
    with store.transaction() as db:
        project = store.project(db, actor, pid)
        item = _asset(store, db, pid, version)
        format = _format(project, item)
        metric = request.primary_metric
        if (
            metric == "retention_30"
            and (format["short"] or format["seconds"] < 30)
            or metric == "stayed"
            and not format["short"]
        ):
            raise RuleError(
                "The primary metric does not match the selected format", 422
            )
        slot = f"experiment/{version}"
        previous = store.head(db, pid, slot)
        data = request.model_dump()
        if previous and previous["data"] == data:
            return previous
        if _observations(store, db, pid, version):
            raise RuleError("The hypothesis is locked after recording observations")
        return store.add(db, pid, slot, "experiment", data)


def save_observation(store, actor, pid, version, request):
    with store.transaction() as db:
        project = store.project(db, actor, pid)
        item = _asset(store, db, pid, version)
        if item["kind"] != "export":
            raise RuleError("Record Studio observations on the published final export")
        if not store.head(db, pid, f"experiment/{version}"):
            raise RuleError("Save the hypothesis first")
        format = _format(project, item)
        if (
            request.retention_30 is not None
            and (format["short"] or format["seconds"] < 30)
            or request.stayed is not None
            and not format["short"]
        ):
            raise RuleError("Enter the Studio metrics for this format", 422)
        if request.ctr is not None and not request.impressions:
            raise RuleError("Enter the impressions measured for this CTR", 422)
        if not request.views and any(
            metric is not None
            for metric in (request.retention_30, request.average_viewed, request.stayed)
        ):
            raise RuleError("Enter views for the audience figures", 422)
        if any(
            o["data"]["youtube_id"] != request.youtube_id
            for o in _observations(store, db, pid, version)
        ):
            raise RuleError("The export is already linked to a different YouTube video")
        slot = f"observation/{version}/{request.days}/{request.traffic}"
        data = request.model_dump() | {"content_version": version}
        previous = store.head(db, pid, slot)
        if previous and previous["data"] == data:
            return previous
        return store.add(db, pid, slot, "observation", data)


def _comparison(store, db, project, format, own, metric):
    current = own["data"]
    rows = db.execute(
        """SELECT v.id,v.project,v.data FROM versions v JOIN projects p ON p.id=v.project
        WHERE p.owner=? AND v.kind='observation' AND v.id<>?
        AND json_extract(v.data,'$.days')=? AND json_extract(v.data,'$.traffic')=?
        AND json_extract(v.data,'$.youtube_id')<>? AND v.seq=(SELECT MAX(h.seq)
        FROM versions h WHERE h.project=v.project AND h.slot=v.slot)
        ORDER BY v.created DESC""",
        (
            project["owner"],
            own["id"],
            current["days"],
            current["traffic"],
            current["youtube_id"],
        ),
    )
    peers = []
    videos = set()
    for row in rows:
        item = unpack(row, "data")
        data = item["data"]
        if data[metric] is None or data["youtube_id"] in videos:
            continue
        peer = store.project(db, project["owner"], row["project"])
        asset = _asset(store, db, peer["id"], data["content_version"])
        peer_format = _format(peer, asset)
        if any(
            peer_format[key] != format[key]
            for key in ("series", "channel", "aspect", "language", "short")
        ):
            continue
        if abs(peer_format["seconds"] - format["seconds"]) > format["seconds"] * 0.20:
            continue
        videos.add(data["youtube_id"])
        experiment = store.head(db, peer["id"], f"experiment/{asset['id']}")
        peers.append(
            {
                "observation": row["id"],
                "export": asset["id"],
                "title": peer["data"]["title"],
                "value": data[metric],
                "views": data["views"],
                "impressions": data["impressions"],
                "hypothesis_version": experiment["id"],
                "experiment": experiment["data"],
            }
        )
        if len(peers) == 12:
            break
    baseline = median(p["value"] for p in peers) if peers else None
    value = current[metric]
    return {
        "metric": metric,
        "value": value,
        "median": baseline,
        "peers": peers,
        "delta": round(value - baseline, 3)
        if value is not None and baseline is not None
        else None,
        "method": "observational",
    }


def _snapshot(store, db, project, item):
    pid, version = project["id"], item["id"]
    plan = store.head(db, pid, f"experiment/{version}")
    format = _format(project, item)
    observations = _observations(store, db, pid, version)
    report = store.head(db, pid, f"analysis/{version}")
    if report:
        report["fresh"] = store.fresh(db, report)
    return {
        "plan": plan,
        "observations": observations,
        "comparisons": {
            row["id"]: _comparison(
                store, db, project, format, row, plan["data"]["primary_metric"]
            )
            for row in observations
        }
        if plan
        else {},
        "report": report,
        "format": format,
    }


def snapshot(store, actor, pid, version):
    with store.transaction(write=False) as db:
        project = store.project(db, actor, pid)
        return _snapshot(store, db, project, _asset(store, db, pid, version))


def analysis_context(store, db, project, version, observation):
    pid = project["id"]
    item = _asset(store, db, pid, version)
    record = _snapshot(store, db, project, item)
    plan = record["plan"]
    if not plan:
        raise RuleError("Save the hypothesis before requesting analysis")
    selected = next(
        (row for row in record["observations"] if row["id"] == observation), None
    )
    if observation and not selected:
        raise RuleError("Choose a current observation of the selected export", 404)
    assembly = item
    if item["kind"] == "export":
        assembly = next(
            (
                store.asset(db, pid, dependency["id"])
                for dependency in item["dependencies"]
                if "id" in dependency
            ),
            None,
        )
        if not assembly or assembly["kind"] != "assembly":
            raise RuleError("The exported cut's source assembly is unavailable")
    language = record["format"]["language"]
    material = []
    for dependency in assembly["dependencies"]:
        if "id" not in dependency:
            continue
        asset = store.asset(db, pid, dependency["id"])
        if asset["kind"] == "scene":
            material.append(
                {
                    "scene_version": asset["id"],
                    "title": asset["data"]["title"],
                    "description": asset["data"].get("description"),
                }
            )
        if asset["kind"] == "voice" and asset["data"].get("language") == language:
            material.append(
                {
                    "voice_version": asset["id"],
                    "narration": asset["data"].get("narration"),
                }
            )
    if not material:
        raise RuleError("The cut has no versioned narration or storyboard for analysis")
    deps = [
        {"id": row["id"], "status": row["status"]}
        for row in [plan, *([selected] if selected else [])]
    ]
    return {
        "content_version": version,
        "assembly_version": assembly["id"],
        "title": item["data"].get("metadata", {}).get(language, {}).get("title")
        or (project["data"].get("profile") or {}).get("public_title")
        or project["data"]["title"],
        "topic": project["data"]["topic"],
        "format": record["format"],
        "timings": assembly["data"].get("retention", []),
        "material": material,
        "hypothesis_version": plan["id"],
        "experiment": plan["data"],
        "observation_version": observation,
        "observation": selected["data"] if selected else None,
        "comparison": record["comparisons"].get(observation),
    }, deps
