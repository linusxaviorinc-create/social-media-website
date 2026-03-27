#!/usr/bin/env python3
"""
Create a repost-review manifest from the latest Instagram reshare log.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from platforms import ROOT


LOGS_DIR = ROOT / "social" / "logs"
REPOSTS_DIR = ROOT / "social" / "content" / "reposts"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Missing required log: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def outreach_state(handle: str, thread_label: str) -> dict[str, Any]:
    dm_send = load_optional_json(LOGS_DIR / "instagram-latest-dm-send.json")
    if not dm_send:
        return {
            "status": "needs_review",
            "summary": "Confirm rights, add credit language, and prep approval package.",
            "next_step": "Verify the story/post can be reshared and request a resend if the surface is unavailable.",
            "outreach_sent": False,
        }

    sent_thread = (dm_send.get("thread_label") or "").lower()
    reply = (dm_send.get("reply") or "").lower()
    handle_match = handle.lower() in sent_thread or handle.lower() in thread_label.lower()
    resend_language = "tag us again" in reply or "resend it our way" in reply or "send it over one more time" in reply

    if handle_match and resend_language:
        return {
            "status": "waiting_external",
            "summary": "Reshare request was already sent. Waiting for a fresh tag or resend from the artist.",
            "next_step": "Watch for a new tag, story mention, or resent asset, then prepare the reshare flow again.",
            "outreach_sent": True,
            "outreach_log": str(LOGS_DIR / "instagram-latest-dm-send.json"),
        }

    return {
        "status": "needs_review",
        "summary": "Confirm rights, add credit language, and prep approval package.",
        "next_step": "Verify the story/post can be reshared and request a resend if the surface is unavailable.",
        "outreach_sent": False,
    }


def main() -> None:
    REPOSTS_DIR.mkdir(parents=True, exist_ok=True)
    reshare = load_json(LOGS_DIR / "instagram-latest-story-reshare.json")
    handle = reshare.get("handle") or "unknown"
    output_path = REPOSTS_DIR / f"instagram-{handle}-latest.json"

    story_state = reshare.get("story_state", {})
    policy = reshare.get("policy", {})
    thread_label = reshare.get("thread_label") or ""
    outreach = outreach_state(handle, thread_label)
    payload: dict[str, Any] = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "platform": "instagram",
        "status": outreach["status"],
        "priority": 80,
        "approval_required": not outreach["outreach_sent"],
        "source_type": "repost_candidate",
        "title": f"Review repost candidate from {handle}",
        "summary": outreach["summary"],
        "next_step": outreach["next_step"],
        "handle": handle,
        "thread_label": thread_label,
        "policy": policy,
        "story_state": story_state,
        "rights_status": "needs_permission_review",
        "source_log": str(LOGS_DIR / "instagram-latest-story-reshare.json"),
        "outreach_sent": outreach["outreach_sent"],
    }
    if outreach.get("outreach_log"):
        payload["outreach_log"] = outreach["outreach_log"]
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Saved repost candidate to {output_path}")


if __name__ == "__main__":
    main()
