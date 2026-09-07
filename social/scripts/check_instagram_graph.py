#!/usr/bin/env python3
"""
Verify the official Instagram publishing setup before trying to post.

Checks the access token and its scopes, the linked Instagram professional
account, the remaining publishing quota, and the GCS bucket used to host images.
Everything here is read-only unless --check-upload is passed, which round-trips
one tiny throwaway object and deletes it afterwards.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import gcs_upload
from instagram_graph_config import (
    GraphError,
    graph_request,
    load_config,
    redact,
)
from platforms import ROOT


LOGS_DIR = ROOT / "social" / "logs"
CHECK_LOG = LOGS_DIR / "instagram-graph-check.json"

REQUIRED_SCOPES = ("instagram_basic", "instagram_content_publish", "pages_read_engagement")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the Instagram Graph API publishing setup.")
    parser.add_argument("--config", help="Path to the Instagram Graph config file.")
    parser.add_argument(
        "--check-upload",
        action="store_true",
        help="Round-trip a small test object through the bucket and a signed URL.",
    )
    parser.add_argument(
        "--exchange-token",
        action="store_true",
        help="Exchange the current short-lived token for a long-lived one. Needs app_id and app_secret.",
    )
    parser.add_argument(
        "--output",
        help="Where to write the long-lived token. Required with --exchange-token. Keep it outside this repo.",
    )
    return parser.parse_args()


def check_token(config: Any) -> dict[str, Any]:
    """Inspect the token's validity, scopes, and expiry."""
    inspector = (
        f"{config.app_id}|{config.app_secret}"
        if config.app_id and config.app_secret
        else config.access_token
    )
    try:
        payload = graph_request(
            config,
            "debug_token",
            {"input_token": config.access_token, "access_token": inspector},
        )
    except GraphError as exc:
        return {"ok": False, "error": exc.as_dict()}

    data = payload.get("data") or {}
    scopes = data.get("scopes") or []
    expires_at = data.get("expires_at")
    result: dict[str, Any] = {
        "ok": bool(data.get("is_valid")),
        "type": data.get("type"),
        "app_id": data.get("app_id"),
        "scopes": scopes,
        "missing_scopes": [scope for scope in REQUIRED_SCOPES if scope not in scopes],
        "expires_at": expires_at,
    }
    if expires_at:
        expiry = dt.datetime.fromtimestamp(int(expires_at), tz=dt.timezone.utc)
        result["expires_at_readable"] = expiry.isoformat()
        result["days_until_expiry"] = round(
            (expiry - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400, 1
        )
    else:
        result["expires_at_readable"] = "never (long-lived or page token)"
    return result


def check_account(config: Any) -> dict[str, Any]:
    try:
        account = graph_request(
            config,
            config.instagram_user_id,
            {"fields": "id,username,name,followers_count,media_count"},
        )
    except GraphError as exc:
        return {"ok": False, "error": exc.as_dict()}
    account["ok"] = True
    return account


def check_page_link(config: Any) -> dict[str, Any]:
    if not config.facebook_page_id:
        return {"ok": None, "note": "No facebook_page_id configured, so the link was not verified."}
    try:
        page = graph_request(
            config,
            config.facebook_page_id,
            {"fields": "name,instagram_business_account{id,username}"},
        )
    except GraphError as exc:
        return {"ok": False, "error": exc.as_dict()}

    linked = (page.get("instagram_business_account") or {}).get("id")
    return {
        "ok": linked == config.instagram_user_id,
        "page_name": page.get("name"),
        "linked_instagram_user_id": linked,
        "note": (
            "Linked account matches the configured instagram_user_id."
            if linked == config.instagram_user_id
            else "The Page's linked Instagram account does not match instagram_user_id."
        ),
    }


def check_quota(config: Any) -> dict[str, Any]:
    try:
        payload = graph_request(
            config,
            f"{config.instagram_user_id}/content_publishing_limit",
            {"fields": "config,quota_usage"},
        )
    except GraphError as exc:
        return {"ok": False, "error": exc.as_dict()}
    rows = payload.get("data") or []
    if not rows:
        return {"ok": True, "note": "No quota data returned."}
    row = rows[0]
    return {
        "ok": True,
        "quota_usage": row.get("quota_usage"),
        "quota_total": (row.get("config") or {}).get("quota_total"),
    }


def check_upload_round_trip(config: Any) -> dict[str, Any]:
    """Prove the whole hosting path works: upload, sign, fetch, delete."""
    try:
        from PIL import Image
    except ImportError:
        return {"ok": False, "error": "Pillow is not installed, so the upload test was skipped."}

    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "signed-url-probe.jpg"
        Image.new("RGB", (320, 320), (240, 240, 240)).save(probe, format="JPEG", quality=70)

        object_name = gcs_upload.build_object_name(
            f"{config.gcs.object_prefix.strip('/')}/_healthcheck", "probe"
        )
        try:
            upload = gcs_upload.upload_and_sign(config.gcs, probe, object_name)
        except SystemExit as exc:
            return {"ok": False, "error": str(exc)}

        fetch = gcs_upload.verify_fetchable(upload["signed_url"])
        deleted = gcs_upload.delete_object(config.gcs, object_name)
        return {
            "ok": bool(fetch.get("ok")) and deleted,
            "object_name": object_name,
            "fetch": fetch,
            "expires_at": upload["signed_url_expires_at"],
            "cleaned_up": deleted,
        }


def exchange_long_lived_token(config: Any, output: str) -> dict[str, Any]:
    if not (config.app_id and config.app_secret):
        raise SystemExit("Token exchange needs app_id and app_secret in the config file.")

    payload = graph_request(
        config,
        "oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": config.app_id,
            "client_secret": config.app_secret,
            "fb_exchange_token": config.access_token,
            "access_token": None,
        },
    )
    token = payload.get("access_token")
    if not token:
        raise SystemExit(f"No token returned from the exchange: {payload}")

    target = Path(output).expanduser()
    if ROOT in target.resolve().parents or target.resolve() == ROOT:
        raise SystemExit(f"Refusing to write a token inside the repository: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(token + "\n", encoding="utf-8")
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)

    return {
        "written_to": str(target),
        "token": redact(token),
        "expires_in_days": round(int(payload.get("expires_in", 0)) / 86400, 1)
        if payload.get("expires_in")
        else None,
    }


