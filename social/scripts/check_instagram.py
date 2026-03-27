#!/usr/bin/env python3
"""
Capture a lightweight Instagram activity snapshot using a saved browser session.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Page, sync_playwright

from platforms import PLATFORM_FILES, ROOT


INBOX_DIR = ROOT / "social" / "inbox"
LOGS_DIR = ROOT / "social" / "logs"
SCREENSHOT_DIR = INBOX_DIR / "screenshots"
NOISE_TOKENS = {
    "Home",
    "Reels",
    "Messages",
    "Search",
    "Explore",
    "Notifications",
    "Create",
    "Dashboard",
    "Profile",
    "More",
    "Also from Meta",
    "Primary",
    "General",
    "Requests",
    "Share a note",
    "Your note",
    "Comments",
    "All",
    "Follow Back",
    "Unread",
    "more",
    "Today",
    "Yesterday",
    "This week",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture Instagram activity into local files.")
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Show the browser window while running.",
    )
    return parser.parse_args()


def ensure_dirs() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def dismiss_cookie_or_modal_ui(page: Page) -> None:
    labels = [
        "Allow all cookies",
        "Allow essential and optional cookies",
        "Not now",
        "Cancel",
    ]
    for label in labels:
        try:
            button = page.get_by_role("button", name=label)
            if button.count():
                button.first.click(timeout=1500)
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue


def collect_links(page: Page, limit: int = 40) -> List[str]:
    js = """
    (limit) => Array.from(document.querySelectorAll('a'))
      .map((link) => {
        const text = (link.innerText || '').replace(/\\s+/g, ' ').trim();
        const href = link.getAttribute('href') || '';
        const aria = link.getAttribute('aria-label') || '';
        return { text, href, aria };
      })
      .filter((item) => item.text || item.aria || item.href)
      .slice(0, limit)
    """
    items = page.evaluate(js, limit)
    rendered = []
    for item in items:
        label = item["text"] or item["aria"] or "(no label)"
        rendered.append(f"{label} -> {item['href']}")
    return rendered


def collect_notifications(page: Page) -> List[str]:
    dismiss_cookie_or_modal_ui(page)

    selectors = [
        "a[href='/accounts/activity/']",
        "a[href='/accounts/activity']",
        "[aria-label='Notifications']",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count():
                locator.first.click(timeout=3000)
                page.wait_for_timeout(2000)
                break
        except Exception:
            continue

    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightTimeoutError:
        pass

    texts = page.locator("text=/./").all_inner_texts()
    cleaned = []
    for text in texts:
        if not text:
            continue
        line = " ".join(str(text).split())
        if 3 <= len(line) <= 300 and line not in cleaned:
            cleaned.append(line)
        if len(cleaned) >= 50:
            break
    return cleaned


def collect_messages(page: Page) -> List[str]:
    try:
        page.goto("https://www.instagram.com/direct/inbox/", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
    except Exception:
        return []

    dismiss_cookie_or_modal_ui(page)
    items = page.locator("text=/./").all_inner_texts()
    cleaned = []
    for text in items:
        if not text:
            continue
        line = " ".join(str(text).split())
        if 3 <= len(line) <= 250 and line not in cleaned:
            cleaned.append(line)
        if len(cleaned) >= 40:
            break
    return cleaned


def collect_message_threads(page: Page, limit: int = 12) -> List[dict]:
    js = """
    (limit) => {
      const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
      const hrefs = new Set();
      const rows = [];

      for (const link of Array.from(document.querySelectorAll('a[href*="/direct/t/"]'))) {
        const href = link.getAttribute('href') || '';
        if (!href || hrefs.has(href)) continue;
        hrefs.add(href);

        const text = normalize(link.innerText);
        if (!text) continue;

        const parts = text.split('\\n').map(normalize).filter(Boolean);
        if (!parts.length) continue;

        rows.push({
          href,
          parts,
          aria: normalize(link.getAttribute('aria-label') || '')
        });
        if (rows.length >= limit) break;
      }

      return rows;
    }
    """
    return page.evaluate(js, limit)


def collect_inbox_debug_links(page: Page, limit: int = 30) -> List[dict]:
    js = """
    (limit) => {
      const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
      return Array.from(document.querySelectorAll('a, button, [role="link"], [role="button"]'))
        .map((node) => ({
          tag: node.tagName,
          href: normalize(node.getAttribute('href') || ''),
          role: normalize(node.getAttribute('role') || ''),
          aria: normalize(node.getAttribute('aria-label') || ''),
          text: normalize(node.innerText || node.textContent || '')
        }))
        .filter((item) => item.href || item.aria || item.text)
        .slice(0, limit);
    }
    """
    return page.evaluate(js, limit)


def collect_message_rows(page: Page, limit: int = 20) -> List[str]:
    js = """
    (limit) => {
      const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
      const rows = [];
      for (const node of Array.from(document.querySelectorAll('[role="button"]'))) {
        const text = normalize(node.innerText || node.textContent || '');
        if (!text) continue;
        if (
          text === 'New message' ||
          text === 'giantrockmeetingroom' ||
          text.startsWith('Share a note')
        ) continue;
        rows.push(text);
        if (rows.length >= limit) break;
      }
      return rows;
    }
    """
    return page.evaluate(js, limit)


def write_snapshot(snapshot_path: Path, payload: dict) -> None:
    snapshot_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def looks_like_time_token(value: str) -> bool:
    return bool(re.fullmatch(r"\d+[smhdw]|[A-Z][a-z]{2} \d+", value))


def extract_message_badge(home_links: List[str]) -> int | None:
    for entry in home_links:
        match = re.match(r"(\d+)\s+Messages?\s+->", entry)
        if match:
            return int(match.group(1))
    return None


def summarize_notifications(notifications: List[str]) -> dict:
    follows = []
    mentions = []
    hashtags = []
    raw_names = []

    for item in notifications:
        if item in NOISE_TOKENS or looks_like_time_token(item):
            continue
        if item.startswith("@"):
            mentions.append(item)
            continue
        if item.startswith("#"):
            hashtags.append(item)
            continue
        if re.fullmatch(r"[a-z0-9._]{3,}", item):
            raw_names.append(item)

    for name in raw_names[:8]:
        if name not in follows:
            follows.append(name)

    return {
        "likely_new_followers": follows,
        "mentions": mentions[:10],
        "hashtags_seen": hashtags[:10],
        "needs_manual_review": bool(mentions or hashtags),
    }


def summarize_messages(messages: List[str]) -> dict:
    threads = []
    unread_count = None
    partnership_signals = []

    for index, item in enumerate(messages):
        if item in NOISE_TOKENS or item == "giantrockmeetingroom":
            continue
        unread_match = re.fullmatch(r"(\d+)\s+new messages?", item)
        if unread_match:
            unread_count = int(unread_match.group(1))
            continue
        if "partnership" in item.lower():
            partnership_signals.append(item)
            continue
        if looks_like_time_token(item):
            continue
        if len(item) > 2 and item not in threads and not item.startswith("@"):
            next_item = messages[index + 1] if index + 1 < len(messages) else None
            threads.append(
                {
                    "name": item,
                    "preview": next_item if next_item and next_item not in NOISE_TOKENS else None,
                }
            )

    return {
        "unread_messages": unread_count,
        "thread_candidates": threads[:8],
        "partnership_signals": partnership_signals,
    }


def summarize_message_threads(threads: List[dict], rows: List[str]) -> dict:
    cleaned_threads = []
    unread_threads = []
    partnership_signals = []

    for thread in threads:
        parts = [part for part in thread.get("parts", []) if part and part not in NOISE_TOKENS]
        if not parts:
            continue

        name = parts[0]
        preview = parts[1] if len(parts) > 1 else None
        record = {
            "name": name,
            "preview": preview,
            "href": thread.get("href"),
        }
        cleaned_threads.append(record)

        joined = " ".join(parts).lower()
        if "new message" in joined or "unread" in joined:
            unread_threads.append(record)
        if "partnership" in joined:
            partnership_signals.append(record)

    if not cleaned_threads:
        for row in rows:
            record = {
                "name": row.split(" ")[0],
                "preview": row,
                "href": None,
            }
            cleaned_threads.append(record)
            lowered = row.lower()
            if "unread" in lowered or "new messages" in lowered:
                unread_threads.append(record)
            if "partnership" in lowered:
                partnership_signals.append(record)

    return {
        "thread_count": len(cleaned_threads),
        "threads": cleaned_threads[:8],
        "unread_threads": unread_threads[:8],
        "partnership_threads": partnership_signals[:8],
    }


def build_summary(payload: dict) -> dict:
    return {
        "captured_at": payload["captured_at"],
        "platform": payload["platform"],
        "account": "giantrockmeetingroom",
        "message_badge_count": extract_message_badge(payload["home_links"]),
        "notifications": summarize_notifications(payload["notifications_excerpt"]),
        "messages": summarize_messages(payload["messages_excerpt"]),
        "message_threads": summarize_message_threads(
            payload["message_threads"], payload["message_rows"]
        ),
        "recommended_next_actions": [
            "Review unread DMs first.",
            "Check partnership-related messages before general inbox browsing.",
            "Confirm whether new followers need follow-backs or outreach.",
        ],
    }


def main() -> None:
    args = parse_args()
    ensure_dirs()

    session_path = PLATFORM_FILES["instagram"]
    if not session_path.exists():
        raise SystemExit(
            f"Instagram session file not found: {session_path}. Run the login script first."
        )

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    snapshot_path = INBOX_DIR / f"instagram-snapshot-{timestamp}.json"
    summary_path = INBOX_DIR / f"instagram-summary-{timestamp}.json"
    home_shot = SCREENSHOT_DIR / f"instagram-home-{timestamp}.png"
    activity_shot = SCREENSHOT_DIR / f"instagram-activity-{timestamp}.png"
    inbox_shot = SCREENSHOT_DIR / f"instagram-dm-{timestamp}.png"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.show_browser)
        context = browser.new_context(storage_state=str(session_path))
        page = context.new_page()

        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        dismiss_cookie_or_modal_ui(page)
        page.screenshot(path=str(home_shot), full_page=True)

        home_title = page.title()
        home_url = page.url
        home_links = collect_links(page)
        notifications = collect_notifications(page)
        page.screenshot(path=str(activity_shot), full_page=True)
        messages = collect_messages(page)
        message_threads = collect_message_threads(page)
        inbox_debug_links = collect_inbox_debug_links(page)
        message_rows = collect_message_rows(page)
        page.screenshot(path=str(inbox_shot), full_page=True)

        browser.close()

    payload = {
        "captured_at": datetime.now().astimezone().isoformat(),
        "platform": "instagram",
        "home_title": home_title,
        "home_url": home_url,
        "home_links": home_links,
        "notifications_excerpt": notifications,
        "messages_excerpt": messages,
        "message_threads": message_threads,
        "message_rows": message_rows,
        "inbox_debug_links": inbox_debug_links,
        "screenshots": {
            "home": str(home_shot),
            "activity": str(activity_shot),
            "dm": str(inbox_shot),
        },
    }
    write_snapshot(snapshot_path, payload)
    summary = build_summary(payload)
    write_snapshot(summary_path, summary)

    latest_log = LOGS_DIR / "instagram-latest-check.json"
    latest_log.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    latest_summary = LOGS_DIR / "instagram-latest-summary.json"
    latest_summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"Saved Instagram snapshot to {snapshot_path}")
    print(f"Saved Instagram summary to {summary_path}")


if __name__ == "__main__":
    main()
