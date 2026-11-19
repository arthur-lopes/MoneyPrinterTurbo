"""Bounded local media ingestion and rendering with FFmpeg."""

from contextlib import suppress
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import textwrap
import time
import warnings
import wave
from uuid import uuid4

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.utils.subtitle_writer import write_subtitle_file

from .clip_cache import ClipCache, checksum


MAX_IMAGE = 12 * 1024 * 1024
MAX_AUDIO = 40 * 1024 * 1024
MAX_VIDEO = 200 * 1024 * 1024
SAFE_NAME = re.compile(r"^[a-f0-9]{32}\.(png|jpg|wav|mp4|srt)$")
FORMATS = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "wav": "audio/wav",
    "mp4": "video/mp4",
    "srt": "text/plain; charset=utf-8",
}
FFMPEG_OPTIONS = (
    "-nostdin",
    "-hide_banner",
    "-loglevel",
    "error",
    "-filter_threads",
    "2",
    "-filter_complex_threads",
    "2",
    "-y",
)


class MediaError(Exception):
    pass


class Stopped(MediaError):
    pass


def asset_path(root, name):
    if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
        raise MediaError("Invalid media identifier")
    path = (root / name).resolve()
    if path.parent != root.resolve():
        raise MediaError("Invalid media location")
    return path


def run(args, *, cwd, stop, timeout, process_group=False):
    start = time.monotonic()
    grouped = process_group and os.name != "nt"
    with subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=grouped,
    ) as child:
        try:
            while child.poll() is None:
                if stop():
                    raise Stopped("Job stopped")
                if time.monotonic() - start >= timeout:
                    raise MediaError("Local media operation timed out")
                time.sleep(0.05)
        finally:
            if child.poll() is None:
                if grouped:
                    import signal
                    from contextlib import suppress

                    with suppress(ProcessLookupError):
                        os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
                child.wait()
        if child.returncode:
            raise MediaError("Media could not be decoded or rendered")


def ffmpeg(args, *, cwd, stop, timeout):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), *FFMPEG_OPTIONS]
    for arg in args:
        if arg == "-i":
            command.extend(["-protocol_whitelist", "file,pipe"])
        command.append(str(arg))
    run(command, cwd=cwd, stop=stop, timeout=timeout)


