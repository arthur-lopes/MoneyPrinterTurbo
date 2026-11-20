"""Inspect and propose edits with a project credential."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import httpx

from .agent import bearer_digest


def project_url(value):
    url = urlsplit(value)
    local = url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not (url.scheme == "https" or local)
        or not url.hostname
        or url.username is not None
        or url.query
        or url.fragment
        or not re.fullmatch(r"/api/agent/v1/projects/[a-f0-9]{32}", url.path)
    ):
        raise argparse.ArgumentTypeError("Use the project API URL from Agent access")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-url", required=True, type=project_url)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("context", help="Read the project and current cut")
    commands.add_parser(
        "evidence", help="Read the research bound to a scene"
    ).add_argument("scene")
    commands.add_parser("notes", help="Read review notes on an assembly").add_argument(
        "assembly"
    )
    for command in ("validate", "submit"):
        commands.add_parser(
            command, help=f"{command.capitalize()} an edit plan"
        ).add_argument("plan", type=Path, help="JSON edit plan")
    commands.add_parser(
        "status", help="Read a submitted proposal and its decision"
    ).add_argument("proposal", help="Proposal ID")
    args = parser.parse_args(argv)
    header = "Bearer " + os.getenv("MPT_AGENT_TOKEN", "")
    if not bearer_digest(header):
        parser.error("Set MPT_AGENT_TOKEN to the project's agent token")

    try:
        with httpx.Client(
            headers={"Authorization": header}, follow_redirects=False, timeout=40
        ) as client:
            if args.command == "context":
                response = client.get(args.project_url)
            elif args.command == "evidence":
                if not re.fullmatch(r"[a-f0-9]{32}", args.scene):
                    parser.error("Use a scene ID returned by context")
                response = client.get(
                    f"{args.project_url}/scenes/{args.scene}/evidence"
                )
            elif args.command == "notes":
                if not re.fullmatch(r"[a-f0-9]{32}", args.assembly):
                    parser.error("Use the assembly ID returned by context")
                response = client.get(
                    f"{args.project_url}/assemblies/{args.assembly}/notes"
                )
            elif args.command == "status":
                if not re.fullmatch(r"[a-f0-9]{32}", args.proposal):
                    parser.error("Use the proposal ID returned by submit")
                response = client.get(f"{args.project_url}/proposals/{args.proposal}")
            else:
                plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
                if not isinstance(plan, dict):
                    parser.error("The edit plan must be a JSON object")
                endpoint = args.project_url + "/proposals"
                if args.command == "validate":
                    endpoint += "/validate"
                    plan.pop("request_id", None)
                    plan.pop("expected_fingerprint", None)
                response = client.post(endpoint, json=plan)
            result = response.json()
            if not response.is_success:
                detail = result.get("detail") if isinstance(result, dict) else None
                print(
                    f"HTTP {response.status_code}: {detail or 'Request failed'}",
                    file=sys.stderr,
                )
                return 1
    except (OSError, ValueError, httpx.RequestError) as error:
        print(
            f"Could not complete the request ({type(error).__name__})", file=sys.stderr
        )
        return 1

    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