def main() -> None:
    args = parse_args()
    # The token is optional here so the Google Cloud side can be verified while
    # the Meta app is still being set up.
    config = load_config(args.config, require_token=False)
    has_token = bool(config.access_token)

    print(f"Config:   {config.config_path}")
    print(f"API:      {config.api_version}")
    print(f"IG user:  {config.instagram_user_id}")
    print(f"Token:    {redact(config.access_token) if has_token else '(not set yet)'}")
    print(f"Bucket:   gs://{config.gcs.bucket}/{config.gcs.object_prefix}")
    print()

    report: dict[str, Any] = {
        "checked_at": dt.datetime.now().astimezone().isoformat(),
        "config_path": str(config.config_path),
        "api_version": config.api_version,
        "instagram_user_id": config.instagram_user_id,
    }

    if args.exchange_token:
        if not has_token:
            raise SystemExit("--exchange-token needs an existing short-lived token to exchange.")
        if not args.output:
            raise SystemExit("--exchange-token requires --output pointing outside this repo.")
        exchanged = exchange_long_lived_token(config, args.output)
        report["token_exchange"] = exchanged
        print(f"Long-lived token written to {exchanged['written_to']} ({exchanged['token']})")
        if exchanged.get("expires_in_days"):
            print(f"It expires in about {exchanged['expires_in_days']} days.")
        print("Point access_token_file at that path, then re-run this check.\n")

    skipped = {"ok": None, "note": "Skipped: no Instagram access token configured yet."}
    if has_token:
        report["token"] = check_token(config)
        report["account"] = check_account(config)
        report["page_link"] = check_page_link(config)
        report["quota"] = check_quota(config)
    else:
        report["token"] = report["account"] = dict(skipped)
        report["page_link"] = report["quota"] = dict(skipped)
    try:
        report["bucket"] = gcs_upload.inspect_bucket(config.gcs)
    except SystemExit as exc:
        # A bucket problem should not hide the Instagram side of the report.
        report["bucket"] = {"exists": False, "lifecycle_ok": False, "warnings": [str(exc)]}
    if args.check_upload:
        report["upload_round_trip"] = check_upload_round_trip(config)

    if not has_token:
        print("Instagram checks:  skipped, no access token configured yet.")
        print("  Set $IG_GRAPH_ACCESS_TOKEN or fill in access_token_file, then re-run.\n")

    token = report["token"]
    print(f"Token valid:      {token.get('ok')}")
    if token.get("error"):
        print(f"  error:          {token['error'].get('message')}")
    if token.get("scopes"):
        print(f"  scopes:         {', '.join(token['scopes'])}")
    if token.get("missing_scopes"):
        print(f"  MISSING scopes: {', '.join(token['missing_scopes'])}")
    if token.get("expires_at_readable"):
        print(f"  expires:        {token.get('expires_at_readable')}")

    account = report["account"]
    if account.get("ok"):
        print(f"Instagram account: @{account.get('username')} ({account.get('media_count')} posts)")
    else:
        print(f"Instagram account: FAILED {account.get('error', {}).get('message')}")

    page = report["page_link"]
    print(f"Page link:        {page.get('note') or page.get('error', {}).get('message')}")

    quota = report["quota"]
    if quota.get("ok"):
        print(f"Publishing quota: {quota.get('quota_usage')} used of {quota.get('quota_total')} per 24h")
    else:
        print(f"Publishing quota: FAILED {quota.get('error', {}).get('message')}")

    bucket = report["bucket"]
    print(f"Bucket exists:    {bucket.get('exists')}")
    print(f"7-day cleanup:    {bucket.get('lifecycle_ok')}")
    if args.check_upload:
        print(f"Signed URL test:  {report['upload_round_trip'].get('ok')}")

    for warning in bucket.get("warnings") or []:
        print(f"Warning: {warning}")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    CHECK_LOG.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nSaved the full report to {CHECK_LOG}")


if __name__ == "__main__":
    main()
