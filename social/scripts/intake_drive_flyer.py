#!/usr/bin/env python3
"""
Find likely flyer/media assets in the local Google Drive Desktop mount and turn
them into a social draft plus staging manifest when requested.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from platforms import ROOT


DRIVE_ROOT = Path.home() / "Library" / "CloudStorage" / "GoogleDrive-linus@giantrockmeetingroom.com" / "My Drive"
DEFAULT_DIRECTORIES = [
    DRIVE_ROOT / "GRMR Mac Docs" / "Marketing&Graphics" / "INSTAGRAM FLYERS",
    DRIVE_ROOT / "GRMR Mac Docs" / "Marketing&Graphics" / "Flyers",
    DRIVE_ROOT / "GRMR Mac Docs" / "Events",
]
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf", ".webp"}
ASSET_DROP_DIR = ROOT / "social" / "assets" / "drive-flyers"
INTAKE_DIR = ROOT / "social" / "content" / "intake"
LOGS_DIR = ROOT / "social" / "logs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Intake a likely flyer from the local Google Drive mount.")
    parser.add_argument("--path", help="Specific Drive file to intake.")
    parser.add_argument("--title", help="Optional internal title override.")
    parser.add_argument("--max-depth", type=int, default=3, help="Search depth for the default directories.")
    parser.add_argument(
        "--directory",
        action="append",
        help="Additional directory to search. Use more than once if needed.",
    )
    parser.add_argument("--cta", default="See you out here.", help="CTA to use for draft creation.")
    parser.add_argument("--tags", default="", help="Comma-separated tags or handles.")
    parser.add_argument(
        "--angle",
        default="event",
        choices=["community", "event", "artist", "gratitude", "announcement"],
        help="Caption angle to use while drafting.",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Create a content draft and staging manifest from the selected asset.",
    )
    return parser.parse_args()


def slugify(value: str) -> str:
    lowered = value.lower().strip()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-") or "drive-flyer"


def titleize_stem(path: Path) -> str:
    stem = path.stem.replace("_", " ").replace("-", " ").strip()
    stem = re.sub(r"\s+", " ", stem)
    return stem or "Drive flyer"


def candidate_paths(args: argparse.Namespace) -> list[Path]:
    if args.path:
        path = Path(args.path).expanduser()
        if not path.exists():
            raise SystemExit(f"Drive path not found: {path}")
        return [path]

    directories = list(DEFAULT_DIRECTORIES)
    if args.directory:
        directories.extend(Path(value).expanduser() for value in args.directory)

    candidates: list[Path] = []
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            relative_parts = path.relative_to(directory).parts
            if len(relative_parts) > args.max_depth + 1:
                continue
            candidates.append(path)
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)


def choose_candidate(args: argparse.Namespace) -> Path:
    candidates = candidate_paths(args)
    if not candidates:
        raise SystemExit("No Drive flyer/media candidates found.")
    return candidates[0]


def copy_asset(source: Path, title: str) -> Path:
    ASSET_DROP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    target_dir = ASSET_DROP_DIR / f"{timestamp}-{slugify(title)[:60]}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"01-{slugify(source.stem)[:60]}{source.suffix.lower()}"
    target_path.write_bytes(source.read_bytes())
    return target_path


def build_context(source: Path) -> str:
    parent = source.parent.name
    grandparent = source.parent.parent.name if source.parent.parent != source.parent else ""
    lines = [
        f"Drive source: {source}",
        f"Source folder: {parent}",
    ]
    if grandparent:
        lines.append(f"Source folder parent: {grandparent}")
    return "\n".join(lines)


def newest_path(directory: Path, pattern: str, before: set[Path]) -> Path | None:
    current = set(directory.glob(pattern))
    created = sorted(current - before)
    return created[-1] if created else None


def create_draft(title: str, asset: Path, context: str, cta: str, angle: str, tags: str) -> tuple[str | None, str | None]:
    drafts_dir = ROOT / "social" / "content" / "drafts"
    staging_dir = ROOT / "social" / "content" / "staging"
    draft_before = set(drafts_dir.glob("*.md"))
    staging_before = set(staging_dir.glob("*.json"))
    draft_result = subprocess.run(
        [
            sys.executable,
            "social/scripts/draft_asset_post.py",
            "--title",
            title,
            "--asset",
            str(asset),
            "--angle",
            angle,
            "--context",
            context,
            "--cta",
            cta,
            "--tags",
            tags,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    if draft_result.returncode != 0:
        raise RuntimeError(draft_result.stderr.strip() or draft_result.stdout.strip() or "Draft creation failed.")
    draft_path = newest_path(drafts_dir, "*.md", draft_before)
    if not draft_path:
        return None, None

    manifest_result = subprocess.run(
        [sys.executable, "social/scripts/create_staging_manifest.py", "--draft", str(draft_path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    if manifest_result.returncode != 0:
        raise RuntimeError(
            manifest_result.stderr.strip() or manifest_result.stdout.strip() or "Staging manifest creation failed."
        )
    staging_path = newest_path(staging_dir, "*.json", staging_before)
    return str(draft_path), str(staging_path) if staging_path else None


def main() -> None:
    args = parse_args()
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    source = choose_candidate(args)
    title = args.title.strip() if args.title else titleize_stem(source)
    copied_asset = copy_asset(source, title)
    context = build_context(source)

    draft_path = None
    staging_manifest = None
    if args.draft:
        draft_path, staging_manifest = create_draft(title, copied_asset, context, args.cta, args.angle, args.tags)

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    intake_path = INTAKE_DIR / f"{timestamp}-{slugify(title)}-drive-flyer.json"
    payload = {
        "created_at": datetime.now().astimezone().isoformat(),
        "source": "drive_flyer",
        "title": title,
        "source_path": str(source),
        "copied_asset": str(copied_asset),
        "draft_path": draft_path,
        "staging_manifest": staging_manifest,
        "context": context,
    }
    intake_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (LOGS_DIR / "instagram-latest-drive-flyer-intake.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved Drive flyer intake to {intake_path}")
    if draft_path:
        print(f"Created draft at {draft_path}")


if __name__ == "__main__":
    main()
