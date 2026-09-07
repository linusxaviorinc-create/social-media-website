#!/usr/bin/env python3
"""
Publish an approved post to Instagram through Meta's official Graph API.

This is the supported publishing path for an Instagram professional account
linked to a Facebook Page. It replaces the browser automation in
stage_instagram_post.py, which stays available as a fallback.

The flow is: validate the approved post, convert the flyer to JPEG, upload it to
a private GCS bucket, hand Meta a short-lived signed URL, create a media
container, poll until Meta has fetched the image, then publish.

Nothing is published unless --publish is passed. Being in the approved lane is
never enough on its own. Without --publish the script stops at a safe stage and
reports exactly what it would do.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import yaml

import gcs_upload
import image_prep
from instagram_graph_config import GraphError, graph_request, load_config, redact_url
from platforms import ROOT


APPROVED_DIR = ROOT / "social" / "content" / "approved"
POSTED_DIR = ROOT / "social" / "content" / "posted"
PREPARED_DIR = ROOT / "social" / "assets" / "prepared"
LOGS_DIR = ROOT / "social" / "logs"

RUN_LOG = LOGS_DIR / "instagram-graph-latest-run.json"
ERROR_LOG = LOGS_DIR / "instagram-graph-latest-error.json"
LEDGER = LOGS_DIR / "instagram-graph-publish-ledger.json"

CAPTION_MAX_CHARS = 2200
CAPTION_MAX_HASHTAGS = 30
CAPTION_MAX_MENTIONS = 20

STAGES = ("prep", "upload", "container")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish an approved post to Instagram via the official Graph API.",
    )
    parser.add_argument("--post", help="Path to the approved markdown post. Defaults to the latest one.")
    parser.add_argument(
        "--stage",
        choices=STAGES,
        default="prep",
        help="How far to run as a dry run: prep (default), upload, or container. Never publishes.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Actually publish to Instagram. Required for anything to go public.",
    )
    parser.add_argument("--config", help="Path to the Instagram Graph config file.")
    parser.add_argument("--caption", help="Override the caption from the post frontmatter.")
    parser.add_argument("--asset-index", type=int, default=0, help="Which asset to post when a draft has several.")
    parser.add_argument("--fit", choices=image_prep.FIT_CHOICES, help="How to handle an out-of-range aspect ratio.")
    parser.add_argument("--pad-color", help="Padding color used when fit is 'pad'.")
    parser.add_argument("--quality", type=int, help="JPEG quality to start from.")
    parser.add_argument(
        "--allow-unapproved",
        action="store_true",
        help="Allow a post that is not marked approved. Still requires --publish to go public.",
    )
    parser.add_argument("--force", action="store_true", help="Publish even if this post is already in the ledger.")
    parser.add_argument("--keep-remote", action="store_true", help="Keep the uploaded object if the run fails.")
    parser.add_argument("--poll-timeout", type=int, default=180, help="Seconds to wait for Meta to fetch the image.")
    parser.add_argument("--poll-interval", type=int, default=3, help="Seconds between container status checks.")
    return parser.parse_args()


def latest_approved_path() -> Path:
    approved = sorted(APPROVED_DIR.glob("*.md"))
    if not approved:
        raise SystemExit("No approved posts found. Run approve_content_post.py first.")
    return approved[-1]


def split_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise SystemExit(f"Post is missing frontmatter: {path}")
    _, rest = text.split("---\n", 1)
    frontmatter_text, body = rest.split("\n---\n", 1)
    return yaml.safe_load(frontmatter_text) or {}, body


def render_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    rendered = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{rendered}\n---\n{body.lstrip()}"


def resolve_asset(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (ROOT / path)


def validate_caption(caption: str) -> list[str]:
    warnings: list[str] = []
    if len(caption) > CAPTION_MAX_CHARS:
        raise SystemExit(
            f"Caption is {len(caption)} characters, over Instagram's {CAPTION_MAX_CHARS} limit."
        )
    hashtags = re.findall(r"(?<!\w)#\w+", caption)
    mentions = re.findall(r"(?<!\w)@[\w.]+", caption)
    if len(hashtags) > CAPTION_MAX_HASHTAGS:
        raise SystemExit(f"Caption has {len(hashtags)} hashtags, over Instagram's {CAPTION_MAX_HASHTAGS} limit.")
    if len(mentions) > CAPTION_MAX_MENTIONS:
        raise SystemExit(f"Caption has {len(mentions)} mentions, over Instagram's {CAPTION_MAX_MENTIONS} limit.")
    if len(caption) > CAPTION_MAX_CHARS * 0.9:
        warnings.append("Caption is close to the 2200 character limit.")
    return warnings


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_ledger() -> list[dict[str, Any]]:
    if not LEDGER.exists():
        return []
    try:
        entries = json.loads(LEDGER.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return entries if isinstance(entries, list) else []


def append_ledger(entry: dict[str, Any]) -> None:
    entries = load_ledger()
    entries.append(entry)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def already_published(post_path: Path, source_digest: str) -> dict[str, Any] | None:
    for entry in load_ledger():
        if entry.get("source_sha256") == source_digest or entry.get("post") == str(post_path):
            return entry
    return None


def write_log(path: Path, payload: dict[str, Any]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def check_publishing_quota(config: Any) -> dict[str, Any]:
    """Meta allows 100 API-published posts per rolling 24 hours."""
    payload = graph_request(
        config,
        f"{config.instagram_user_id}/content_publishing_limit",
        {"fields": "config,quota_usage"},
    )
    rows = payload.get("data") or []
    if not rows:
        return {"quota_usage": None, "quota_total": None}
    row = rows[0]
    return {
        "quota_usage": row.get("quota_usage"),
        "quota_total": (row.get("config") or {}).get("quota_total"),
    }


def create_container(config: Any, image_url: str, caption: str) -> str:
    payload = graph_request(
        config,
        f"{config.instagram_user_id}/media",
        {"image_url": image_url, "caption": caption},
        method="POST",
    )
    container_id = payload.get("id")
    if not container_id:
        raise SystemExit(f"Meta did not return a container id: {payload}")
    return str(container_id)


def poll_container(config: Any, container_id: str, timeout: int, interval: int) -> dict[str, Any]:
    """Wait until Meta has fetched and accepted the image."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = graph_request(config, container_id, {"fields": "status_code,status"})
        status_code = last.get("status_code")

        if status_code == "FINISHED":
            return last
        if status_code in ("ERROR", "EXPIRED"):
            raise SystemExit(
                f"Media container {container_id} returned {status_code}: "
                f"{last.get('status') or 'no detail provided'}"
            )
        if status_code == "PUBLISHED":
            return last
        time.sleep(max(1, interval))

    raise SystemExit(
        f"Media container {container_id} was still {last.get('status_code') or 'unknown'} "
        f"after {timeout}s. Meta may not be able to fetch the signed URL."
    )


