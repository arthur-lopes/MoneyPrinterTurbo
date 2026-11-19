"""Local, versioned thumbnail concepts and publication-ready JPEG bundles."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from zipfile import ZIP_STORED, ZipFile

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
from pydantic import Field, field_validator

from . import media, models as m
from .store import RuleError


Concept = Literal["impact", "contrast", "detail"]
SLOTS = {"impact": "A", "contrast": "B", "detail": "C"}
PALETTES = {"mint": "#6cf1d0", "amber": "#ffbc68", "blue": "#9bd9ff"}
SIZES = {"16:9": (3840, 2160), "9:16": (2160, 3840)}
JPG_LIMIT = 1_950_000
FONT = "BeVietnamPro-Bold.ttf"


class Variant(m.Input):
    concept: Concept
    headline: str = Field(default="", max_length=64)
    source: m.Id | None = None
    comparison: m.Id | None = None
    focal: Literal["left", "center", "right"] = "center"
    accent: Literal["mint", "amber", "blue"] = "mint"

    @field_validator("headline")
    @classmethod
    def clean_headline(cls, value):
        return " ".join(value.split())


class Batch(m.Input):
    variants: list[Variant] = Field(min_length=1, max_length=3)

    @field_validator("variants")
    @classmethod
    def unique_concepts(cls, values):
        if len({value.concept for value in values}) != len(values):
            raise ValueError("Use a different concept for each thumbnail")
        return values


def _font(size):
    pack = Path(__file__).resolve().parents[1]
    for folder in (pack / "_assets/resource/fonts", pack.parent / "resource/fonts"):
        path = folder / FONT
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _backdrop(canvas, accent, offset, visual):
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    center_x, center_y = int(w * offset), int(h * 0.45)
    radius = int(min(w, h) * 0.29)
    if visual == "detail":
        draw.ellipse(
            (
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
            ),
            fill="#1b3341",
            outline=accent,
            width=max(2, radius // 25),
        )
        draw.ellipse(
            (
                center_x - radius // 2,
                center_y - radius // 2,
                center_x + radius // 2,
                center_y + radius // 2,
            ),
            fill=accent,
        )
    else:
        shape = (
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
        )
        draw.rounded_rectangle(
            shape, radius // 5, fill="#1b3341", outline=accent, width=radius // 17
        )
        for i, ratio in enumerate((0.32, 0.56, 0.80)):
            y = center_y - radius + round(2 * radius * ratio)
            draw.line(
                (center_x - radius // 2, y, center_x + radius // 2, y),
                fill=accent if i == 1 else "#65818d",
                width=max(3, radius // 22),
            )


def _picture(path, size, center, accent, visual):
    if path is None:
        picture = Image.new("RGB", size, "#0d1f2d")
        _backdrop(
            picture,
            accent,
            0.69 if visual == "impact" and size[0] > size[1] else 0.5,
            visual,
        )
        return picture
    with Image.open(path) as source:
        frame = ImageOps.exif_transpose(source).convert("RGB")
        if visual == "detail":
            w, h = frame.size
            left = round(w * 0.32 * center)
            top = round(h * 0.32 * 0.5)
            frame = frame.crop(
                (left, top, left + round(w * 0.68), top + round(h * 0.68))
            )
        return ImageOps.fit(
            frame, size, Image.Resampling.LANCZOS, centering=(center, 0.5)
        )


def _headline(draw, text, x, y, usable_width, usable_height, scale, accent):
    words = text.upper().split()
    if not words:
        return
    longest = max(words, key=len)
    for factor in (1, 0.87, 0.75, 0.64, 0.54, 0.45, 0.36, 0.29):
        size = max(38, round(scale * factor))
        font = _font(size)
        if draw.textlength(longest, font=font) > usable_width:
            continue
        lines = [words[0]]
        for word in words[1:]:
            line = lines[-1] + " " + word
            if draw.textlength(line, font=font) <= usable_width:
                lines[-1] = line
            else:
                lines.append(word)
        spacing = int(size * 1.035)
        if len(lines) * spacing <= usable_height:
            break
    else:
        raise RuleError(
            "Shorten the thumbnail headline so it fits at a readable size", 422
        )
    for index, text in enumerate(lines):
        draw.text(
            (x, y + index * spacing),
            text,
            font=font,
            anchor="lt",
            fill="#ffffff" if index == 0 else accent,
            stroke_width=max(2, size // 36),
            stroke_fill="#0b141c",
        )


def render(variant, aspect, subject, comparison, output):
    size = SIZES[aspect]
    w, h = size
    portrait = h > w
    accent = PALETTES[variant.accent]
    center = {"left": 0.14, "center": 0.5, "right": 0.86}[variant.focal]
    if variant.concept == "contrast":
        split = h // 2 if portrait else w // 2
        panel = (w, split) if portrait else (split, h)
        left = _picture(subject, panel, center, "#759bb9", "contrast")
        right = _picture(comparison or subject, panel, 0.8, accent, "detail")
        left = ImageEnhance.Color(ImageEnhance.Brightness(left).enhance(0.67)).enhance(
            0.55
        )
        canvas = Image.new("RGB", size)
        canvas.paste(left, (0, 0))
        canvas.paste(right, (0, split) if portrait else (split, 0))
        draw = ImageDraw.Draw(canvas)
        line = (0, split, w, split) if portrait else (split, 0, split, h)
        draw.line(line, fill=accent, width=max(8, min(size) // 96))
    else:
        canvas = _picture(subject, size, center, accent, variant.concept)
    if variant.headline:
        if variant.concept == "impact" and not portrait:
            fade = Image.linear_gradient("L").rotate(90).resize(size)
            fade = fade.point(lambda a: round(220 * (1 - a / 255) ** 1.65))
            x, y, tw, th, fs = (
                int(w * 0.065),
                int(h * 0.55),
                int(w * 0.58),
                int(h * 0.35),
                int(h * 0.14),
            )
        else:
            linear = Image.linear_gradient("L").resize(size)
            fade = linear.point(lambda a: round(238 * (a / 255) ** 2.2))
            x, y = int(w * 0.065), int(h * (0.70 if portrait else 0.63))
            tw, th, fs = (
                int(w * 0.82),
                int(h * (0.23 if portrait else 0.29)),
                int(min(w, h) * 0.135),
            )
        canvas.paste("#080f17", (0, 0, w, h), fade)
        _headline(ImageDraw.Draw(canvas), variant.headline, x, y, tw, th, fs, accent)
    for quality in (91, 84, 76, 66, 56, 46):
        canvas.save(output, format="JPEG", quality=quality, optimize=True)
        if output.stat().st_size < JPG_LIMIT:
            return
    raise RuleError("Thumbnail exceeds the JPEG file limit", 422)


def generate(store, root, actor, pid, batch):
    def inputs(db):
        project = store.project(db, actor, pid)["data"]
        aspect = project["style"]["aspect"]
        result = []
        for variant in batch.variants:
            if variant.comparison and variant.concept != "contrast":
                raise RuleError("Only the contrast concept accepts a second image", 422)
            slot = "thumbnail/" + variant.concept
            previous = store.head(db, pid, slot)
            references = []
            for ref in (variant.source, variant.comparison):
                item = store.asset(db, pid, ref) if ref else None
                if item and (
                    item["kind"] not in {"image", "reference"}
                    or item["status"] != "APPROVED"
                    or not item["media"]
                    or not store.fresh(db, item)
                ):
                    raise RuleError(
                        "Choose a current approved image from this project", 422
                    )
                references.append(item)
            result.append((slot, previous, references))
        return aspect, result

    with store.transaction(write=False) as db:
        captured = inputs(db)
    aspect, rows = captured
    names = []
    try:
        with TemporaryDirectory(prefix="thumbs-", dir=store.directory) as temp:
            drafts = []
            for variant, (slot, previous, refs) in zip(batch.variants, rows):
                data = dict(
                    variant.model_dump(),
                    aspect=aspect,
                    width=SIZES[aspect][0],
                    height=SIZES[aspect][1],
                )
                if (
                    previous
                    and previous["data"] == data
                    and previous["status"] in {"APPROVED", "AWAITING_REVIEW"}
                ):
                    drafts.append((slot, None, previous, data, refs))
                    continue
                output = Path(temp) / (variant.concept + ".jpg")
                paths = [
                    media.asset_path(root, item["media"]) if item else None
                    for item in refs
                ]
                render(variant, aspect, *paths, output)
                drafts.append((slot, output, None, data, refs))
            with store.transaction() as db:
                if inputs(db) != captured:
                    raise RuleError(
                        "Thumbnail inputs changed during rendering. Reload the project and retry."
                    )
                versions = []
                for slot, output, previous, data, refs in drafts:
                    if previous:
                        versions.append(previous)
                        continue
                    names.append(media.publish(root, output))
                    deps = [{"style_aspect": aspect}]
                    deps.extend({"id": item["id"]} for item in refs if item)
                    versions.append(
                        store.add(db, pid, slot, "thumbnail", data, deps, names[-1])
                    )
                return versions
    except BaseException:
        for name in names:
            media.asset_path(root, name).unlink(missing_ok=True)
        raise


def bundle(store, root, actor, pid):
    output = BytesIO()
    with store.transaction(write=False) as db:
        project = store.project(db, actor, pid)["data"]
        with ZipFile(output, "w", compression=ZIP_STORED) as archive:
            details = []
            for concept, letter in SLOTS.items():
                asset = store.head(db, pid, "thumbnail/" + concept)
                if (
                    not asset
                    or not store.fresh(db, asset)
                    or asset["status"] not in {"APPROVED", "AWAITING_REVIEW"}
                ):
                    continue
                file = media.asset_path(root, asset["media"])
                if not file.is_file() or file.stat().st_size >= JPG_LIMIT:
                    raise RuleError(
                        "A thumbnail is unavailable. Generate a new revision.", 409
                    )
                archive.writestr(f"{letter}-{concept}.jpg", file.read_bytes())
                details.append(
                    f"{letter} - {concept}: {asset['data']['headline'] or '(sem texto)'}"
                )
            if not details:
                raise RuleError("Generate thumbnail options first", 404)
            archive.writestr(
                "opcoes.txt", project["title"] + "\n\n" + "\n".join(details) + "\n"
            )
    return output.getvalue()
