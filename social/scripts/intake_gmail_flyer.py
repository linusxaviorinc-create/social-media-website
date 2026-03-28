#!/usr/bin/env python3
"""
Find likely flyer emails in Gmail, download supported attachments, and turn them
into a local content draft plus staging manifest when possible.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from platforms import ROOT


ASSETS_DIR = ROOT / "social" / "assets" / "inbox-flyers"
INTAKE_DIR = ROOT / "social" / "content" / "intake"
LOGS_DIR = ROOT / "social" / "logs"
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}
FLYER_HINTS = {
    "flyer",
    "poster",
    "show",
    "music",
    "live music",
    "all ages",
    "doors",
    "band",
    "lineup",
    "tonight",
    "friday",
    "saturday",
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Intake a likely flyer email from Gmail.")
    parser.add_argument("--account", default="linusxaviorinc@gmail.com", help="Google account for gog.")
    parser.add_argument(
        "--query",
        default='in:inbox newer_than:21d has:attachment -category:promotions -category:social',
        help="Gmail query used to find flyer candidates.",
    )
    parser.add_argument("--message-id", help="Specific Gmail message ID to intake.")
    parser.add_argument("--max", type=int, default=10, help="Max Gmail candidates to inspect.")
    parser.add_argument("--title", help="Optional title override for the drafted post.")
    parser.add_argument("--cta", default="See you out here.", help="CTA to use when drafting a post.")
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
        help="Create a content draft and staging manifest when supported flyer assets are found.",
    )
    return parser.parse_args()


def slugify(value: str) -> str:
    lowered = value.lower().strip()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-") or "flyer"


def clean_text(value: str) -> str:
    value = value.replace("\r", "\n")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"\$\{[^}]+\}", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def run_json_command(*args: str) -> Any:
    result = subprocess.run(
        ["zsh", "-lic", " ".join(args)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Command failed.")
    return json.loads(result.stdout or "null")


def run_command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["zsh", "-lic", " ".join(args)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )


def newest_path(directory: Path, pattern: str, before: set[Path]) -> Path | None:
    current = set(directory.glob(pattern))
    created = sorted(current - before)
    return created[-1] if created else None


def gmail_search(account: str, query: str, max_results: int) -> list[dict[str, Any]]:
    payload = run_json_command(
        "gog gmail messages search",
        json.dumps(query),
        "-a",
        json.dumps(account),
        "-j --results-only",
        "--max",
        str(max_results),
    )
    return payload if isinstance(payload, list) else []


def gmail_get(account: str, message_id: str) -> dict[str, Any]:
    payload = run_json_command(
        "gog gmail get",
        json.dumps(message_id),
        "-a",
        json.dumps(account),
        "--format full",
        "-j --results-only",
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Gmail payload.")
    return payload


def header_value(payload: dict[str, Any], name: str) -> str:
    headers = ((payload.get("headers") or {}) if isinstance(payload.get("headers"), dict) else {}) or {}
    direct = headers.get(name.lower()) or headers.get(name) or ""
    if direct:
        return str(direct)

    for part in iter_parts(payload):
        for header in part.get("headers", []):
            if str(header.get("name") or "").lower() == name.lower():
                return str(header.get("value") or "")
    return ""


def iter_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    queue = [payload.get("payload") or {}]
    parts: list[dict[str, Any]] = []
    while queue:
        current = queue.pop(0)
        if not isinstance(current, dict):
            continue
        parts.append(current)
        queue.extend(part for part in current.get("parts", []) if isinstance(part, dict))
    return parts


def extract_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for part in iter_parts(payload):
        filename = str(part.get("filename") or "").strip()
        body = part.get("body") or {}
        attachment_id = str(body.get("attachmentId") or "").strip()
        mime_type = str(part.get("mimeType") or "").strip()
        size = int(body.get("size") or 0)
        suffix = Path(filename).suffix.lower()
        disposition = ""
        for header in part.get("headers", []):
            if str(header.get("name") or "").lower() == "content-disposition":
                disposition = str(header.get("value") or "").lower()
                break
        if not filename or not attachment_id or suffix not in SUPPORTED_SUFFIXES:
            continue
        if size < 5_000:
            continue
        if disposition and "attachment" not in disposition and "inline" not in disposition:
            continue
        attachments.append(
            {
                "filename": filename,
                "attachment_id": attachment_id,
                "mime_type": mime_type,
                "size": size,
            }
        )
    return attachments


def flyer_score(message: dict[str, Any], payload: dict[str, Any]) -> int:
    text = " ".join(
        [
            str(message.get("subject") or ""),
            str(message.get("from") or ""),
            str(message.get("snippet") or ""),
            str(payload.get("body") or "")[:4000],
        ]
    ).lower()
    score = 0
    for hint in FLYER_HINTS:
        if hint in text:
            score += 2
    if re.search(r"\b\d{1,2}(:\d{2})?\s?(am|pm)\b", text):
        score += 3
    if re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b", text):
        score += 3
    if re.search(r"\b\d{1,2}/\d{1,2}\b", text):
        score += 2
    score += min(len(extract_attachments(payload)) * 3, 9)
    return score


def parse_sender(sender_header: str) -> str:
    if "<" in sender_header:
        return sender_header.split("<", 1)[0].strip().strip('"')
    return sender_header.strip()


def message_summary_from_payload(message_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": message_id,
        "threadId": payload.get("threadId"),
        "from": header_value(payload, "From"),
        "subject": header_value(payload, "Subject"),
        "date": header_value(payload, "Date"),
        "snippet": payload.get("snippet", ""),
    }


def infer_title(message: dict[str, Any], payload: dict[str, Any], override: str | None) -> str:
    if override and override.strip():
        return override.strip()
    subject = str(message.get("subject") or "").strip()
    if subject:
        subject = re.sub(r"^(re|fwd?):\s*", "", subject, flags=re.IGNORECASE)
        return subject
    attachments = extract_attachments(payload)
    if attachments:
        stem = Path(attachments[0]["filename"]).stem.replace("_", " ").replace("-", " ").strip()
        return " ".join(stem.split()).title()
    return "Flyer intake"


def extract_date_hints(text: str) -> list[str]:
    patterns = [
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?\b",
        r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
        r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b",
    ]
    hints: list[str] = []
    lowered = text.lower()
    for pattern in patterns:
        for match in re.findall(pattern, lowered, flags=re.IGNORECASE):
            normalized = " ".join(str(match).split())
            if normalized not in hints:
                hints.append(normalized)
    return hints[:5]


def calendar_matches(account: str, title: str) -> tuple[list[dict[str, Any]], str | None]:
    result = run_command(
        "gog calendar search",
        json.dumps(title),
        "-a",
        json.dumps(account),
        "-j --results-only",
        "--days 120",
        "--max 5",
    )
    if result.returncode != 0:
        return [], (result.stderr.strip() or result.stdout.strip() or "Calendar search failed.")
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return [], "Calendar search returned invalid JSON."
    return payload if isinstance(payload, list) else [], None


def download_attachments(account: str, message_id: str, title: str, attachments: list[dict[str, Any]]) -> list[str]:
    if not attachments:
        return []
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output_dir = ASSETS_DIR / f"{timestamp}-{slugify(title)[:60]}"
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for index, attachment in enumerate(attachments, start=1):
        filename = attachment["filename"]
        suffix = Path(filename).suffix.lower()
        target = output_dir / f"{index:02d}-{slugify(Path(filename).stem)[:50]}{suffix}"
        result = run_command(
            "gog gmail attachment",
            json.dumps(message_id),
            json.dumps(attachment["attachment_id"]),
            "-a",
            json.dumps(account),
            "--out",
            json.dumps(str(target)),
        )
        if result.returncode == 0 and target.exists():
            saved.append(str(target))
    return saved


def build_context(message: dict[str, Any], payload: dict[str, Any], date_hints: list[str], calendar_error: str | None) -> str:
    sender = parse_sender(str(message.get("from") or ""))
    snippet = clean_text(str(payload.get("snippet") or message.get("snippet") or ""))[:400]
    body_text = clean_text(str(payload.get("body") or ""))[:1000]
    body_excerpt = body_text if body_text else snippet
    lines = [
        f"Email sender: {sender}",
        f"Email subject: {str(message.get('subject') or '').strip()}",
    ]
    if date_hints:
        lines.append(f"Possible event dates mentioned: {', '.join(date_hints)}")
    if body_excerpt:
        lines.append(f"Email excerpt: {body_excerpt}")
    if calendar_error:
        lines.append(f"Calendar lookup status: unavailable ({calendar_error})")
    return "\n".join(lines)


def create_draft(title: str, assets: list[str], context: str, cta: str, angle: str, tags: str) -> tuple[str | None, str | None]:
    if not assets:
        return None, None
    drafts_dir = ROOT / "social" / "content" / "drafts"
    staging_dir = ROOT / "social" / "content" / "staging"
    draft_before = set(drafts_dir.glob("*.md"))
    staging_before = set(staging_dir.glob("*.json"))
    command = [
        sys.executable,
        "social/scripts/draft_asset_post.py",
        "--title",
        title,
        "--angle",
        angle,
        "--context",
        context,
        "--cta",
        cta,
        "--tags",
        tags,
    ]
    for asset in assets:
        command.extend(["--asset", asset])
    draft_result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, check=False, timeout=180)
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


def choose_candidate(messages: list[dict[str, Any]], account: str, requested_message_id: str | None) -> tuple[dict[str, Any], dict[str, Any], int]:
    candidates: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for message in messages:
        message_id = str(message.get("id") or "")
        if requested_message_id and message_id != requested_message_id:
            continue
        payload = gmail_get(account, message_id)
        score = flyer_score(message, payload)
        candidates.append((score, message, payload))
    if not candidates:
        raise SystemExit("No Gmail flyer candidates found.")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1], candidates[0][2], candidates[0][0]


def main() -> None:
    args = parse_args()
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    if args.message_id:
        payload = gmail_get(args.account, args.message_id)
        message = message_summary_from_payload(args.message_id, payload)
        score = flyer_score(message, payload)
    else:
        messages = gmail_search(args.account, args.query, args.max)
        message, payload, score = choose_candidate(messages, args.account, args.message_id)

    title = infer_title(message, payload, args.title)
    attachments = extract_attachments(payload)
    downloaded_assets = []
    if args.draft:
        downloaded_assets = download_attachments(args.account, str(message.get("id") or ""), title, attachments)
    text_for_dates = " ".join(
        [
            str(message.get("subject") or ""),
            str(message.get("snippet") or ""),
            str(payload.get("body") or "")[:3000],
        ]
    )
    date_hints = extract_date_hints(text_for_dates)
    calendar_results, calendar_error = calendar_matches(args.account, title)
    context = build_context(message, payload, date_hints, calendar_error)

    draft_path = None
    staging_manifest = None
    if args.draft and downloaded_assets:
        draft_path, staging_manifest = create_draft(title, downloaded_assets, context, args.cta, args.angle, args.tags)

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    intake_path = INTAKE_DIR / f"{timestamp}-{slugify(title)}-gmail-flyer.json"
    intake_payload = {
        "created_at": datetime.now().astimezone().isoformat(),
        "source": "gmail_flyer",
        "gmail_account": args.account,
        "gmail_query": args.query,
        "message_id": message.get("id"),
        "thread_id": message.get("threadId"),
        "sender": message.get("from"),
        "subject": message.get("subject"),
        "received_at": message.get("date"),
        "title": title,
        "score": score,
        "snippet": clean_text(str(message.get("snippet") or payload.get("snippet") or "")),
        "possible_event_dates": date_hints,
        "attachments": attachments,
        "downloaded_assets": downloaded_assets,
        "calendar_matches": calendar_results,
        "calendar_error": calendar_error,
        "draft_path": draft_path,
        "staging_manifest": staging_manifest,
        "context": context,
    }
    intake_path.write_text(json.dumps(intake_payload, indent=2) + "\n", encoding="utf-8")
    (LOGS_DIR / "instagram-latest-gmail-flyer-intake.json").write_text(
        json.dumps(intake_payload, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Saved Gmail flyer intake to {intake_path}")
    if draft_path:
        print(f"Created draft at {draft_path}")


if __name__ == "__main__":
    main()
