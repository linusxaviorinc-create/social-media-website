#!/usr/bin/env python3
"""
Turn the latest asset in social/assets into a draft post and staging manifest.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from platforms import ROOT


ASSETS_DIR = ROOT / "social" / "assets"
INTAKE_DIR = ROOT / "social" / "content" / "intake"
LOGS_DIR = ROOT / "social" / "logs"
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".m4v", ".pdf"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a draft/staging package from the latest asset.")
    parser.add_argument("--asset", help="Specific asset to intake. Defaults to the latest supported asset in social/assets.")
    parser.add_argument("--title", help="Optional internal title override.")
    parser.add_argument("--context", default="", help="Optional short context for the post.")
    parser.add_argument(
        "--angle",
        default="community",
        choices=["community", "event", "artist", "gratitude", "announcement"],
        help="Caption angle to use while drafting.",
    )
    parser.add_argument("--cta", default="", help="Optional CTA to include in the draft.")
    parser.add_argument("--tags", default="", help="Comma-separated tags or handles.")
    return parser.parse_args()


def latest_asset_path() -> Path:
    candidates = [
        path
        for path in ASSETS_DIR.rglob("*")
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    if not candidates:
        raise SystemExit("No supported assets found in social/assets.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def normalize_asset(value: str | None) -> Path:
    if value:
        path = Path(value)
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        return path
    return latest_asset_path()


def default_title(asset_path: Path) -> str:
    stem = asset_path.stem.replace("_", " ").replace("-", " ").strip()
    stem = " ".join(stem.split())
    if not stem:
        return "New social post"
    return stem[0].upper() + stem[1:]


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def newest_path(directory: Path, pattern: str, before: set[Path]) -> Path | None:
    current = set(directory.glob(pattern))
    created = sorted(current - before)
    return created[-1] if created else None


def main() -> None:
    args = parse_args()
    asset_path = normalize_asset(args.asset)
    if not asset_path.exists():
        raise SystemExit(f"Asset not found: {asset_path}")

    title = args.title.strip() if args.title else default_title(asset_path)
    draft_before = set((ROOT / "social" / "content" / "drafts").glob("*.md"))
    manifest_before = set((ROOT / "social" / "content" / "staging").glob("*.json"))

    draft_result = run_script(
        "social/scripts/draft_asset_post.py",
        "--title",
        title,
        "--asset",
        str(asset_path),
        "--angle",
        args.angle,
        "--context",
        args.context,
        "--cta",
        args.cta,
        "--tags",
        args.tags,
    )
    if draft_result.returncode != 0:
        raise SystemExit(draft_result.stderr.strip() or draft_result.stdout.strip() or "Failed to draft asset post.")

    draft_path = newest_path(ROOT / "social" / "content" / "drafts", "*.md", draft_before)
    if not draft_path:
        raise SystemExit("Draft script completed but no new content draft was found.")

    manifest_result = run_script("social/scripts/create_staging_manifest.py", "--draft", str(draft_path))
    if manifest_result.returncode != 0:
        raise SystemExit(
            manifest_result.stderr.strip() or manifest_result.stdout.strip() or "Failed to create staging manifest."
        )

    manifest_path = newest_path(ROOT / "social" / "content" / "staging", "*.json", manifest_before)
    if not manifest_path:
        raise SystemExit("Staging manifest script completed but no new manifest was found.")

    payload: dict[str, Any] = {
        "created_at": datetime.now().astimezone().isoformat(),
        "asset": str(asset_path),
        "title": title,
        "angle": args.angle,
        "context": args.context.strip(),
        "cta": args.cta.strip(),
        "tags": [tag.strip() for tag in args.tags.split(",") if tag.strip()],
        "draft_path": str(draft_path),
        "staging_manifest": str(manifest_path),
    }
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    (LOGS_DIR / "instagram-latest-asset-intake.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Created asset intake package for {asset_path}")


if __name__ == "__main__":
    main()
