"""Time-coded review notes on immutable rendered assemblies."""

import time
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, StringConstraints

from . import models as m
from .store import RuleError


class Note(m.Input):
    request_id: m.Id
    position_ms: int = Field(ge=0, strict=True)
    body: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]


class NoteState(m.Input):
    state: Literal["OPEN", "RESOLVED"]
    expected_version: int = Field(ge=0, strict=True)


NOTE_ROWS = """SELECT n.id,n.assembly,n.actor,n.position_ms,n.body,n.created,
    COALESCE(e.state,'OPEN') AS state, COALESCE(e.seq,0) AS state_version,
    e.actor AS state_actor,e.created AS state_changed
    FROM review_notes n LEFT JOIN review_note_events e ON e.note=n.id AND e.seq=(
        SELECT MAX(seq) FROM review_note_events WHERE note=n.id
    ) WHERE n.project=? AND n.assembly=?"""


def assembly_asset(store, db, pid, version):
    assembly = store.asset(db, pid, version)
    if assembly["kind"] != "assembly" or not assembly["media"]:
        raise RuleError("Choose a rendered assembly", 404)
    return assembly


def list_notes(store, db, pid, version, *, include_scene=False):
    assembly = assembly_asset(store, db, pid, version)
    notes = [
        serialize(pid, row)
        for row in db.execute(
            NOTE_ROWS + " ORDER BY n.position_ms,n.created,n.id",
            (pid, version),
        )
    ]
    if include_scene and notes:
        windows = scene_windows(store, db, pid, assembly)
        for note in notes:
            note["scene"] = scene_at(windows, note["position_ms"])
    return {
        "assembly": version,
        "revision": assembly["data"].get("timeline_revision"),
        "notes": notes,
    }


def scene_windows(store, db, pid, assembly):
    data = assembly["data"]
    entries = data.get("retention") or []
    windows = []
    for index, entry in enumerate(entries):
        take = store.asset(db, pid, entry.get("video"), missing=True)
        if not take or not take["slot"].startswith("video/"):
            continue
        start = round(entry.get("start", 0) * 1000)
        end = round(entry.get("end", 0) * 1000)
        if end > start:
            windows.append(
                {
                    "id": take["slot"].removeprefix("video/"),
                    "title": entry.get("scene", ""),
                    "start_ms": start,
                    "end_ms": end,
                    "last": index == len(entries) - 1,
                }
            )
    return windows


def scene_at(windows, position):
    for window in windows:
        end = window["end_ms"]
        if window["start_ms"] <= position and (
            position < end or (window["last"] and position == end)
        ):
            return {
                **{key: value for key, value in window.items() if key != "last"},
                "offset_ms": position - window["start_ms"],
            }
    return None


def proposal_anchor(store, db, pid, ident, revision):
    row = db.execute(
        "SELECT id,assembly,position_ms FROM review_notes WHERE project=? AND id=?",
        (pid, ident),
    ).fetchone()
    if not row:
        raise RuleError("Review note not found", 404)
    assembly = assembly_asset(store, db, pid, row["assembly"])
    if assembly["data"].get("timeline_revision") != revision:
        raise RuleError("The review note belongs to a different timeline revision")
    if not store.fresh(db, assembly):
        raise RuleError(
            "The reviewed assembly's inputs changed. Review a current assembly."
        )
    scene = scene_at(scene_windows(store, db, pid, assembly), row["position_ms"])
    if not scene:
        raise RuleError("The review note could not be located in a rendered scene", 422)
    return serialize(pid, row) | {"scene": scene}


def serialize(pid, row):
    result = dict(row)
    result["url"] = (
        f"/?project={pid}&view=delivery&assembly={row['assembly']}"
        f"&t={row['position_ms']}&note={row['id']}"
    )
    return result


class ReviewNotes:
    def __init__(self, store):
        self.store = store

    def list(self, actor, pid, version):
        with self.store.transaction(write=False) as db:
            self.store.project(db, actor, pid)
            return list_notes(self.store, db, pid, version)

    def create(self, actor, pid, version, request):
        with self.store.transaction() as db:
            self.store.project(db, actor, pid)
            assembly = assembly_asset(self.store, db, pid, version)
            if request.position_ms > round(assembly["data"]["seconds"] * 1000):
                raise RuleError("Choose a time within the assembly", 422)
            existing = db.execute(
                """SELECT id,assembly,position_ms,body FROM review_notes
                   WHERE project=? AND actor=? AND request_id=?""",
                (pid, actor, request.request_id),
            ).fetchone()
            if existing:
                if (
                    existing["assembly"],
                    existing["position_ms"],
                    existing["body"],
                ) != (
                    version,
                    request.position_ms,
                    request.body,
                ):
                    raise RuleError(
                        "This request ID already identifies a different note"
                    )
                ident = existing["id"]
            else:
                ident = uuid4().hex
                db.execute(
                    """INSERT INTO review_notes
                       (id,project,assembly,actor,request_id,position_ms,body,created)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        ident,
                        pid,
                        version,
                        actor,
                        request.request_id,
                        request.position_ms,
                        request.body,
                        time.time(),
                    ),
                )
            return self._note(db, pid, version, ident)

    def history(self, actor, pid, version, ident):
        with self.store.transaction(write=False) as db:
            self.store.project(db, actor, pid)
            note = self._note(db, pid, version, ident)
            note["events"] = [
                dict(row)
                for row in db.execute(
                    "SELECT seq,state,actor,created FROM review_note_events WHERE note=? ORDER BY seq",
                    (ident,),
                )
            ]
            return note

    def change_state(self, actor, pid, version, ident, request):
        with self.store.transaction() as db:
            self.store.project(db, actor, pid)
            current = self._note(db, pid, version, ident)
            if current["state"] == request.state:
                return current
            if current["state_version"] != request.expected_version:
                raise RuleError("The note changed. Reload its current state.")
            db.execute(
                "INSERT INTO review_note_events(note,seq,state,actor,created) VALUES(?,?,?,?,?)",
                (
                    ident,
                    current["state_version"] + 1,
                    request.state,
                    actor,
                    time.time(),
                ),
            )
            return self._note(db, pid, version, ident)

    @staticmethod
    def _note(db, pid, version, ident):
        row = db.execute(NOTE_ROWS + " AND n.id=?", (pid, version, ident)).fetchone()
        if not row:
            raise RuleError("Review note not found", 404)
        return serialize(pid, row)
