"""Owner-scoped editorial catalog and project templates."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from . import models as m
from .store import RuleError, encode, unpack


EpisodeId = Annotated[str, StringConstraints(pattern=r"^[A-Z][0-9]{2}$")]
DOCUMENTS = {
    "catalog": "hyphotenic-catalogo.json",
    "prompts": "hyphotenic-prompts-moneyprinter.txt",
    "plan": "hyphotenic-plano-editorial.md",
}


class ResearchStart(m.Input):
    label: m.Short
    url: str = Field(max_length=2000)

    @field_validator("url")
    @classmethod
    def safe_url(cls, url):
        return m.Source.safe_url(url)


class EvidenceBrief(m.Research):
    question: m.Short
    finding: m.Text
    angle: m.Text
    uncertainty: m.Text
    sources: list[m.Source] = Field(min_length=1, max_length=8)
    claims: list[m.Claim] = Field(min_length=1, max_length=16)


class Episode(m.Input):
    id: EpisodeId
    series: m.Short
    round: int = Field(ge=1, le=100)
    order_in_round: int = Field(ge=1, le=100)
    pilot: bool
    status: Literal["draft"]
    language: Literal["en"]
    aspect_ratio: Literal["16:9", "9:16"]
    target_duration_seconds: int = Field(ge=2, le=7200)
    title: m.Short
    opening_hook: m.Text
    premise: m.Text
    story_beats: list[m.Short] = Field(min_length=1, max_length=50)
    visual_plan: m.Text
    thumbnail_text: m.Short
    fact_check: m.Text
    research_start: ResearchStart
    draft_prompt: str = Field(min_length=1, max_length=12000)
    evidence: EvidenceBrief | None = None


class Catalog(m.Input):
    schema_version: Literal[1]
    kind: Literal["editorial_catalog"]
    channel: m.Short
    slogan: m.Short
    default_narration_language: Literal["en"]
    optional_localization: Literal["pt-BR"]
    publishing_rounds: list[list[EpisodeId]] = Field(min_length=1, max_length=100)
    episodes: list[Episode] = Field(min_length=1, max_length=100)
    featured: list[EpisodeId] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_rounds(self):
        rounds = {
            episode_id: (number, position)
            for number, group in enumerate(self.publishing_rounds, 1)
            for position, episode_id in enumerate(group, 1)
        }
        if (
            len(rounds) != sum(map(len, self.publishing_rounds))
            or len(rounds) != len(self.episodes)
            or len({episode.id for episode in self.episodes}) != len(self.episodes)
            or any(
                rounds.get(e.id) != (e.round, e.order_in_round) for e in self.episodes
            )
        ):
            raise ValueError("Publishing rounds must list every episode once in order")
        evidence_ids = {episode.id for episode in self.episodes if episode.evidence}
        if (
            len(set(self.featured)) != len(self.featured)
            or not set(self.featured) <= evidence_ids
        ):
            raise ValueError(
                "Featured episodes must have distinct, evidence-backed proposals"
            )
        return self


class Documents(m.Input):
    catalog: str = Field(min_length=1, max_length=300000)
    prompts: str = Field(min_length=1, max_length=200000)
    plan: str = Field(min_length=1, max_length=150000)

    @field_validator("catalog")
    @classmethod
    def check_catalog(cls, value):
        Catalog.model_validate_json(value.lstrip("\ufeff"))
        return value


class ProjectOptions(m.Input):
    localize: bool = False


def upload(store, actor, documents):
    with store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO editorial_catalogs(owner,data) VALUES(?,?)",
            (actor, encode(documents.model_dump())),
        )


def _documents(db, actor):
    row = db.execute(
        "SELECT data FROM editorial_catalogs WHERE owner=?", (actor,)
    ).fetchone()
    return unpack(row, "data")["data"] if row else None


def catalog(store, actor):
    with store.transaction(write=False) as db:
        documents = _documents(db, actor)
        if not documents:
            return {"catalog": None, "projects": {}}
        projects = db.execute(
            "SELECT episode,project FROM editorial_projects WHERE owner=?", (actor,)
        )
        info = Catalog.model_validate_json(documents["catalog"].lstrip("\ufeff"))
        return {
            "catalog": info.model_dump(),
            "projects": {row["episode"]: row["project"] for row in projects},
        }


def document(store, actor, name):
    with store.transaction(write=False) as db:
        documents = _documents(db, actor)
        if not documents:
            raise RuleError("Editorial catalog not found", 404)
        return documents[name]


def create_project(store, actor, episode_id, options):
    with store.transaction() as db:
        row = db.execute(
            "SELECT project FROM editorial_projects WHERE owner=? AND episode=?",
            (actor, episode_id),
        ).fetchone()
        if row:
            return row["project"]
        documents = _documents(db, actor)
        if documents:
            info = Catalog.model_validate_json(documents["catalog"].lstrip("\ufeff"))
            episode = next((e for e in info.episodes if e.id == episode_id), None)
            if episode:
                project = m.Project(
                    title=episode.title,
                    topic=episode.premise,
                    audience="English-language documentary viewers",
                    duration=episode.target_duration_seconds,
                    structure=episode.story_beats,
                    languages=["en-US", "pt-BR"] if options.localize else ["en-US"],
                    style=m.Style(aspect=episode.aspect_ratio),
                    defaults=m.ProviderDefaults(
                        research="codex", script="codex", translation="codex"
                    ),
                    currency="BRL",
                ).model_dump()
                project["editorial"] = episode.model_dump(
                    exclude={"draft_prompt": True, "evidence": {"sources", "claims", "editorial_focus"}},
                    exclude_none=True,
                )
                pid = store.insert_project(db, actor, project)
                if episode.evidence:
                    research = m.Research(
                        sources=episode.evidence.sources,
                        claims=episode.evidence.claims,
                        editorial_focus=m.EditorialFocus(
                            question=episode.evidence.question,
                            finding=episode.evidence.finding,
                            angle=episode.evidence.angle,
                            uncertainty=episode.evidence.uncertainty,
                        ),
                    )
                    store.add(db, pid, "research", "research", research.model_dump())
                db.execute(
                    "INSERT INTO editorial_projects VALUES(?,?,?)",
                    (actor, episode_id, pid),
                )
                return pid
        raise RuleError("Editorial episode not found", 404)
