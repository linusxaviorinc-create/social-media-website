from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from platforms import ROOT


VOICE_DIR = ROOT / "social" / "voice"


def load_trusted_accounts() -> list[dict[str, Any]]:
    path = VOICE_DIR / "trusted-repost-accounts.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def extract_handle(thread_label: str) -> str:
    match = re.search(r"[a-z0-9._]+", thread_label.lower())
    return match.group(0) if match else ""


def classify_account(handle: str, accounts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    accounts = accounts if accounts is not None else load_trusted_accounts()
    for account in accounts:
        if account.get("handle", "").lower() == handle:
            return account
    return {
        "handle": handle,
        "category": "unknown",
        "policy": "review_first",
        "require_approval_to_post": True,
        "notes": "No trusted repost policy found for this handle.",
    }


def normalize_thread_match(match_text: str) -> tuple[str, str, str]:
    normalized_match = re.sub(r"\s+", " ", match_text).strip()
    soft_match = re.sub(r"\b\d+\s+new messages?\b", "", normalized_match, flags=re.IGNORECASE)
    soft_match = re.sub(r"\bunread\b", "", soft_match, flags=re.IGNORECASE).strip()
    soft_match = re.sub(r"\s+[·.-]\s+\d+[smhdw]\b", "", soft_match, flags=re.IGNORECASE).strip()
    soft_match = re.sub(r"\bsent an attachment\b", "", soft_match, flags=re.IGNORECASE).strip()
    name_match = extract_handle(soft_match) if soft_match else ""
    return normalized_match, soft_match, name_match

