#!/usr/bin/env python3
"""
Shared configuration and HTTP helpers for official Instagram Graph API posting.

This module is the only place that knows how to find the access token and the
Google Cloud Storage settings. Secrets never live in this repository: the token
comes from an environment variable or a file outside the repo, and anything
written to a log is redacted first.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platforms import ROOT


CONFIG_DIR = ROOT / "social" / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "instagram-graph.json"
EXAMPLE_CONFIG_PATH = CONFIG_DIR / "instagram-graph.example.json"

GRAPH_HOST = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v25.0"

CONFIG_PATH_ENV = "IG_GRAPH_CONFIG"
TOKEN_ENV = "IG_GRAPH_ACCESS_TOKEN"
USER_ID_ENV = "IG_GRAPH_USER_ID"
API_VERSION_ENV = "IG_GRAPH_API_VERSION"
BUCKET_ENV = "IG_GRAPH_GCS_BUCKET"


class GraphError(RuntimeError):
    """A Graph API call failed. Carries Meta's own error fields for triage."""

    def __init__(
        self,
        message: str,
        code: Any = None,
        subcode: Any = None,
        error_type: str | None = None,
        fbtrace_id: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.subcode = subcode
        self.error_type = error_type
        self.fbtrace_id = fbtrace_id
        self.http_status = http_status

    def as_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "code": self.code,
            "subcode": self.subcode,
            "type": self.error_type,
            "fbtrace_id": self.fbtrace_id,
            "http_status": self.http_status,
        }


@dataclass
class GcsConfig:
    bucket: str
    object_prefix: str = "instagram"
    signed_url_ttl_seconds: int = 3600
    signing_mode: str = "auto"
    service_account_key_file: str | None = None
    impersonate_service_account: str | None = None
    project: str | None = None
    expected_lifecycle_days: int = 7
    region: str | None = None
    gcloud_path: str = "gcloud"


@dataclass
class ImageConfig:
    fit: str = "pad"
    pad_color: str = "#ffffff"
    jpeg_quality: int = 90
    max_width: int = 1440


@dataclass
class GraphConfig:
    instagram_user_id: str
    access_token: str
    gcs: GcsConfig
    image: ImageConfig
    api_version: str = DEFAULT_API_VERSION
    facebook_page_id: str | None = None
    app_id: str | None = None
    app_secret: str | None = None
    config_path: Path | None = None


def redact(value: str | None) -> str:
    """Render a secret safe to print or log."""
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"***{value[-4:]}"


def redact_url(url: str | None) -> str:
    """Drop the query string so signed-URL signatures never reach a log file."""
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return f"{base}?<redacted-signature>" if parsed.query else base


