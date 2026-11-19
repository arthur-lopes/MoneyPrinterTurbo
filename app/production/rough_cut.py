"""First-pass visual references for reading an approved script on a timeline."""

from PIL import Image, ImageDraw, ImageFont

from . import media
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
                **(
                    {"evidence_graphic": section["evidence_graphic"]}
                    if section.get("evidence_graphic")
                    else {}
                ),
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


def evidence_clip(entry, project, image, output, seconds, stop, timeout):
    """Draw an illustrative diagram and reveal its evidence on a continuous shot."""
    design = entry["evidence_graphic"]
    layout = design["layout"]
    portrait = project["style"]["aspect"] == "9:16"
    width, height = (720, 1280) if portrait else (1280, 720)
    left, right = round(width * 0.085), round(width * 0.915)
    span = right - left
    top, bottom = round(height * 0.30), round(height * 0.73)
    panel = bottom - top
    ink, soft, line = "#eaf5fa", "#b8c8d7", "#516b82"
    aqua, amber = "#68e5da", "#ffd299"
    pt = project["languages"][0] == "pt-BR"
    canvas = Image.new("RGB", (width, height), "#0b1724")
    base = ImageDraw.Draw(canvas)
    foreground = Image.new("RGBA", canvas.size)
    accent = ImageDraw.Draw(foreground)
    for y in range(height):
        shade = round(y / height * 22)
        base.line((0, y, width, y), fill=(11 + shade, 23 + shade, 36 + shade))

    title = ImageFont.load_default(size=48 if portrait else 52)
    label = ImageFont.load_default(size=30)
    small = ImageFont.load_default(size=22)
    emphasis = ImageFont.load_default(size=36)
    base.text(
        (left, height * 0.10),
        "ESQUEMA ILUSTRATIVO" if pt else "ILLUSTRATIVE SCHEMATIC",
        font=small,
        fill=aqua,
    )
    base.multiline_text(
        (left, height * 0.146),
        _lines(base, entry["scene"]["title"], title, span, 2),
        font=title,
        fill=ink,
        spacing=4,
    )
    base.rounded_rectangle(
        (left, top, right, bottom),
        radius=22,
        fill="#102637",
        outline="#34516a",
        width=2,
    )

    def line_at(draw, y, color=line):
        draw.line((left + 30, y, right - 30, y), fill=color, width=4)

    def focal_point(draw, x, y, color, r=10):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)

    def centered(draw, text, x, y, font, color, width):
        draw.multiline_text(
            (x, y),
            _lines(draw, text, font, width, 2),
            font=font,
            fill=color,
            anchor="ma",
            align="center",
            spacing=3,
        )

    def track(name, y, clearance=12, width=span - 60):
        title = _lines(base, name, label, width, 2)
        _, _, _, text_bottom = base.multiline_textbbox(
            (0, 0), title, font=label, spacing=3
        )
        base.multiline_text(
            (left + 30, y - text_bottom - clearance),
            title,
            font=label,
            fill=ink,
            spacing=3,
        )
        line_at(base, y)

    first, second = design["labels"]
    drift = 0
    if layout == "sequence":
        y = top + round(panel * 0.46)
        early, late = left + round(span * 0.23), left + round(span * 0.77)
        line_at(base, y)
        focal_point(base, early, y, line)
        focal_point(base, late, y, soft)
        centered(
            base, first, early, y - round(panel * 0.30), label, ink, int(span * 0.40)
        )
        centered(
            base, second, late, y - round(panel * 0.30), label, ink, int(span * 0.40)
        )
        accent.line((early, y, late, y), fill=aqua, width=5)
        focal_point(accent, early, y, aqua, 15)
        focal_point(accent, late, y, amber, 12)
        centered(
            accent,
            design["annotation"],
            (early + late) // 2,
            top + panel * 0.67,
            emphasis,
            aqua,
            span - 65,
        )
    elif layout == "alignment":
        upper, lower = top + round(panel * 0.32), top + round(panel * 0.67)
        x = left + round(span * 0.82)
        for name, y in ((first, upper), (second, lower)):
            track(name, y, width=x - left - 54)
        accent.line((x, upper, x, lower), fill=aqua, width=4)
        focal_point(accent, x, upper, amber, 12)
        focal_point(accent, x, lower, aqua, 12)
        centered(
            accent,
            design["annotation"],
            (left + right) // 2,
            top + panel * 0.80,
            small,
            aqua,
            span - 65,
        )
    elif layout == "coupling":
        upper, lower = top + round(panel * 0.34), top + round(panel * 0.62)
        track(first, upper)
        base.rectangle((left + 30, lower, right - 30, lower + 8), fill="#165468")
        line_at(base, lower)
        base.multiline_text(
            (left + 30, lower + 12),
            _lines(base, second, label, span - 60, 2),
            font=label,
            fill=ink,
            spacing=3,
        )
        x = left + round(span * 0.24)
        drift = round(span * 0.48)
        accent.line((x, upper, x, lower - 11), fill=amber, width=3)
        focal_point(accent, x, upper, amber, 14)
        accent.arc(
            (x - 37, lower - 18, x + 37, lower + 18), 180, 360, fill=aqua, width=6
        )
        centered(
            base,
            design["annotation"],
            (left + right) // 2,
            top + panel * 0.88,
            small,
            soft,
            span - 65,
        )
    else:
        upper, lower = top + round(panel * 0.32), top + round(panel * 0.68)
        rise = min(25, round(panel * 0.06))
        track(first, upper)
        track(second, lower, clearance=rise + 12)
        start_x, end_x = left + 38, right - 38
        accent.line((start_x, upper, end_x, upper), fill=soft, width=5)
        accent.line(
            (
                start_x,
                lower,
                start_x + span * 0.21,
                lower - rise,
                end_x - span * 0.22,
                lower - rise,
                end_x,
                lower,
            ),
            fill=aqua,
            width=5,
            joint="curve",
        )
        for y, color in ((upper, soft), (lower, aqua)):
            accent.polygon(
                ((end_x, y), (end_x - 15, y - 8), (end_x - 15, y + 8)), fill=color
            )
        centered(
            accent,
            design["annotation"],
            (left + right) // 2,
            top + panel * 0.82,
            small,
            aqua,
            span - 65,
        )

    source = "  /  ".join(entry["sources"])
    base.multiline_text(
        (left, height * 0.76),
        _lines(base, source, small, span, 2),
        font=small,
        fill=soft,
        spacing=4,
    )
    canvas.save(image)
    overlay = output.with_name(output.stem + "-reveal.png")
    foreground.save(overlay)
    start = design["reveal_at"]
    fade = min(0.55, max(0.04, (seconds - start) / 3))
    motion = min(3.2, max(0.04, seconds - start - fade))
    x = f"{drift}*clip((t-{start})/{motion},0,1)"
    media.ffmpeg(
        [
            "-loop",
            1,
            "-framerate",
            24,
            "-i",
            image,
            "-loop",
            1,
            "-framerate",
            24,
            "-i",
            overlay,
            "-filter_complex_threads",
            1,
            "-filter_complex",
            f"[1:v]format=rgba,fade=t=in:st={start}:d={fade}:alpha=1[a];"
            f"[0:v][a]overlay=x='{x}':y=0",
            "-t",
            seconds,
            "-an",
            "-c:v",
            "libx264",
            "-threads",
            2,
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            output,
        ],
        cwd=output.parent,
        stop=stop,
        timeout=timeout,
    )
