"""Read host credentials or store operator-provided keys outside the database."""

import hashlib
import os
from tempfile import NamedTemporaryFile


KEYS = {
    "openai": "MPT_PRODUCTION_OPENAI_KEY",
    "runway": "MPT_PRODUCTION_RUNWAY_KEY",
    "elevenlabs": "MPT_PRODUCTION_ELEVENLABS_KEY",
}
MANAGED = {"elevenlabs", "runway"}


def get(directory, provider):
    configured = os.getenv(KEYS[provider], "").strip()
    if configured or provider not in MANAGED:
        return configured
    try:
        return (
            (directory / ".provider-credentials" / provider)
            .read_text(encoding="ascii")
            .strip()
        )
    except FileNotFoundError:
        return ""


def revision(key):
    return hashlib.sha256(key.encode()).hexdigest() if key else ""


def save(directory, provider, key):
    if provider not in MANAGED:
        raise ValueError("Unsupported managed provider")
    target = directory / ".provider-credentials"
    target.mkdir(mode=0o700, exist_ok=True)
    with NamedTemporaryFile(dir=target, delete=False) as output:
        staged = target / os.path.basename(output.name)
        try:
            output.write(key.encode("ascii"))
            output.close()
            staged.replace(target / provider)
        finally:
            staged.unlink(missing_ok=True)