def archive_published_post(
    post_path: Path,
    media_id: str,
    permalink: str | None,
    published_at: str,
) -> Path:
    POSTED_DIR.mkdir(parents=True, exist_ok=True)
    frontmatter, body = split_frontmatter(post_path)
    frontmatter["status"] = "posted"
    frontmatter["publish_intent"] = "completed"
    frontmatter["publish_method"] = "instagram_graph_api"
    frontmatter["instagram_media_id"] = media_id
    if permalink:
        frontmatter["instagram_permalink"] = permalink
    frontmatter["published_at"] = published_at

    target = POSTED_DIR / post_path.name
    target.write_text(render_frontmatter(frontmatter, body), encoding="utf-8")
    if post_path != target and post_path.exists():
        post_path.unlink()
    return target


def main() -> None:
    args = parse_args()
    if args.publish and args.stage != "prep":
        raise SystemExit("Use either --stage for a dry run or --publish for the real thing, not both.")

    config = load_config(args.config)
    post_path = Path(args.post).resolve() if args.post else latest_approved_path()
    if not post_path.exists():
        raise SystemExit(f"Post not found: {post_path}")

    frontmatter, _ = split_frontmatter(post_path)
    warnings: list[str] = []

    status = str(frontmatter.get("status") or "unknown")
    if status == "posted" or frontmatter.get("publish_intent") == "completed":
        raise SystemExit(f"{post_path.name} is already marked posted. Nothing to do.")
    if status != "approved" and not args.allow_unapproved:
        raise SystemExit(
            f"{post_path.name} has status '{status}', not 'approved'.\n"
            "Approve it first, or pass --allow-unapproved for a dry run."
        )

    assets = frontmatter.get("assets") or []
    if not assets:
        raise SystemExit(f"Post has no assets: {post_path}")
    if len(assets) > 1:
        warnings.append(
            f"Post has {len(assets)} assets. This publisher posts a single image; "
            f"using index {args.asset_index}. Carousels are not part of this phase."
        )
    if args.asset_index >= len(assets):
        raise SystemExit(f"--asset-index {args.asset_index} is out of range for {len(assets)} assets.")

    source_asset = resolve_asset(str(assets[args.asset_index]))
    if not source_asset.exists():
        raise SystemExit(f"Asset not found: {source_asset}")

    caption = args.caption if args.caption is not None else str(frontmatter.get("caption") or "")
    if not caption.strip():
        raise SystemExit(f"Post has no caption: {post_path}")
    warnings.extend(validate_caption(caption))

    source_digest = file_digest(source_asset)
    prior = already_published(post_path, source_digest)
    if prior and not args.force:
        raise SystemExit(
            f"This post looks already published on {prior.get('published_at')} "
            f"as media {prior.get('media_id')}.\n"
            "Pass --force only if you are certain you want to post it again."
        )

    run_stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    record: dict[str, Any] = {
        "run_at": dt.datetime.now().astimezone().isoformat(),
        "post": str(post_path),
        "source_asset": str(source_asset),
        "source_sha256": source_digest,
        "caption_chars": len(caption),
        "instagram_user_id": config.instagram_user_id,
        "graph_api_version": config.api_version,
        "mode": "publish" if args.publish else f"dry-run:{args.stage}",
        "stage_reached": "prep",
        "published": False,
        "warnings": warnings,
    }

    uploaded_object: str | None = None
    published_media_id: str | None = None

    try:
        prepared = image_prep.prepare_jpeg(
            source_asset,
            PREPARED_DIR,
            fit=args.fit or config.image.fit,
            pad_color=args.pad_color or config.image.pad_color,
            quality=args.quality or config.image.jpeg_quality,
            max_width=config.image.max_width,
            stem=f"{run_stamp}-{post_path.stem}",
        )
        record["image"] = prepared
        record["warnings"].extend(prepared.get("warnings") or [])

        print(f"Post:    {post_path.name}")
        print(f"Asset:   {source_asset.name} ({prepared['original_format']}, "
              f"{prepared['original_size'][0]}x{prepared['original_size'][1]}, "
              f"ratio {prepared['original_aspect_ratio']})")
        print(f"Prepared: {Path(prepared['output']).name} "
              f"({prepared['final_size'][0]}x{prepared['final_size'][1]}, "
              f"ratio {prepared['final_aspect_ratio']}, "
              f"{prepared['size_bytes'] / 1024:.0f} KB)")
        for action in prepared.get("actions") or []:
            print(f"  - {action}")

        if not args.publish and args.stage == "prep":
            record["next_step"] = "Re-run with --stage upload to test GCS hosting."
            write_log(RUN_LOG, record)
            print("\nDry run (prep only). Nothing was uploaded and nothing was published.")
            print("Next: --stage upload, then --stage container, then --publish.")
            for warning in record["warnings"]:
                print(f"Warning: {warning}")
            return

        bucket_report = gcs_upload.inspect_bucket(config.gcs)
        record["bucket"] = bucket_report
        record["warnings"].extend(bucket_report.get("warnings") or [])
        if not bucket_report.get("exists"):
            raise SystemExit(f"GCS bucket {config.gcs.bucket} is not usable. See warnings above.")

        object_name = gcs_upload.build_object_name(config.gcs.object_prefix, post_path.stem)
        upload = gcs_upload.upload_and_sign(config.gcs, Path(prepared["output"]), object_name)
        uploaded_object = upload["object_name"]
        record["stage_reached"] = "upload"
        record["upload"] = {
            "gs_uri": upload["gs_uri"],
            "object_name": upload["object_name"],
            "signed_url": redact_url(upload["signed_url"]),
            "signed_url_expires_at": upload["signed_url_expires_at"],
            "signed_url_ttl_seconds": upload["signed_url_ttl_seconds"],
        }

        fetch_check = gcs_upload.verify_fetchable(upload["signed_url"])
        record["upload"]["fetch_check"] = fetch_check
        if not fetch_check.get("ok"):
            raise SystemExit(
                f"The signed URL is not fetchable ({fetch_check.get('status')}: "
                f"{fetch_check.get('error')}). Meta would fail to fetch it too."
            )

        print(f"\nUploaded: {upload['gs_uri']}")
        print(f"Signed URL expires at {upload['signed_url_expires_at']} (verified fetchable)")

        if not args.publish and args.stage == "upload":
            record["next_step"] = "Re-run with --stage container to create a media container."
            write_log(RUN_LOG, record)
            print("\nDry run (upload only). Nothing was sent to Instagram.")
            for warning in record["warnings"]:
                print(f"Warning: {warning}")
            return

        container_id = create_container(config, upload["signed_url"], caption)
        record["container_id"] = container_id
        status_payload = poll_container(config, container_id, args.poll_timeout, args.poll_interval)
        record["container_status"] = status_payload.get("status_code")
        record["stage_reached"] = "container"
        print(f"\nMedia container {container_id}: {status_payload.get('status_code')}")

        if not args.publish:
            record["next_step"] = "Re-run with --publish to post it."
            write_log(RUN_LOG, record)
            print("\nDry run (container only). The post was NOT published.")
            print("Meta discards an unpublished container after 24 hours.")
            for warning in record["warnings"]:
                print(f"Warning: {warning}")
            return

        quota = check_publishing_quota(config)
        record["quota"] = quota
        usage, total = quota.get("quota_usage"), quota.get("quota_total")
        if usage is not None and total is not None and usage >= total:
            raise SystemExit(f"Instagram publishing quota is used up ({usage}/{total} in 24 hours).")

        publish_payload = graph_request(
            config,
            f"{config.instagram_user_id}/media_publish",
            {"creation_id": container_id},
            method="POST",
        )
        published_media_id = str(publish_payload.get("id") or "")
        if not published_media_id:
            raise SystemExit(f"Meta did not return a media id: {publish_payload}")

        permalink = None
        try:
            media = graph_request(config, published_media_id, {"fields": "permalink,timestamp"})
            permalink = media.get("permalink")
            record["published_timestamp"] = media.get("timestamp")
        except GraphError as exc:
            record["warnings"].append(f"Published, but could not read the permalink: {exc.message}")

        published_at = dt.datetime.now().astimezone().isoformat()
        archived = archive_published_post(post_path, published_media_id, permalink, published_at)

        record.update(
            {
                "stage_reached": "publish",
                "published": True,
                "media_id": published_media_id,
                "permalink": permalink,
                "published_at": published_at,
                "archived_post": str(archived),
            }
        )
        write_log(RUN_LOG, record)
        append_ledger(
            {
                "published_at": published_at,
                "post": str(post_path),
                "archived_post": str(archived),
                "source_sha256": source_digest,
                "media_id": published_media_id,
                "permalink": permalink,
                "gs_uri": upload["gs_uri"],
            }
        )

        print(f"\nPublished to Instagram as media {published_media_id}")
        if permalink:
            print(f"Permalink: {permalink}")
        print(f"Archived:  {archived}")
        for warning in record["warnings"]:
            print(f"Warning: {warning}")

    except BaseException as exc:
        record["error"] = exc.as_dict() if isinstance(exc, GraphError) else str(exc)
        record["published"] = published_media_id is not None
        if uploaded_object and published_media_id is None and not args.keep_remote:
            record["cleanup_deleted_object"] = gcs_upload.delete_object(config.gcs, uploaded_object)
        write_log(ERROR_LOG, record)
        write_log(RUN_LOG, record)
        raise


if __name__ == "__main__":
    main()
