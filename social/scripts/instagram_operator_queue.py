#!/usr/bin/env python3
"""
Build a single operator-friendly summary from the latest Instagram workflow logs.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from policy import extract_handle, normalize_thread_match
from platforms import ROOT


LOGS_DIR = ROOT / "social" / "logs"
CONTENT_DRAFTS_DIR = ROOT / "social" / "content" / "drafts"
CONTENT_APPROVED_DIR = ROOT / "social" / "content" / "approved"
CONTENT_STAGING_DIR = ROOT / "social" / "content" / "staging"
CONTENT_REPOSTS_DIR = ROOT / "social" / "content" / "reposts"
ASSETS_DIR = ROOT / "social" / "assets"
DM_DRAFTS_DIR = ROOT / "social" / "inbox" / "reply-drafts"
HISTORY_PATH = LOGS_DIR / "instagram-operator-history.json"
BACKLOG_PATH = LOGS_DIR / "instagram-operator-backlog.json"


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def parse_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    _, rest = text.split("---\n", 1)
    frontmatter, _ = rest.split("\n---\n", 1)
    data: dict[str, Any] = {}
    for raw_line in frontmatter.splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def stageable_assets(assets: list[str]) -> bool:
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".m4v"}
    if not assets:
        return False
    for asset in assets:
        asset_path = Path(asset)
        if not asset_path.exists():
            return False
        if asset_path.name.startswith("."):
            return False
        if asset_path.suffix.lower() not in allowed_suffixes:
            return False
    return True


def load_manifest(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        payload = load_json(path) or {}
    elif path.suffix.lower() == ".md":
        payload = parse_frontmatter(path)
    else:
        payload = {}
    payload["source_path"] = str(path)
    return payload


def recent_dm_drafts(limit: int = 10) -> list[dict[str, Any]]:
    if not DM_DRAFTS_DIR.exists():
        return []

    drafts: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    for path in sorted(DM_DRAFTS_DIR.glob("instagram-dm-draft-*.json"), reverse=True):
        payload = load_json(path)
        if not payload:
            continue
        thread_label = (payload.get("thread_label") or "").strip()
        handle = extract_handle(thread_label)
        identity = handle or normalize_thread_key(thread_label)
        if handle == "giantrockmeetingroom":
            continue
        if not thread_label or identity in seen_identities:
            continue
        seen_identities.add(identity)
        payload["source_path"] = str(path)
        drafts.append(payload)
        if len(drafts) >= limit:
            break
    return drafts


def normalize_thread_key(thread_label: str) -> str:
    normalized, soft_match, name_match = normalize_thread_match(thread_label)
    return soft_match or name_match or normalized or thread_label.strip().lower()


def thread_identity(thread_label: str) -> str:
    handle = extract_handle(thread_label)
    return handle or normalize_thread_key(thread_label)


def manifest_items(directory: Path, item_type: str, action: str, default_status: str, lane: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not directory.exists():
        return items
    seen_identity: set[str] = set()
    for path in sorted(directory.glob("*"), reverse=True)[:10]:
        if path.name.startswith(".") or not path.is_file():
            continue
        if path.stem.endswith("template"):
            continue
        manifest = load_manifest(path)
        if item_type == "content_stage" and str(manifest.get("status", "")).lower() == "approved":
            continue
        identity = (
            str(manifest.get("handle") or "")
            or str(manifest.get("source_draft") or "")
            or str(manifest.get("title") or path.stem)
        )
        if identity in seen_identity:
            continue
        seen_identity.add(identity)
        title = manifest.get("title") or path.stem
        item = {
            "type": item_type,
            "draft": str(path),
            "status": manifest.get("status", default_status),
            "action": action,
            "details": {
                "title": title,
                "lane": lane,
                "summary": manifest.get("summary", ""),
            },
        }
        items.append(item)
    return items


def pending_asset_items(limit: int = 5) -> list[dict[str, Any]]:
    if not ASSETS_DIR.exists():
        return []
    known_assets: set[str] = set()
    for directory in [CONTENT_DRAFTS_DIR, CONTENT_APPROVED_DIR, CONTENT_STAGING_DIR]:
        for path in directory.glob("*"):
            if not path.is_file() or path.name.startswith("."):
                continue
            manifest = load_manifest(path)
            for asset in manifest.get("assets", []) if isinstance(manifest.get("assets"), list) else []:
                known_assets.add(str(Path(asset).resolve()))

    items: list[dict[str, Any]] = []
    candidates = [
        path
        for path in ASSETS_DIR.rglob("*")
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".m4v", ".pdf"}
    ]
    for path in sorted(candidates, key=lambda candidate: candidate.stat().st_mtime, reverse=True):
        resolved = str(path.resolve())
        if resolved in known_assets:
            continue
        items.append(
            {
                "type": "asset_intake",
                "draft": resolved,
                "status": "review",
                "action": "draft_from_asset",
                "details": {
                    "title": path.name,
                    "lane": "assets",
                    "summary": "New asset is available for caption drafting and staging.",
                },
            }
        )
        if len(items) >= limit:
            break
    return items


def normalize_status(status: str | None) -> str:
    if not status:
        return "review"
    normalized = status.strip().lower()
    if normalized in {"needs_review", "ready_for_review", "draft_for_approval"}:
        return "review"
    if normalized in {"staged", "staged_only"}:
        return "ready"
    if normalized in {"awaiting_resend", "story_unavailable", "waiting_external"}:
        return "blocked"
    return normalized


def item_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    status_rank = {
        "ready": 0,
        "review": 1,
        "blocked": 2,
        "done": 3,
    }
    item_rank = {
        "dm_reply": 0,
        "story_reshare": 1,
        "dm_triage": 2,
        "partnership_triage": 3,
        "repost_candidate": 4,
        "content_stage": 5,
        "asset_intake": 6,
        "content_post": 7,
    }
    status = normalize_status(item.get("status"))
    return (
        status_rank.get(status, 9),
        item_rank.get(item.get("type", ""), 9),
        item.get("thread_label") or item.get("draft") or item.get("handle") or "",
    )


def build_queue() -> dict[str, Any]:
    dm_draft = load_json(LOGS_DIR / "instagram-latest-dm-draft.json")
    dm_send = load_json(LOGS_DIR / "instagram-latest-dm-send.json")
    dm_stage = load_json(LOGS_DIR / "instagram-latest-dm-stage.json")
    dm_compose_debug = load_json(LOGS_DIR / "instagram-latest-dm-compose-debug.json")
    reshare = load_json(LOGS_DIR / "instagram-latest-story-reshare.json")
    reshare_prepare = load_json(LOGS_DIR / "instagram-latest-story-prepare.json")
    summary = load_json(LOGS_DIR / "instagram-latest-summary.json")
    post_stage = load_json(LOGS_DIR / "instagram-latest-post-stage.json")
    post_share = load_json(LOGS_DIR / "instagram-latest-post-share.json")

    items: list[dict[str, Any]] = []
    existing_keys: set[str] = set()

    if dm_send:
        item = {
            "type": "dm_reply",
            "thread_label": dm_send.get("thread_label"),
            "status": "done",
            "action": "reply_sent",
            "details": dm_send.get("reply"),
        }
        items.append(item)
        existing_keys.add(item_key(item))

    handled_thread_keys = {
        thread_identity(dm_send.get("thread_label", ""))
        for dm_send in [dm_send]
        if dm_send and dm_send.get("thread_label")
    }

    if dm_stage:
        item = {
            "type": "dm_reply",
            "thread_label": dm_stage.get("thread_label"),
            "status": "ready",
            "action": "confirm_send",
            "details": {
                "title": dm_stage.get("thread_label"),
                "lane": "dm",
                "summary": dm_stage.get("reply"),
                "source_draft": dm_stage.get("source_draft"),
                "stage_status": dm_stage.get("status"),
            },
        }
        if item_key(item) not in existing_keys:
            items.append(item)
            existing_keys.add(item_key(item))
        if dm_stage.get("thread_label"):
            handled_thread_keys.add(thread_identity(dm_stage.get("thread_label", "")))

    draft_candidates = recent_dm_drafts()
    if dm_draft:
        latest_thread = (dm_draft.get("thread_label") or "").strip()
        latest_key = normalize_thread_key(latest_thread)
        existing_draft_keys = {
            normalize_thread_key((d.get("thread_label") or "").strip())
            for d in draft_candidates
            if d.get("thread_label")
        }
        if latest_thread and latest_key not in existing_draft_keys:
            draft_candidates.insert(0, dm_draft)

    for candidate in draft_candidates:
        thread_key = thread_identity((candidate.get("thread_label") or "").strip())
        if thread_key in handled_thread_keys:
            continue
        status = "review"
        action = "review_draft"
        details: Any = candidate.get("suggested_reply")
        if (
            candidate.get("conversation_type") == "partnership_outreach"
            and dm_compose_debug
            and dm_compose_debug.get("reason") == "partnership_creator_selection_required"
            and normalize_thread_key(dm_compose_debug.get("body_excerpt", "")) != ""
        ):
            status = "blocked"
            action = "wait_for_partnership_creator_selection"
            details = {
                "title": candidate.get("thread_label"),
                "lane": "dm",
                "summary": "Instagram partnership inbox is still on the creator-selection surface, so the outreach draft cannot be staged yet.",
                "debug_reason": dm_compose_debug.get("reason"),
                "debug_screenshot": dm_compose_debug.get("screenshot"),
            }
        item = {
            "type": "dm_reply",
            "thread_label": candidate.get("thread_label"),
            "status": status,
            "action": action,
            "details": details,
        }
        if item_key(item) not in existing_keys:
            items.append(item)
            existing_keys.add(item_key(item))
            handled_thread_keys.add(thread_key)

    drafted_thread_keys = {
        thread_identity(item.get("thread_label", ""))
        for item in items
        if item.get("type") == "dm_reply"
    }

    if reshare_prepare and reshare_prepare.get("status") == "story_reshare_prepared":
        item = {
            "type": "story_reshare",
            "handle": extract_handle(reshare_prepare.get("thread_label", "")),
            "thread_label": reshare_prepare.get("thread_label"),
            "status": "ready",
            "action": "confirm_story_share",
            "details": {
                "title": reshare_prepare.get("thread_label"),
                "lane": "repost",
                "summary": "Live story mention has been opened into the story composer and is ready for final review/share.",
                "clicked_action": reshare_prepare.get("clicked_action"),
                "debug_screenshot": reshare_prepare.get("screenshot"),
            },
        }
        items.append(item)
        existing_keys.add(item_key(item))
    elif reshare:
        story_state = reshare.get("story_state", {})
        policy = reshare.get("policy", {})
        recommended = story_state.get("recommended_action")

        if recommended == "continue_reshare_flow":
            status = "ready"
            action = "prepare_reshare"
        elif recommended == "request_resend":
            status = "blocked"
            action = "wait_for_new_tag_or_story_resend"
        else:
            status = "review"
            action = "manual_check_story_surface"

        item = {
            "type": "story_reshare",
            "handle": reshare.get("handle"),
            "thread_label": reshare.get("thread_label"),
            "status": status,
            "action": action,
            "policy": policy.get("policy"),
            "details": story_state,
        }
        items.append(item)
        existing_keys.add(item_key(item))

    if post_share:
        item = {
            "type": "content_post",
            "draft": post_share.get("draft"),
            "status": "done",
            "action": "post_shared",
            "details": {
                "caption": post_share.get("caption"),
                "assets": post_share.get("assets", []),
            },
        }
        items.append(item)
        existing_keys.add(item_key(item))

    if post_stage:
        item = {
            "type": "content_post",
            "draft": post_stage.get("draft"),
            "status": "ready",
            "action": "confirm_post_share",
            "details": {
                "caption": post_stage.get("caption"),
                "assets": post_stage.get("assets", []),
            },
        }
        if item_key(item) not in existing_keys:
            items.append(item)
            existing_keys.add(item_key(item))

    approved_paths = sorted(CONTENT_APPROVED_DIR.glob("*.md"))
    for draft_path in approved_paths[-5:]:
        frontmatter = parse_frontmatter(draft_path)
        assets = frontmatter.get("assets", [])
        is_stageable = False
        if isinstance(assets, list):
            is_stageable = stageable_assets(assets)
        item = {
            "type": "content_post",
            "draft": str(draft_path),
            "status": "ready" if is_stageable else "review",
            "action": "stage_or_share_post" if is_stageable else "fix_post_assets",
            "details": {
                "title": draft_path.stem,
                "lane": "approved",
                "summary": "" if is_stageable else "Approved draft still needs real Instagram-safe assets before staging.",
            },
        }
        if item_key(item) not in existing_keys:
            items.append(item)
            existing_keys.add(item_key(item))

    for item in manifest_items(
        CONTENT_STAGING_DIR,
        item_type="content_stage",
        action="review_staging_manifest",
        default_status="review",
        lane="staging",
    ):
        if item_key(item) not in existing_keys:
            items.append(item)
            existing_keys.add(item_key(item))

    for item in manifest_items(
        CONTENT_REPOSTS_DIR,
        item_type="repost_candidate",
        action="review_repost_candidate",
        default_status="review",
        lane="repost",
    ):
        if item_key(item) not in existing_keys:
            items.append(item)
            existing_keys.add(item_key(item))

    for item in pending_asset_items():
        if item_key(item) not in existing_keys:
            items.append(item)
            existing_keys.add(item_key(item))

    draft_paths = sorted(CONTENT_DRAFTS_DIR.glob("*.md"))
    approved_names = {path.name for path in approved_paths}
    for draft_path in draft_paths[-5:]:
        if draft_path.name in approved_names:
            continue
        item = {
            "type": "content_post",
            "draft": str(draft_path),
            "status": "review",
            "action": "review_post_draft",
            "details": {
                "title": draft_path.stem,
                "lane": "draft",
            },
        }
        if item_key(item) not in existing_keys:
            items.append(item)
            existing_keys.add(item_key(item))

    if summary:
        unread_threads = summary.get("message_threads", {}).get("unread_threads", [])
        for thread in unread_threads:
            thread_key = thread_identity(thread.get("preview", "") or thread.get("name", ""))
            if thread_key in drafted_thread_keys:
                continue
            item = {
                "type": "dm_triage",
                "handle": extract_handle(thread.get("preview", "") or thread.get("name", "")),
                "thread_label": thread.get("preview"),
                "status": "review",
                "action": "draft_reply",
                "details": thread,
            }
            if item_key(item) not in existing_keys:
                items.append(item)
                existing_keys.add(item_key(item))

        partnership_threads = summary.get("message_threads", {}).get("partnership_threads", [])
        for thread in partnership_threads:
            thread_key = thread_identity(thread.get("preview", "") or thread.get("name", ""))
            if thread_key in drafted_thread_keys:
                continue
            item = {
                "type": "partnership_triage",
                "handle": extract_handle(thread.get("preview", "") or thread.get("name", "")),
                "thread_label": thread.get("preview"),
                "status": "review",
                "action": "draft_partnership_reply",
                "details": thread,
            }
            if item_key(item) not in existing_keys:
                items.append(item)
                existing_keys.add(item_key(item))

    for item in items:
        item["status"] = normalize_status(item.get("status"))

    items.sort(key=item_sort_key)

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "platform": "instagram",
        "items": items,
        "summary": {
            "total_items": len(items),
            "done": sum(1 for item in items if item["status"] == "done"),
            "ready": sum(1 for item in items if item["status"] == "ready"),
            "review": sum(1 for item in items if item["status"] == "review"),
            "blocked": sum(1 for item in items if item["status"] == "blocked"),
        },
    }


def item_key(item: dict[str, Any]) -> str:
    if item["type"] == "dm_reply":
        return f"dm_reply::{normalize_thread_key(item.get('thread_label', ''))}"
    if item["type"] == "story_reshare":
        return f"story_reshare::{item.get('handle', '')}"
    if item["type"] == "dm_triage":
        return f"dm_triage::{normalize_thread_key(item.get('handle', '') or item.get('thread_label', ''))}"
    if item["type"] == "partnership_triage":
        return f"partnership_triage::{normalize_thread_key(item.get('thread_label', ''))}"
    if item["type"] == "content_post":
        return f"content_post::{item.get('draft', '')}"
    if item["type"] == "content_stage":
        return f"content_stage::{item.get('draft', '')}"
    if item["type"] == "asset_intake":
        return f"asset_intake::{item.get('draft', '')}"
    if item["type"] == "repost_candidate":
        return f"repost_candidate::{item.get('draft', '')}"
    return json.dumps(item, sort_keys=True)


def load_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def update_history_and_backlog(queue: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    history = load_list(HISTORY_PATH)
    backlog = load_list(BACKLOG_PATH)

    for item in queue["items"]:
        keyed_item = {
            "key": item_key(item),
            "updated_at": queue["generated_at"],
            **item,
        }
        history.append(keyed_item)

        replaced = False
        for index, existing in enumerate(backlog):
            if existing.get("key") == keyed_item["key"]:
                backlog[index] = keyed_item
                replaced = True
                break
        if not replaced:
            backlog.append(keyed_item)

    return history, backlog


def main() -> None:
    payload = build_queue()
    queue_path = LOGS_DIR / "instagram-operator-queue.json"
    latest_path = LOGS_DIR / "instagram-latest-operator-queue.json"
    history, backlog = update_history_and_backlog(payload)
    rendered = json.dumps(payload, indent=2) + "\n"
    queue_path.write_text(rendered, encoding="utf-8")
    latest_path.write_text(rendered, encoding="utf-8")
    HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    BACKLOG_PATH.write_text(json.dumps(backlog, indent=2) + "\n", encoding="utf-8")
    print(f"Saved Instagram operator queue to {queue_path}")


if __name__ == "__main__":
    main()
