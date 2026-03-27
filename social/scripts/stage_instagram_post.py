#!/usr/bin/env python3
"""
Stage an Instagram post from a markdown draft without publishing it.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from check_instagram import dismiss_cookie_or_modal_ui
from platforms import PLATFORM_FILES, ROOT


CONTENT_DRAFTS_DIR = ROOT / "social" / "content" / "drafts"
CONTENT_APPROVED_DIR = ROOT / "social" / "content" / "approved"
CONTENT_POSTED_DIR = ROOT / "social" / "content" / "posted"
LOGS_DIR = ROOT / "social" / "logs"
INBOX_DIR = ROOT / "social" / "inbox"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage an Instagram post from a draft file.")
    parser.add_argument("--draft", help="Path to a markdown draft file. Defaults to the latest draft.")
    parser.add_argument("--show-browser", action="store_true", help="Show the browser window while running.")
    parser.add_argument("--share", action="store_true", help="Actually publish the staged post.")
    return parser.parse_args()


def latest_draft_path() -> Path:
    approved = sorted(CONTENT_APPROVED_DIR.glob("*.md"))
    if approved:
        return approved[-1]

    drafts = sorted(CONTENT_DRAFTS_DIR.glob("*.md"))
    if not drafts:
        raise SystemExit("No content drafts found. Run draft_asset_post.py first.")
    return drafts[-1]


def load_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise SystemExit(f"Draft is missing frontmatter: {path}")
    _, rest = text.split("---\n", 1)
    frontmatter, _ = rest.split("\n---\n", 1)
    return yaml.safe_load(frontmatter)


def split_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise SystemExit(f"Draft is missing frontmatter: {path}")
    _, rest = text.split("---\n", 1)
    frontmatter_text, body = rest.split("\n---\n", 1)
    return yaml.safe_load(frontmatter_text), body


def render_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    rendered_frontmatter = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{rendered_frontmatter}\n---\n{body.lstrip()}"


def open_home(page: Page) -> None:
    page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    dismiss_cookie_or_modal_ui(page)


def open_create_dialog(page: Page) -> None:
    candidates = [
        page.get_by_text("Create", exact=False),
        page.get_by_text("New post", exact=False),
        page.get_by_role("link", name="Create"),
        page.get_by_role("button", name="Create"),
        page.locator("[aria-label='New post']"),
        page.locator("[aria-label='Create']"),
    ]
    for candidate in candidates:
        try:
            if candidate.count():
                candidate.first.click(timeout=3000)
                page.wait_for_timeout(1500)
                return
        except Exception:
            continue
    raise SystemExit("Could not open the Instagram create-post flow.")


def upload_assets(page: Page, assets: list[str]) -> None:
    file_input = page.locator("input[type='file']")
    if not file_input.count():
        buttons = [
            page.get_by_role("button", name="Select from computer"),
            page.get_by_text("Select from computer", exact=False),
            page.get_by_text("Select from Computer", exact=False),
        ]
        for button in buttons:
            try:
                if button.count():
                    button.first.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    break
            except Exception:
                continue
        file_input = page.locator("input[type='file']")

    if not file_input.count():
        file_input = page.locator("input[accept*='image'], input[accept*='video']")

    if not file_input.count():
        raise SystemExit("Instagram file input not found in the create-post flow.")

    file_input.first.set_input_files(assets)
    page.wait_for_timeout(3000)


def click_next(page: Page, attempts: int = 2) -> None:
    for _ in range(attempts):
        candidates = [
            page.get_by_role("button", name="Next"),
            page.get_by_text("Next", exact=False),
        ]
        clicked = False
        for candidate in candidates:
            try:
                if candidate.count():
                    candidate.first.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            return


def fill_caption(page: Page, caption: str) -> None:
    candidates = [
        page.locator("textarea"),
        page.locator("[contenteditable='true'][role='textbox']"),
        page.locator("[aria-label*='Write a caption']"),
    ]
    for candidate in candidates:
        try:
            if candidate.count():
                candidate.first.click(timeout=3000)
                try:
                    candidate.first.fill(caption, timeout=3000)
                except Exception:
                    candidate.first.press_sequentially(caption, timeout=6000)
                return
        except Exception:
            continue
    raise SystemExit("Could not find the Instagram caption field.")


def click_share(page: Page) -> None:
    candidates = [
        page.get_by_role("button", name="Share"),
        page.get_by_text("Share", exact=False),
    ]
    for candidate in candidates:
        try:
            if candidate.count():
                candidate.first.click(timeout=3000)
                page.wait_for_timeout(2500)
                return
        except Exception:
            continue
    raise SystemExit("Could not find the Instagram Share button.")


def archive_posted_draft(draft_path: Path) -> Path:
    CONTENT_POSTED_DIR.mkdir(parents=True, exist_ok=True)
    frontmatter, body = split_frontmatter(draft_path)
    frontmatter["status"] = "posted"
    frontmatter["publish_intent"] = "completed"
    target_path = CONTENT_POSTED_DIR / draft_path.name
    target_path.write_text(render_frontmatter(frontmatter, body), encoding="utf-8")
    if draft_path != target_path and draft_path.exists():
        draft_path.unlink()
    return target_path


def collect_debug_state(page: Page) -> dict[str, Any]:
    body_text = ""
    try:
        body_text = " ".join(page.locator("body").inner_text(timeout=3000).split())
    except Exception:
        body_text = ""

    visible_actions: list[str] = []
    try:
        buttons = page.locator("button, a, [role='button'], [role='link']")
        count = min(buttons.count(), 20)
        for index in range(count):
            label = " ".join((buttons.nth(index).inner_text(timeout=800) or "").split())
            if label and label not in visible_actions:
                visible_actions.append(label)
    except Exception:
        pass

    return {
        "url": page.url,
        "body_excerpt": body_text[:800],
        "visible_actions": visible_actions,
    }


def main() -> None:
    args = parse_args()
    draft_path = Path(args.draft).resolve() if args.draft else latest_draft_path()
    frontmatter = load_frontmatter(draft_path)
    assets = frontmatter.get("assets", [])
    caption = frontmatter.get("caption", "")
    if not assets or not caption:
        raise SystemExit(f"Draft is missing assets or caption: {draft_path}")

    run_stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    screenshot_path = INBOX_DIR / "screenshots" / f"instagram-post-stage-{run_stamp}.png"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.show_browser)
        context = browser.new_context(storage_state=str(PLATFORM_FILES["instagram"]))
        page = context.new_page()
        try:
            open_home(page)
            open_create_dialog(page)
            upload_assets(page, assets)
            click_next(page, attempts=2)
            fill_caption(page, caption)
            if args.share:
                click_share(page)

            final_draft_path = archive_posted_draft(draft_path) if args.share else draft_path

            payload = {
                "draft": str(final_draft_path),
                "assets": assets,
                "caption": caption,
                "source_status": frontmatter.get("status", "unknown"),
                "status": "shared" if args.share else "staged_only",
            }
            log_name = "instagram-latest-post-share.json" if args.share else "instagram-latest-post-stage.json"
            (LOGS_DIR / log_name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
            except PlaywrightTimeoutError:
                pass
            debug_payload = {
                "draft": str(draft_path),
                "assets": assets,
                "caption": caption,
                "status": "failed",
                "error": str(exc),
                "screenshot": str(screenshot_path),
                "debug_state": collect_debug_state(page),
            }
            (LOGS_DIR / "instagram-latest-post-stage-debug.json").write_text(
                json.dumps(debug_payload, indent=2) + "\n",
                encoding="utf-8",
            )
            browser.close()
            raise
        browser.close()

    print(f"Instagram post {payload['status']}: {final_draft_path}")


if __name__ == "__main__":
    main()
