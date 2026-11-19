"""Immutable generation quotes, scoped budget admission and queue transitions."""

from datetime import datetime, timezone
import os
import time
from uuid import uuid4

from . import codex_cli
from .store import ACTIVE, SCENE_OPERATIONS, RuleError, Store, encode, unpack


KEYS = {"openai": "MPT_PRODUCTION_OPENAI_KEY", "runway": "MPT_PRODUCTION_RUNWAY_KEY"}
MODELS = {
    ("openai", "research"): ("gpt-4.1-mini",),
    ("openai", "script"): ("gpt-4.1-mini",),
    ("openai", "translation"): ("gpt-4.1-mini",),
    ("openai", "image"): ("gpt-image-1.5",),
    ("openai", "voice"): ("tts-1", "gpt-4o-mini-tts"),
    ("runway", "video"): ("gen4_turbo",),
}
LOCAL_MODELS = {
    "research": "worksheet",
    "script": "outline",
    "image": "composition",
    "video": "motion",
    "translation": "placeholder",
    "voice": "system",
    "assembly": "ffmpeg",
    "export": "mp4",
}


class Workbench(Store):
    def provider(self, actor, request):
        if request.model not in MODELS.get((request.provider, request.operation), ()):
            raise RuleError("Unsupported provider and model combination", 422)
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO providers VALUES(?,?,?,?,?)",
                (
                    actor,
                    request.provider,
                    request.operation,
                    request.currency,
                    encode(request.model_dump(exclude={"confirmed"})),
                ),
            )

    def providers(self, actor):
        with self.transaction(write=False) as db:
            rows = {
                (r["provider"], r["operation"], r["currency"]): unpack(r, "data")[
                    "data"
                ]
                for r in db.execute("SELECT * FROM providers WHERE owner=?", (actor,))
            }
        providers = [
            {
                "provider": name,
                "operation": operation,
                "models": models,
                "configurations": {
                    currency: rows.get((name, operation, currency))
                    for currency in ("USD", "BRL")
                },
                "credential_present": bool(os.getenv(KEYS[name], "").strip()),
                "capabilities": {
                    "durations": list(range(2, 11)),
                    "resolution": ["1280:720", "720:1280"],
                    "approved_first_frame": True,
                }
                if name == "runway"
                else {},
            }
            for (name, operation), models in MODELS.items()
        ]
        state = codex_cli.connection_status()
        try:
            model = codex_cli.model()
        except codex_cli.CodexError:
            model, state = None, "invalid_model"
        providers.insert(
            0,
            {
                "provider": "codex",
                "operation": "text",
                "billing": "subscription",
                "status": state,
                "model": model,
                "credential_present": state == "chatgpt",
                "capabilities": {"operations": sorted(codex_cli.OPERATIONS)},
            },
        )
        return providers

    def budget(self, actor, pid, request):
        with self.transaction() as db:
            project = self.project(db, actor, pid)["data"]
            scope, target = request.scope, request.target
            if scope in {"project", "video"}:
                target = pid
            elif scope in {"day", "month"}:
                target = ""
            elif scope == "scene":
                if target not in self.order(db, pid):
                    raise RuleError("Unknown project scene", 422)
            elif target not in KEYS:
                raise RuleError("Unknown paid provider", 422)
            values = (actor, project["currency"], scope, target, request.limit)
            db.execute("INSERT OR REPLACE INTO budgets VALUES(?,?,?,?,?)", values)
            db.execute(
                "INSERT INTO budget_history(owner,currency,scope,target,amount,created) VALUES(?,?,?,?,?,?)",
                (*values, time.time()),
            )

    def _settings(self, db, project, provider, operation):
        if provider == "local":
            return {
                "provider": provider,
                "model": LOCAL_MODELS[operation],
                "estimate": 0,
                "timeout": 1800 if operation == "assembly" else 180,
                "interval": 0,
            }
        if provider == "codex":
            if not codex_cli.executable():
                raise RuleError("Install the official Codex CLI on the production host")
            try:
                model = codex_cli.model()
            except codex_cli.CodexError as exc:
                raise RuleError(str(exc)) from None
            return {
                "provider": "codex",
                "model": model,
                "estimate": 0,
                "timeout": 180,
                "interval": 3,
                "billing": "subscription",
            }
        if (provider, operation) not in MODELS:
            raise RuleError("This provider does not support the operation")
        row = db.execute(
            "SELECT data FROM providers WHERE owner=? AND provider=? AND operation=? AND currency=?",
            (project["owner"], provider, operation, project["data"]["currency"]),
        ).fetchone()
        if not row or not os.getenv(KEYS[provider], "").strip():
            raise RuleError(
                "Enable this provider and configure its server-side credential"
            )
        setup = unpack(row, "data")["data"]
        if not setup["enabled"]:
            raise RuleError("Provider is disabled")
        return dict(setup, estimate=setup["ceiling"])

    def _timeline(self, db, pid, project, timeline, deps):
        if not timeline or not set(timeline.languages) <= set(project["languages"]):
            raise RuleError("Choose a timeline and project languages")
        order = self.order(db, pid)
        if set(order) != {entry.scene for entry in timeline.clips}:
            raise RuleError("Include every active storyboard scene")
        deps.append({"order": order})
        clips = []
        native_width, native_height = (
            (3840, 2160) if timeline.aspect == "16:9" else (2160, 3840)
        )
        for clip in timeline.clips:
            sid = clip.scene
            scene = self.require(db, pid, f"scene/{sid}")
            video = (
                self.asset(db, pid, clip.video)
                if clip.video
                else self.require(db, pid, f"video/{sid}")
            )
            if (
                video["slot"] != f"video/{sid}"
                or video["status"] != "APPROVED"
                or not self.fresh(db, video)
            ):
                raise RuleError(
                    "Timeline clips must be approved matching scene versions"
                )
            if timeline.resolution == 2160 and (
                video["data"].get("width", 0) < native_width
                or video["data"].get("height", 0) < native_height
            ):
                raise RuleError(
                    "4K export requires approved footage at the selected aspect and resolution"
                )
            seconds = clip.seconds or scene["data"]["seconds"]
            if (
                clip.trim + seconds > video["data"]["seconds"] + 0.01
                or clip.narration_offset >= seconds
            ):
                raise RuleError(
                    "Clip trim, duration or narration offset exceeds the approved footage"
                )
            deps.extend(
                [{"id": scene["id"]}, {"id": video["id"], "pinned": bool(clip.video)}]
            )
            entry = dict(
                clip.model_dump(), seconds=seconds, video=video["media"], audio={}
            )
            for language in timeline.languages:
                voice = self.require(db, pid, f"voice/{sid}/{language}")
                duration = voice["data"]["seconds"]
                if duration + clip.narration_offset > seconds + 0.025:
                    raise RuleError(
                        f"Narration for {scene['data']['title']} ({language}) exceeds the clip. Shorten narration or use longer footage."
                    )
                deps.append({"id": voice["id"]})
                entry["audio"][language] = {
                    "media": voice["media"],
                    "narration": voice["data"]["narration"],
                    "seconds": duration,
                }
            if clip.effect:
                effect = self._attachment(db, pid, clip.effect, "audio")
                deps.append({"id": effect["id"]})
                entry["effect"] = effect["media"]
            clips.append(entry)
        result = dict(timeline.model_dump(), clips=clips)
        if timeline.music:
            music = self._attachment(db, pid, timeline.music, "audio")
            deps.append({"id": music["id"]})
            result["music"] = music["media"]
        return result

    def _attachment(self, db, pid, vid, kind):
        asset = self.asset(db, pid, vid)
        if asset["kind"] != kind or asset["status"] != "APPROVED":
            raise RuleError("Select an approved attachment of the appropriate type")
        return asset

    def _spec(self, db, project, request):
        pid, context = project["id"], project["data"]
        kind, sid, language = request.operation, request.scene, request.language
        if (kind in SCENE_OPERATIONS) != bool(sid):
            raise RuleError("Operation requires a matching project or scene scope", 422)
        if (kind in {"translation", "voice"}) != bool(language):
            raise RuleError("Choose a language for narration operations", 422)
        slot, deps, payload = (
            kind,
            [],
            {"project": context, "instructions": request.instructions},
        )
        default_provider = context.get("defaults", {}).get(
            kind, "openai" if kind == "translation" else "local"
        )
        provider = request.provider or default_provider
        if provider == "codex" and kind not in codex_cli.OPERATIONS:
            raise RuleError(
                "The Codex subscription provider supports research, scripts and translations"
            )
        if kind == "translation" and provider == "local":
            raise RuleError(
                "Use an AI translation provider or enter a translation manually"
            )
        if request.timeline is not None and kind != "assembly":
            raise RuleError("A timeline is only used for assembly", 422)
        if request.sources and kind != "research":
            raise RuleError("Sources are only accepted for research", 422)
        if request.version and kind not in {"script", "export"}:
            raise RuleError(
                "A version override is only used for script revision or export", 422
            )
        if sid:
            if sid not in self.order(db, pid):
                raise RuleError("Scene not found", 404)
            scene = self.require(db, pid, f"scene/{sid}")
            deps.append(
                {"speech": [sid, scene["data"]["narration"]]}
                if kind in {"translation", "voice"}
                else {"id": scene["id"]}
            )
            slot += f"/{sid}"
            payload["scene"] = scene["data"]
            payload["style"] = dict(
                context["style"], **scene["data"]["style_overrides"]
            )
        if language:
            if language not in context["languages"]:
                raise RuleError("Language is unavailable for this project")
            if kind == "translation" and language == context["languages"][0]:
                raise RuleError("Choose a target language different from the original")
            slot += f"/{language}"
            payload.update(language=language, voice=request.voice)
        if kind == "research":
            payload["sources"] = [source.model_dump() for source in request.sources]
        if kind == "script":
            research = self.require(db, pid, "research")
            deps.append({"id": research["id"]})
            payload["research"] = research["data"]
            if request.version:
                previous = self.asset(db, pid, request.version)
                if previous["kind"] != "script":
                    raise RuleError("Select a script revision")
                payload["previous"] = previous["data"]
        if kind == "image":
            payload["reference_media"] = [
                self._attachment(db, pid, ref, "reference")["media"]
                for ref in scene["data"]["references"]
            ]
        if kind == "video":
            image = self.require(db, pid, f"image/{sid}")
            deps.append({"id": image["id"]})
            payload["first_frame"] = image["media"]
            provider = request.provider or scene["data"]["provider"]
            if provider != scene["data"]["provider"]:
                raise RuleError("Approve a scene revision with the selected provider")
            if provider == "runway" and (
                scene["data"]["seconds"] not in range(2, 11)
                or len(scene["data"]["video_prompt"]) > 1000
                or scene["data"]["negative_prompt"]
            ):
                raise RuleError(
                    "Gen-4 Turbo requires 2–10 whole seconds and a prompt up to 1,000 characters"
                )
        if kind == "voice":
            if language == context["languages"][0]:
                payload["narration"] = scene["data"]["narration"]
            else:
                translation = self.require(db, pid, f"translation/{sid}/{language}")
                deps.append({"id": translation["id"]})
                payload["narration"] = translation["data"]["narration"]
        if kind == "assembly":
            payload["timeline"] = self._timeline(
                db, pid, context, request.timeline, deps
            )
        if kind == "export":
            assembly = self.require(db, pid, "assembly")
            if assembly["id"] != request.version:
                raise RuleError("Select the approved assembly version")
            if assembly["data"]["watermark"]:
                raise RuleError(
                    "Download the watermarked assembly as a draft. Approve an unwatermarked assembly for final export."
                )
            deps.append({"id": assembly["id"]})
            payload["assembly"] = assembly["media"]
            payload["metadata"] = assembly["data"]
        setup = self._settings(db, project, provider, kind)
        if provider == "runway":
            setup["estimate"] *= int(scene["data"]["seconds"])
        if setup["estimate"] > 1_000_000_000:
            raise RuleError("Estimated charge exceeds the supported ceiling")
        current = self.head(db, pid, slot)
        if (
            kind == "video"
            and current
            and current["status"] == "APPROVED"
            and self.fresh(db, current)
        ):
            raise RuleError(
                "This scene already has an approved video. Revise the scene to make a replacement."
            )
        if (
            kind == "image"
            and current
            and current["status"] == "APPROVED"
            and self.fresh(db, current)
        ):
            raise RuleError(
                "This scene asset is already approved. Revise its inputs to make a replacement."
            )
        return dict(
            setup,
            payload=payload,
            deps=deps,
            slot=slot,
            operation=kind,
            scene=sid,
            request=request.model_dump(),
            head=current["id"] if current else None,
            head_status=current["status"] if current else None,
        )

    def quote(self, actor, pid, requests):
        qid = uuid4().hex
        with self.transaction() as db:
            project = self.project(db, actor, pid)
            specs = [self._spec(db, project, request) for request in requests]
            if len({spec["slot"] for spec in specs}) != len(specs):
                raise RuleError("Authorize at most one attempt per asset slot")
            total = sum(spec["estimate"] for spec in specs)
            if total > 1_000_000_000:
                raise RuleError("Quote exceeds the supported ceiling")
            db.execute(
                "INSERT INTO quotes VALUES(?,?,?,?,?)",
                (qid, pid, encode(specs), total, time.time()),
            )
            return {
                "id": qid,
                "total": total,
                "currency": project["data"]["currency"],
                "attempts": len(specs),
                "scene_count": len({spec["scene"] for spec in specs if spec["scene"]}),
                "items": [
                    {
                        key: spec[key]
                        for key in ("slot", "provider", "model", "estimate")
                    }
                    | {
                        "billing": spec.get(
                            "billing", "local" if spec["provider"] == "local" else "api"
                        )
                    }
                    for spec in specs
                ],
                "budget": self._costs(db, actor, project),
            }

    def _usage(self, db, actor, currency, scope, target, day, month):
        conditions = {
            "project": ("j.project=?", target),
            "video": ("j.project=?", target),
            "scene": ("json_extract(j.spec,'$.scene')=?", target),
            "provider": ("j.provider=?", target),
            "day": ("l.period_day=?", day),
            "month": ("l.period_month=?", month),
        }
        clause, value = conditions[scope]
        # Clause comes from the fixed map above. Every caller-supplied value is bound.
        row = db.execute(
            f"""SELECT COALESCE(SUM(reserved),0) AS reserved,
            COALESCE(SUM(actual),0) AS actual, COALESCE(SUM(uncertain),0) AS uncertain,
            COALESCE(SUM(quoted),0) AS estimated FROM ledger l JOIN jobs j ON j.id=l.job
            JOIN projects p ON p.id=j.project WHERE p.owner=? AND json_extract(p.data,'$.currency')=?
            AND {clause}""",
            (actor, currency, value),
        ).fetchone()
        return dict(row)

    def _budget_check(self, db, actor, project, spec, day, month, additional):
        pid, currency = project["id"], project["data"]["currency"]
        scopes = [
            ("project", pid),
            ("video", pid),
            ("day", ""),
            ("month", ""),
            ("provider", spec["provider"]),
        ]
        if spec["scene"]:
            scopes.append(("scene", spec["scene"]))
        for scope, target in scopes:
            limit = db.execute(
                """SELECT amount FROM budgets
                WHERE owner=? AND currency=? AND scope=? AND target=?""",
                (actor, currency, scope, target),
            ).fetchone()
            if limit is None:
                if scope == "project":
                    raise RuleError("Set a project budget before authorizing paid jobs")
                continue
            used = self._usage(db, actor, currency, scope, target, day, month)
            if (
                used["actual"] + used["uncertain"] + used["reserved"] + additional
                > limit["amount"]
            ):
                raise RuleError(f"Job exceeds the remaining {scope} budget")

    def _costs(self, db, actor, project):
        currency = project["data"]["currency"]
        limits = db.execute(
            "SELECT * FROM budgets WHERE owner=? AND currency=?", (actor, currency)
        )
        scopes = {(r["scope"], r["target"]): r["amount"] for r in limits}
        timestamp = datetime.now(timezone.utc).date().isoformat()
        pid = project["id"]
        totals = self._usage(
            db, actor, currency, "project", pid, timestamp, timestamp[:7]
        )
        remaining = min(
            (
                scopes.get((scope, pid), 0)
                for scope in ("project", "video")
                if (scope, pid) in scopes
            ),
            default=0,
        )
        remaining -= totals["actual"] + totals["uncertain"] + totals["reserved"]
        return dict(
            totals,
            currency=currency,
            remaining=max(0, remaining),
            limits=[
                {"scope": scope, "target": target, "limit": limit}
                for (scope, target), limit in scopes.items()
                if scope not in {"project", "video", "scene"}
                or target == pid
                or target in self.order(db, pid)
            ],
        )

    def authorize(self, actor, pid, qid, maximum):
        from .models import RequestJob

        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM quotes WHERE project=? AND id=?", (pid, qid)
            ).fetchone()
            project = self.project(db, actor, pid)
            if not row:
                raise RuleError("Quote not found", 404)
            quote = unpack(row, "requests")
            ar = db.execute(
                "SELECT maximum FROM authorizations WHERE quote=?", (qid,)
            ).fetchone()
            if ar:
                if ar["maximum"] != maximum:
                    raise RuleError(
                        "Quote was already authorized with a different maximum"
                    )
                return [
                    r["id"]
                    for r in db.execute(
                        "SELECT id FROM jobs WHERE quote=? ORDER BY ordinal", (qid,)
                    )
                ]
            now = time.time()
            if quote["created"] < now - 900 or maximum < quote["total"]:
                raise RuleError(
                    "Quote expired or the confirmed maximum is below the estimate"
                )
            timestamp = datetime.now(timezone.utc).date().isoformat()
            for spec in quote["requests"]:
                if (
                    self._spec(db, project, RequestJob.model_validate(spec["request"]))
                    != spec
                ):
                    raise RuleError(
                        "Quote inputs or pricing changed. Request a new estimate."
                    )
                if db.execute(
                    "SELECT 1 FROM jobs WHERE project=? AND slot=? AND status IN (?,?)",
                    (pid, spec["slot"], *ACTIVE),
                ).fetchone():
                    raise RuleError("A job is already active for this asset")
                if spec["estimate"]:
                    unclear = db.execute(
                        """SELECT 1 FROM jobs j JOIN ledger l ON l.job=j.id
                        WHERE j.project=? AND j.slot=? AND j.status IN ('FAILED','CANCELLED')
                        AND l.uncertain>0""",
                        (pid, spec["slot"]),
                    ).fetchone()
                    if unclear:
                        raise RuleError(
                            "Settle the previous job's uncertain charge before trying again"
                        )
            db.execute(
                "INSERT INTO authorizations VALUES(?,?,?,?)", (qid, actor, maximum, now)
            )
            ids = []
            for ordinal, spec in enumerate(quote["requests"]):
                amount = spec["estimate"]
                if amount:
                    self._budget_check(
                        db, actor, project, spec, timestamp, timestamp[:7], amount
                    )
                jid = uuid4().hex
                db.execute(
                    """INSERT INTO jobs(id,project,quote,ordinal,slot,operation,provider,spec,status,available,created)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        jid,
                        pid,
                        qid,
                        ordinal,
                        spec["slot"],
                        spec["operation"],
                        spec["provider"],
                        encode(spec),
                        "DRAFT",
                        now,
                        now,
                    ),
                )
                db.execute(
                    "INSERT INTO ledger(job,reserved,quoted,period_day,period_month) VALUES(?,?,?,?,?)",
                    (jid, amount, amount, timestamp, timestamp[:7]),
                )
                ids.append(jid)
            return ids

    def _current(self, db, job):
        head = self.head(db, job["project"], job["slot"])
        expected = job["spec"]
        return (
            (head["id"] if head else None) == expected["head"]
            and (head["status"] if head else None) == expected["head_status"]
            and self._dependencies_current(db, job["project"], expected["deps"])
        )

    def _provider_enabled(self, db, job):
        if job["provider"] == "local":
            return True
        if job["provider"] == "codex":
            return bool(codex_cli.executable())
        row = db.execute(
            """SELECT c.data FROM providers c JOIN projects p ON p.owner=c.owner
            AND c.currency=json_extract(p.data,'$.currency') WHERE p.id=?
            AND c.provider=? AND c.operation=?""",
            (job["project"], job["provider"], job["operation"]),
        ).fetchone()
        return bool(
            row
            and unpack(row, "data")["data"]["enabled"]
            and os.getenv(KEYS[job["provider"]], "").strip()
        )

    def recover(self):
        with self.transaction() as db:
            for row in db.execute(
                "SELECT * FROM jobs WHERE status='GENERATING'"
            ).fetchall():
                job = unpack(row, "spec")
                if job["provider"] == "local" or job["remote"]:
                    db.execute(
                        "UPDATE jobs SET status='DRAFT',available=? WHERE id=?",
                        (time.time(), job["id"]),
                    )
                else:
                    self._finish(
                        db,
                        job,
                        "FAILED",
                        "Interrupted during Codex generation. Check account usage before retrying."
                        if job["provider"] == "codex"
                        else "Interrupted during provider submission. Verify billing.",
                    )

    def claim(self):
        with self.transaction() as db:
            now = time.time()
            for row in db.execute(
                "SELECT * FROM jobs WHERE status='DRAFT' AND available<=? ORDER BY available,created",
                (now,),
            ).fetchall():
                job = unpack(row, "spec")
                if not job["remote"] and (
                    job["cancel"]
                    or not self._current(db, job)
                    or not self._provider_enabled(db, job)
                ):
                    self._finish(
                        db,
                        job,
                        "CANCELLED",
                        "Inputs changed or job cancelled",
                        safe=True,
                    )
                    continue
                submitting = job["provider"] != "local" and not job["remote"]
                if submitting and job["spec"]["estimate"]:
                    reservation = db.execute(
                        "SELECT period_day,period_month FROM ledger WHERE job=?",
                        (job["id"],),
                    ).fetchone()
                    owner = db.execute(
                        "SELECT owner FROM projects WHERE id=?", (job["project"],)
                    ).fetchone()["owner"]
                    try:
                        self._budget_check(
                            db,
                            owner,
                            self.project(db, owner, job["project"]),
                            job["spec"],
                            reservation["period_day"],
                            reservation["period_month"],
                            0,
                        )
                    except RuleError as exc:
                        self._finish(db, job, "CANCELLED", str(exc), safe=True)
                        continue
                if submitting:
                    other = db.execute(
                        """SELECT MAX(submitted) AS last FROM jobs
                        WHERE provider=? AND id!=? AND attempts>0""",
                        (job["provider"], job["id"]),
                    ).fetchone()["last"]
                    if other is not None and other + job["spec"]["interval"] > now:
                        continue
                db.execute(
                    """UPDATE jobs SET status='GENERATING',attempts=attempts+?,progress=MAX(progress,10),
                               submitted=COALESCE(?,submitted) WHERE id=?""",
                    (int(not job["remote"]), now if submitting else None, job["id"]),
                )
                return job
        return None

    def remote(self, jid, remote):
        with self.transaction() as db:
            db.execute(
                """UPDATE jobs SET remote=?,progress=35,poll_deadline=?+json_extract(spec,'$.timeout')
                WHERE id=? AND status='GENERATING'""",
                (remote, time.time(), jid),
            )

    def defer(self, job, delay, progress):
        with self.transaction() as db:
            current = self._job(db, job["project"], job["id"])
            if current["status"] != "GENERATING":
                return
            polls = current["polls"] + 1
            if polls >= 240 or (
                current["poll_deadline"] and time.time() > current["poll_deadline"]
            ):
                self._finish(
                    db,
                    current,
                    "FAILED",
                    "Provider polling timed out. Reconcile the recorded task.",
                )
                return
            db.execute(
                "UPDATE jobs SET status='DRAFT',polls=?,available=?,progress=MAX(progress,?) WHERE id=?",
                (polls, time.time() + delay, progress, job["id"]),
            )

    def update_progress(self, jid, progress):
        with self.transaction() as db:
            db.execute(
                "UPDATE jobs SET progress=MAX(progress,?) WHERE id=? AND status='GENERATING'",
                (max(10, min(99, int(progress))), jid),
            )

    def cancelled(self, jid, stop_event):
        if stop_event.is_set():
            return True
        with self.transaction(write=False) as db:
            return bool(
                db.execute("SELECT cancel FROM jobs WHERE id=?", (jid,)).fetchone()[
                    "cancel"
                ]
            )

    def cancel(self, actor, pid, jid):
        with self.transaction() as db:
            self.project(db, actor, pid)
            job = self._job(db, pid, jid)
            if job["status"] == "DRAFT" and not job["remote"]:
                self._finish(
                    db, job, "CANCELLED", "Cancelled before execution", safe=True
                )
            elif job["status"] in ACTIVE:
                db.execute(
                    "UPDATE jobs SET cancel=1,available=? WHERE id=?",
                    (time.time(), jid),
                )

    def _job(self, db, pid, jid):
        row = db.execute(
            "SELECT * FROM jobs WHERE project=? AND id=?", (pid, jid)
        ).fetchone()
        if not row:
            raise RuleError("Job not found", 404)
        return unpack(row, "spec")

    def _finish(self, db, job, state, message=None, result=None, safe=False):
        db.execute(
            "UPDATE jobs SET status=?,progress=?,last_error=?,result=? WHERE id=?",
            (state, 100, message, result, job["id"]),
        )
        db.execute(
            "UPDATE ledger SET uncertain=CASE WHEN ? THEN 0 ELSE reserved END,reserved=0 WHERE job=?",
            (safe, job["id"]),
        )

    def fail(self, job, message, safe=False, cancelled=False, retry=False):
        with self.transaction() as db:
            current = self._job(db, job["project"], job["id"])
            if current["status"] != "GENERATING":
                return
            if (
                retry
                and current["provider"] == "local"
                and current["attempts"] < 3
                and not current["cancel"]
            ):
                db.execute(
                    "UPDATE jobs SET status='DRAFT',available=?,last_error=? WHERE id=?",
                    (time.time() + 2 ** current["attempts"], message, job["id"]),
                )
            else:
                self._finish(
                    db,
                    current,
                    "CANCELLED" if cancelled or current["cancel"] else "FAILED",
                    message,
                    safe=safe,
                )

    def complete(self, job, data, media, captions=None):
        with self.transaction() as db:
            pid = job["project"]
            current = self._job(db, pid, job["id"])
            if current["status"] != "GENERATING":
                return False
            kind = job["operation"]
            safe = job["provider"] == "local"
            if current["cancel"] or not self._current(db, job):
                self._finish(
                    db, job, "CANCELLED", "Inputs changed or job cancelled", safe=safe
                )
                return False
            deps = job["spec"]["deps"]
            if kind == "script":
                item = self.script(
                    db, pid, data, self.asset(db, pid, deps[0]["id"]), job["id"]
                )
            else:
                if captions:
                    caption_versions = {
                        lang: self.add(
                            db,
                            pid,
                            f"captions/{job['id']}/{lang}",
                            "captions",
                            {"language": lang},
                            deps,
                            name,
                            job["id"],
                        )["id"]
                        for lang, name in captions.items()
                    }
                    data = dict(data, captions=caption_versions)
                item = self.add(
                    db, pid, job["slot"], kind, data, deps, media, job["id"]
                )
            self._finish(db, job, "COMPLETED", result=item["id"], safe=safe)
            return True

    def settle(self, actor, pid, jid, request):
        with self.transaction() as db:
            self.project(db, actor, pid)
            job = self._job(db, pid, jid)
            ledger = db.execute("SELECT * FROM ledger WHERE job=?", (jid,)).fetchone()
            if job["status"] in ACTIVE or not ledger["uncertain"]:
                raise RuleError("Job does not have an uncertain charge to settle")
            db.execute(
                "INSERT INTO settlements(job,actor,actual,receipt,created) VALUES(?,?,?,?,?)",
                (jid, actor, request.actual, request.receipt, time.time()),
            )
            db.execute(
                "UPDATE ledger SET actual=?,uncertain=0 WHERE job=?",
                (request.actual, jid),
            )

    def reconcile(self, actor, pid, jid):
        with self.transaction() as db:
            self.project(db, actor, pid)
            job = self._job(db, pid, jid)
            if (
                job["provider"] != "runway"
                or not job["remote"]
                or job["status"] != "FAILED"
            ):
                raise RuleError("A failed job with a recorded Runway task is required")
            now = time.time()
            db.execute(
                "UPDATE jobs SET status='DRAFT',available=?,cancel=0,polls=0,poll_deadline=? WHERE id=?",
                (now, now + job["spec"]["timeout"], jid),
            )
            db.execute(
                "UPDATE ledger SET reserved=uncertain,uncertain=0 WHERE job=?", (jid,)
            )

    def snapshot(self, actor, pid=None):
        with self.transaction(write=False) as db:
            if pid is None:
                return [
                    self._overview(db, unpack(row, "data"))
                    for row in db.execute(
                        "SELECT * FROM projects WHERE owner=? ORDER BY created DESC",
                        (actor,),
                    ).fetchall()
                ]
            project = self.project(db, actor, pid)
            item = self._overview(db, project)
            rows = db.execute(
                "SELECT id FROM versions WHERE project=? ORDER BY created DESC", (pid,)
            ).fetchall()
            versions = []
            seen_slots = set()
            visited = {}
            for row in rows:
                asset = self.asset(db, pid, row["id"])
                asset["current"] = asset["slot"] not in seen_slots
                asset["fresh"] = self.fresh(db, asset, visited)
                seen_slots.add(asset["slot"])
                versions.append(asset)
            jobs = [
                dict(row)
                for row in db.execute(
                    """SELECT j.id,j.slot,j.operation,j.provider,j.status,j.attempts,
                j.progress,j.last_error,j.result,j.created,j.cancel,j.remote,j.quote,l.quoted,l.actual,l.uncertain,
                json_extract(j.spec,'$.model') AS model FROM jobs j JOIN ledger l ON l.job=j.id
                WHERE j.project=? ORDER BY j.created DESC,j.ordinal""",
                    (pid,),
                )
            ]
            reviews = [
                dict(row)
                for row in db.execute(
                    """SELECT r.* FROM reviews r JOIN versions v ON v.id=r.version
                WHERE v.project=? ORDER BY r.id DESC""",
                    (pid,),
                )
            ]
            return dict(
                item,
                scenes=self.order(db, pid),
                versions=versions,
                jobs=jobs,
                reviews=reviews,
            )

    def authorization(self, actor, pid, qid):
        with self.transaction(write=False) as db:
            self.project(db, actor, pid)
            row = db.execute(
                """SELECT q.*,a.maximum,a.actor,a.created AS authorized_at FROM quotes q
                LEFT JOIN authorizations a ON a.quote=q.id WHERE q.project=? AND q.id=?""",
                (pid, qid),
            ).fetchone()
            if not row:
                raise RuleError("Quote not found", 404)
            return unpack(row, "requests")

    def _overview(self, db, project):
        pid = project["id"]
        order = self.order(db, pid)
        visited = {}

        def accepted(slot):
            asset = self.head(db, pid, slot)
            return bool(
                asset
                and asset["status"] == "APPROVED"
                and self.fresh(db, asset, visited)
            )

        approved = sum(accepted(f"video/{sid}") for sid in order)
        rejected = 0
        for sid in order:
            for kind in ("video", "image", "scene"):
                asset = self.head(db, pid, f"{kind}/{sid}")
                if asset and self.fresh(db, asset, visited):
                    rejected += asset["status"] in {"REJECTED", "REVISION_REQUESTED"}
                    break
        exported = self.head(db, pid, "export")
        assembly = self.head(db, pid, "assembly")
        script = self.head(db, pid, "script")
        final_export = bool(exported and self.fresh(db, exported, visited))
        assembled = bool(assembly and self.fresh(db, assembly, visited))
        scene_approvals = sum(accepted(f"scene/{sid}") for sid in order)
        image_approvals = sum(accepted(f"image/{sid}") for sid in order)
        current_storyboard = bool(order) and scene_approvals == len(order)
        images_ready = current_storyboard and image_approvals == len(order)
        research_ready, script_ready = accepted("research"), accepted("script")
        running = db.execute(
            """SELECT 1 FROM jobs WHERE project=? AND operation IN ('image','video')
            AND status IN ('DRAFT','GENERATING')""",
            (pid,),
        ).fetchone()
        if final_export:
            stage = "EXPORT"
        elif assembled:
            stage = "FINAL_REVIEW"
        elif order and approved == len(order):
            stage = "VIDEO_ASSEMBLY"
        elif running:
            stage = "ASSET_GENERATION"
        elif current_storyboard and any(
            self.head(db, pid, f"video/{sid}") for sid in order
        ):
            stage = "SCENE_REVIEW"
        elif images_ready:
            stage = "COST_ESTIMATION"
        elif order and not current_storyboard and script_ready:
            stage = "STORYBOARD_APPROVAL"
        elif script_ready:
            stage = "STORYBOARD"
        elif script and self.fresh(db, script):
            stage = "SCRIPT_APPROVAL"
        elif research_ready:
            stage = "SCRIPT"
        elif self.head(db, pid, "research"):
            stage = "RESEARCH"
        else:
            stage = "IDEA"
        failed = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE project=? AND status='FAILED'", (pid,)
        ).fetchone()[0]
        delivery_languages = (
            assembly["data"]["languages"] if assembled else project["data"]["languages"]
        )
        voice_total = len(order) * len(delivery_languages)
        voices = sum(
            accepted(f"voice/{sid}/{lang}")
            for sid in order
            for lang in delivery_languages
        )
        progress = (
            10 * research_ready + 15 * script_ready + 10 * assembled + 5 * final_export
        )
        if order:
            progress += (
                10 * scene_approvals + 15 * image_approvals + 20 * approved
            ) / len(order)
        if voice_total:
            progress += 15 * voices / voice_total
        export_status = "COMPLETED" if final_export else "DRAFT"
        export_job = db.execute(
            "SELECT status FROM jobs WHERE project=? AND operation='export' ORDER BY created DESC LIMIT 1",
            (pid,),
        ).fetchone()
        if export_job and export_job["status"] in {"FAILED", "GENERATING", "CANCELLED"}:
            export_status = export_job["status"]
        return dict(
            project,
            stage=stage,
            approved=approved,
            rejected=rejected,
            pending=len(order) - approved - rejected,
            progress=round(progress),
            failed=failed,
            export_status=export_status,
            costs=self._costs(db, project["owner"], project),
        )
