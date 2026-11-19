"""Validated inputs for the production workspace."""

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Short = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
Id = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
Money = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]
Seconds = Annotated[float, Field(ge=0.1, le=120)]
Language = Literal["en-US", "pt-BR"]
Operation = Literal[
    "research", "script", "image", "video", "translation", "voice", "assembly", "export"
]
Review = Literal["APPROVED", "REJECTED", "REVISION_REQUESTED"]


def require_confirmation(value):
    if value is not True:
        raise ValueError("An explicit true confirmation is required")
    return value


Confirmed = Annotated[Literal[True], BeforeValidator(require_confirmation)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Style(Input):
    art_direction: str = Field(default="Cinematic documentary", max_length=500)
    palette: str = Field(default="", max_length=500)
    lighting: str = Field(default="", max_length=500)
    camera: str = Field(default="", max_length=500)
    environments: str = Field(default="", max_length=1000)
    characters: str = Field(default="", max_length=1000)
    restrictions: str = Field(default="", max_length=1000)
    realism: str = Field(default="Photorealistic", max_length=200)
    aspect: Literal["16:9", "9:16"] = "16:9"


class ProviderDefaults(Input):
    research: Literal["local", "openai"] = "local"
    script: Literal["local", "openai"] = "local"
    image: Literal["local", "openai"] = "local"
    video: Literal["local", "runway"] = "local"
    voice: Literal["local", "openai"] = "local"


class Project(Input):
    title: Short
    topic: Text
    audience: Short
    tone: Short = "Documentary"
    duration: int = Field(default=60, ge=2, le=7200)
    structure: list[Short] = Field(
        default_factory=lambda: [
            "Opening hook",
            "Scenario setup",
            "Initial consequences",
            "Escalation",
            "Unexpected developments",
            "Long-term consequences",
            "Conclusion",
        ],
        min_length=1,
        max_length=50,
    )
    languages: list[Language] = Field(
        default_factory=lambda: ["en-US", "pt-BR"], min_length=1, max_length=2
    )
    style: Style = Field(default_factory=Style)
    defaults: ProviderDefaults = Field(default_factory=ProviderDefaults)
    currency: Literal["USD", "BRL"] = "USD"

    @field_validator("languages")
    @classmethod
    def unique_languages(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Choose each language once")
        return values


class Source(Input):
    title: Short
    url: str = Field(max_length=2000)
    excerpt: Text

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        url = urlsplit(value)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
        ):
            raise ValueError("Use a public HTTP or HTTPS reference URL")
        return value


class Claim(Input):
    text: Text
    category: Literal["verified", "assumption", "speculation", "uncertain"]
    sources: list[int] = Field(default_factory=list, max_length=20)


class Research(Input):
    sources: list[Source] = Field(default_factory=list, max_length=30)
    claims: list[Claim] = Field(default_factory=list, max_length=60)

    @model_validator(mode="after")
    def provenance(self):
        for claim in self.claims:
            if any(not 1 <= ref <= len(self.sources) for ref in claim.sources):
                raise ValueError(
                    "Source references are one-based indices into the supplied list"
                )
            if claim.category == "verified" and not claim.sources:
                raise ValueError("Verified claims require a supplied source")
        return self


class Section(Input):
    key: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    title: Short
    narration: Text
    seconds: Seconds
    category: Literal["verified", "assumption", "speculation", "uncertain"] = (
        "uncertain"
    )
    sources: list[int] = Field(default_factory=list, max_length=20)


class Script(Input):
    sections: list[Section] = Field(min_length=1, max_length=100)

    @field_validator("sections")
    @classmethod
    def unique_sections(cls, values):
        if len({section.key for section in values}) != len(values):
            raise ValueError("Script section keys must be unique")
        return values


class Scene(Input):
    title: Short
    sections: list[Short] = Field(min_length=1, max_length=100)
    narration: Text
    seconds: Seconds
    description: Text
    camera_angle: str = Field(default="Wide shot", max_length=500)
    camera_movement: str = Field(default="Slow tracking", max_length=500)
    lighting: str = Field(default="", max_length=500)
    environment: str = Field(default="", max_length=500)
    artistic_style: str = Field(default="", max_length=500)
    image_prompt: Text
    video_prompt: Text
    negative_prompt: str = Field(default="", max_length=1000)
    references: list[Id] = Field(default_factory=list, max_length=5)
    style_overrides: dict[
        Literal[
            "art_direction",
            "palette",
            "lighting",
            "camera",
            "environments",
            "characters",
            "restrictions",
            "realism",
        ],
        Short,
    ] = Field(default_factory=dict)
    provider: Literal["local", "runway"] = "local"
    model: Literal["motion", "gen4_turbo"] = "motion"


class Translation(Input):
    title: Short
    description: Text
    narration: Text


class ReviewInput(Input):
    status: Review
    comment: str = Field(default="", max_length=2000)


class Move(Input):
    scenes: list[Id] = Field(min_length=1, max_length=100)


class Split(Input):
    seconds: Seconds
    first: Text
    second: Text


class Merge(Input):
    scenes: list[Id] = Field(min_length=2, max_length=10)


class Clip(Input):
    scene: Id
    video: Id | None = None
    trim: float = Field(default=0, ge=0, le=120)
    seconds: Seconds | None = None
    narration_offset: float = Field(default=0, ge=0, le=120)
    effect: Id | None = None
    transition: Literal["cut", "fade"] = "cut"


class Timeline(Input):
    clips: list[Clip] = Field(min_length=1, max_length=100)
    languages: list[Language] = Field(min_length=1, max_length=2)
    aspect: Literal["16:9", "9:16"] = "16:9"
    resolution: Literal[1080, 1440, 2160] = 1080
    subtitles: bool = True
    watermark: bool = False
    music: Id | None = None
    music_volume: float = Field(default=0.15, ge=0, le=1)
    metadata: dict[Language, "DeliveryMetadata"] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_items(self):
        if len({clip.scene for clip in self.clips}) != len(self.clips) or len(
            set(self.languages)
        ) != len(self.languages):
            raise ValueError("Timeline entries and languages must be unique")
        if not self.metadata.keys() <= set(self.languages):
            raise ValueError("Delivery metadata must match the selected languages")
        return self


class DeliveryMetadata(Input):
    title: Short
    description: Text


class RequestJob(Input):
    operation: Operation
    provider: Literal["local", "openai", "runway"] | None = None
    scene: Id | None = None
    language: Language | None = None
    version: Id | None = None
    sources: list[Source] = Field(default_factory=list, max_length=30)
    instructions: str = Field(default="", max_length=3000)
    voice: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9 -]{1,80}$")] = "alloy"
    timeline: Timeline | None = None


class QuoteInput(Input):
    jobs: list[RequestJob] = Field(min_length=1, max_length=100)


class Confirm(Input):
    maximum: Money
    confirmed: Confirmed


class Budget(Input):
    scope: Literal["project", "video", "scene", "day", "month", "provider"]
    target: str = Field(default="", max_length=40)
    limit: Money
    confirmed: Confirmed


class Provider(Input):
    provider: Literal["openai", "runway"]
    operation: Literal["research", "script", "image", "video", "translation", "voice"]
    model: Short
    currency: Literal["USD", "BRL"] = "USD"
    ceiling: Annotated[int, Field(strict=True, gt=0, le=1_000_000_000)]
    enabled: bool = False
    timeout: int = Field(default=180, ge=15, le=1800)
    interval: int = Field(default=3, ge=1, le=300)
    confirmed: Confirmed


class Settlement(Input):
    actual: Money
    receipt: Text
    confirmed: Confirmed
