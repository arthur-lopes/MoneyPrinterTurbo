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
