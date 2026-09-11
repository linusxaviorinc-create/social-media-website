#!/usr/bin/env python3
"""
Publish a show flyer to the @giantrockmeetingroom Instagram Story.

This sits alongside publish_instagram_graph.py, the feed publisher, and reuses
its infrastructure unchanged: config and token handling, the private GCS upload
with short-lived signed URLs, container polling, and the quota check. Only the
image composition is Story-specific (see story_image_prep.py).

Flow: check gcloud credentials, compose the Story image, upload it, create a
media container with media_type=STORIES, poll until Meta has fetched it, then
publish.

No sticker or link is attached. Meta does not allow stickers on Stories
published through the API, so the image carries a burned-in
"TICKETS IN BIO!!" label and the ticket URL lives in the profile bio.

Nothing is published unless --publish is passed. Without it the script stops at
a safe stage and reports what it would do.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import gcs_upload
import story_image_prep
from instagram_graph_config import GraphError, graph_request, load_config, redact_url
from platforms import ROOT
from publish_instagram_graph import check_publishing_quota, file_digest, poll_container


PREPARED_DIR = ROOT / "social" / "assets" / "prepared" / "stories"
LOGS_DIR = ROOT / "social" / "logs"

RUN_LOG = LOGS_DIR / "instagram-graph-story-latest-run.json"
ERROR_LOG = LOGS_DIR / "instagram-graph-story-latest-error.json"
LEDGER = LOGS_DIR / "instagram-graph-story-ledger.json"

STAGES = ("prep", "upload", "container")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish a show flyer to the Instagram Story via the official Graph API.",
    )
    parser.add_argument("--flyer", required=True, help="Path to the show's flyer image.")
    parser.add_argument(
        "--stage",
        choices=STAGES,
        default="prep",
        help="How far to run as a dry run: prep (default), upload, or container. Never publishes.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Actually publish the Story. Required for anything to go public.",
    )
    parser.add_argument("--label", help="Short name for logs and file names. Defaults to the flyer's name.")
    parser.add_argument(
        "--ticket-url",
        help="Ticket link for the record. It is only logged; the Story points people to the bio.",
    )
    parser.add_argument(
        "--ticket-text",
        default=story_image_prep.DEFAULT_TICKET_TEXT,
        help="Text burned into the Story near the bottom.",
    )
    parser.add_argument("--plate", default=str(story_image_prep.DEFAULT_PLATE), help="Transparent Annex plate PNG.")
    parser.add_argument("--config", help="Path to the Instagram Graph config file.")
    parser.add_argument("--force", action="store_true", help="Publish even if this flyer is already in the Story ledger.")
    parser.add_argument("--keep-remote", action="store_true", help="Keep the uploaded object if the run fails.")
    parser.add_argument("--poll-timeout", type=int, default=180, help="Seconds to wait for Meta to fetch the image.")
    parser.add_argument("--poll-interval", type=int, default=3, help="Seconds between container status checks.")
    return parser.parse_args()


def gcloud_auth_status(config: Any) -> tuple[bool, str]:
    """Ask gcloud for a token without letting it prompt.

    The Workspace account expires gcloud credentials about daily. Checking up
    front turns a confusing mid-run failure into a clear instruction. The token
    printed by gcloud is captured and discarded, never shown or logged.
    """
    executable = shutil.which(config.gcs.gcloud_path or "gcloud")
    if not executable:
        return False, "The gcloud CLI is not installed or not on PATH (brew install --cask gcloud-cli)."
    try:
        result = subprocess.run(
            [executable, "auth", "print-access-token", "--quiet"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "gcloud did not answer within 60 seconds."

    if result.returncode == 0 and result.stdout.strip():
        return True, ""
    lines = [line for line in (result.stderr or "").strip().splitlines() if line.strip()]
    return False, "\n".join(lines[-3:]) or f"gcloud exited with status {result.returncode}."


def require_gcloud_auth(config: Any) -> None:
    ok, detail = gcloud_auth_status(config)
    if not ok:
        raise SystemExit(
            "Google Cloud credentials are stale or missing, so nothing was started.\n\n"
            "Run this, then re-run the same command:\n\n"
            "    gcloud auth login\n\n"
            f"gcloud said: {detail}"
        )


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


def already_published(source_digest: str) -> dict[str, Any] | None:
    for entry in load_ledger():
        if entry.get("source_sha256") == source_digest:
            return entry
    return None


def write_log(path: Path, payload: dict[str, Any]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def create_story_container(config: Any, image_url: str) -> str:
    payload = graph_request(
        config,
        f"{config.instagram_user_id}/media",
        {"image_url": image_url, "media_type": "STORIES"},
        method="POST",
    )
    container_id = payload.get("id")
    if not container_id:
        raise SystemExit(f"Meta did not return a Story container id: {payload}")
    return str(container_id)


def print_warnings(record: dict[str, Any]) -> None:
    for warning in record.get("warnings") or []:
        print(f"Warning: {warning}")


def main() -> None:
    args = parse_args()
    if args.publish and args.stage != "prep":
        raise SystemExit("Use either --stage for a dry run or --publish for the real thing, not both.")

    config = load_config(args.config)
    needs_gcloud = args.publish or args.stage != "prep"

    # Credentials are checked before any work so a stale login never fails mid-run.
    if needs_gcloud:
        require_gcloud_auth(config)
        gcloud_note = "ok"
    else:
        ok, detail = gcloud_auth_status(config)
        gcloud_note = "ok" if ok else "stale"

    flyer = Path(args.flyer).expanduser().resolve()
    if not flyer.exists():
        raise SystemExit(f"Flyer not found: {flyer}")

    label = args.label or flyer.stem
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", label).strip("-").lower() or "story"

    source_digest = file_digest(flyer)
    prior = already_published(source_digest)
    if prior and not args.force:
        raise SystemExit(
            f"This flyer was already published as a Story on {prior.get('published_at')} "
            f"(media {prior.get('media_id')}).\n"
            "Stories disappear after 24 hours, so reposting as a reminder is normal. "
            "Pass --force when that is what you intend."
        )

    run_stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    record: dict[str, Any] = {
        "run_at": dt.datetime.now().astimezone().isoformat(),
        "flyer": str(flyer),
        "label": label,
        "source_sha256": source_digest,
        "ticket_url": args.ticket_url,
        "ticket_text": args.ticket_text,
        "instagram_user_id": config.instagram_user_id,
        "graph_api_version": config.api_version,
        "media_type": "STORIES",
        "mode": "publish" if args.publish else f"dry-run:{args.stage}",
        "gcloud_auth": gcloud_note,
        "stage_reached": "prep",
        "published": False,
        "warnings": [],
    }
    if gcloud_note == "stale":
        record["warnings"].append(
            "gcloud credentials are stale. This prep-only run does not need them, but "
            "run `gcloud auth login` before --stage upload, --stage container, or --publish."
        )

    uploaded_object: str | None = None
    published_media_id: str | None = None

    try:
        composed = story_image_prep.compose_story(
            flyer,
            PREPARED_DIR / f"{run_stamp}-{slug}-story.jpg",
            plate_path=Path(args.plate).expanduser(),
            ticket_text=args.ticket_text,
        )
        record["image"] = composed
        record["warnings"].extend(composed.get("warnings") or [])

        out_w, out_h = composed["output_size"]
        print(f"Flyer:    {flyer.name} ({composed['flyer_original_size'][0]}x{composed['flyer_original_size'][1]})")
        print(f"Story:    {Path(composed['output']).name} ({composed['size_bytes'] / 1024:.0f} KB)")
        print(f"  final dimensions: {out_w}x{out_h} {composed['output_format']}")
        print(f"  background: RGB{tuple(composed['background_rgb'])}, muted from the flyer's dominant "
              f"RGB{tuple(composed['dominant_rgb'])}; white logos at {composed['contrast_with_white']}:1 contrast")
        print(f"  flyer: {composed['flyer_placed_size'][0]}x{composed['flyer_placed_size'][1]} "
              f"({composed['flyer_width_pct']}% width), centered horizontally, not cropped")
        print(f"  gaps: {composed['gap_top_px']}px top, {composed['gap_bottom_px']}px bottom, "
              f"{composed['gap_side_px']}px each side")
        print(f"  label: \"{composed['ticket_text']}\"")
        print(f"gcloud:   {gcloud_note}")

        if not args.publish and args.stage == "prep":
            record["next_step"] = "Re-run with --stage upload to test GCS hosting."
            write_log(RUN_LOG, record)
            print("\nDry run (prep only). Nothing was uploaded and nothing was published.")
            print(f"Preview: {composed['output']}")
            print_warnings(record)
            return

        bucket_report = gcs_upload.inspect_bucket(config.gcs)
        record["bucket"] = bucket_report
        record["warnings"].extend(bucket_report.get("warnings") or [])
        if not bucket_report.get("exists"):
            detail = "\n".join(f"  - {w}" for w in (bucket_report.get("warnings") or []))
            raise SystemExit(f"GCS bucket {config.gcs.bucket} is not usable.\n" + (detail or "  - No detail returned."))

        object_name = gcs_upload.build_object_name(f"{config.gcs.object_prefix}/stories", slug)
        upload = gcs_upload.upload_and_sign(config.gcs, Path(composed["output"]), object_name)
        uploaded_object = upload["object_name"]
        record["stage_reached"] = "upload"
        record["upload"] = {
            "gs_uri": upload["gs_uri"],
            "object_name": upload["object_name"],
            "signed_url": redact_url(upload["signed_url"]),
            "signed_url_expires_at": upload["signed_url_expires_at"],
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
            record["next_step"] = "Re-run with --stage container to create a Story container."
            write_log(RUN_LOG, record)
            print("\nDry run (upload only). Nothing was sent to Instagram.")
            print_warnings(record)
            return

        container_id = create_story_container(config, upload["signed_url"])
        record["container_id"] = container_id
        status_payload = poll_container(config, container_id, args.poll_timeout, args.poll_interval)
        record["container_status"] = status_payload.get("status_code")
        record["stage_reached"] = "container"
        print(f"\nStory container {container_id}: {status_payload.get('status_code')}")

        if not args.publish:
            record["next_step"] = "Re-run with --publish to post the Story."
            write_log(RUN_LOG, record)
            print("\nDry run (container only). The Story was NOT published.")
            print("Meta discards an unpublished container after 24 hours.")
            print_warnings(record)
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
        record.update(
            {
                "stage_reached": "publish",
                "published": True,
                "media_id": published_media_id,
                "permalink": permalink,
                "published_at": published_at,
            }
        )
        write_log(RUN_LOG, record)
        append_ledger(
            {
                "published_at": published_at,
                "flyer": str(flyer),
                "label": label,
                "source_sha256": source_digest,
                "media_id": published_media_id,
                "permalink": permalink,
                "ticket_url": args.ticket_url,
                "gs_uri": upload["gs_uri"],
            }
        )

        print(f"\nPublished to the Instagram Story as media {published_media_id}")
        if permalink:
            print(f"Permalink: {permalink}")
        print("It will disappear from the Story after 24 hours.")
        print_warnings(record)

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
