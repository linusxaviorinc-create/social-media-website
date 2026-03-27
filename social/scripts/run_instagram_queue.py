#!/usr/bin/env python3
"""
Advance the Instagram operator queue by executing safe, non-public next steps.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from platforms import ROOT


LOGS_DIR = ROOT / "social" / "logs"
QUEUE_PATH = LOGS_DIR / "instagram-operator-queue.json"
EXECUTION_LOG = LOGS_DIR / "instagram-latest-queue-run.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run safe next actions from the Instagram operator queue.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh Instagram inbox state and rebuild the queue before taking action.",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=1,
        help="Maximum number of safe actions to execute in one run.",
    )
    return parser.parse_args()


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def refresh_queue() -> list[dict[str, Any]]:
    runs = []
    for script in [
        ("social/scripts/check_instagram.py",),
        ("social/scripts/instagram_operator_queue.py",),
        ("social/scripts/refresh_operator_artifacts.py",),
    ]:
        result = run_script(*script)
        runs.append(
            {
                "command": " ".join(script),
                "returncode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        )
        if result.returncode != 0:
            raise SystemExit(f"Failed to refresh queue with {' '.join(script)}: {result.stderr.strip()}")
    return runs


def load_queue() -> dict[str, Any]:
    if not QUEUE_PATH.exists():
        raise SystemExit("Instagram operator queue not found. Run instagram_operator_queue.py first.")
    return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))


def load_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    _, rest = text.split("---\n", 1)
    frontmatter_text, _ = rest.split("\n---\n", 1)
    return yaml.safe_load(frontmatter_text) or {}


def stageable_assets(draft_path: Path) -> bool:
    if not draft_path.exists():
        return False
    frontmatter = load_frontmatter(draft_path)
    assets = frontmatter.get("assets", [])
    allowed_suffixes = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".mp4",
        ".mov",
        ".m4v",
    }
    if not assets:
        return False
    for asset in assets:
        asset_path = Path(asset)
        if not asset_path.exists():
            return False
        if asset_path.name.startswith("."):
            return False
        if asset_path.suffix.lower() not in allowed_suffixes:
            return False
    return True


def item_priority(item: dict[str, Any]) -> tuple[int, str]:
    item_type = item.get("type")
    action = item.get("action", "")
    status = item.get("status", "")

    if item_type == "dm_triage" and action == "draft_reply":
        return (0, action)
    if item_type == "partnership_triage" and action == "draft_partnership_reply":
        return (1, action)
    if item_type == "story_reshare" and status == "ready":
        return (2, action)
    if item_type == "asset_intake" and action == "draft_from_asset":
        return (3, action)
    if item_type == "content_post" and status == "ready":
        return (4, action)
    return (9, action)


def resolve_command(item: dict[str, Any]) -> list[str] | None:
    item_type = item.get("type")
    action = item.get("action")

    if item_type == "dm_triage" and action == "draft_reply":
        match_text = item.get("thread_label")
        if not match_text:
            return None
        return ["social/scripts/draft_instagram_dm_reply.py", "--match", match_text]

    if item_type == "partnership_triage" and action == "draft_partnership_reply":
        match_text = item.get("thread_label")
        if not match_text:
            return None
        return ["social/scripts/draft_instagram_dm_reply.py", "--match", match_text]

    if item_type == "story_reshare" and action == "prepare_reshare" and item.get("status") == "ready":
        return ["social/scripts/prepare_instagram_story_reshare.py"]

    if item_type == "asset_intake" and action == "draft_from_asset":
        draft_path = item.get("draft")
        if not draft_path:
            return None
        return ["social/scripts/intake_latest_asset.py", "--asset", draft_path]

    if item_type == "content_post" and action == "stage_or_share_post" and item.get("status") == "ready":
        draft_path = item.get("draft")
        if not draft_path:
            return None
        if not stageable_assets(Path(draft_path)):
            return None
        return ["social/scripts/stage_instagram_post.py", "--draft", draft_path]

    return None


def main() -> None:
    args = parse_args()
    refresh_runs: list[dict[str, Any]] = []
    if args.refresh:
        refresh_runs = refresh_queue()

    queue = load_queue()
    executed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in sorted(queue.get("items", []), key=item_priority):
        if len(executed) >= args.max_actions:
            break

        command = resolve_command(item)
        if not command:
            skipped.append(
                {
                    "item_type": item.get("type"),
                    "action": item.get("action"),
                    "status": item.get("status"),
                    "draft": item.get("draft"),
                    "reason": "no_safe_command",
                }
            )
            continue

        try:
            result = run_script(*command)
        except subprocess.TimeoutExpired:
            executed.append(
                {
                    "item_type": item.get("type"),
                    "action": item.get("action"),
                    "status": item.get("status"),
                    "command": " ".join(command),
                    "returncode": 124,
                    "stdout": "",
                    "stderr": "Timed out after 180 seconds.",
                }
            )
            continue
        record = {
            "item_type": item.get("type"),
            "action": item.get("action"),
            "status": item.get("status"),
            "command": " ".join(command),
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
        executed.append(record)

    queue_refresh_runs = refresh_queue()

    payload = {
        "executed_at": datetime.now().astimezone().isoformat(),
        "max_actions": args.max_actions,
        "refresh_requested": args.refresh,
        "refresh_runs": refresh_runs,
        "executed": executed,
        "skipped": skipped[:20],
        "post_action_refresh_runs": queue_refresh_runs,
    }
    EXECUTION_LOG.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Executed {len(executed)} safe queue action(s).")
    if executed:
        for item in executed:
            print(f"- {item['command']} -> {item['returncode']}")


if __name__ == "__main__":
    main()
