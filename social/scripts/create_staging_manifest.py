#!/usr/bin/env python3
"""
Create a staging manifest from a content draft for review and packaging.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from platforms import ROOT


DRAFTS_DIR = ROOT / "social" / "content" / "drafts"
STAGING_DIR = ROOT / "social" / "content" / "staging"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a staging manifest from a content draft.")
    parser.add_argument("--draft", help="Path to the markdown draft. Defaults to the latest draft.")
    parser.add_argument("--priority", type=int, default=70, help="Priority score for the staging item.")
    return parser.parse_args()


def latest_draft_path() -> Path:
    drafts = sorted(DRAFTS_DIR.glob("*.md"))
    if not drafts:
        raise SystemExit("No content drafts found.")
    return drafts[-1]


def load_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise SystemExit(f"Draft is missing frontmatter: {path}")
    _, rest = text.split("---\n", 1)
    frontmatter_text, _ = rest.split("\n---\n", 1)
    return yaml.safe_load(frontmatter_text) or {}


def main() -> None:
    args = parse_args()
    draft_path = Path(args.draft).resolve() if args.draft else latest_draft_path()
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    frontmatter = load_frontmatter(draft_path)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output_path = STAGING_DIR / f"{stamp}-{draft_path.stem}.json"

    payload = {
        "created_at": datetime.now().astimezone().isoformat(),
        "platform": frontmatter.get("platform", "instagram"),
        "status": "ready_for_review",
        "priority": args.priority,
        "approval_required": True,
        "source_type": "staged_post",
        "title": frontmatter.get("title", draft_path.stem),
        "summary": frontmatter.get("caption", ""),
        "next_step": "Review the caption, assets, and CTA, then move it to approved or stage it in Instagram.",
        "assets": frontmatter.get("assets", []),
        "caption": frontmatter.get("caption", ""),
        "caption_options": frontmatter.get("caption_options", []),
        "source_draft": str(draft_path),
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Saved staging manifest to {output_path}")


if __name__ == "__main__":
    main()
