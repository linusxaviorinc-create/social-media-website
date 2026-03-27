#!/usr/bin/env python3
"""
Move a content draft into the approved lane and update its frontmatter.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from platforms import ROOT


DRAFTS_DIR = ROOT / "social" / "content" / "drafts"
APPROVED_DIR = ROOT / "social" / "content" / "approved"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Approve a content draft for posting.")
    parser.add_argument("--draft", help="Path to the markdown draft. Defaults to the latest draft.")
    return parser.parse_args()


def latest_draft_path() -> Path:
    drafts = sorted(DRAFTS_DIR.glob("*.md"))
    if not drafts:
        raise SystemExit("No content drafts found.")
    return drafts[-1]


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise SystemExit("Draft is missing frontmatter.")
    _, rest = text.split("---\n", 1)
    frontmatter_text, body = rest.split("\n---\n", 1)
    frontmatter = yaml.safe_load(frontmatter_text) or {}
    return frontmatter, body


def render(frontmatter: dict, body: str) -> str:
    rendered_frontmatter = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{rendered_frontmatter}\n---\n{body.lstrip()}"


def main() -> None:
    args = parse_args()
    draft_path = Path(args.draft).resolve() if args.draft else latest_draft_path()
    if not draft_path.exists():
        raise SystemExit(f"Draft not found: {draft_path}")

    APPROVED_DIR.mkdir(parents=True, exist_ok=True)

    frontmatter, body = split_frontmatter(draft_path.read_text(encoding="utf-8"))
    frontmatter["status"] = "approved"
    frontmatter["publish_intent"] = "ready"

    target_path = APPROVED_DIR / draft_path.name
    target_path.write_text(render(frontmatter, body), encoding="utf-8")

    if draft_path != target_path and draft_path.exists():
        draft_path.unlink()

    print(f"Approved content draft: {target_path}")


if __name__ == "__main__":
    main()
