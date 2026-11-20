"""Revocable project access and reviewable proposals for the shared timeline."""

import hashlib
import re
import secrets
import time
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from . import models as m, review_notes
from .store import ACTIVE, RuleError, encode, unpack


TOKEN = re.compile(r"Bearer hyph_[A-Za-z0-9_-]{43}")
JobView = Literal["all", "active", "attention", "completed"]
JOB_FIELDS = """j.id,j.operation,j.slot,j.provider,j.status,j.attempts,j.progress,j.created,
                j.cancel,j.result,j.remote IS NOT NULL AS has_task,l.uncertain>0 AS verify_charge"""
JOB_FILTERS = {
    "all": "1",
    "active": "j.status IN ('DRAFT','GENERATING')",
    "attention": "(j.status='FAILED' OR l.uncertain>0)",
    "completed": "j.status='COMPLETED'",
}


def bearer_digest(header):
    if TOKEN.fullmatch(header or ""):
        return hashlib.sha256(
            header.removeprefix("Bearer ").encode("ascii")
        ).hexdigest()
    return None


class Access(m.Input):
    label: m.Short
    scope: Literal["read", "propose"] = "propose"
    days: int = Field(default=7, ge=1, le=365)


class ClipEdit(m.Input):
    scene: m.Id
    video: m.Id | None = None
    trim: float | None = Field(default=None, ge=0, le=120)
    seconds: m.Seconds | None = None
    captions: dict[m.Language, m.Text | None] = Field(default_factory=dict)


class EditPlan(m.Input):
    base_revision: m.Id
    rationale: m.Text
    review_note: m.Id | None = None
    changes: list[ClipEdit] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_clips(self):
        if len({edit.scene for edit in self.changes}) != len(self.changes):
            raise ValueError("Edit each scene at most once per proposal")
        return self


class Proposal(EditPlan):
    request_id: m.Id
    expected_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class Decision(m.Input):
    decision: Literal["apply", "reject"]
    comment: str = Field(default="", max_length=4000)


KEY_FIELDS = "id, label, scope, created, expires, revoked"
PROPOSAL_FIELDS = """p.id,p.request_id,p.request,p.plan,p.fingerprint,p.created,p.status,
    p.decided,p.decided_by,p.comment,p.applied_revision,k.id AS agent_id,k.label AS agent"""


