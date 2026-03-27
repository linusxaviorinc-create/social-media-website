#!/usr/bin/env python3
"""
Promote a reviewed staging manifest into the approved content lane.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from platforms import ROOT


STAGING_DIR = ROOT / "social" / "content" / "staging"
APPROVED_DIR = ROOT / "social" / "content" / "approved"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Approve a staging manifest into the approved content lane.")
    parser.add_argument("--manifest", help="Path to the staging manifest. Defaults to the latest one.")
    return parser.parse_args()


def latest_manifest_path() -> Path:
    manifests = sorted(STAGING_DIR.glob("*.json"))
    if not manifests:
        raise SystemExit("No staging manifests found.")
    return manifests[-1]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise SystemExit("Draft is missing frontmatter.")
    _, rest = text.split("---\n", 1)
    frontmatter_text, body = rest.split("\n---\n", 1)
    frontmatter = yaml.safe_load(frontmatter_text) or {}
    return frontmatter, body


def render(frontmatter: dict[str, Any], body: str) -> str:
    rendered_frontmatter = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{rendered_frontmatter}\n---\n{body.lstrip()}"


def fallback_body(manifest: dict[str, Any]) -> str:
    lines = [
        "## Hook",
        "",
        manifest.get("title", ""),
        "",
        "## Caption",
        "",
        manifest.get("caption", ""),
        "",
        "## CTA",
        "",
        "See you out here.",
        "",
        "## Approval Notes",
        "",
        f"Promoted from staging manifest: {manifest.get('title', '')}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    manifest_path = Path(args.manifest).resolve() if args.manifest else latest_manifest_path()
    if not manifest_path.exists():
        raise SystemExit(f"Staging manifest not found: {manifest_path}")

    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_json(manifest_path)
    source_draft = manifest.get("source_draft")
    source_path = Path(source_draft).resolve() if source_draft else None

    if source_path and source_path.exists():
        frontmatter, body = split_frontmatter(source_path.read_text(encoding="utf-8"))
    else:
        frontmatter, body = {}, fallback_body(manifest)

    if manifest.get("title"):
        frontmatter["title"] = manifest["title"]
    if manifest.get("platform"):
        frontmatter["platform"] = manifest["platform"]
    if manifest.get("assets"):
        frontmatter["assets"] = manifest["assets"]
    if manifest.get("caption"):
        frontmatter["caption"] = manifest["caption"]
    if manifest.get("caption_options"):
        frontmatter["caption_options"] = manifest["caption_options"]

    frontmatter["status"] = "approved"
    frontmatter["publish_intent"] = "ready"
    frontmatter["source_staging_manifest"] = str(manifest_path)

    target_name = source_path.name if source_path else f"{manifest_path.stem}.md"
    target_path = APPROVED_DIR / target_name
    target_path.write_text(render(frontmatter, body), encoding="utf-8")

    manifest["status"] = "approved"
    manifest["approval_required"] = False
    manifest["next_step"] = "Stage this approved post in Instagram when ready."
    manifest["approved_draft"] = str(target_path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Approved staging manifest into {target_path}")


if __name__ == "__main__":
    main()
