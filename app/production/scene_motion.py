"""Render reviewed, data-driven local scene templates with HyperFrames."""

from html import escape
import os
from pathlib import Path
import shutil

from . import media


DEFAULT_RENDERER = "/opt/moneyprinter/hyperframes/bin/hyperframes"
WORLD = Path(__file__).with_name("world-land.svg")
CSS = """
* { box-sizing: border-box; }
body { margin: 0; font-family: Arial, sans-serif; color: #f1f6fc; }
main {
  position: relative; overflow: hidden; height: 100vh; width: 100vw;
  background: radial-gradient(ellipse at 85% 0%, #15394b, #101d2c 60%, #0c1520);
}
.graphic { height: 100%; padding: 6%; display: flex; flex-direction: column; gap: 3%; }
h1 { margin: 0; font-size: 48px; line-height: 1.1; max-width: 95%; overflow-wrap: anywhere; }
.body { flex: 1; min-height: 0; display: flex; flex-direction: column; justify-content: center; gap: 1.1vh; }
.entry {
  border-left: 4px solid #66e0cc; min-height: 6vh; background: #1a3346;
  border-radius: 0 11px 11px 0; padding: 7px 17px; display: flex; align-items: center;
  gap: 14px; font-size: 23px; animation: reveal var(--reveal) ease-out var(--delay) both;
}
.entry .index { color: #78e8d2; font-weight: bold; min-width: 35px; font-size: 20px; }
.bar { min-height: 7vh; font-size: 19px; gap: 5px; flex-direction: column; align-items: stretch; }
.bar .numbers { display: flex; justify-content: space-between; gap: 18px; }
.track { height: 8px; min-height: 8px; background: #122535; border-radius: 4px; }
.fill { height: 100%; background: #62debe; border-radius: 4px; transform-origin: left;
  animation: grow var(--grow) ease-out var(--delay) both; }
.map { display: flex; flex: 1; align-items: center; }
.map svg { width: 100%; max-height: 100%; overflow: visible; }
.marker { animation: reveal var(--reveal) ease-out var(--delay) both; }
.route { stroke-dasharray: 100; stroke-dashoffset: 100;
  animation: draw var(--grow) ease-out var(--delay) forwards; }
.layer { position: absolute; inset: -5%; width: 110%; height: 110%; object-fit: cover;
  animation: pan var(--duration) ease-in-out both; }
.particles { position: absolute; inset: 0; pointer-events: none; }
.particles i { position: absolute; background: #fff4d1; width: 4px; height: 4px;
  border-radius: 50%; filter: blur(.65px); opacity: .32;
  animation: drift var(--duration) ease-in-out both; }
@keyframes pan { from { transform: translateX(var(--left)); } to { transform: translateX(var(--right)); } }
@keyframes reveal { from { opacity: 0; } to { opacity: 1; } }
@keyframes grow { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@keyframes draw { to { stroke-dashoffset: 0; } }
@keyframes drift { from { transform: translateY(9px); opacity: .14; }
  to { transform: translateY(-30px); opacity: .4; } }
"""


def executable():
    configured = os.getenv("MPT_PRODUCTION_HYPERFRAMES", DEFAULT_RENDERER)
    return shutil.which(configured)


