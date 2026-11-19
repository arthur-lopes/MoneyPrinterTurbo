"""First-pass visual references for reading an approved script on a timeline."""

from PIL import Image, ImageDraw, ImageFont

from .models import Scene


def plan(sections, sources):
    result = []
    for section in sections:
        visual = section.get("visual_beat") or section["title"]
        result.append(
            {
                "scene": Scene(
                    title=section["title"],
                    sections=[section["key"]],
                    narration=section["narration"],
                    seconds=section["seconds"],
                    description=visual,
                    image_prompt=visual,
                    video_prompt=visual,
                ).model_dump(),
                "sources": [
                    sources[index - 1]["title"] for index in section["sources"]
                ],
                "callout": next(
                    (
                        cue["label"]
                        for cue in section.get("motion_cues") or ()
                        if cue["kind"] == "reveal" and cue.get("label")
                    ),
                    "",
                ),
            }
        )
    return result


def _lines(draw, text, font, width, limit):
    """Wrap by pixels, reserving space for ellipsis on the last line."""
    lines = []
    words = text.split()
    for word in words:
        if draw.textlength(word, font=font) > width:
            while word and draw.textlength(word + "…", font=font) > width:
                word = word[:-1]
            word += "…"
        candidate = (lines[-1] + " " + word) if lines else word
        if lines and draw.textlength(candidate, font=font) <= width:
            lines[-1] = candidate
        elif len(lines) < limit:
            lines.append(word)
        else:
            while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > width:
                lines[-1] = lines[-1][:-1]
            lines[-1] += "…"
            break
    return "\n".join(lines)


def frame(entry, number, total, project, target):
    width, height = (720, 1280) if project["style"]["aspect"] == "9:16" else (1280, 720)
    canvas = Image.new("RGB", (width, height), "#0d1824")
    draw = ImageDraw.Draw(canvas)
    pt = project["languages"][0] == "pt-BR"
    accent = ("#f4bd74", "#79dfd6", "#b2c6ff")[number % 3]
    margin = round(width * 0.12)
    font = ImageFont.load_default(size=48 if width == 720 else 58)
    regular = ImageFont.load_default(size=27)
    small = ImageFont.load_default(size=21)
    for y in range(height):
        shade = round(28 * y / height)
        draw.line((0, y, width, y), fill=(13 + shade, 24 + shade, 36 + shade))
    draw.text(
        (margin, height * 0.17),
        ("GUIA VISUAL" if pt else "VISUAL GUIDE")
        + f"  /  {number + 1:02} · {total:02}",
        font=small,
        fill=accent,
    )
    draw.line(
        (margin, height * 0.228, width - margin, height * 0.228), fill=accent, width=3
    )
    draw.multiline_text(
        (margin, height * 0.28),
        _lines(
            draw,
            entry["scene"]["title"],
            font,
            width - 2 * margin,
            3 if width == 720 else 2,
        ),
        font=font,
        fill="#f6f8fb",
        spacing=8,
    )
    if entry["callout"]:
        draw.multiline_text(
            (margin, height * 0.54),
            _lines(draw, entry["callout"], regular, width - 2 * margin, 2),
            font=regular,
            fill=accent,
            spacing=5,
        )
    if entry["sources"]:
        draw.multiline_text(
            (margin, height * 0.68),
            _lines(draw, "  /  ".join(entry["sources"]), small, width - 2 * margin, 2),
            font=small,
            fill="#ccd5de",
            spacing=4,
        )
    canvas.save(target)
