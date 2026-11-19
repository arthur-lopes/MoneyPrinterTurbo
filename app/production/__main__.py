"""Run the production workspace on loopback."""

import argparse
import os
from pathlib import Path

import uvicorn

from app.paths import runtime_root

from .api import create_app


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mpt production")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--data-dir", type=Path, default=runtime_root() / "storage" / "production"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    token = os.getenv("MPT_PRODUCTION_ACCESS_TOKEN", "")
    if not 32 <= len(token) <= 256 or not token.isascii():
        parser.error(
            "Set MPT_PRODUCTION_ACCESS_TOKEN to a random ASCII value of at least 32 characters"
        )
    actor = os.getenv("MPT_PRODUCTION_OPERATOR", "local-operator").strip()
    if not actor or len(actor) > 80:
        parser.error("Set a valid MPT_PRODUCTION_OPERATOR identity")
    uvicorn.run(
        create_app(args.data_dir, {token: actor}),
        host="127.0.0.1",
        port=args.port,
        proxy_headers=False,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
