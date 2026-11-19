"""Build a local HyperFrames composition from approved artwork and typed cues."""

from html import escape
from pathlib import Path
import shutil


PACKAGE = Path(__file__).parent
ANIME_BUNDLE = PACKAGE / "vendor" / "anime-4.5.0.umd.min.js"

CSS = """
* { box-sizing: border-box; }
body { margin: 0; background: #0d141d; }
main, .frame, .cue-svg { position: absolute; inset: 0; overflow: hidden; }
.frame { display: block; width: 100%; height: 100%; object-fit: cover; }
.cue-svg { width: 100%; height: 100%; }
.cue-svg > g, .reveal { opacity: 0; }
.outline, .route { fill: none; stroke: #ffdaaa; stroke-width: 3.4;
  stroke-linecap: round; stroke-linejoin: round; }
.route { stroke-width: 4; }
.end { fill: #ffe8c8; }
.sym { filter: drop-shadow(0 1px 5px #101420) drop-shadow(0 1px 12px #884828); }
.reveal { position: absolute; inset: auto auto auto auto; width: max-content;
  max-width: min(28em, 76vw); font: 650 clamp(28px, 4.8vmin, 65px)/1.08 Arial, sans-serif;
  color: #fff2dc; letter-spacing: -.045em; overflow-wrap: anywhere;
  padding-block: .13em; background: linear-gradient(90deg, #111b27f5, #111b2745);
  box-shadow: -.3em 0 #111b27f5; }
.reveal::before { content: ''; display: block; height: 2px; width: 1.35em;
  background: #ffbf7b; margin-bottom: .34em; }
"""


def cue_markup(cue, width, height):
    x, y = cue["x"] * width, cue["y"] * height
    kind = cue["kind"]
    timing = f'data-start-ms="{cue["start"] * 1000:.3f}" data-duration-ms="{cue["duration"] * 1000:.3f}"'
    data = f'data-editorial-cue="{kind}" {timing}'
    if kind == "reveal":
        horizontal = (
            f"right:{(1 - cue['x']) * 100:.3f}%"
            if cue["x"] > 0.5
            else f"left:{cue['x'] * 100:.3f}%"
        )
        max_width = (cue["x"] if cue["x"] > 0.5 else 1 - cue["x"]) * 100 - 5
        return (
            f'<div class="reveal" {data} style="{horizontal};top:{y:.3f}px;max-width:{max_width:.3f}vw">'
            f"{escape(cue['label'])}</div>"
        )
    if kind == "path":
        target_x, target_y = cue["x2"] * width, cue["y2"] * height
        mid_x = (x + target_x) / 2
        art = (
            f'<path class="route" d="M{x:.3f},{y:.3f} H{mid_x:.3f} L{target_x:.3f},{target_y:.3f}"/>'
            f'<circle class="end" cx="{target_x:.3f}" cy="{target_y:.3f}" r="6"/>'
        )
    else:
        art = (
            f'<g transform="translate({x:.3f} {y:.3f})">'
            '<path class="outline" d="M-54-31v-23h23m62 0h23v23m0 62v23H31m-62 0h-23V31"/>'
            "</g>"
        )
    return (
        f'<svg class="cue-svg" viewBox="0 0 {width} {height}" aria-hidden="true">'
        f'<g class="sym" {data}>{art}</g></svg>'
    )


def prepare(scratch, first_frame, cues, width, height):
    shutil.copyfile(first_frame, scratch / "editorial-frame.png")
    shutil.copyfile(ANIME_BUNDLE, scratch / "anime.js")
    shutil.copyfile(PACKAGE / "editorial_overlay.js", scratch / "editorial-overlay.js")
    return '<img class="frame" src="editorial-frame.png" alt="">' + "".join(
        cue_markup(cue, width, height) for cue in cues
    )
