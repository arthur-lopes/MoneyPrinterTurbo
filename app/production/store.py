"""Project, revision, approval and queue persistence for one local workstation."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from . import models as m


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scenes (
    id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id),
    position INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id), slot TEXT NOT NULL,
    kind TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL, dependencies TEXT NOT NULL,
    media TEXT, job TEXT, created REAL NOT NULL, UNIQUE(project, slot, seq)
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY, version TEXT NOT NULL REFERENCES versions(id),
    actor TEXT NOT NULL, decision TEXT NOT NULL, comment TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
    owner TEXT NOT NULL, provider TEXT NOT NULL, operation TEXT NOT NULL, currency TEXT NOT NULL,
    data TEXT NOT NULL, PRIMARY KEY(owner, provider, operation, currency)
);
CREATE TABLE IF NOT EXISTS budgets (
    owner TEXT NOT NULL, currency TEXT NOT NULL, scope TEXT NOT NULL, target TEXT NOT NULL,
    amount INTEGER NOT NULL, PRIMARY KEY(owner, currency, scope, target)
);
CREATE TABLE IF NOT EXISTS budget_history (
    id INTEGER PRIMARY KEY, owner TEXT NOT NULL, currency TEXT NOT NULL, scope TEXT NOT NULL,
    target TEXT NOT NULL, amount INTEGER NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
    id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id), requests TEXT NOT NULL,
    total INTEGER NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS authorizations (
    quote TEXT PRIMARY KEY REFERENCES quotes(id), actor TEXT NOT NULL, maximum INTEGER NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id),
    quote TEXT NOT NULL REFERENCES authorizations(quote), ordinal INTEGER NOT NULL,
    slot TEXT NOT NULL, operation TEXT NOT NULL, provider TEXT NOT NULL, spec TEXT NOT NULL,
    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, progress INTEGER NOT NULL DEFAULT 0,
    remote TEXT, polls INTEGER NOT NULL DEFAULT 0, last_error TEXT, result TEXT REFERENCES versions(id), cancel INTEGER NOT NULL DEFAULT 0,
    submitted REAL, poll_deadline REAL,
    available REAL NOT NULL, created REAL NOT NULL, UNIQUE(quote, ordinal)
);
CREATE TABLE IF NOT EXISTS ledger (
    job TEXT PRIMARY KEY REFERENCES jobs(id), reserved INTEGER NOT NULL,
    actual INTEGER NOT NULL DEFAULT 0, uncertain INTEGER NOT NULL DEFAULT 0,
    quoted INTEGER NOT NULL, period_day TEXT NOT NULL, period_month TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY, job TEXT NOT NULL REFERENCES jobs(id), actor TEXT NOT NULL,
    actual INTEGER NOT NULL, receipt TEXT NOT NULL, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS project_versions ON versions(project, slot, seq DESC);
CREATE INDEX IF NOT EXISTS project_jobs ON jobs(project, created);
CREATE INDEX IF NOT EXISTS ready_jobs ON jobs(status, available);
PRAGMA user_version=1;
"""

ACTIVE = ("DRAFT", "GENERATING")
SCENE_OPERATIONS = {"image", "video", "translation", "voice"}


class RuleError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def unpack(row, *fields):
    result = dict(row)
    for field in fields:
        result[field] = json.loads(result[field])
    return result


