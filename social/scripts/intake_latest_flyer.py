#!/usr/bin/env python3
"""
Run the best available flyer intake path from one command.

This wrapper lets the operator use a single entry point while the project keeps
its Gmail and Drive intake logic in separate scripts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from platforms import ROOT


LOGS_DIR = ROOT / "social" / "logs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the latest flyer intake workflow.")
    parser.add_argument(
        "--source",
        default="auto",
        choices=["auto", "drive", "gmail"],
        help="Which intake source to use. Auto tries Drive first, then Gmail.",
    )
    parser.add_argument("--draft", action="store_true", help="Create a draft and staging manifest when possible.")
    parser.add_argument("--title", help="Optional title override.")
    parser.add_argument("--cta", default="See you out here.", help="CTA to use while drafting.")
    parser.add_argument("--tags", default="", help="Comma-separated tags or handles.")
    parser.add_argument(
        "--angle",
        default="event",
        choices=["community", "event", "artist", "gratitude", "announcement"],
        help="Caption angle used while drafting.",
    )

    parser.add_argument("--path", help="Specific Drive file to intake.")
    parser.add_argument(
        "--directory",
        action="append",
        help="Additional Drive directory to search. Use more than once if needed.",
    )
    parser.add_argument("--max-depth", type=int, default=3, help="Drive search depth for default directories.")

    parser.add_argument("--account", default="linusxaviorinc@gmail.com", help="Google account for Gmail intake.")
    parser.add_argument(
        "--query",
        default='in:inbox newer_than:21d has:attachment -category:promotions -category:social',
        help="Gmail query used to find flyer candidates.",
    )
    parser.add_argument("--message-id", help="Specific Gmail message ID to intake.")
    parser.add_argument("--max", type=int, default=10, help="Max Gmail candidates to inspect.")
    return parser.parse_args()


def build_common_args(args: argparse.Namespace) -> list[str]:
    command = []
    if args.title:
        command.extend(["--title", args.title])
    if args.cta:
        command.extend(["--cta", args.cta])
    if args.tags:
        command.extend(["--tags", args.tags])
    if args.angle:
        command.extend(["--angle", args.angle])
    if args.draft:
        command.append("--draft")
    return command


def drive_command(args: argparse.Namespace) -> list[str]:
    command = [sys.executable, "social/scripts/intake_drive_flyer.py"]
    command.extend(build_common_args(args))
    if args.path:
        command.extend(["--path", args.path])
    if args.directory:
        for directory in args.directory:
            command.extend(["--directory", directory])
    if args.max_depth is not None:
        command.extend(["--max-depth", str(args.max_depth)])
    return command


def gmail_command(args: argparse.Namespace) -> list[str]:
    command = [sys.executable, "social/scripts/intake_gmail_flyer.py"]
    command.extend(build_common_args(args))
    if args.account:
        command.extend(["--account", args.account])
    if args.query:
        command.extend(["--query", args.query])
    if args.message_id:
        command.extend(["--message-id", args.message_id])
    if args.max is not None:
        command.extend(["--max", str(args.max)])
    return command


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, check=False, timeout=300)


def record_result(payload: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    target = LOGS_DIR / "instagram-latest-flyer-intake.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def attempt(name: str, command: list[str]) -> tuple[bool, dict]:
    started_at = datetime.now().astimezone().isoformat()
    result = run_command(command)
    payload = {
        "source": name,
        "command": command,
        "started_at": started_at,
        "completed_at": datetime.now().astimezone().isoformat(),
        "returncode": result.returncode,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
    }
    return result.returncode == 0, payload


def main() -> None:
    args = parse_args()
    attempts: list[dict] = []

    if args.source == "drive":
        ok, payload = attempt("drive", drive_command(args))
        attempts.append(payload)
        record_result({"mode": args.source, "success": ok, "attempts": attempts})
        if not ok:
            raise SystemExit(payload["stderr"] or payload["stdout"] or "Drive flyer intake failed.")
        print(payload["stdout"] or "Drive flyer intake complete.")
        return

    if args.source == "gmail":
        ok, payload = attempt("gmail", gmail_command(args))
        attempts.append(payload)
        record_result({"mode": args.source, "success": ok, "attempts": attempts})
        if not ok:
            raise SystemExit(payload["stderr"] or payload["stdout"] or "Gmail flyer intake failed.")
        print(payload["stdout"] or "Gmail flyer intake complete.")
        return

    for name, command in (("drive", drive_command(args)), ("gmail", gmail_command(args))):
        ok, payload = attempt(name, command)
        attempts.append(payload)
        if ok:
            record_result({"mode": args.source, "success": True, "selected_source": name, "attempts": attempts})
            print(payload["stdout"] or f"{name.title()} flyer intake complete.")
            return

    record_result({"mode": args.source, "success": False, "attempts": attempts})
    errors = [attempt["stderr"] or attempt["stdout"] for attempt in attempts if attempt["stderr"] or attempt["stdout"]]
    raise SystemExit("\n\n".join(errors) or "No flyer intake source succeeded.")


if __name__ == "__main__":
    main()