def sanitize_image(source, destination, *, preserve_alpha=False):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source, formats=("PNG", "JPEG", "WEBP")) as image:
                if (
                    image.width * image.height > 16_000_000
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise MediaError("Image must be a single frame under 16 megapixels")
                mode = (
                    "RGBA"
                    if preserve_alpha
                    and ("A" in image.getbands() or "transparency" in image.info)
                    else "RGB"
                )
                frame = ImageOps.exif_transpose(image).convert(mode)
                frame.thumbnail((1536, 1536))
                clean = Image.new(mode, frame.size)
                clean.paste(frame)
                clean.save(destination, optimize=True)
                return mode == "RGBA" and clean.getchannel("A").getextrema()[0] < 255
    except (
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise MediaError("Invalid image format or dimensions") from exc


def sanitize_audio(source, destination, stop, timeout, seconds=600):
    ffmpeg(
        [
            "-f",
            sniff_audio(source),
            "-i",
            source,
            "-map",
            "0:a:0",
            "-t",
            seconds,
            "-ac",
            1,
            "-ar",
            24000,
            "-c:a",
            "pcm_s16le",
            destination,
        ],
        cwd=destination.parent,
        stop=stop,
        timeout=timeout,
    )
    if destination.stat().st_size <= 100:
        raise MediaError("Audio contains no samples")


def audio_duration(path):
    with wave.open(str(path), "rb") as stream:
        return stream.getnframes() / stream.getframerate()


def sanitize_video(source, target, stop, timeout):
    import av

    try:
        with av.open(
            str(source),
            format="mov",
            options={"protocol_whitelist": "file,pipe", "enable_drefs": "0"},
        ) as container:
            video = container.streams.video[0]
            width, height = video.width, video.height
            seconds = float(video.duration * video.time_base) if video.duration else 0
    except (av.FFmpegError, IndexError, TypeError, ValueError) as exc:
        raise MediaError("Use an MP4 or MOV file with a readable video track") from exc
    if not 0 < seconds <= 120 or not 2 <= width <= 4096 or not 2 <= height <= 4096:
        raise MediaError(
            "Footage must be at most 120 seconds and 4096 pixels on either axis"
        )
    ffmpeg(
        [
            "-f",
            "mov",
            "-enable_drefs",
            "0",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-t",
            seconds,
            "-c:v",
            "libx264",
            "-threads",
            2,
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            target,
        ],
        cwd=target.parent,
        stop=stop,
        timeout=timeout,
    )
    return {
        "seconds": seconds,
        "resolution": min(width, height),
        "width": width,
        "height": height,
        "provider": "local",
        "model": "import",
    }


def sniff_audio(path):
    with path.open("rb") as source:
        header = source.read(12)
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "wav"
    if header.startswith(b"fLaC"):
        return "flac"
    if header.startswith(b"ID3") or (
        len(header) > 1 and header[0] == 255 and header[1] & 224 == 224
    ):
        return "mp3"
    raise MediaError("Use WAV, MP3 or FLAC audio")


def composition(payload, paths, target):
    scene, style = payload["scene"], payload["style"]
    size = (1280, 720) if style["aspect"] == "16:9" else (720, 1280)
    if paths:
        with Image.open(paths[0]) as reference:
            ImageOps.fit(reference, size, method=Image.Resampling.LANCZOS).save(target)
        return
    canvas = Image.new("RGB", size, "#172638")
    draw = ImageDraw.Draw(canvas)
    width, height = size
    for y in range(height):
        ratio = y / height
        draw.line(
            (0, y, width, y),
            fill=(int(19 + 28 * ratio), int(27 + 45 * ratio), int(44 + 41 * ratio)),
        )
    draw.ellipse(
        (width * 0.36, height * 0.19, width * 0.69, height * 0.52), fill="#b78a60"
    )
    draw.polygon(
        [
            (0, height * 0.67),
            (width * 0.25, height * 0.44),
            (width * 0.52, height * 0.66),
            (width * 0.78, height * 0.4),
            (width, height * 0.55),
            (width, height),
            (0, height),
        ],
        fill="#172330",
    )
    draw.rectangle(
        (width * 0.06, height * 0.74, width * 0.94, height * 0.93), fill="#111a25"
    )
    font = ImageFont.load_default(size=28)
    draw.multiline_text(
        (width * 0.09, height * 0.78),
        textwrap.fill(scene["title"], width=36)[:165],
        font=font,
        fill="#eef2f4",
        spacing=6,
    )
    canvas.save(target)


def speech(text, language, voice, output, stop, timeout):
    scratch = output.parent / "speech.txt"
    scratch.write_text(text, encoding="utf-8")
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if espeak:
        selected = {"en-US": "en-us", "pt-BR": "pt-br"}[language]
        run(
            [espeak, "-v", selected, "-f", str(scratch), "-w", str(output)],
            cwd=output.parent,
            stop=stop,
            timeout=timeout,
        )
        return
    powershell = shutil.which("powershell.exe") if os.name == "nt" else None
    if not powershell:
        raise MediaError("Install eSpeak NG or configure a speech provider")
    script = output.parent / "speech.ps1"
    script.write_text(
        "param([string]$Locale, [string]$Voice, [string]$Output)\n"
        "$ErrorActionPreference = 'Stop'\nAdd-Type -AssemblyName System.Speech\n"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "$matches = @($s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq $Locale })\n"
        "if ($Voice -ne 'alloy' -and $Voice -ne 'system') { $matches = @($matches | Where-Object { $_.VoiceInfo.Name -eq $Voice }) }\n"
        "if ($matches.Count -eq 0) { throw 'Missing system voice' }\n"
        "$s.SelectVoice($matches[0].VoiceInfo.Name)\n$s.SetOutputToWaveFile($Output)\n"
        "$s.Speak([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'speech.txt')))\n$s.Dispose()\n",
        encoding="utf-8",
    )
    try:
        run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(script),
                language,
                voice,
                str(output),
            ],
            cwd=output.parent,
            stop=stop,
            timeout=timeout,
        )
    except MediaError as exc:
        if isinstance(exc, Stopped):
            raise
        raise MediaError(
            f"System voice unavailable for {language}. Install it or choose a speech provider."
        ) from exc


