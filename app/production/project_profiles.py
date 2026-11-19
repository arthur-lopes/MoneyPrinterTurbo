"""Reusable HYPHOTENIC drafts for the production workspace."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from . import models as m


DIRECTORY = Path(__file__).with_name("presets")


class HyphotenicProject(m.Input):
    channel: Literal["hyphotenic"]
    series: Literal["what-if", "alternate-history", "first-24-hours", "shorts"]
    public_title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=187)
    ]
    topic: m.Text
    changed_condition: m.Text | None = None
    unchanged_conditions: m.Text | None = None
    scope: m.Text | None = None
    sources: list[m.Source] = Field(default_factory=list, max_length=30)
    audience: m.Short | None = None
    tone: m.Short | None = None
    duration: int | None = Field(default=None, ge=2, le=7200)
    structure: list[m.Short] | None = Field(default=None, min_length=1, max_length=50)
    languages: list[m.Language] | None = Field(default=None, min_length=1, max_length=2)
    style: dict[str, str] = Field(default_factory=dict)


def hyphotenic():
    return json.loads((DIRECTORY / "hyphotenic.json").read_text(encoding="utf-8"))


def create_hyphotenic(store, actor, draft):
    profile = hyphotenic()
    series = profile["series"][draft.series]
    base = profile["base"]
    changes = draft.model_dump(
        include={"audience", "tone", "duration", "structure", "languages"},
        exclude_none=True,
    )
    settings = {
        **base,
        "duration": series["duration"],
        "structure": series["structure"],
        **changes,
        "style": {**base["style"], **series.get("style", {}), **draft.style},
    }
    project = m.Project(
        title=profile["title_prefix"] + draft.public_title,
        topic=draft.topic,
        **settings,
    ).model_dump()
    project["profile"] = {
        "channel": profile["channel"],
        "revision": profile["revision"],
        "signature": profile["signature"],
        "territory": profile["territory"],
        "series": draft.series,
        "series_name": series["name"],
        "public_title": draft.public_title,
        "brief": draft.model_dump(
            include={"changed_condition", "unchanged_conditions", "scope"},
            exclude_none=True,
        ),
        "script_instructions": profile["script_instructions"],
        "research_checklist": profile["research_checklist"],
        "deliverables": profile["deliverables"]
        + (
            ["Prepare two Shorts with distinct questions for this long episode."]
            if draft.series != "shorts"
            else []
        ),
        "approvals": profile["approvals"],
        "manual_controls": profile["manual_controls"],
    }
    with store.transaction() as db:
        pid = store.insert_project(db, actor, project)
        if draft.sources:
            research = m.Research(sources=draft.sources)
            store.add(db, pid, "research", "research", research.model_dump())
        return pid
