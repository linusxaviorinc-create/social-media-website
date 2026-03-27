#!/usr/bin/env python3
"""
Attempt to move from a live Instagram story mention into the story composer
without posting.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

from check_instagram import dismiss_cookie_or_modal_ui
from platforms import PLATFORM_FILES, ROOT
from reshare_instagram_story import click_matching_thread, click_story_surface


LOGS_DIR = ROOT / "social" / "logs"
INBOX_DIR = ROOT / "social" / "inbox"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare a live Instagram story reshare without posting.")
    parser.add_argument("--show-browser", action="store_true", help="Show the browser window while running.")
    return parser.parse_args()


def load_latest_reshare() -> dict[str, Any]:
    path = LOGS_DIR / "instagram-latest-story-reshare.json"
    if not path.exists():
        raise SystemExit("No Instagram reshare log found. Run reshare_instagram_story.py first.")
    return json.loads(path.read_text(encoding="utf-8"))


def open_inbox(page: Page) -> None:
    page.goto("https://www.instagram.com/direct/inbox/", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    dismiss_cookie_or_modal_ui(page)


def click_live_story_action(page: Page) -> str | None:
    candidates = [
        page.get_by_text("Add to story", exact=False),
        page.get_by_text("Add to Story", exact=False),
        page.get_by_text("View story", exact=False),
        page.get_by_text("View Story", exact=False),
    ]
    for candidate in candidates:
        try:
            if candidate.count():
                label = candidate.first.inner_text(timeout=1500)
                candidate.first.click(timeout=3000)
                page.wait_for_timeout(2500)
                return label
        except Exception:
            continue
    return None


def collect_surface_state(page: Page) -> dict[str, Any]:
    text = " ".join(page.locator("body").inner_text(timeout=3000).split())
    return {
        "has_share_button": "Share" in text or "Your story" in text,
        "has_story_editor": "Your story" in text or "Close Friends" in text,
        "body_excerpt": text[:600],
    }


def main() -> None:
    args = parse_args()
    reshare = load_latest_reshare()
    thread_label = reshare.get("thread_label", "").strip()
    story_state = reshare.get("story_state", {})
    if not thread_label:
        raise SystemExit("Latest reshare log is missing thread text.")

    if story_state.get("recommended_action") != "continue_reshare_flow":
        raise SystemExit("Latest story mention is not in a live reshareable state.")

    run_stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    screenshot_path = INBOX_DIR / "screenshots" / f"instagram-story-prepare-{run_stamp}.png"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.show_browser)
        context = browser.new_context(storage_state=str(PLATFORM_FILES["instagram"]))
        page = context.new_page()

        open_inbox(page)
        click_matching_thread(page, thread_label)
        click_story_surface(page)
        clicked = click_live_story_action(page)
        state = collect_surface_state(page)
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_path))
        browser.close()

    payload = {
        "status": "story_reshare_prepared",
        "thread_label": thread_label,
        "clicked_action": clicked,
        "surface_state": state,
        "screenshot": str(screenshot_path),
    }
    (LOGS_DIR / "instagram-latest-story-prepare.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    print("Saved Instagram story reshare preparation log.")


if __name__ == "__main__":
    main()