def motion(image, output, seconds, aspect, stop, timeout, preset=None):
    size = "1280x720" if aspect == "16:9" else "720x1280"
    frames = max(1, math.ceil(seconds * 24))
    width, height = size.split("x")
    progress = f"on/{max(1, frames - 1)}"
    zoom = "min(zoom+0.0007,1.15)" if preset is None else f"1+0.16*{progress}"
    offset = "iw/2-iw/zoom/2"
    if preset == "zoom_out":
        zoom = f"1.16-0.16*{progress}"
    if preset in {"pan_left", "pan_right"}:
        zoom = "1.2"
        position = progress if preset == "pan_right" else f"1-{progress}"
        offset = f"(iw-iw/zoom)*({position})"
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
        f"scale=1920:-2,zoompan=z='{zoom}':x='{offset}':"
        f"y='ih/2-ih/zoom/2':d={frames}:s={size}:fps=24"
    )
    ffmpeg(
        [
            "-f",
            "image2",
            "-i",
            image,
            "-vf",
            vf,
            "-frames:v",
            frames,
            "-an",
            "-c:v",
            "libx264",
            "-threads",
            2,
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


def timestamp(seconds):
    ms = round(seconds * 1000)
    return (
        f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    )


def captions(path, timeline, language):
    elapsed, index = 0, 1
    blocks = []
    for clip in timeline["clips"]:
        voice = clip["audio"][language]
        words = clip.get("captions", {}).get(language, voice["narration"]).split()
        lines = textwrap.wrap(" ".join(words), width=66)
        if lines:
            start = elapsed + clip["narration_offset"]
            span = voice["seconds"] / len(lines)
            for line in lines:
                blocks.append(
                    f"{index}\n{timestamp(start)} --> {timestamp(start + span)}\n{line}\n"
                )
                start += span
                index += 1
        elapsed += clip["seconds"]
    if not write_subtitle_file(path, "\n".join(blocks)):
        raise MediaError("The selected timeline has no subtitle cues")


def webvtt(path):
    """Present the same generated subtitle cues as a native browser track."""
    lines = path.read_text(encoding="utf-8-sig").split("\n\n")
    for index, cue in enumerate(lines):
        parts = cue.split("\n", 2)
        if len(parts) == 3 and parts[0].isdecimal():
            parts[1] = parts[1].replace(",", ".")
            lines[index] = "\n".join(parts)
    return "WEBVTT\n\n" + "\n\n".join(lines)


def render(root, timeline, output, stop, timeout, progress, *, cache_scope=None):
    scratch = output.parent
    height = timeline["resolution"]
    width = height * 16 // 9
    width -= width % 2
    if timeline["aspect"] == "9:16":
        width, height = height, width
    languages = timeline["languages"]
    deadline = time.monotonic() + timeout

    def invoke(args):
        ffmpeg(
            args, cwd=scratch, stop=stop, timeout=max(0.1, deadline - time.monotonic())
        )

    cache = None
    if cache_scope:
        encoder = imageio_ffmpeg.get_ffmpeg_exe()
        with suppress(OSError):
            cache = ClipCache(
                root.parent / "render-cache",
                cache_scope,
                Path(shutil.which(encoder) or encoder),
            )
    draft = None
    if timeline["watermark"]:
        # Bundled builds vary in drawtext support. Burn in a generated RGBA label.
        label = Image.new("RGBA", (140, 42), (0, 0, 0, 170))
        ImageDraw.Draw(label).text(
            (15, 8), "DRAFT", fill="white", font=ImageFont.load_default(size=23)
        )
        label.save(scratch / "draft.png")
        draft = checksum(scratch / "draft.png") if cache else None

    total = 0
    for number, clip in enumerate(timeline["clips"]):
        if stop():
            raise Stopped("Job stopped")
        seconds = clip["seconds"]
        total += seconds
        sources = [asset_path(root, clip["video"])]
        args = ["-ss", clip["trim"], "-f", "mov", "-i", sources[0]]
        for language in languages:
            sources.append(asset_path(root, clip["audio"][language]["media"]))
            args.extend(["-f", "wav", "-i", sources[-1]])
        if clip["effect"]:
            sources.append(asset_path(root, clip["effect"]))
            args.extend(["-f", "wav", "-i", sources[-1]])
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"setsar=1,fps=24,tpad=stop_mode=clone:stop_duration={seconds}"
        )
        if clip["transition"] == "fade":
            fade = min(0.4, seconds / 3)
            vf += f",fade=t=in:d={fade},fade=t=out:st={seconds - fade}:d={fade}"
        if timeline["watermark"]:
            args.extend(["-f", "image2", "-i", "draft.png"])
            label_index = 1 + len(languages) + int(bool(clip["effect"]))
            filters = [f"[0:v]{vf}[base];[base][{label_index}:v]overlay=24:24[v]"]
        else:
            filters = [f"[0:v]{vf}[v]"]
        if clip["effect"]:
            filters.append(
                f"[{1 + len(languages)}:a]apad,asplit={len(languages)}"
                + "".join(f"[effect{i}]" for i in range(len(languages)))
            )
        for i, _language in enumerate(languages):
            chain = (
                f"[{i + 1}:a]adelay={round(clip['narration_offset'] * 1000)}:all=1,apad"
            )
            if clip["effect"]:
                filters.extend(
                    [
                        f"{chain}[speech{i}]",
                        f"[speech{i}][effect{i}]amix=duration=first[a{i}]",
                    ]
                )
            else:
                filters.append(f"{chain}[a{i}]")
        args.extend(["-filter_complex", ";".join(filters), "-map", "[v]"])
        for i in range(len(languages)):
            args.extend(["-map", f"[a{i}]"])
        args.extend(
            [
                "-t",
                seconds,
                "-c:v",
                "libx264",
                "-threads",
                3,
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                48000,
                "-ac",
                2,
            ]
        )
        segment = scratch / f"clip-{number}.mp4"
        key = cache.key([*FFMPEG_OPTIONS, *args], sources, draft) if cache else None
        reused = False
        if key:
            with suppress(OSError):
                reused = cache.restore(key, segment)
        if not reused:
            invoke([*args, segment.name])
            if key:
                cache.remember(key, segment)
        progress(15 + 70 * (number + 1) / len(timeline["clips"]))
    (scratch / "clips.txt").write_text(
        "".join(f"file clip-{i}.mp4\n" for i in range(len(timeline["clips"]))),
        encoding="ascii",
    )
    args = ["-f", "concat", "-safe", "1", "-i", "clips.txt"]
    next_input = 1
    if timeline["music"]:
        args.extend(
            [
                "-stream_loop",
                "-1",
                "-f",
                "wav",
                "-i",
                asset_path(root, timeline["music"]),
            ]
        )
        next_input += 1
    if timeline["subtitles"]:
        for i, language in enumerate(languages):
            captions(scratch / f"captions-{i}.srt", timeline, language)
            args.extend(["-f", "srt", "-i", f"captions-{i}.srt"])
    args.extend(["-map", "0:v", "-c:v", "copy"])
    if timeline["music"]:
        filters = [
            f"[1:a]volume={timeline['music_volume']},asplit={len(languages)}"
            + "".join(f"[m{i}]" for i in range(len(languages)))
        ]
        for i in range(len(languages)):
            filters.append(f"[0:a:{i}][m{i}]amix=duration=first:normalize=0[a{i}]")
        args.extend(["-filter_complex", ";".join(filters)])
        for i in range(len(languages)):
            args.extend(["-map", f"[a{i}]"])
        args.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        args.extend(["-map", "0:a", "-c:a", "copy"])
    tags = {"en-US": "eng", "pt-BR": "por"}
    if timeline["subtitles"]:
        for i in range(len(languages)):
            args.extend(["-map", f"{next_input + i}:s"])
        args.extend(["-c:s", "mov_text"])
    for i, language in enumerate(languages):
        args.extend([f"-metadata:s:a:{i}", f"language={tags[language]}"])
        if timeline["subtitles"]:
            args.extend([f"-metadata:s:s:{i}", f"language={tags[language]}"])
    invoke([*args, "-t", total, "-movflags", "+faststart", output])
    return total


def publish(root, staged):
    name = uuid4().hex + staged.suffix
    if not staged.is_file() or not staged.stat().st_size:
        raise MediaError("Generation produced no media")
    os.replace(staged, asset_path(root, name))
    return name
