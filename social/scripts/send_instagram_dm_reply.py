#!/usr/bin/env python3
"""
Open an Instagram DM draft, stage the reply in the composer, and optionally send it.
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


LOGS_DIR = ROOT / "social" / "logs"
DM_DRAFT_DIR = ROOT / "social" / "inbox" / "reply-drafts"
SCREENSHOT_DIR = ROOT / "social" / "inbox" / "screenshots"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage or send the latest Instagram DM draft.")
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Show the browser window while running.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send the drafted reply. By default this only stages the text.",
    )
    parser.add_argument("--draft-file", help="Path to a specific saved DM draft JSON file.")
    parser.add_argument("--match", help="Match text to select a saved draft by thread label.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def recent_draft_paths() -> list[Path]:
    if not DM_DRAFT_DIR.exists():
        return []
    return sorted(DM_DRAFT_DIR.glob("instagram-dm-draft-*.json"), reverse=True)


def load_draft(args: argparse.Namespace) -> dict[str, Any]:
    if args.draft_file:
        path = Path(args.draft_file)
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        if not path.exists():
            raise SystemExit(f"Draft file not found: {path}")
        payload = load_json(path)
        payload["_source_path"] = str(path)
        return payload

    if args.match:
        wanted = args.match.lower()
        for path in recent_draft_paths():
            draft = load_json(path)
            thread_label = (draft.get("thread_label") or "").lower()
            if wanted in thread_label:
                draft["_source_path"] = str(path)
                return draft
        raise SystemExit(f"No saved DM draft matched: {args.match}")

    path = LOGS_DIR / "instagram-latest-dm-draft.json"
    if not path.exists():
        raise SystemExit("No Instagram DM draft found. Run draft_instagram_dm_reply.py first.")
    payload = load_json(path)
    payload["_source_path"] = str(path)
    return payload


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


def fill_reply(page: Page, message: str) -> None:
    def try_fill() -> bool:
        candidates = [
            page.locator("textarea[placeholder='Message...']"),
            page.locator("input[placeholder='Message...']"),
            page.locator("[contenteditable='true'][role='textbox']"),
            page.locator("[contenteditable='true'][aria-label*='Message']"),
            page.locator("div[contenteditable='true']"),
        ]
        for candidate in candidates:
            try:
                if candidate.count():
                    candidate.first.click(timeout=3000)
                    candidate.first.fill(message, timeout=3000)
                    return True
            except Exception:
                continue
        return False

    if try_fill():
        return

    onboarding_buttons = [
        page.get_by_role("button", name="Send message"),
        page.get_by_text("Send message", exact=False),
        page.get_by_role("button", name="Chat"),
        page.get_by_text("Chat", exact=False),
    ]
    for button in onboarding_buttons:
        try:
            if button.count():
                button.first.click(timeout=3000)
                page.wait_for_timeout(1500)
                if try_fill():
                    return
        except Exception:
            continue

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    screenshot_path = SCREENSHOT_DIR / f"instagram-dm-compose-failure-{stamp}.png"
    debug_path = LOGS_DIR / "instagram-latest-dm-compose-debug.json"
    page.screenshot(path=str(screenshot_path), full_page=True)
    body_excerpt = " ".join(page.locator("body").inner_text(timeout=3000).split())[:2000]
    reason = "composer_not_found"
    if "partnership inbox" in body_excerpt.lower() and "to:" in body_excerpt.lower():
        reason = "partnership_creator_selection_required"
    debug_payload = {
        "captured_at": datetime.now().astimezone().isoformat(),
        "reason": reason,
        "screenshot": str(screenshot_path),
        "body_excerpt": body_excerpt,
    }
    debug_path.write_text(json.dumps(debug_payload, indent=2) + "\n", encoding="utf-8")
    if reason == "partnership_creator_selection_required":
        raise SystemExit(
            "Instagram partnership inbox is still on the creator-selection surface, so there is no direct composer to stage into yet."
        )
    raise SystemExit("Could not find the Instagram DM composer to stage the reply.")


def click_send(page: Page) -> None:
    candidates = [
        page.get_by_role("button", name="Send"),
        page.locator("text=Send"),
    ]
    for candidate in candidates:
        try:
            if candidate.count():
                candidate.first.click(timeout=3000)
                page.wait_for_timeout(1500)
                return
        except Exception:
            continue
    raise SystemExit("Could not find the Send button after staging the reply.")


def main() -> None:
    args = parse_args()
    session_path = PLATFORM_FILES["instagram"]
    if not session_path.exists():
        raise SystemExit("Instagram session file not found. Run the login script first.")

    draft = load_draft(args)
    reply = draft.get("suggested_reply", "").strip()
    thread_label = draft.get("thread_label", "").strip()
    if not reply or not thread_label:
        raise SystemExit("Latest Instagram DM draft is missing thread or reply text.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.show_browser)
        context = browser.new_context(storage_state=str(session_path))
        page = context.new_page()

        open_inbox(page)
        click_matching_thread(page, thread_label)
        fill_reply(page, reply)
        if args.send:
            click_send(page)

        if args.send:
            log_name = "instagram-latest-dm-send.json"
            status = "sent"
        else:
            log_name = "instagram-latest-dm-stage.json"
            status = "staged_only"

        payload = {
            "status": status,
            "thread_label": thread_label,
            "reply": reply,
            "source_draft": draft.get("_source_path"),
        }
        (LOGS_DIR / log_name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        page.wait_for_timeout(1000)
        browser.close()

    print(f"Instagram DM reply {status}: {thread_label}")


if __name__ == "__main__":
    main()
