#!/usr/bin/env python3
"""
Create a post draft from one or more local media assets and optional context.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from platforms import ROOT


CONTENT_DIR = ROOT / "social" / "content" / "drafts"
INTAKE_DIR = ROOT / "social" / "content" / "intake"
VOICE_PATH = ROOT / "social" / "voice" / "brand-voice.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Draft a social post from local assets.")
    parser.add_argument("--platform", default="instagram", help="Target platform.")
    parser.add_argument("--title", required=True, help="Short internal title for the post.")
    parser.add_argument(
        "--asset",
        action="append",
        required=True,
        help="Absolute or workspace-relative path to an image/video/document asset. Use multiple times for multi-asset posts.",
    )
    parser.add_argument("--context", default="", help="Optional notes about the asset or event.")
    parser.add_argument("--context-file", help="Optional text or markdown file with longer notes for the post.")
    parser.add_argument("--cta", default="", help="Optional call to action.")
    parser.add_argument("--tags", default="", help="Comma-separated handles or hashtags to consider.")
    parser.add_argument(
        "--angle",
        default="community",
        choices=["community", "event", "artist", "gratitude", "announcement"],
        help="Primary posting angle to shape the caption.",
    )
    return parser.parse_args()


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "post"


def normalize_asset(path_str: str) -> str:
    path = Path(path_str)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return str(path)


def load_context(args: argparse.Namespace) -> str:
    pieces: list[str] = []
    if args.context.strip():
        pieces.append(args.context.strip())
    if args.context_file:
        context_path = Path(args.context_file)
        if not context_path.is_absolute():
            context_path = (ROOT / context_path).resolve()
        if not context_path.exists():
            raise SystemExit(f"Context file not found: {context_path}")
        pieces.append(context_path.read_text(encoding="utf-8").strip())
    return "\n\n".join(piece for piece in pieces if piece).strip()


def sentence_case(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def clean_sentence(text: str) -> str:
    text = sentence_case(text)
    if not text:
        return ""
    if text[-1] not in ".!?":
        text += "."
    return text


def humanize_asset_name(path_str: str) -> str:
    stem = Path(path_str).stem
    stem = stem.lstrip(".")
    stem = re.sub(r"[_-]+", " ", stem).strip()
    return sentence_case(stem) or "Uploaded asset"


def summarize_context(context: str) -> str:
    if not context:
        return ""
    normalized = re.sub(r"\s+", " ", context).strip()
    return normalized[:220].rstrip()


def build_hook(title: str, angle: str, asset_names: list[str]) -> str:
    if angle == "event":
        return f"{title.strip()} out here."
    if angle == "artist":
        return f"Appreciation for {title.strip()}."
    if angle == "gratitude":
        return f"Thanks for being part of {title.strip()}."
    if angle == "announcement":
        return f"{title.strip()} is coming together."
    if asset_names:
        return f"{title.strip()} in the high desert."
    return title.strip()


def build_caption_options(title: str, context: str, cta: str, angle: str, asset_names: list[str]) -> list[str]:
    summary = summarize_context(context)
    asset_note = ""
    if asset_names:
        if len(asset_names) == 1:
            asset_note = f"Featuring {asset_names[0].lower()}."
        else:
            asset_note = f"Featuring {len(asset_names)} pieces from this set."

    angle_openers = {
        "community": f"{title.strip()} is part of what keeps this high desert community feeling alive out here.",
        "event": f"{title.strip()} is coming together out here, and we’re glad to make room for it.",
        "artist": f"We’re glad to make space for artists like this out here.",
        "gratitude": f"Really grateful to share {title.strip()} with the community out here.",
        "announcement": f"Sharing a fresh look at {title.strip()} out here.",
    }
    opener = angle_openers[angle]
    middle = summary or asset_note or "More music, gathering, and local art from out here."
    close = cta.strip() or "Thanks for being part of it. See you out here."

    option_one = " ".join(
        part for part in [clean_sentence(opener), clean_sentence(middle), clean_sentence(close)] if part
    )

    option_two = " ".join(
        part
        for part in [
            clean_sentence(title.strip()),
            clean_sentence(summary or "A little more from the high desert community."),
            clean_sentence(asset_note or "Local art, music, and gathering out here."),
            clean_sentence(cta.strip() or "See you out here."),
        ]
        if part
    )
    return [option_one, option_two]


def build_notes(platform: str, assets: list[str], context: str, tags: list[str]) -> list[str]:
    notes = [
        f"Voice guide: {VOICE_PATH}",
        f"Platform: {platform}",
        f"Assets: {len(assets)}",
    ]
    if context:
        notes.append(f"Context: {context}")
    if tags:
        notes.append(f"Tags: {', '.join(tags)}")
    return notes


def build_frontmatter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {json.dumps(item)}")
        else:
            lines.append(f"{key}: {json.dumps(value)}")
    lines.append("---")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)

    assets = [normalize_asset(asset) for asset in args.asset]
    asset_names = [humanize_asset_name(asset) for asset in assets]
    tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
    context = load_context(args)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    slug = slugify(args.title)

    draft_path = CONTENT_DIR / f"{timestamp}-{slug}.md"
    intake_path = INTAKE_DIR / f"{timestamp}-{slug}.json"

    caption_options = build_caption_options(args.title, context, args.cta, args.angle, asset_names)
    caption = caption_options[0]
    hook = build_hook(args.title, args.angle, asset_names)
    cta = args.cta.strip() if args.cta.strip() else "See you out here."

    frontmatter = {
        "platform": args.platform,
        "status": "draft",
        "title": args.title.strip(),
        "angle": args.angle,
        "publish_intent": "review",
        "assets": assets,
        "source_intake": str(intake_path),
        "caption": caption,
        "caption_options": caption_options,
        "hashtags": tags,
        "notes": build_notes(args.platform, assets, context, tags),
    }

    body = "\n\n".join(
        [
            build_frontmatter(frontmatter),
            f"## Hook\n\n{hook}",
            f"## Caption\n\n{caption}",
            "## Caption Options\n\n"
            + "\n".join(f"{index}. {option}" for index, option in enumerate(caption_options, start=1)),
            f"## CTA\n\n{cta}",
            f"## Asset Notes\n\nAssets: {', '.join(asset_names)}",
            "## Approval Notes\n\nReview facts, tags, and whether this should stay in review or move to approved.",
        ]
    )
    draft_path.write_text(body + "\n", encoding="utf-8")

    intake = {
        "created_at": datetime.now().astimezone().isoformat(),
        "title": args.title.strip(),
        "platform": args.platform,
        "assets": assets,
        "context": context,
        "cta": args.cta.strip(),
        "angle": args.angle,
        "tags": tags,
        "draft_path": str(draft_path),
    }
    intake_path.write_text(json.dumps(intake, indent=2) + "\n", encoding="utf-8")

    print(f"Saved post draft to {draft_path}")


if __name__ == "__main__":
    main()