def config_path_from_env(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    from_env = os.environ.get(CONFIG_PATH_ENV)
    if from_env:
        return Path(from_env).expanduser().resolve()
    return DEFAULT_CONFIG_PATH


def _read_token(raw: dict[str, Any]) -> str:
    from_env = os.environ.get(TOKEN_ENV, "").strip()
    if from_env:
        return from_env

    token_file = raw.get("access_token_file")
    if token_file:
        path = Path(str(token_file)).expanduser()
        if not path.exists():
            raise SystemExit(f"Access token file not found: {path}")
        return path.read_text(encoding="utf-8").strip()

    return str(raw.get("access_token") or "").strip()


def load_config(explicit_path: str | None = None, require_token: bool = True) -> GraphConfig:
    path = config_path_from_env(explicit_path)
    if not path.exists():
        raise SystemExit(
            f"Instagram Graph config not found: {path}\n"
            f"Copy {EXAMPLE_CONFIG_PATH} to {DEFAULT_CONFIG_PATH} and fill it in."
        )

    raw = json.loads(path.read_text(encoding="utf-8"))
    gcs_raw = dict(raw.get("gcs") or {})
    image_raw = dict(raw.get("image") or {})

    bucket = os.environ.get(BUCKET_ENV, "").strip() or str(gcs_raw.get("bucket") or "").strip()
    if not bucket:
        raise SystemExit(f"No GCS bucket configured. Set gcs.bucket in {path} or ${BUCKET_ENV}.")

    instagram_user_id = (
        os.environ.get(USER_ID_ENV, "").strip() or str(raw.get("instagram_user_id") or "").strip()
    )
    if not instagram_user_id:
        raise SystemExit(f"No instagram_user_id configured. Set it in {path} or ${USER_ID_ENV}.")

    token = _read_token(raw)
    if require_token and not token:
        raise SystemExit(
            "No Instagram access token found.\n"
            f"Set ${TOKEN_ENV}, or point access_token_file in {path} at a file outside this repo."
        )

    gcs = GcsConfig(
        bucket=bucket,
        object_prefix=str(gcs_raw.get("object_prefix") or "instagram"),
        signed_url_ttl_seconds=int(gcs_raw.get("signed_url_ttl_seconds") or 3600),
        signing_mode=str(gcs_raw.get("signing_mode") or "auto"),
        service_account_key_file=gcs_raw.get("service_account_key_file") or None,
        impersonate_service_account=gcs_raw.get("impersonate_service_account") or None,
        project=gcs_raw.get("project") or None,
        expected_lifecycle_days=int(gcs_raw.get("expected_lifecycle_days") or 7),
        region=gcs_raw.get("region") or None,
        gcloud_path=str(gcs_raw.get("gcloud_path") or "gcloud"),
    )

    image = ImageConfig(
        fit=str(image_raw.get("fit") or "pad"),
        pad_color=str(image_raw.get("pad_color") or "#ffffff"),
        jpeg_quality=int(image_raw.get("jpeg_quality") or 90),
        max_width=int(image_raw.get("max_width") or 1440),
    )

    return GraphConfig(
        instagram_user_id=instagram_user_id,
        access_token=token,
        gcs=gcs,
        image=image,
        api_version=(
            os.environ.get(API_VERSION_ENV, "").strip()
            or str(raw.get("graph_api_version") or DEFAULT_API_VERSION)
        ),
        facebook_page_id=str(raw.get("facebook_page_id") or "") or None,
        app_id=str(raw.get("app_id") or "") or None,
        app_secret=str(raw.get("app_secret") or "") or None,
        config_path=path,
    )


def _error_from_payload(payload: Any, http_status: int | None) -> GraphError:
    error = {}
    if isinstance(payload, dict):
        error = payload.get("error") or {}
    if not isinstance(error, dict):
        error = {}
    return GraphError(
        message=str(error.get("error_user_msg") or error.get("message") or "Unknown Graph API error."),
        code=error.get("code"),
        subcode=error.get("error_subcode"),
        error_type=error.get("type"),
        fbtrace_id=error.get("fbtrace_id"),
        http_status=http_status,
    )


def graph_request(
    config: GraphConfig,
    path: str,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    timeout: int = 60,
) -> dict[str, Any]:
    """Call the Graph API and return the decoded JSON body.

    The access token is added here and is never included in raised errors, so a
    failure can be logged verbatim without leaking the credential.
    """
    method = method.upper()
    raw_params = params or {}
    # An explicit None for access_token means the endpoint must not receive one,
    # which the token exchange endpoint relies on.
    omit_token = "access_token" in raw_params and raw_params["access_token"] is None
    call_params: dict[str, Any] = {k: v for k, v in raw_params.items() if v is not None}
    if not omit_token:
        call_params.setdefault("access_token", config.access_token)

    url = f"{GRAPH_HOST}/{config.api_version}/{path.lstrip('/')}"
    body = None
    if method == "POST":
        body = urllib.parse.urlencode(call_params).encode("utf-8")
    else:
        url = f"{url}?{urllib.parse.urlencode(call_params)}"

    request = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            parsed = {"error": {"message": raw[:400]}}
        raise _error_from_payload(parsed, exc.code) from None
    except urllib.error.URLError as exc:
        raise GraphError(f"Could not reach the Graph API: {exc.reason}") from None

    if isinstance(payload, dict) and payload.get("error"):
        raise _error_from_payload(payload, None)
    return payload if isinstance(payload, dict) else {"data": payload}
