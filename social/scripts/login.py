#!/usr/bin/env python3
"""
Open a login browser for a supported platform and save authenticated session
state after the user confirms login is complete.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SESSIONS_DIR = ROOT / "social" / "sessions"
LOGS_DIR = ROOT / "social" / "logs"
BROWSERS_DIR = ROOT / ".playwright-browsers"

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS_DIR))

from playwright.sync_api import sync_playwright

PLATFORMS = {
    "x": {
        "label": "X",
        "login_url": "https://x.com/i/flow/login",
        "session_file": "x-session.json",
    },
    "instagram": {
        "label": "Instagram",
        "login_url": "https://www.instagram.com/accounts/login/",
        "session_file": "instagram-session.json",
    },
    "linkedin": {
        "label": "LinkedIn",
        "login_url": "https://www.linkedin.com/login",
        "session_file": "linkedin-session.json",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Save an authenticated browser session.")
    parser.add_argument(
        "--platform",
        choices=sorted(PLATFORMS.keys()),
        required=True,
        help="Platform to open for login.",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Show the browser window. Login normally needs this.",
    )
    return parser.parse_args()


def ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def write_log(platform_key: str, session_path: Path) -> None:
    payload = {
        "action": "login_session_saved",
        "platform": platform_key,
        "session_path": str(session_path),
    }
    log_path = LOGS_DIR / f"{platform_key}-latest-login.json"
    log_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    ensure_dirs()

    platform = PLATFORMS[args.platform]
    session_path = SESSIONS_DIR / platform["session_file"]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.show_browser)
        context = browser.new_context()
        page = context.new_page()
        page.goto(platform["login_url"], wait_until="domcontentloaded")

        print(f"Opened {platform['label']} login page.")
        print("Log in manually in the browser window.")
        input("Press Enter here after login is complete and the home feed is visible...")

        context.storage_state(path=str(session_path))
        write_log(args.platform, session_path)
        browser.close()

    print(f"Saved session to {session_path}")


if __name__ == "__main__":
    main()
