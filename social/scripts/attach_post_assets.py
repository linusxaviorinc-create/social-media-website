#!/usr/bin/env python3
"""
Attach or replace assets on an existing content draft or approved post.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from platforms import ROOT


CONTENT_DIRS = [
    ROOT / "social" / "content" / "approved",
    ROOT / "social" / "content" / "drafts",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attach real assets to a content draft or approved post.")
    parser.add_argument("--post", help="Path to the markdown post file. Defaults to the latest approved post, then latest draft.")
    parser.add_argument(
        "--asset",
        action="append",
        required=True,
        help="Absolute or workspace-relative path to an asset. Use multiple times for multiple files.",
    )
    return parser.parse_args()


def latest_post_path() -> Path:
    for directory in CONTENT_DIRS:
        posts = sorted(directory.glob("*.md"))
        if posts:
            return posts[-1]
    raise SystemExit("No content posts found.")


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise SystemExit("Post is missing frontmatter.")
    _, rest = text.split("---\n", 1)
    frontmatter_text, body = rest.split("\n---\n", 1)
    return yaml.safe_load(frontmatter_text) or {}, body


def render(frontmatter: dict[str, Any], body: str) -> str:
    rendered_frontmatter = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{rendered_frontmatter}\n---\n{body.lstrip()}"


def normalize_asset(path_str: str) -> str:
    path = Path(path_str)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.exists():
        raise SystemExit(f"Asset not found: {path}")
    return str(path)


def main() -> None:
    args = parse_args()
    post_path = Path(args.post).resolve() if args.post else latest_post_path()
    if not post_path.exists():
        raise SystemExit(f"Post not found: {post_path}")

    frontmatter, body = split_frontmatter(post_path.read_text(encoding="utf-8"))
    assets = [normalize_asset(asset) for asset in args.asset]
    frontmatter["assets"] = assets

    if frontmatter.get("status") == "approved":
        frontmatter["publish_intent"] = "ready"

    post_path.write_text(render(frontmatter, body), encoding="utf-8")
    print(f"Attached {len(assets)} asset(s) to {post_path}")


if __name__ == "__main__":
    main()
