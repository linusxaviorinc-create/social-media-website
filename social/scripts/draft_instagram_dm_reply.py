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
from platforms import PLATFORM_FILES, ROOT


INBOX_DIR = ROOT / "social" / "inbox"
LOGS_DIR = ROOT / "social" / "logs"
DM_DRAFT_DIR = INBOX_DIR / "reply-drafts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Draft a reply for a priority Instagram DM.")
    parser.add_argument("--headless", action="store_true", help="Run without showing the browser.")
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
    normalized_match = re.sub(r"\s+", " ", match_text).strip()
    soft_match = re.sub(r"\b\d+\s+new messages?\b", "", normalized_match, flags=re.IGNORECASE)
    soft_match = re.sub(r"\bunread\b", "", soft_match, flags=re.IGNORECASE).strip()
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
        ):
            button.click(timeout=3000)
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
      const seen = new Set();
      const values = [];
      const containers = Array.from(document.querySelectorAll('main section, main [role="main"], main'));
      const target = containers[containers.length - 1] || document.querySelector('main') || document.body;
      for (const node of Array.from(target.querySelectorAll('h1, h2, h3, div, span'))) {
        const text = normalize(node.innerText || node.textContent || '');
        if (!text || seen.has(text)) continue;
        if (
          text === 'Primary' ||
          text === 'General' ||
          text === 'Requests' ||
          text === 'Search' ||
          text === 'New message' ||
          text === 'Down chevron icon'
        ) continue;
        if (text.length < 2) continue;
        seen.add(text);
        values.push(text);
        if (values.length >= limit) break;
      }
      return values;
    }
    """
    return page.evaluate(js, limit)


def build_reply_draft(thread_label: str, visible_lines: list[str]) -> dict[str, Any]:
    latest_excerpt = visible_lines[:12]
    reply_text = (
        "Thanks for reaching out. I saw your message and wanted to follow up. "
        "Could you share a little more detail so I can point you in the right direction?"
    )

    return {
        "captured_at": datetime.now().astimezone().isoformat(),
        "platform": "instagram",
        "thread_label": thread_label,
        "status": "draft_for_approval",
        "latest_visible_lines": latest_excerpt,
        "suggested_reply": reply_text,
        "notes": [
            "Review the visible lines before sending.",
            "Adjust tone once brand voice is filled in.",
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
        browser = playwright.chromium.launch(headless=args.headless)
        context = browser.new_context(storage_state=str(session_path))
        page = context.new_page()

        open_inbox(page)
        thread_label = click_matching_thread(page, target_text)
        wait_for_thread_panel(page, thread_label)
        visible_lines = collect_visible_thread_text(page)
        page.screenshot(path=str(screenshot_path), full_page=True)

        browser.close()

    draft = build_reply_draft(thread_label, visible_lines)
    draft["thread_screenshot"] = str(screenshot_path)
    draft_path.write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")

    latest_path = LOGS_DIR / "instagram-latest-dm-draft.json"
    latest_path.write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")

    print(f"Saved Instagram DM draft to {draft_path}")


if __name__ == "__main__":
    main()
