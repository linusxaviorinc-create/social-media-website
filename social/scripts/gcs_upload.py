#!/usr/bin/env python3
"""
Host a prepared flyer in a private Google Cloud Storage bucket for Meta to fetch.

Meta's publishing API does not accept a file upload: it needs a URL its servers
can fetch once. Rather than leaving flyers on a public bucket, this uploads to a
private bucket and hands Meta a V4 signed URL that expires in about an hour. A
bucket lifecycle rule deletes the objects after 7 days as a backstop, and this
module warns when that rule is missing.

This shells out to the gcloud CLI instead of using the Python client library,
because both credential paths that library needs are closed on this Workspace
org: service account keys are blocked by the org policy
constraints/iam.disableServiceAccountKeyCreation, and Application Default
Credentials fail because the ADC OAuth client is not allowlisted. The gcloud
CLI's own credentials do work. This repo already shells out to the gog CLI for
Gmail and Drive, so the pattern is consistent.

Signing is done by impersonating a service account, so no private key is ever
stored on disk.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from instagram_graph_config import GcsConfig


PUBLIC_MEMBERS = {"allUsers", "allAuthenticatedUsers"}

_REGION_CACHE: dict[str, str] = {}


def _gcloud(config: GcsConfig) -> str:
    resolved = shutil.which(config.gcloud_path or "gcloud")
    if not resolved:
        raise SystemExit(
            f"The gcloud CLI was not found on PATH as '{config.gcloud_path or 'gcloud'}'.\n"
            "Install it with: brew install --cask gcloud-cli\n"
            "Then run: gcloud auth login"
        )
    return resolved


def _run(config: GcsConfig, args: list[str], timeout: int = 300) -> str:
    """Run one gcloud command and return stdout, raising a readable error."""
    result = subprocess.run(
        [_gcloud(config)] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        # Drop the impersonation notice so the real error is not buried.
        detail = "\n".join(
            line for line in stderr.splitlines()
            if "using service account impersonation" not in line
        ).strip()
        raise SystemExit(f"gcloud {' '.join(args[:2])} failed:\n{detail or stderr}")
    return (result.stdout or "").strip()


def _key_file(config: GcsConfig) -> Path | None:
    if not config.service_account_key_file:
        return None
    return Path(str(config.service_account_key_file)).expanduser()


def bucket_region(config: GcsConfig) -> str:
    """Signing needs the bucket's region, which the signer cannot always look up.

    The impersonated service account only holds object permissions, not
    storage.buckets.get, so gcloud cannot auto-detect the region while
    impersonating. Detecting it here as the logged-in user avoids granting the
    service account any bucket-level access it does not otherwise need.
    """
    if config.region:
        return config.region
    if config.bucket in _REGION_CACHE:
        return _REGION_CACHE[config.bucket]

    location = _run(
        config,
        ["storage", "buckets", "describe", f"gs://{config.bucket}", "--format", "value(location)"],
    )
    if not location:
        raise SystemExit(
            f"Could not determine the region for gs://{config.bucket}. "
            "Set gcs.region in the config file."
        )
    _REGION_CACHE[config.bucket] = location
    return location


def build_object_name(prefix: str, stem: str) -> str:
    """Namespace objects by date and add a random suffix so runs never collide."""
    safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-").lower() or "flyer"
    today = dt.datetime.now().astimezone()
    return (
        f"{prefix.strip('/')}/{today:%Y/%m/%d}/"
        f"{today:%Y%m%d-%H%M%S}-{safe_stem}-{secrets.token_hex(4)}.jpg"
    )


def _signing_args(config: GcsConfig) -> list[str]:
    key_file = _key_file(config)
    if config.signing_mode in ("auto", "key") and key_file and key_file.exists():
        return ["--private-key-file", str(key_file)]
    if config.impersonate_service_account:
        return ["--impersonate-service-account", config.impersonate_service_account]
    raise SystemExit(
        "No way to sign a URL is configured.\n"
        "Set gcs.impersonate_service_account to a service account you hold the "
        "Service Account Token Creator role on, or gcs.service_account_key_file "
        "to a key stored outside this repo."
    )


def upload_and_sign(
    config: GcsConfig,
    local_path: Path,
    object_name: str,
    content_type: str = "image/jpeg",
) -> dict[str, Any]:
    """Upload one file to the private bucket and return a short-lived signed URL."""
    uri = f"gs://{config.bucket}/{object_name}"
    _run(config, ["storage", "cp", str(local_path), uri, "--content-type", content_type])

    ttl = int(config.signed_url_ttl_seconds)
    signed_url = _run(
        config,
        [
            "storage", "sign-url", uri,
            "--duration", f"{ttl}s",
            "--http-verb", "GET",
            "--region", bucket_region(config),
            "--format", "value(signed_url)",
        ]
        + _signing_args(config),
    )
    if not signed_url.startswith("http"):
        raise SystemExit(f"gcloud did not return a signed URL for {uri}.")

    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl)
    return {
        "bucket": config.bucket,
        "object_name": object_name,
        "gs_uri": uri,
        "signed_url": signed_url,
        "signed_url_ttl_seconds": ttl,
        "signed_url_expires_at": expires_at.isoformat(),
        "size_bytes": local_path.stat().st_size,
    }


def verify_fetchable(signed_url: str, timeout: int = 30) -> dict[str, Any]:
    """Fetch the first byte to confirm the signed URL works before Meta tries it.

    A GET-signed V4 URL rejects a HEAD request, so this asks for one byte.
    """
    request = urllib.request.Request(signed_url, headers={"Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {
                "ok": True,
                "status": response.status,
                "content_type": response.headers.get("Content-Type"),
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": exc.reason}
    except urllib.error.URLError as exc:
        return {"ok": False, "status": None, "error": str(exc.reason)}


def inspect_bucket(config: GcsConfig) -> dict[str, Any]:
    """Report whether the bucket is private and has the 7-day delete backstop."""
    report: dict[str, Any] = {
        "bucket": config.bucket,
        "exists": False,
        "lifecycle_ok": False,
        "lifecycle_rules": [],
        "public_members": [],
        "warnings": [],
    }

    try:
        raw = _run(
            config,
            ["storage", "buckets", "describe", f"gs://{config.bucket}", "--format", "json"],
        )
    except SystemExit as exc:
        report["warnings"].append(str(exc))
        return report

    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        report["warnings"].append(f"Could not parse the description of gs://{config.bucket}.")
        return report

    report["exists"] = True
    report["location"] = data.get("location")
    report["uniform_bucket_level_access"] = bool(data.get("uniform_bucket_level_access"))
    report["public_access_prevention"] = data.get("public_access_prevention")

    lifecycle = data.get("lifecycle_config") or data.get("lifecycle") or {}
    rules = lifecycle.get("rule") or []
    report["lifecycle_rules"] = rules
    for rule in rules:
        action = (rule.get("action") or {}).get("type")
        age = (rule.get("condition") or {}).get("age")
        if action == "Delete" and age is not None and int(age) <= config.expected_lifecycle_days:
            report["lifecycle_ok"] = True
            report["lifecycle_days"] = int(age)
            break
    if not report["lifecycle_ok"]:
        report["warnings"].append(
            f"No Delete lifecycle rule of {config.expected_lifecycle_days} days or fewer. "
            "Flyers uploaded for publishing will not be cleaned up automatically."
        )

    if report["public_access_prevention"] == "enforced":
        # Public access prevention makes a public grant impossible, so an IAM
        # read is not needed to know the bucket is private.
        return report

    try:
        policy_raw = _run(
            config,
            ["storage", "buckets", "get-iam-policy", f"gs://{config.bucket}", "--format", "json"],
        )
        policy = json.loads(policy_raw or "{}")
        for binding in policy.get("bindings") or []:
            exposed = PUBLIC_MEMBERS.intersection(set(binding.get("members") or []))
            if exposed:
                report["public_members"].append(
                    {"role": binding.get("role"), "members": sorted(exposed)}
                )
        if report["public_members"]:
            report["warnings"].append(
                "Bucket grants access to allUsers or allAuthenticatedUsers. "
                "It should stay private and rely on signed URLs."
            )
    except SystemExit as exc:
        report["public_members"] = None
        report["warnings"].append(f"Could not check public access: {exc}")

    return report


def delete_object(config: GcsConfig, object_name: str) -> bool:
    """Remove one uploaded object. The lifecycle rule is still the real backstop."""
    try:
        _run(config, ["storage", "rm", f"gs://{config.bucket}/{object_name}"])
        return True
    except Exception:
        return False
