#!/usr/bin/env python3
"""
Open the latest Instagram DM thread and attempt to reach the story reshare flow.
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
from policy import classify_account, extract_handle, load_trusted_accounts, normalize_thread_match
from platforms import PLATFORM_FILES, ROOT


LOGS_DIR = ROOT / "social" / "logs"
INBOX_DIR = ROOT / "social" / "inbox"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Try to reach Instagram story reshare from the latest DM thread.")
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Show the browser window while running.",
    )
    return parser.parse_args()


def load_latest_draft() -> dict[str, Any]:
    path = LOGS_DIR / "instagram-latest-dm-draft.json"
    if not path.exists():
        raise SystemExit("No Instagram DM draft found. Run draft_instagram_dm_reply.py first.")
    return json.loads(path.read_text(encoding="utf-8"))

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


def click_story_surface(page: Page) -> str:
    candidates = [
        page.get_by_text("Mentioned you in their story", exact=False),
        page.get_by_text("Story unavailable", exact=False),
        page.get_by_text("View story", exact=False),
        page.get_by_text("Add to story", exact=False),
    ]
    for candidate in candidates:
        try:
            if candidate.count():
                candidate.first.click(timeout=3000)
                page.wait_for_timeout(2500)
                return candidate.first.inner_text(timeout=1500)
        except Exception:
            continue
    raise SystemExit("Could not find a story mention/share surface in the current DM thread.")


def collect_visible_actions(page: Page, limit: int = 20) -> list[str]:
    js = """
    (limit) => {
      const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
      const items = [];
      for (const node of Array.from(document.querySelectorAll('button, a, [role="button"], [role="link"]'))) {
        const text = normalize(node.innerText || node.textContent || node.getAttribute('aria-label') || '');
        if (!text) continue;
        items.push(text);
        if (items.length >= limit) break;
      }
      return items;
    }
    """
    return page.evaluate(js, limit)


def detect_story_state(page: Page) -> dict[str, Any]:
    body_text = page.locator("body").inner_text(timeout=3000)
    normalized = " ".join(body_text.split())
    unavailable = "Story unavailable" in normalized
    add_to_story = "Add to story" in normalized or "Add to Story" in normalized
    view_story = "View story" in normalized or "View Story" in normalized

    if unavailable:
        recommended = "request_resend"
    elif add_to_story or view_story:
        recommended = "continue_reshare_flow"
    else:
        recommended = "manual_review"

    return {
        "story_unavailable": unavailable,
        "has_add_to_story": add_to_story,
        "has_view_story": view_story,
        "recommended_action": recommended,
    }


def main() -> None:
    args = parse_args()
    session_path = PLATFORM_FILES["instagram"]
    if not session_path.exists():
        raise SystemExit("Instagram session file not found. Run the login script first.")

    draft = load_latest_draft()
    thread_label = draft.get("thread_label", "").strip()
    if not thread_label:
        raise SystemExit("Latest Instagram DM draft is missing thread text.")
    handle = extract_handle(thread_label)
    policy = classify_account(handle, load_trusted_accounts())

    run_stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    screenshot_path = INBOX_DIR / "screenshots" / f"instagram-story-reshare-{run_stamp}.png"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.show_browser)
        context = browser.new_context(storage_state=str(session_path))
        page = context.new_page()

        open_inbox(page)
        click_matching_thread(page, thread_label)
        surface = click_story_surface(page)
        actions = collect_visible_actions(page)
        story_state = detect_story_state(page)
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_path), full_page=True)

        payload = {
            "status": "story_surface_opened",
            "thread_label": thread_label,
            "handle": handle,
            "policy": policy,
            "surface_clicked": surface,
            "story_state": story_state,
            "visible_actions": actions,
            "screenshot": str(screenshot_path),
        }
        (LOGS_DIR / "instagram-latest-story-reshare.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        page.wait_for_timeout(1000)
        browser.close()

    print(f"Instagram story reshare surface opened from thread: {thread_label}")


if __name__ == "__main__":
    main()
