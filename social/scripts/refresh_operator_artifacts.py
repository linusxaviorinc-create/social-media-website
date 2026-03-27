#!/usr/bin/env python3
"""
Render human-readable operator artifacts from the current Instagram queue and logs.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from platforms import ROOT


SOCIAL_DIR = ROOT / "social"
LOGS_DIR = SOCIAL_DIR / "logs"
OPERATOR_DIR = SOCIAL_DIR / "operator"
CARDS_DIR = OPERATOR_DIR / "cards"
QUEUE_JSON_PATH = LOGS_DIR / "instagram-operator-queue.json"
QUEUE_MD_PATH = OPERATOR_DIR / "operator-queue.md"
QUEUE_COPY_PATH = OPERATOR_DIR / "operator-queue.json"
PROGRESS_PATH = OPERATOR_DIR / "progress-latest.md"
QUEUE_RUN_PATH = LOGS_DIR / "instagram-latest-queue-run.json"


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_dirs() -> None:
    OPERATOR_DIR.mkdir(parents=True, exist_ok=True)
    CARDS_DIR.mkdir(parents=True, exist_ok=True)


def summarize_item(item: dict[str, Any]) -> str:
    details = item.get("details")
    if isinstance(details, str):
        return details
    if isinstance(details, dict):
        if details.get("summary"):
            return details["summary"]
        if details.get("recommended_action"):
            return f"Recommended action: {details['recommended_action']}."
        if details.get("title"):
            lane = details.get("lane")
            return f"{details['title']}" + (f" ({lane})" if lane else "")
    return "Needs review."


def title_for_item(item: dict[str, Any]) -> str:
    details = item.get("details")
    if item.get("thread_label"):
        return item["thread_label"]
    if isinstance(details, dict) and details.get("title"):
        return str(details["title"])
    if item.get("handle"):
        return str(item["handle"])
    if item.get("draft"):
        draft_path = Path(str(item["draft"]))
        return draft_path.stem
    return str(item.get("type", "item"))


def slugify(value: str) -> str:
    lowered = value.lower()
    cleaned = []
    last_dash = False
    for char in lowered:
        if char.isalnum():
            cleaned.append(char)
            last_dash = False
            continue
        if not last_dash:
            cleaned.append("-")
            last_dash = True
    return "".join(cleaned).strip("-") or "item"


def card_filename(index: int, item: dict[str, Any]) -> str:
    title = title_for_item(item)
    return f"{index:02d}-{slugify(item.get('status', 'item'))}-{slugify(title)[:72]}.md"


def render_item_card(index: int, item: dict[str, Any]) -> str:
    lines = [
        f"# {title_for_item(item)}",
        "",
        f"- Queue position: {index}",
        f"- Status: {item.get('status', 'unknown')}",
        f"- Action: {item.get('action', 'review')}",
        f"- Type: {item.get('type', 'item')}",
        f"- Summary: {summarize_item(item)}",
    ]
    if item.get("policy"):
        lines.append(f"- Policy: {item['policy']}")

    details = item.get("details")
    if isinstance(details, dict):
        if details.get("source_draft"):
            lines.append(f"- Source draft: {details['source_draft']}")
        if details.get("debug_reason"):
            lines.append(f"- Debug reason: {details['debug_reason']}")
        if details.get("debug_screenshot"):
            lines.append(f"- Debug screenshot: {details['debug_screenshot']}")
        if details.get("recommended_action"):
            lines.append(f"- Recommended action: {details['recommended_action']}")
        if details.get("story_unavailable") is not None:
            lines.append(f"- Story unavailable: {details['story_unavailable']}")
        if details.get("has_add_to_story") is not None:
            lines.append(f"- Has add to story: {details['has_add_to_story']}")

    if item.get("draft"):
        lines.append(f"- Draft: {item['draft']}")
    if item.get("thread_label"):
        lines.append(f"- Thread label: {item['thread_label']}")
    if item.get("handle"):
        lines.append(f"- Handle: {item['handle']}")

    lines.extend(
        [
            "",
            "## Next step",
            "",
            f"`{item.get('action', 'review')}`",
            "",
        ]
    )
    return "\n".join(lines)


def render_queue_markdown(queue: dict[str, Any]) -> str:
    lines = [
        f"# Operator Queue ({queue['generated_at']})",
        "",
        f"Total items: {queue['summary']['total_items']}",
        "",
    ]

    for index, item in enumerate(queue.get("items", []), start=1):
        title = title_for_item(item)
        status = item.get("status", "unknown")
        action = item.get("action", "review")
        lines.extend(
            [
                f"{index}. [{status}] {title}",
                f"   Action: {action}",
                f"   Summary: {summarize_item(item)}",
            ]
        )
        details = item.get("details")
        if isinstance(details, dict) and details.get("source_draft"):
            lines.append(f"   Source draft: {details['source_draft']}")
        if isinstance(details, dict) and details.get("debug_screenshot"):
            lines.append(f"   Debug screenshot: {details['debug_screenshot']}")
        if item.get("policy"):
            lines.append(f"   Policy: {item['policy']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_progress_markdown(queue: dict[str, Any], queue_run: dict[str, Any] | None) -> str:
    lines = [
        "# Progress Update",
        "",
        f"Generated: {datetime.now().astimezone().isoformat()}",
        "",
        "## Current state",
        f"- Queue items: {queue['summary']['total_items']}",
        f"- Ready: {queue['summary']['ready']}",
        f"- Review: {queue['summary']['review']}",
        f"- Blocked: {queue['summary']['blocked']}",
        "",
    ]

    if queue_run:
        executed = queue_run.get("executed", [])
        if executed:
            lines.append("## Last queue run")
            for item in executed[:5]:
                result = "ok" if item.get("returncode") == 0 else f"failed ({item.get('returncode')})"
                lines.append(f"- `{item.get('command')}`: {result}")
            lines.append("")

    next_items = [item for item in queue.get("items", []) if item.get("status") != "done"][:3]
    lines.append("## Next up")
    if next_items:
        for item in next_items:
            lines.append(f"- `{item.get('action', 'review')}` on {title_for_item(item)}")
    else:
        lines.append("- No queued work yet.")
    lines.append("")

    return "\n".join(lines)


def render_cards(queue: dict[str, Any]) -> None:
    active_paths: set[Path] = set()
    for index, item in enumerate(queue.get("items", []), start=1):
        filename = card_filename(index, item)
        path = CARDS_DIR / filename
        path.write_text(render_item_card(index, item), encoding="utf-8")
        active_paths.add(path)

    for existing in CARDS_DIR.glob("*.md"):
        if existing not in active_paths:
            existing.unlink()


def main() -> None:
    ensure_dirs()
    queue = load_json(QUEUE_JSON_PATH)
    if not queue:
        raise SystemExit("Operator queue not found. Run instagram_operator_queue.py first.")

    queue_run = load_json(QUEUE_RUN_PATH)
    QUEUE_COPY_PATH.write_text(json.dumps(queue, indent=2) + "\n", encoding="utf-8")
    QUEUE_MD_PATH.write_text(render_queue_markdown(queue), encoding="utf-8")
    PROGRESS_PATH.write_text(render_progress_markdown(queue, queue_run), encoding="utf-8")
    render_cards(queue)
    print(f"Refreshed operator artifacts in {OPERATOR_DIR}")


if __name__ == "__main__":
    main()