class AgentProjects:
    def __init__(self, store):
        self.store = store

    def access(self, actor, pid):
        with self.store.transaction(write=False) as db:
            self.store.project(db, actor, pid)
            return [
                dict(row)
                for row in db.execute(
                    f"SELECT {KEY_FIELDS} FROM agent_keys WHERE project=? ORDER BY created DESC",
                    (pid,),
                )
            ]

    def issue(self, actor, pid, access):
        token = "hyph_" + secrets.token_urlsafe(32)
        key = uuid4().hex
        now = time.time()
        expires = now + access.days * 86400
        with self.store.transaction() as db:
            self.store.project(db, actor, pid)
            db.execute(
                """INSERT INTO agent_keys
                   (id, project, created_by, label, scope, digest, created, expires)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    key,
                    pid,
                    actor,
                    access.label,
                    access.scope,
                    bearer_digest("Bearer " + token),
                    now,
                    expires,
                ),
            )
        return {
            "id": key,
            "label": access.label,
            "scope": access.scope,
            "expires": expires,
            "token": token,
        }

    def revoke(self, actor, pid, key):
        with self.store.transaction() as db:
            self.store.project(db, actor, pid)
            count = db.execute(
                "UPDATE agent_keys SET revoked=COALESCE(revoked, ?) WHERE project=? AND id=?",
                (time.time(), pid, key),
            ).rowcount
            if not count:
                raise RuleError("Agent access not found", 404)

    def _grant(self, db, pid, digest, *, propose=False):
        key = db.execute(
            "SELECT * FROM agent_keys WHERE digest=? AND revoked IS NULL AND expires>?",
            (digest, time.time()),
        ).fetchone()
        if not key:
            raise RuleError("Agent authentication required", 401)
        if key["project"] != pid:
            raise RuleError("Project not found", 404)
        if propose and key["scope"] != "propose":
            raise RuleError("This credential is read-only", 403)
        return key, self.store.project(db, key["created_by"], pid)

    def context(self, pid, digest):
        with self.store.transaction(write=False) as db:
            key, project = self._grant(db, pid, digest)
            timeline = self.store.head(db, pid, "timeline")
            research = self.store.head(db, pid, "research")
            script = self.store.head(db, pid, "script")
            assembly = self.store.head(db, pid, "assembly")
            prefix = f"/api/agent/v1/projects/{pid}/media/"
            scenes = []
            for sid in self.store.order(db, pid):
                storyboard = self.store.head(db, pid, f"scene/{sid}")
                slot = f"video/{sid}"
                head = self.store.head(db, pid, slot)
                takes = []
                for row in db.execute(
                    "SELECT id FROM versions WHERE project=? AND slot=? ORDER BY seq DESC",
                    (pid, slot),
                ):
                    try:
                        take, _ = self.store._timeline_input(
                            db,
                            pid,
                            slot,
                            bool(timeline and timeline["data"]["watermark"]),
                            row["id"],
                        )
                    except RuleError:
                        continue
                    takes.append(
                        {
                            "id": take["id"],
                            "seq": take["seq"],
                            "status": take["status"],
                            "seconds": take["data"]["seconds"],
                            "url": prefix + take["id"],
                        }
                    )
                scenes.append(
                    {
                        "id": sid,
                        "version": storyboard["id"],
                        "data": storyboard["data"],
                        "evidence_url": f"/api/agent/v1/projects/{pid}/scenes/{sid}/evidence",
                        "current_take": head["id"]
                        if head and takes and takes[0]["id"] == head["id"]
                        else None,
                        "takes": takes,
                    }
                )
            return {
                "id": pid,
                "title": project["data"]["title"],
                "scope": key["scope"],
                "languages": project["data"]["languages"],
                "jobs_url": f"/api/agent/v1/projects/{pid}/jobs",
                "timeline": _version(timeline),
                "research": _version(research),
                "script": _version(script),
                "preview": assembly
                and {
                    "id": assembly["id"],
                    "revision": assembly["data"].get("timeline_revision"),
                    "fresh": self.store.fresh(db, assembly),
                    "url": prefix + assembly["id"],
                    "notes_url": f"/api/agent/v1/projects/{pid}/assemblies/{assembly['id']}/notes",
                },
                "scenes": scenes,
            }

    def jobs(self, pid, digest, state, limit):
        with self.store.transaction(write=False) as db:
            self._grant(db, pid, digest)
            rows = db.execute(
                f"""SELECT {JOB_FIELDS} FROM jobs j JOIN ledger l ON l.job=j.id
                    WHERE j.project=? AND {JOB_FILTERS[state]}
                    ORDER BY j.created DESC,j.ordinal DESC,j.id DESC LIMIT ?""",
                (pid, limit),
            )
            visited = {}
            return {"jobs": [self._job_status(db, pid, row, visited) for row in rows]}

    def job(self, pid, digest, ident):
        with self.store.transaction(write=False) as db:
            self._grant(db, pid, digest)
            row = db.execute(
                f"""SELECT {JOB_FIELDS} FROM jobs j JOIN ledger l ON l.job=j.id
                    WHERE j.project=? AND j.id=?""",
                (pid, ident),
            ).fetchone()
            if not row:
                raise RuleError("Job not found", 404)
            return self._job_status(db, pid, row, {})

    def _job_status(self, db, pid, row, visited):
        state = row["status"]
        asset = self.store.asset(db, pid, row["result"]) if row["result"] else None
        result = None
        next_action = None
        if state in ACTIVE:
            next_action = "wait"
        elif state == "FAILED":
            next_action = "review_failure"
            if row["provider"] == "runway" and row["has_task"]:
                job = self.store._job(db, pid, row["id"])
                recovery = self.store._runway_recovery(db, job)
                if recovery != "inputs_changed":
                    next_action = recovery
        if asset:
            fresh = self.store.fresh(db, asset, visited)
            head = self.store.head(db, pid, asset["slot"])
            current = bool(head and head["id"] == asset["id"])
            prefix = f"/api/agent/v1/projects/{pid}/media/"
            result = {
                "id": asset["id"],
                "kind": asset["kind"],
                "status": asset["status"],
                "fresh": fresh,
                "current": current,
                "url": prefix + asset["id"] if asset["media"] else None,
            }
            if current and fresh and asset["status"] == "AWAITING_REVIEW":
                next_action = "review_result"
        if row["verify_charge"] and next_action in (None, "review_failure"):
            next_action = "verify_charge"
        ident = row["id"]
        return {
            "id": ident,
            "operation": row["operation"],
            "slot": row["slot"],
            "provider": row["provider"],
            "status": state,
            "progress": row["progress"] if state in (*ACTIVE, "COMPLETED") else None,
            "attempts": row["attempts"],
            "created": row["created"],
            "cancellation_requested": bool(row["cancel"] and state in ACTIVE),
            "billing_review_required": bool(row["verify_charge"]),
            "next_action": next_action,
            "result": result,
            "owner_url": f"/?project={pid}&view=costs&job={ident}",
        }

    def evidence(self, pid, digest, sid):
        with self.store.transaction(write=False) as db:
            self._grant(db, pid, digest)
            return self.store.scene_evidence(db, pid, sid)

    def media(self, pid, digest, version):
        with self.store.transaction(write=False) as db:
            self._grant(db, pid, digest)
            return self.store.asset(db, pid, version)

    def notes(self, pid, digest, version):
        with self.store.transaction(write=False) as db:
            self._grant(db, pid, digest)
            return review_notes.list_notes(
                self.store, db, pid, version, include_scene=True
            )

    def validate(self, pid, digest, request):
        with self.store.transaction(write=False) as db:
            _, project = self._grant(db, pid, digest, propose=True)
            plan, fingerprint = self._plan(db, project, request)
            return plan | {"fingerprint": fingerprint}

    def _plan(self, db, project, request):
        pid = project["id"]
        base = self.store.head(db, pid, "timeline")
        if not base or base["id"] != request.base_revision:
            raise RuleError("The base revision changed. Read the latest timeline.")
        anchor = (
            review_notes.proposal_anchor(
                self.store, db, pid, request.review_note, base["id"]
            )
            if request.review_note
            else None
        )
        timeline = m.Timeline.model_validate(base["data"])
        clips = {clip.scene: clip.model_dump() for clip in timeline.clips}
        diff = []
        for edit in request.changes:
            before = clips.get(edit.scene)
            if before is None:
                raise RuleError("The proposed scene is absent from the timeline", 422)
            fields = edit.model_dump(exclude_unset=True, exclude={"scene", "captions"})
            captions = dict(before.get("captions", {}))
            for language, text in edit.captions.items():
                if language not in timeline.languages:
                    raise RuleError(
                        "Choose a caption language included in this cut", 422
                    )
                if text is None:
                    captions.pop(language, None)
                else:
                    captions[language] = text
            after = m.Clip.model_validate(
                before | fields | {"captions": captions}
            ).model_dump()
            changes = {
                field: {"before": before.get(field), "after": after.get(field)}
                for field in ("video", "trim", "seconds", "captions")
                if before.get(field) != after.get(field)
            }
            if changes:
                scene = self.store.head(db, pid, f"scene/{edit.scene}")
                if "video" in changes:
                    active = self.store.head(db, pid, f"video/{edit.scene}")
                    changes["video"]["resolved"] = {
                        key: clip["video"] or (active and active["id"])
                        for key, clip in (("before", before), ("after", after))
                    }
                if "captions" in changes:
                    languages = sorted(
                        set(before.get("captions", {})) | set(after.get("captions", {}))
                    )
                    narration = {}
                    for language in languages:
                        voice = self.store.head(
                            db, pid, f"voice/{edit.scene}/{language}"
                        )
                        narration[language] = voice and voice["data"]["narration"]
                    changes["captions"]["resolved"] = {
                        key: {
                            lang: clip.get("captions", {}).get(lang, narration[lang])
                            for lang in languages
                        }
                        for key, clip in (("before", before), ("after", after))
                    }
                diff.append(
                    {
                        "scene": edit.scene,
                        "title": scene["data"]["title"],
                        "changes": changes,
                    }
                )
            clips[edit.scene] = after
        if not diff:
            raise RuleError("Propose at least one change to the saved timeline", 422)
        if anchor and not any(item["scene"] == anchor["scene"]["id"] for item in diff):
            raise RuleError("Edit the scene referenced by the review note", 422)
        candidate = m.Timeline.model_validate(
            timeline.model_dump() | {"clips": list(clips.values())}
        )
        inputs = []
        self.store._timeline(db, pid, project["data"], candidate, inputs)
        plan = {
            "base_revision": base["id"],
            "base_seq": base["seq"],
            "diff": diff,
            "timeline": candidate.model_dump(),
            "rationale": request.rationale,
        }
        if anchor:
            plan["review_note"] = anchor
        fingerprint = hashlib.sha256(
            encode({"plan": plan, "inputs": inputs}).encode("utf-8")
        ).hexdigest()
        return plan, fingerprint

    def submit(self, pid, digest, request):
        document = encode(request.model_dump(exclude_unset=True))
        with self.store.transaction() as db:
            key, project = self._grant(db, pid, digest, propose=True)
            existing = db.execute(
                "SELECT id, request FROM edit_proposals WHERE agent=? AND request_id=?",
                (key["id"], request.request_id),
            ).fetchone()
            if existing:
                if existing["request"] != document:
                    raise RuleError(
                        "This request ID already identifies a different proposal"
                    )
                return self._proposal(db, pid, existing["id"], key["id"])
            plan, fingerprint = self._plan(db, project, request)
            if (
                request.expected_fingerprint
                and request.expected_fingerprint != fingerprint
            ):
                raise RuleError(
                    "Proposal inputs changed after validation. Validate the edit again."
                )
            ident = uuid4().hex
            db.execute(
                """INSERT INTO edit_proposals
                   (id, project, agent, request_id, request, plan, fingerprint, created)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    ident,
                    pid,
                    key["id"],
                    request.request_id,
                    document,
                    encode(plan),
                    fingerprint,
                    time.time(),
                ),
            )
            return self._proposal(db, pid, ident, key["id"])

    def receipt(self, pid, digest, ident):
        with self.store.transaction(write=False) as db:
            key, _ = self._grant(db, pid, digest)
            return self._proposal(db, pid, ident, key["id"])

    def proposals(self, actor, pid, ident=None):
        with self.store.transaction(write=False) as db:
            self.store.project(db, actor, pid)
            if ident:
                return self._proposal(db, pid, ident)
            return [
                self._serialize(pid, row, summary=True)
                for row in db.execute(
                    f"""SELECT {PROPOSAL_FIELDS} FROM edit_proposals p JOIN agent_keys k ON k.id=p.agent
                    WHERE p.project=? ORDER BY p.created DESC LIMIT 50""",
                    (pid,),
                )
            ]

    def _proposal(self, db, pid, ident, agent=None):
        row = db.execute(
            f"""SELECT {PROPOSAL_FIELDS} FROM edit_proposals p JOIN agent_keys k ON k.id=p.agent
                WHERE p.project=? AND p.id=? AND (? IS NULL OR p.agent=?)""",
            (pid, ident, agent, agent),
        ).fetchone()
        if not row:
            raise RuleError("Proposal not found", 404)
        return self._serialize(pid, row)

    @staticmethod
    def _serialize(pid, row, summary=False):
        result = unpack(row, "plan", "request")
        result["review_url"] = f"/?project={pid}&view=proposals&proposal={row['id']}"
        if summary:
            result.pop("request")
            result["plan"].pop("timeline")
        return result

    def _checked_plan(self, db, project, proposal):
        plan, fingerprint = self._plan(
            db, project, Proposal.model_validate(proposal["request"])
        )
        if fingerprint != proposal["fingerprint"]:
            raise RuleError(
                "Proposal inputs changed. Ask the agent to review the current cut."
            )
        return plan

    def preview(self, actor, pid, ident, language, cut):
        with self.store.transaction(write=False) as db:
            project = self.store.project(db, actor, pid)
            proposal = self._proposal(db, pid, ident)
            if proposal["status"] != "PENDING":
                raise RuleError("This proposal already has a decision")
            plan = self._checked_plan(db, project, proposal)
            timeline = plan["timeline"]
            if cut == "saved":
                timeline = self.store.asset(db, pid, plan["base_revision"])["data"]
            return self.store._cut_preview(
                db,
                pid,
                project["data"],
                m.Timeline.model_validate(timeline),
                language,
            )

    def decide(self, actor, pid, ident, decision):
        with self.store.transaction() as db:
            project = self.store.project(db, actor, pid)
            proposal = self._proposal(db, pid, ident)
            status = "APPLIED" if decision.decision == "apply" else "REJECTED"
            if proposal["status"] == status:
                return proposal
            if proposal["status"] != "PENDING":
                raise RuleError("This proposal already has a decision")
            applied_revision = None
            if status == "APPLIED":
                plan = self._checked_plan(db, project, proposal)
                applied_revision = self.store.add(
                    db, pid, "timeline", "timeline", plan["timeline"]
                )["id"]
            db.execute(
                """UPDATE edit_proposals SET status=?,decided=?,decided_by=?,comment=?,applied_revision=?
                   WHERE id=?""",
                (status, time.time(), actor, decision.comment, applied_revision, ident),
            )
            return self._proposal(db, pid, ident)


def _version(asset):
    if not asset:
        return None
    return {name: asset[name] for name in ("id", "seq", "status", "data")}
