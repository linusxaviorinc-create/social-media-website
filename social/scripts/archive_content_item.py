#!/usr/bin/env python3
"""
Archive a content or manifest file out of the active workflow lanes.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from platforms import ROOT


CONTENT_ROOT = ROOT / "social" / "content"
ARCHIVE_ROOT = CONTENT_ROOT / "archive"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive a content item from the active queue.")
    parser.add_argument("--path", required=True, help="Path to the content or manifest file to archive.")
    return parser.parse_args()


def archive_target(path: Path) -> Path:
    try:
        relative = path.resolve().relative_to(CONTENT_ROOT.resolve())
    except ValueError as exc:
        raise SystemExit(f"Path is outside content root: {path}") from exc
    target = ARCHIVE_ROOT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def main() -> None:
    args = parse_args()
    path = Path(args.path)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    target = archive_target(path)
    path.rename(target)
    print(f"Archived {path} -> {target}")


if __name__ == "__main__":
    main()
