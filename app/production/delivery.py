"""Review the exact inputs of a saved cut and prepare its final assembly."""

from pydantic import Field

from . import models as m
from .store import RuleError


class Preparation(m.Input):
    revision: m.Id
    versions: list[m.Id] = Field(min_length=1, max_length=1000)
    confirmed: m.Confirmed


class MaterialReview(Preparation):
    scenes: list[m.Id] = Field(min_length=1, max_length=100)


class Delivery:
    def __init__(self, store):
        self.store = store

    def _head(self, db, pid, revision):
        timeline = self.store.head(db, pid, "timeline")
        if not timeline:
            raise RuleError("Save the timeline before reviewing the delivery")
        if revision and timeline["id"] != revision:
            raise RuleError("The cut changed. Review the new saved version.")
        return timeline

    def _plan(self, db, project, saved):
        pid = project["id"]
        timeline = m.Timeline.model_validate(saved["data"])
        deps = []
        compiled = self.store._timeline(
            db, pid, project["data"], timeline, deps, for_review=True
        )
        materials = {}
        attachments = []
        for dependency in deps:
            if "id" not in dependency:
                continue
            asset = self.store.asset(db, pid, dependency["id"])
            if asset["kind"] == "audio":
                attachments.append(self._material(db, asset))
            else:
                sid = asset["slot"].split("/")[1]
                materials.setdefault(sid, []).append(asset)

        scenes = []
        for clip, timing in zip(timeline.clips, compiled["retention"], strict=True):
            ordered = {}

            def add(asset):
                if asset["id"] in ordered:
                    return
                for dep in asset["dependencies"]:
                    if dep.get("id"):
                        source = self.store.asset(db, pid, dep["id"])
                        if source["kind"] in {
                            "scene",
                            "image",
                            "reference",
                            "translation",
                        }:
                            add(source)
                ordered[asset["id"]] = self._material(db, asset)

            for asset in materials[clip.scene]:
                add(asset)
            scenes.append(
                {
                    "id": clip.scene,
                    "title": timing["scene"],
                    "start": timing["start"],
                    "end": timing["end"],
                    "materials": list(ordered.values()),
                }
            )
        return {
            "revision": saved["id"],
            "seq": saved["seq"],
            "scenes": scenes,
            "attachments": attachments,
            "languages": timeline.languages,
            "aspect": timeline.aspect,
            "resolution": timeline.resolution,
        }

    def _material(self, db, asset):
        data = asset["data"]
        return {
            "id": asset["id"],
            "kind": asset["kind"],
            "seq": asset["seq"],
            "status": asset["status"],
            "url": asset.get("url"),
            "title": data.get("title"),
            "language": data.get("language"),
            "provider": data.get("provider"),
            "model": data.get("model"),
            "provisional": self.store.rough_cut_output(db, asset),
        }

    def snapshot(self, actor, pid):
        with self.store.transaction(write=False) as db:
            project = self.store.project(db, actor, pid)
            try:
                return self._plan(db, project, self._head(db, pid, None))
            except RuleError as error:
                return {"blocked": str(error)}

    def costs(self, actor, pid, version):
        with self.store.transaction(write=False) as db:
            project = self.store.project(db, actor, pid)
            export = self.store.asset(db, pid, version)
            if export["kind"] != "export" or not export["media"]:
                raise RuleError("Export not found", 404)
            approval = self._export_approval(db, pid, export)
            lineage = db.execute(
                """WITH RECURSIVE linked(id) AS (
                    SELECT id FROM versions WHERE project=? AND id=?
                    UNION
                    SELECT COALESCE(json_extract(dep.value,'$.id'),
                                    json_extract(dep.value,'$.timeline_revision'))
                    FROM linked JOIN versions v ON v.project=? AND v.id=linked.id,
                         json_each(v.dependencies) dep
                    WHERE json_extract(dep.value,'$.id') IS NOT NULL
                       OR json_extract(dep.value,'$.timeline_revision') IS NOT NULL
                )
                SELECT v.job,v.kind,v.media IS NOT NULL AS has_media,v.id IS NULL AS missing
                FROM linked LEFT JOIN versions v ON v.project=? AND v.id=linked.id""",
                (pid, version, pid, pid),
            ).fetchall()
            job_links = {row["job"] for row in lineage if row["job"]}
            jobs = db.execute(
                """SELECT j.id,j.provider,l.actual,l.uncertain,l.reserved
                FROM jobs j JOIN ledger l ON l.job=j.id
                WHERE j.project=? AND j.created<=?
                ORDER BY j.created DESC,j.ordinal DESC,j.id DESC""",
                (pid, export["created"]),
            ).fetchall()
            fields = ("actual", "uncertain", "reserved")
            groups = {
                name: dict(dict.fromkeys((*fields, "jobs"), 0), job_ids=[])
                for name in ("materials", "other")
            }
            included = set()
            for job in jobs:
                included.add(job["id"])
                group = groups["materials" if job["id"] in job_links else "other"]
                for field in fields:
                    group[field] += job[field]
                group["jobs"] += 1
                group["job_ids"].append(job["id"])
            material_kinds = {
                "image",
                "video",
                "reference",
                "voice",
                "audio",
                "assembly",
                "export",
            }
            return {
                "export": export["id"],
                "seq": export["seq"],
                "timeline_revision": export["data"].get("timeline_revision"),
                "exported_at": export["created"],
                "currency": project["data"]["currency"],
                "approval": approval,
                "totals": {
                    field: groups["materials"][field] + groups["other"][field]
                    for field in (*fields, "jobs")
                },
                **groups,
                "subscription_jobs": sum(job["provider"] == "codex" for job in jobs),
                "missing_versions": sum(row["missing"] for row in lineage),
                "missing_job_links": len(job_links - included),
                "media_without_job": sum(
                    not row["job"]
                    and row["has_media"]
                    and row["kind"] in material_kinds
                    for row in lineage
                ),
                "pending_job": next(
                    (job["id"] for job in jobs if job["uncertain"]), None
                ),
            }

    @staticmethod
    def _export_approval(db, pid, export):
        for dependency in export["dependencies"]:
            ident = dependency.get("id")
            if not ident:
                continue
            review = db.execute(
                """SELECT a.id AS assembly,r.decision,r.created AS approved_at
                FROM versions a JOIN reviews r ON r.version=a.id
                WHERE a.project=? AND a.id=? AND a.kind='assembly' AND r.created<=?
                ORDER BY r.created DESC,r.id DESC LIMIT 1""",
                (pid, ident, export["created"]),
            ).fetchone()
            if review and review["decision"] == "APPROVED":
                return {
                    "assembly": review["assembly"],
                    "approved_at": review["approved_at"],
                }
        return None

    def approve(self, actor, pid, request):
        with self.store.transaction() as db:
            project = self.store.project(db, actor, pid)
            saved = self._head(db, pid, request.revision)
            plan = self._plan(db, project, saved)
            selected = set(request.scenes)
            rows = [row for row in plan["scenes"] if row["id"] in selected]
            if {row["id"] for row in rows} != selected:
                raise RuleError("Choose scenes from the saved cut", 422)
            materials = {
                material["id"]: material
                for row in rows
                for material in row["materials"]
            }
            if set(materials) != set(request.versions):
                raise RuleError("The selected materials changed. Reload the review.")
            pending = [
                ident
                for ident, material in materials.items()
                if material["status"] == "AWAITING_REVIEW"
            ]
            self.store._review(
                db,
                actor,
                pid,
                pending,
                m.ReviewInput(
                    status="APPROVED",
                    comment=f"Delivery review of timeline {saved['id']}",
                ),
            )
            return {"revision": saved["id"], "approved": pending}

    def prepare(self, actor, pid, request):
        with self.store.transaction() as db:
            project = self.store.project(db, actor, pid)
            saved = self._head(db, pid, None)
            timeline = m.Timeline.model_validate(dict(saved["data"], watermark=False))
            if saved["id"] != request.revision:
                previous = self.store.asset(db, pid, request.revision, missing=True)
                if (
                    not previous
                    or previous["slot"] != "timeline"
                    or m.Timeline.model_validate(
                        dict(previous["data"], watermark=False)
                    )
                    != timeline
                    or saved["data"]["watermark"]
                ):
                    raise RuleError("The cut changed. Review the new saved version.")
            plan = self._plan(db, project, saved)
            materials = [m for row in plan["scenes"] for m in row["materials"]] + plan[
                "attachments"
            ]
            if {item["id"] for item in materials} != set(request.versions):
                raise RuleError("The delivery materials changed. Reload the review.")
            if any(item["status"] != "APPROVED" for item in materials):
                raise RuleError("Review all delivery materials first")
            deps = []
            self.store._timeline(db, pid, project["data"], timeline, deps)
            data = timeline.model_dump()
            if saved["data"] == data and saved["dependencies"] == deps:
                return saved
            if saved["id"] != request.revision:
                raise RuleError("The cut changed. Review the new saved version.")
            return self.store.add(db, pid, "timeline", "timeline", data, deps)
