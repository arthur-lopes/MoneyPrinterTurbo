"""Disposable FFmpeg segments, independent of reviewed project artifacts."""

from contextlib import suppress
from hashlib import file_digest, sha256
import json
import os
import re


MAX_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 256
ENTRY = re.compile(r"^[a-f0-9]{64}-[a-f0-9]{64}\.mp4$")


def fingerprint(path):
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns]


def checksum(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


class ClipCache:
    def __init__(self, directory, project, encoder):
        self.directory = directory
        self.project = project
        self.encoder = fingerprint(encoder)
        self.directory.mkdir(mode=0o700, exist_ok=True)

    def key(self, args, sources, watermark):
        # Source media is immutable in the Studio. File stats also detect a
        # missing or externally replaced file before a cached segment can be used.
        try:
            inputs = [fingerprint(path) for path in sources]
        except OSError:
            return None
        payload = [self.project, self.encoder, inputs, watermark, args]
        return sha256(json.dumps(payload, default=str).encode()).hexdigest()

    def restore(self, key, segment):
        for candidate in self.directory.glob(f"{key}-*.mp4"):
            try:
                if checksum(candidate) != candidate.stem.split("-")[-1]:
                    candidate.unlink()
                    continue
                os.link(candidate, segment)
            except OSError:
                continue
            with suppress(OSError):
                os.utime(candidate, None)
            return True
        return False

    def remember(self, key, segment):
        # A cache entry gains its link only after encoding finishes. The working
        # segment stays available even if older entries are evicted concurrently.
        with suppress(OSError):
            if not 0 < segment.stat().st_size <= MAX_BYTES:
                return
            self._prune(segment.stat().st_size)
            os.link(segment, self.directory / f"{key}-{checksum(segment)}.mp4")

    def _prune(self, incoming):
        entries = []
        for entry in self.directory.iterdir():
            if ENTRY.fullmatch(entry.name):
                with suppress(FileNotFoundError):
                    stat = entry.stat()
                    entries.append((stat.st_mtime_ns, stat.st_size, entry))
        size = sum(entry[1] for entry in entries) + incoming
        count = len(entries) + 1
        for _time, length, entry in sorted(entries):
            if size <= MAX_BYTES and count <= MAX_ENTRIES:
                break
            entry.unlink(missing_ok=True)
            size -= length
            count -= 1