def graphic_map(entries, step):
    coords = [
        ((e["lon"] + 180) * 800 / 360, (90 - e["lat"]) * 400 / 180, e["label"])
        for e in entries
    ]
    items = []
    for i, (x, y, label) in enumerate(coords):
        delay = f' style="--delay:{i * step:.3f}s"'
        if i:
            px, py, _ = coords[i - 1]
            items.append(
                f'<path class="route"{delay} d="M{px:.2f},{py:.2f} L{x:.2f},{y:.2f}" '
                'pathLength="100" fill="none" stroke="#67e4d5" stroke-width="2.6"/>'
            )
        anchor = "end" if x > 565 else "start"
        label_x = x - 8 if anchor == "end" else x + 8
        label_y = y - 10 if y > 14 else y + 15
        items.append(
            f'<g class="marker"{delay}><circle cx="{x:.2f}" cy="{y:.2f}" r="4.7" fill="#ffdc99"/>'
            f'<text x="{label_x:.2f}" y="{label_y:.2f}" text-anchor="{anchor}" '
            f'font-size="14" fill="#f5f8fa" stroke="#0c1520" stroke-width="2.6" '
            f'paint-order="stroke">{escape(label)}</text></g>'
        )
    return (
        '<div class="map"><svg viewBox="-12 -12 824 424">'
        '<image href="world-land.svg" width="800" height="400"/>'
        + "".join(items)
        + "</svg></div>"
    )


def graphic_rows(entries, step, chart):
    largest = (max(e["value"] for e in entries) or 1) if chart else 1
    rows = []
    for i, item in enumerate(entries):
        label = escape(item["label"])
        delay = f' style="--delay:{i * step:.3f}s"'
        if chart:
            value = item["value"]
            rows.append(
                f'<div class="entry bar"{delay}><div class="numbers"><span>{label}</span>'
                f'<strong>{value:,.8g}</strong></div><div class="track">'
                f'<div class="fill" style="width:{100 * value / largest:.4f}%"></div></div></div>'
            )
        else:
            rows.append(
                f'<div class="entry"{delay}><span class="index">{i + 1:02}</span>{label}</div>'
            )
    return '<div class="body">' + "".join(rows) + "</div>"


def render(root, payload, output, stop, timeout):
    runner = executable()
    if not runner:
        raise media.MediaError("HyperFrames renderer is unavailable on this host")
    scratch = output.parent
    animation = payload["animation"]
    mode, seconds = animation["mode"], payload["scene"]["seconds"]
    entries = animation["entries"]
    step = min(0.55, seconds / (len(entries) + 3))
    width, height = (1280, 720) if payload["style"]["aspect"] == "16:9" else (720, 1280)
    if mode == "parallax":
        paths = [payload["first_frame"], *payload["layers"]]
        nodes = []
        for i, path in enumerate(paths):
            filename = f"layer{i}.png"
            shutil.copyfile(media.asset_path(root, path), scratch / filename)
            depth = 1.2 + i * 1.6
            nodes.append(
                f'<img class="layer" src="{filename}" '
                f'style="--left:{-depth:.1f}%;--right:{depth:.1f}%">'
            )
        particles = "".join(
            f'<i style="left:{(i * 29 + 17) % 100}%;top:{(i * 41 + 12) % 100}%"></i>'
            for i in range(18)
        )
        content = "".join(nodes) + '<div class="particles">' + particles + "</div>"
    else:
        if mode == "map":
            shutil.copyfile(WORLD, scratch / "world-land.svg")
            graphic = graphic_map(entries, step)
        else:
            graphic = graphic_rows(entries, step, mode == "bar_chart")
        title = escape(animation["title"] or payload["scene"]["title"])
        content = f'<section class="graphic"><h1>{title}</h1>{graphic}</section>'
    (scratch / "index.html").write_text(
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head>'
        f'<body><main data-composition-id="main" data-start="0" data-duration="{seconds:.4f}" '
        f'data-width="{width}" data-height="{height}" data-no-timeline '
        f'style="--duration:{seconds:.4f}s;--reveal:{min(0.38, step):.4f}s;'
        f'--grow:{min(0.9, 2 * step):.4f}s">{content}</main></body></html>',
        encoding="utf-8",
    )
    media.run(
        [
            runner,
            "render",
            str(scratch),
            "--output",
            str(output),
            "--fps",
            "24",
            "--workers",
            "1",
            "--quality",
            "looks",
            "--quiet",
            "--player-ready-timeout",
            "12000",
        ],
        cwd=scratch,
        stop=stop,
        timeout=timeout,
        process_group=True,
    )