class Store:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.database = self.directory / "production.sqlite3"
        with sqlite3.connect(self.database, timeout=15) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
        db.close()

    @contextmanager
    def transaction(self, write=True):
        db = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, actor, data):
        pid = uuid4().hex
        with self.transaction() as db:
            db.execute(
                "INSERT INTO projects VALUES(?,?,?,?)",
                (pid, actor, encode(data), time.time()),
            )
        return pid

    def project(self, db, actor, pid):
        row = db.execute(
            "SELECT * FROM projects WHERE owner=? AND id=?", (actor, pid)
        ).fetchone()
        if row is None:
            raise RuleError("Project not found", 404)
        return unpack(row, "data")

    def asset(self, db, pid, version, missing=False):
        row = db.execute(
            """SELECT v.*, COALESCE((SELECT decision FROM reviews WHERE version=v.id
               ORDER BY id DESC LIMIT 1),'AWAITING_REVIEW') AS status
               FROM versions v WHERE v.project=? AND v.id=?""",
            (pid, version),
        ).fetchone()
        if row:
            item = unpack(row, "data", "dependencies")
            if item["media"]:
                item["url"] = f"/api/projects/{pid}/media/{version}"
            return item
        if not missing:
            raise RuleError("Version not found", 404)
        return None

    def head(self, db, pid, slot):
        row = db.execute(
            "SELECT id FROM versions WHERE project=? AND slot=? ORDER BY seq DESC LIMIT 1",
            (pid, slot),
        ).fetchone()
        return self.asset(db, pid, row["id"]) if row else None

    def fresh(self, db, asset, visited=None):
        if visited is None:
            visited = {}
        if asset["id"] in visited:
            return visited[asset["id"]]
        pid = asset["project"]
        result = self._dependencies_current(db, pid, asset["dependencies"], visited)
        visited[asset["id"]] = result
        return result

    def _dependencies_current(self, db, pid, dependencies, visited=None):
        for dep in dependencies:
            if "sections" in dep:
                script = self.head(db, pid, "script")
                if (
                    not script
                    or script["status"] != "APPROVED"
                    or not self.fresh(db, script, visited)
                ):
                    return False
                current = {
                    section["key"]: section for section in script["data"]["sections"]
                }
                if any(
                    current.get(section["key"]) != section
                    for section in dep["sections"]
                ):
                    return False
                continue
            if "order" in dep:
                if self.order(db, pid) != dep["order"]:
                    return False
                continue
            if "style" in dep:
                row = db.execute(
                    "SELECT data FROM projects WHERE id=?", (pid,)
                ).fetchone()
                if unpack(row, "data")["data"]["style"] != dep["style"]:
                    return False
                continue
            if "speech" in dep:
                sid, narration = dep["speech"]
                scene = self.head(db, pid, f"scene/{sid}")
                if (
                    sid not in self.order(db, pid)
                    or not scene
                    or scene["status"] != "APPROVED"
                ):
                    return False
                if (
                    not self.fresh(db, scene, visited)
                    or scene["data"]["narration"] != narration
                ):
                    return False
                continue
            asset = self.asset(db, pid, dep["id"], missing=True)
            if (
                not asset
                or asset["status"] != dep.get("status", "APPROVED")
                or not self.fresh(db, asset, visited)
            ):
                return False
            if (
                not dep.get("pinned", False)
                and self.head(db, pid, asset["slot"])["id"] != asset["id"]
            ):
                return False
            if asset["kind"] == "scene" and asset["slot"].split("/")[
                1
            ] not in self.order(db, pid):
                return False
        return True

    def require(self, db, pid, slot):
        asset = self.head(db, pid, slot)
        if not asset or asset["status"] != "APPROVED" or not self.fresh(db, asset):
            raise RuleError(f"Current {slot.split('/')[0]} approval is required")
        return asset

    def add(self, db, pid, slot, kind, data, deps=(), media=None, job=None):
        vid = uuid4().hex
        db.execute(
            """INSERT INTO versions SELECT ?,?,?,?,COALESCE(MAX(seq),0)+1,?,?,?,?,?
               FROM versions WHERE project=? AND slot=?""",
            (
                vid,
                pid,
                slot,
                kind,
                encode(data),
                encode(deps),
                media,
                job,
                time.time(),
                pid,
                slot,
            ),
        )
        return self.asset(db, pid, vid)

    def review(self, actor, pid, versions, request):
        with self.transaction() as db:
            self.project(db, actor, pid)
            assets = [
                self.asset(db, pid, version) for version in dict.fromkeys(versions)
            ]
            for asset in assets:
                if asset["kind"] == "export":
                    raise RuleError("Review the assembly to authorize an export")
                if asset["status"] == "APPROVED":
                    if request.status == "APPROVED":
                        continue
                    raise RuleError(
                        "Approved versions are immutable. Create a new version to revise."
                    )
                if request.status == "APPROVED" and (
                    self.head(db, pid, asset["slot"])["id"] != asset["id"]
                    or not self.fresh(db, asset)
                ):
                    raise RuleError("Version has been superseded or its inputs changed")
                db.execute(
                    "INSERT INTO reviews(version,actor,decision,comment,created) VALUES(?,?,?,?,?)",
                    (asset["id"], actor, request.status, request.comment, time.time()),
                )
            return [self.asset(db, pid, asset["id"]) for asset in assets]

    def script(self, db, pid, data, research, job=None):
        section_data = m.Script.model_validate(data).model_dump()
        source_catalog = research["data"]["sources"]
        for section in section_data["sections"]:
            refs = section["sources"]
            if section["category"] == "verified" and not refs:
                raise RuleError("Verified script sections require a source")
            if any(not 1 <= item <= len(source_catalog) for item in refs):
                raise RuleError("Script section references an unavailable source")
        return self.add(
            db, pid, "script", "script", section_data, [{"id": research["id"]}], job=job
        )

    def order(self, db, pid):
        return [
            row["id"]
            for row in db.execute(
                "SELECT id FROM scenes WHERE project=? AND active=1 ORDER BY position",
                (pid,),
            )
        ]

    def storyboard(self, actor, pid):
        with self.transaction() as db:
            project = self.project(db, actor, pid)["data"]
            script = self.require(db, pid, "script")
            order = self.order(db, pid)
            represented = set()
            current = {item["key"]: item for item in script["data"]["sections"]}
            existing = {sid: self.head(db, pid, f"scene/{sid}") for sid in order}
            occurrences = {}
            for old in existing.values():
                for key in old["data"]["sections"]:
                    occurrences[key] = occurrences.get(key, 0) + 1
            for sid in order:
                old = existing[sid]
                keys = old["data"]["sections"]
                if any(key not in current for key in keys):
                    db.execute("UPDATE scenes SET active=0 WHERE id=?", (sid,))
                else:
                    represented.update(keys)
                    if not self.fresh(db, old):
                        data = dict(old["data"])
                        prior_sections = old["dependencies"][0]["sections"]
                        if prior_sections != [current[key] for key in keys] and all(
                            occurrences[key] == 1 for key in keys
                        ):
                            data.update(
                                narration=" ".join(
                                    current[key]["narration"] for key in keys
                                )[:8000],
                                seconds=min(
                                    120, sum(current[key]["seconds"] for key in keys)
                                ),
                            )
                        self.scene_version(
                            db, pid, sid, m.Scene.model_validate(data), script
                        )
            for section in script["data"]["sections"]:
                key = section["key"]
                if key in represented:
                    continue
                if len(self.order(db, pid)) >= 100:
                    raise RuleError("The storyboard supports up to 100 active scenes")
                sid = uuid4().hex
                description = f"{section['title']}. Documentary illustration of {project['topic']}"[
                    :7900
                ]
                data = m.Scene(
                    title=section["title"],
                    sections=[key],
                    narration=section["narration"],
                    seconds=section["seconds"],
                    description=description,
                    image_prompt=description,
                    video_prompt="Slow camera movement across the approved composition.",
                    provider=project.get("defaults", {}).get("video", "local"),
                    model="gen4_turbo"
                    if project.get("defaults", {}).get("video") == "runway"
                    else "motion",
                )
                db.execute(
                    "INSERT INTO scenes(id,project,position) VALUES(?,?,?)",
                    (sid, pid, len(order)),
                )
                order.append(sid)
                self.scene_version(db, pid, sid, data, script)
            return self.order(db, pid)

    def scene_version(self, db, pid, sid, data, script):
        current = {section["key"]: section for section in script["data"]["sections"]}
        if not set(data.sections) <= current.keys() or len(set(data.sections)) != len(
            data.sections
        ):
            raise RuleError("Scene sections must be unique approved script sections")
        row = db.execute("SELECT data FROM projects WHERE id=?", (pid,)).fetchone()
        deps = [
            {"sections": [current[key] for key in data.sections]},
            {"style": unpack(row, "data")["data"]["style"]},
        ]
        for ref in data.references:
            item = self.asset(db, pid, ref)
            if item["kind"] != "reference" or item["status"] != "APPROVED":
                raise RuleError(
                    "Approve uploaded reference images before attaching them"
                )
            deps.append({"id": ref})
        if (data.provider, data.model) not in {
            ("local", "motion"),
            ("runway", "gen4_turbo"),
        }:
            raise RuleError("Choose a supported scene provider and model")
        return self.add(db, pid, f"scene/{sid}", "scene", data.model_dump(), deps)

    def edit_scene(self, actor, pid, sid, data):
        with self.transaction() as db:
            self.project(db, actor, pid)
            if sid not in self.order(db, pid):
                raise RuleError("Scene not found", 404)
            return self.scene_version(
                db, pid, sid, data, self.require(db, pid, "script")
            )

    def reorder(self, actor, pid, order):
        with self.transaction() as db:
            self.project(db, actor, pid)
            current = self.order(db, pid)
            if len(current) != len(order) or set(current) != set(order):
                raise RuleError("Reorder must contain every active scene exactly once")
            self.set_order(db, pid, order)

    def set_order(self, db, pid, order):
        db.executemany(
            "UPDATE scenes SET position=? WHERE project=? AND id=?",
            [(i, pid, sid) for i, sid in enumerate(order)],
        )

    def restructure(self, actor, pid, selection, split=None):
        with self.transaction() as db:
            self.project(db, actor, pid)
            order = self.order(db, pid)
            if any(sid not in order for sid in selection) or len(set(selection)) != len(
                selection
            ):
                raise RuleError("Select distinct active scenes")
            indexes = sorted(order.index(sid) for sid in selection)
            if indexes != list(range(indexes[0], indexes[-1] + 1)):
                raise RuleError("Select adjacent scenes")
            originals = [
                self.head(db, pid, f"scene/{order[i]}")["data"] for i in indexes
            ]
            first = originals[0]
            if split:
                if split.seconds >= first["seconds"] or len(order) >= 100:
                    raise RuleError(
                        "Split must be inside the scene and total scenes cannot exceed 100"
                    )
                parts = [
                    dict(first, seconds=split.seconds, narration=split.first),
                    dict(
                        first,
                        seconds=first["seconds"] - split.seconds,
                        narration=split.second,
                    ),
                ]
            else:
                total = sum(item["seconds"] for item in originals)
                narration = " ".join(item["narration"] for item in originals)
                if total > 120 or len(narration) > 8000:
                    raise RuleError(
                        "Merged scene exceeds the supported duration or narration length"
                    )
                parts = [
                    dict(
                        first,
                        seconds=total,
                        narration=narration,
                        sections=list(
                            dict.fromkeys(
                                key for item in originals for key in item["sections"]
                            )
                        ),
                    )
                ]
            script = self.require(db, pid, "script")
            new_ids = []
            for data in parts:
                sid = uuid4().hex
                db.execute(
                    "INSERT INTO scenes(id,project,position) VALUES(?,?,?)",
                    (sid, pid, indexes[0]),
                )
                self.scene_version(db, pid, sid, m.Scene.model_validate(data), script)
                new_ids.append(sid)
            db.executemany(
                "UPDATE scenes SET active=0 WHERE id=?", [(sid,) for sid in selection]
            )
            self.set_order(
                db, pid, order[: indexes[0]] + new_ids + order[indexes[-1] + 1 :]
            )
            return new_ids
