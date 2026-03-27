#!/usr/bin/env python3
"""
Open a priority Instagram DM thread and save a reply draft for review.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

from check_instagram import dismiss_cookie_or_modal_ui
from policy import normalize_thread_match
from platforms import PLATFORM_FILES, ROOT


INBOX_DIR = ROOT / "social" / "inbox"
LOGS_DIR = ROOT / "social" / "logs"
DM_DRAFT_DIR = INBOX_DIR / "reply-drafts"
VOICE_PATH = ROOT / "social" / "voice" / "brand-voice.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Draft a reply for a priority Instagram DM.")
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Show the browser window while running.",
    )
    parser.add_argument(
        "--match",
        help="Optional text to match a specific DM row instead of using the latest unread thread.",
    )
    return parser.parse_args()


def ensure_dirs() -> None:
    DM_DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def load_latest_summary() -> dict[str, Any]:
    path = LOGS_DIR / "instagram-latest-summary.json"
    if not path.exists():
        raise SystemExit("No Instagram summary found. Run check_instagram.py first.")
    return json.loads(path.read_text(encoding="utf-8"))


def pick_target(summary: dict[str, Any], explicit_match: str | None) -> str:
    if explicit_match:
        return explicit_match

    unread_threads = summary.get("message_threads", {}).get("unread_threads", [])
    if unread_threads:
        return unread_threads[0]["preview"]

    threads = summary.get("message_threads", {}).get("threads", [])
    if threads:
        return threads[0]["preview"]

    raise SystemExit("No Instagram DM target found in the latest summary.")


def open_inbox(page: Page) -> None:
    page.goto("https://www.instagram.com/direct/inbox/", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    dismiss_cookie_or_modal_ui(page)


def click_matching_thread(page: Page, match_text: str) -> str:
    normalized_match, soft_match, name_match = normalize_thread_match(match_text)
    preferred_patterns = [soft_match, name_match, normalized_match]
    for pattern in preferred_patterns:
        if not pattern:
            continue
        try:
            locator = page.locator("[role='button']").filter(has_text=pattern)
            if locator.count():
                text = " ".join((locator.first.inner_text(timeout=1500) or "").split())
                locator.first.click(timeout=3000, force=True)
                page.wait_for_timeout(2000)
                return text
        except Exception:
            continue

    buttons = page.locator("[role='button']")
    count = buttons.count()
    for index in range(count):
        button = buttons.nth(index)
        text = " ".join((button.inner_text(timeout=1500) or "").split())
        if not text:
            continue
        normalized_text = re.sub(r"\s+", " ", text).strip()
        if (
            normalized_match in normalized_text
            or normalized_text in normalized_match
            or (soft_match and soft_match in normalized_text)
            or (name_match and name_match in normalized_text.lower())
        ):
            try:
                button.click(timeout=3000)
            except Exception:
                button.click(timeout=3000, force=True)
            page.wait_for_timeout(2000)
            return text
    raise SystemExit(f"Could not find a DM row matching: {match_text}")


def wait_for_thread_panel(page: Page, thread_label: str) -> None:
    normalized = thread_label.split(" Unread")[0].split(" 2 new messages")[0].strip()
    try:
        page.get_by_text(normalized, exact=False).first.wait_for(timeout=5000)
    except Exception:
        page.wait_for_timeout(1500)


def collect_visible_thread_text(page: Page, limit: int = 25) -> list[str]:
    js = """
    (limit) => {
      const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
      const isVisible = (node) => {
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      };
      const composer =
        document.querySelector('textarea[placeholder="Message..."]') ||
        document.querySelector('[contenteditable="true"][aria-label*="Message"]') ||
        document.querySelector('input[placeholder="Message..."]');
      const composerRect = composer ? composer.getBoundingClientRect() : { top: window.innerHeight, left: 480 };
      const rightPaneLeft = Math.max(450, composerRect.left - 40);

      const seen = new Set();
      const values = [];
      const root = document.querySelector('main') || document.body;
      for (const node of Array.from(root.querySelectorAll('h1, h2, h3, div, span'))) {
        if (!isVisible(node)) continue;
        const rect = node.getBoundingClientRect();
        if (rect.left < rightPaneLeft) continue;
        if (rect.bottom > composerRect.top + 10) continue;
        if (rect.width < 40) continue;
        const text = normalize(node.innerText || node.textContent || '');
        if (!text || seen.has(text)) continue;
        if (
          text === 'Message...' ||
          text === 'View profile'
        ) continue;
        if (text.includes('Primary General Requests')) continue;
        if (text.includes('Share a note')) continue;
        if (text === 'stonelevitation') continue;
        if (text.length < 2) continue;
        seen.add(text);
        values.push(text);
        if (values.length >= limit) break;
      }
      return values;
    }
    """
    return page.evaluate(js, limit)


def extract_transcript_chunks(visible_lines: list[str]) -> list[str]:
    chunks: list[str] = []
    seen: set[str] = set()
    timestamp_pattern = re.compile(
        r"((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{1,2}:\d{2}\s+(?:AM|PM))"
    )

    for line in visible_lines:
        if "Instagram View profile" not in line:
            continue
        cleaned = line.split("Instagram View profile", 1)[1].strip()
        parts = timestamp_pattern.split(cleaned)
        current_stamp = None
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if timestamp_pattern.fullmatch(part):
                current_stamp = part
                continue
            part = re.sub(r"^(stonelevitation\s+)+", "", part).strip()
            part = re.sub(r"\s+Message\.\.\.$", "", part).strip()
            if not part:
                continue
            rendered = f"{current_stamp} {part}" if current_stamp else part
            if rendered in seen:
                continue
            seen.add(rendered)
            chunks.append(rendered)

    return chunks


def suggest_reply(transcript_chunks: list[str]) -> str:
    combined = " ".join(transcript_chunks).lower()
    if "mentioned you in their story" in combined:
        return (
            "Thanks so much for the story mention and for spreading the word. "
            "Really appreciate you shouting out Giant Rock Meeting Room. "
            "Thanks for being part of it, and see you out here."
        )
    if "thank you" in combined and "@giantrockmeetingroom" in combined:
        return (
            "Thanks for the kind words and for being part of it. "
            "We loved having you out here and would be glad to have you back."
        )
    return (
        "Thanks for reaching out and for being part of the community out here. "
        "Let us know a little more and we can point you in the right direction."
    )


def build_reply_draft(thread_label: str, visible_lines: list[str]) -> dict[str, Any]:
    transcript_chunks = extract_transcript_chunks(visible_lines)
    latest_excerpt = transcript_chunks[:6] if transcript_chunks else visible_lines[:12]
    reply_source = transcript_chunks if transcript_chunks else visible_lines
    combined_visible = " ".join(reply_source).lower()
    conversation_type = "dm"

    if "partnership inbox" in combined_visible or "creator marketplace" in combined_visible:
        conversation_type = "partnership_outreach"
        reply_text = (
            "Thanks for reaching out. We host music, art, and community gatherings out here in the high desert. "
            "If you want to share a little more about your project or what kind of partnership you have in mind, "
            "we’d be glad to take a look."
        )
    elif any(
        phrase in combined_visible
        for phrase in [
            "all ages",
            "7pm doors",
            "hot patooties",
            "undercover monsters",
            "pizza and punk rock",
        ]
    ):
        reply_text = (
            "Thanks so much for sending this over. Love this lineup, and thanks for keeping us in the loop. "
            "We’re glad to help spread the word out here."
        )
    else:
        reply_text = suggest_reply(reply_source)

    return {
        "captured_at": datetime.now().astimezone().isoformat(),
        "platform": "instagram",
        "thread_label": thread_label,
        "status": "draft_for_approval",
        "conversation_type": conversation_type,
        "latest_visible_lines": latest_excerpt,
        "parsed_transcript": transcript_chunks,
        "suggested_reply": reply_text,
        "notes": [
            "Review the visible lines before sending.",
            f"Voice guide: {VOICE_PATH}",
            "Do not send without approval.",
        ],
    }


def main() -> None:
    args = parse_args()
    ensure_dirs()

    session_path = PLATFORM_FILES["instagram"]
    if not session_path.exists():
        raise SystemExit("Instagram session file not found. Run the login script first.")

    summary = load_latest_summary()
    target_text = pick_target(summary, args.match)

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    draft_path = DM_DRAFT_DIR / f"instagram-dm-draft-{timestamp}.json"
    screenshot_path = DM_DRAFT_DIR / f"instagram-dm-thread-{timestamp}.png"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.show_browser)
        context = browser.new_context(storage_state=str(session_path))
        page = context.new_page()

        open_inbox(page)
        thread_label = click_matching_thread(page, target_text)
        wait_for_thread_panel(page, thread_label)
        visible_lines = collect_visible_thread_text(page)
        page.screenshot(path=str(screenshot_path))

        browser.close()

    draft = build_reply_draft(thread_label, visible_lines)
    draft["thread_screenshot"] = str(screenshot_path)
    draft_path.write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")

    latest_path = LOGS_DIR / "instagram-latest-dm-draft.json"
    latest_path.write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")

    print(f"Saved Instagram DM draft to {draft_path}")


if __name__ == "__main__":
    main()
