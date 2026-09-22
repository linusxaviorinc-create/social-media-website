#!/usr/bin/env python3
"""
Post Instagram Stories for every show that is due, straight from the GRMR Show
List sheet.

This is the unattended front end to publish_instagram_story.py. It reads the
sheet, works out which shows are due today, fetches each flyer from Drive by
its flyer_ref, publishes a Story to each of the show's accounts, writes
last_posted back to the sheet, and reports what happened.

The sheet is the contract (see its README tab). A row posts only when show_id,
event_date, ticket_link and flyer_ref are all present and usable, status is
active or blank, the show has not already happened, and either last_posted is
blank or cadence_days have passed since it.

Nothing is published unless --publish is passed. Without it this is a dry run
that reports exactly which shows it would post to which accounts.

Credentials: the sheet read, the sheet write and the Drive download all use the
gcloud user credentials (gcloud auth login --enable-gdrive-access). Those
expire roughly daily under the Workspace Cloud session control policy, which an
unattended run cannot answer, so the run stops early and says so rather than
posting a partial set.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from platforms import ROOT


SHEET_ID = "1L3r8_zj4m0ZYbjstqAvnO1PVqVG60Whj5aIMvjKTXUg"
SHEET_TAB = "shows"
USER_PROJECT = "grmr-instagram"

PUBLISHER = ROOT / "social" / "scripts" / "publish_instagram_story.py"
FLYER_CACHE = ROOT / "social" / "assets" / "drive-flyers" / "by-ref"
LOGS_DIR = ROOT / "social" / "logs"

RUN_LOG = LOGS_DIR / "story-scheduler-latest-run.json"
HISTORY_LOG = LOGS_DIR / "story-scheduler-history.jsonl"
LOCK_FILE = LOGS_DIR / "story-scheduler.lock"

ANNEX_CONFIG = Path.home() / ".config" / "grmr" / "instagram-graph-annex.json"
GMAIL_SEND_CONFIG = Path.home() / ".config" / "grmr" / "gmail-send.json"

REQUIRED_FIELDS = ("show_id", "event_date", "ticket_link", "flyer_ref")
DEFAULT_CADENCE_DAYS = 2
DEFAULT_TICKET_TEXT = "TICKETS IN BIO!!"
FLYER_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png"}

# Per-account publisher settings. "grmr" uses the repo's default config.
ACCOUNTS = {
    "grmr": {"handle": "@giantrockmeetingroom", "config": None},
    "annex": {"handle": "@letsgototheannex", "config": ANNEX_CONFIG},
}


class SchedulerError(RuntimeError):
    """Something stopped the whole run, not just one show."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Post Instagram Stories for every show that is due, from the GRMR Show List sheet.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Actually publish. Without it this is a dry run that posts nothing.",
    )
    parser.add_argument("--today", help="Override today's date (YYYY-MM-DD), for testing.")
    parser.add_argument("--only", help="Run just this show_id, ignoring cadence.")
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Skip the email report even when a send credential is configured.",
    )
    parser.add_argument(
        "--ignore-cadence",
        action="store_true",
        help="Post every eligible show even if it was posted within cadence_days.",
    )
    return parser.parse_args()


# --- credentials -----------------------------------------------------------


def gcloud_token() -> str:
    """The active gcloud user token, or a clear failure when the login expired."""
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token", "--quiet"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    except FileNotFoundError:
        raise SchedulerError("gcloud is not installed, so the sheet cannot be read.") from None
    except subprocess.TimeoutExpired:
        raise SchedulerError("gcloud did not respond; it is probably waiting for a login.") from None
    if result.returncode != 0:
        raise SchedulerError(
            "The gcloud login has expired, so nothing was posted. Re-run:\n"
            "  gcloud auth login linus@giantrockmeetingroom.com --enable-gdrive-access\n"
            "To stop this recurring, exempt gcloud in Admin console -> Security -> "
            "Access and data control -> Google Cloud session control."
        )
    return result.stdout.strip()


def api_request(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    raw: bool = False,
) -> Any:
    """A Google API call with the gcloud user token."""
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "x-goog-user-project": USER_PROJECT,
    }
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:  # Google's error JSON is long; its message is the useful part.
            detail = json.loads(body)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detail = " ".join(body.split())
        api = url.split("//")[-1].split("/")[0]
        raise SchedulerError(f"{api} {method} failed ({exc.code}): {detail[:200]}") from None
    return body if raw else json.loads(body.decode())


# --- the sheet -------------------------------------------------------------


def read_sheet(token: str) -> tuple[list[str], list[dict[str, str]], list[int]]:
    """Return the header, one dict per non-empty row, and each row's sheet row number."""
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
        f"{urllib.parse.quote(SHEET_TAB)}!A1:Z500"
    )
    values = api_request(url, token).get("values", [])
    if not values:
        raise SchedulerError(f"The '{SHEET_TAB}' tab is empty.")
    header = [cell.strip() for cell in values[0]]
    rows: list[dict[str, str]] = []
    row_numbers: list[int] = []
    for offset, raw_row in enumerate(values[1:], start=2):
        if not any(cell.strip() for cell in raw_row):
            continue
        row = {name: (raw_row[i].strip() if i < len(raw_row) else "") for i, name in enumerate(header)}
        rows.append(row)
        row_numbers.append(offset)
    return header, rows, row_numbers


def column_letter(index: int) -> str:
    """0-based column index to its A1 letter."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def write_last_posted(token: str, header: list[str], row_number: int, stamp: str) -> None:
    """Write one cell. The scheduler writes last_posted and nothing else."""
    if "last_posted" not in header:
        raise SchedulerError("The sheet has no last_posted column.")
    cell = f"{SHEET_TAB}!{column_letter(header.index('last_posted'))}{row_number}"
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
        f"{urllib.parse.quote(cell)}?valueInputOption=RAW"
    )
    api_request(url, token, method="PUT", payload={"values": [[stamp]]})


# --- eligibility -----------------------------------------------------------


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def parse_last_posted(value: str) -> dt.date | None:
    """last_posted is ISO 8601 with an offset; only its date matters here."""
    try:
        return dt.datetime.fromisoformat(value).date()
    except ValueError:
        return parse_date(value[:10])


def cadence_days(row: dict[str, str]) -> int:
    raw = row.get("cadence_days", "").strip()
    if not raw:
        return DEFAULT_CADENCE_DAYS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_CADENCE_DAYS


def target_accounts(row: dict[str, str]) -> list[str]:
    value = row.get("accounts", "").strip().lower() or "both"
    if value == "both":
        return ["grmr", "annex"]
    if value in ACCOUNTS:
        return [value]
    return []


def assess(row: dict[str, str], today: dt.date, args: argparse.Namespace) -> tuple[bool, str]:
    """Decide whether this row posts today, and say why when it does not."""
    missing = [field for field in REQUIRED_FIELDS if not row.get(field, "").strip()]
    if missing:
        return False, f"missing required field(s): {', '.join(missing)}"

    status = row.get("status", "").strip().lower()
    if status and status != "active":
        return False, f"status is {status}"

    event_date = parse_date(row["event_date"])
    if event_date is None:
        return False, f"event_date {row['event_date']!r} is not a valid YYYY-MM-DD date"
    if event_date < today:
        return False, f"show was {event_date.isoformat()}, already past"

    if not target_accounts(row):
        return False, f"accounts value {row.get('accounts')!r} is not both, grmr or annex"

    if args.only:
        return True, "selected with --only"

    last_posted_raw = row.get("last_posted", "").strip()
    if last_posted_raw and not args.ignore_cadence:
        last_posted = parse_last_posted(last_posted_raw)
        if last_posted is None:
            return True, f"last_posted {last_posted_raw!r} is unreadable, posting anyway"
        days = (today - last_posted).days
        needed = cadence_days(row)
        if days < needed:
            return False, f"posted {days} day(s) ago, cadence is {needed}"
    return True, "due"


# --- flyers ----------------------------------------------------------------


def fetch_flyer(file_id: str, token: str) -> Path:
    """Download the flyer for a flyer_ref, reusing the cached copy when it matches."""
    meta_url = (
        f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id)}"
        "?fields=name,mimeType,size,md5Checksum&supportsAllDrives=true"
    )
    meta = api_request(meta_url, token)
    mime = meta.get("mimeType", "")
    if mime not in FLYER_SUFFIXES:
        raise SchedulerError(f"flyer_ref {file_id} is {mime or 'an unknown type'}, not a JPEG or PNG")

    FLYER_CACHE.mkdir(parents=True, exist_ok=True)
    target = FLYER_CACHE / f"{file_id}{FLYER_SUFFIXES[mime]}"
    expected_size = int(meta.get("size", 0) or 0)
    if target.exists() and expected_size and target.stat().st_size == expected_size:
        return target

    media_url = (
        f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id)}"
        "?alt=media&supportsAllDrives=true"
    )
    content = api_request(media_url, token, raw=True)
    target.write_bytes(content)
    return target


# --- publishing ------------------------------------------------------------


def ticket_text(row: dict[str, str]) -> str:
    age = row.get("age_restriction", "").strip()
    return f"{DEFAULT_TICKET_TEXT} · {age}" if age else DEFAULT_TICKET_TEXT


def publish_story(row: dict[str, str], account: str, flyer: Path, publish: bool) -> dict[str, Any]:
    """Run the Story publisher for one show on one account."""
    command = [
        sys.executable,
        str(PUBLISHER),
        "--flyer",
        str(flyer),
        "--label",
        row["show_id"],
        "--ticket-text",
        ticket_text(row),
        "--ticket-url",
        row["ticket_link"],
        # The ledger exists to stop accidental duplicate posts by hand. Reposting
        # on a cadence is the whole point here, so the cadence rule governs instead.
        "--force",
    ]
    config = ACCOUNTS[account]["config"]
    if config:
        command += ["--config", str(config)]
    if publish:
        command.append("--publish")

    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    output = result.stdout + result.stderr
    if result.returncode == 0 and publish:
        for line in output.splitlines():
            if "as media" in line:
                return {"status": "posted", "media_id": line.strip().split()[-1]}
        return {"status": "posted", "media_id": None}
    if result.returncode == 0:
        return {"status": "would_post", "media_id": None}

    reason = ""
    for line in reversed(output.splitlines()):
        if line.strip():
            reason = line.strip()[:200]
            break
    return {"status": "failed", "media_id": None, "error": reason}


# --- reporting -------------------------------------------------------------


def build_report(run: dict[str, Any]) -> tuple[str, str]:
    """A subject line and a plain-text body for the run."""
    posted = [r for r in run["results"] if r["status"] == "posted"]
    failed = [r for r in run["results"] if r["status"] == "failed"]
    mode = "posted" if run["published"] else "dry run"

    ready = [r for r in run["results"] if r["status"] == "would_post"]

    if run.get("fatal"):
        subject = "GRMR Stories: run FAILED, nothing posted"
    elif failed:
        subject = f"GRMR Stories: {len(posted)} up, {len(failed)} FAILED"
    elif posted:
        subject = f"GRMR Stories: {len(posted)} posted"
    elif ready:
        subject = f"GRMR Stories: {len(ready)} ready (dry run, nothing posted)"
    else:
        subject = "GRMR Stories: nothing due today"

    lines = [f"Run {run['run_at']} ({mode})", ""]
    if run.get("fatal"):
        lines += ["STOPPED BEFORE POSTING:", run["fatal"], ""]

    if run["results"]:
        lines.append("Stories:")
        for r in run["results"]:
            mark = {"posted": "OK  ", "would_post": "WOULD", "failed": "FAIL"}.get(r["status"], "?   ")
            detail = r.get("media_id") or r.get("error", "")
            lines.append(f"  {mark} {r['show_id']:32} {r['handle']:22} {detail}")
        lines.append("")

    if run["skipped"]:
        lines.append("Skipped:")
        for s in run["skipped"]:
            lines.append(f"  -   {s['show_id']:32} {s['reason']}")
        lines.append("")

    if failed:
        lines.append("Something did not go up. Those shows will be retried on the next run.")
    return subject, "\n".join(lines)


def send_email(subject: str, body: str) -> str:
    """Email the report to linus@. Returns a short status for the log."""
    if not GMAIL_SEND_CONFIG.exists():
        return f"not sent: no send credential at {GMAIL_SEND_CONFIG}"
    try:
        config = json.loads(GMAIL_SEND_CONFIG.read_text())
        token_response = urllib.request.urlopen(
            urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=urllib.parse.urlencode(
                    {
                        "client_id": config["client_id"],
                        "client_secret": config["client_secret"],
                        "refresh_token": config["refresh_token"],
                        "grant_type": "refresh_token",
                    }
                ).encode(),
                method="POST",
            ),
            timeout=60,
        )
        access_token = json.loads(token_response.read().decode())["access_token"]

        message = EmailMessage()
        message["To"] = config.get("to", "linus@giantrockmeetingroom.com")
        message["From"] = config.get("from", config.get("to", "linus@giantrockmeetingroom.com"))
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        urllib.request.urlopen(
            urllib.request.Request(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                data=json.dumps({"raw": raw}).encode(),
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            ),
            timeout=60,
        )
        return "sent"
    except Exception as exc:  # a failed report must never fail the run
        return f"not sent: {type(exc).__name__}: {str(exc)[:160]}"


def record(run: dict[str, Any]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text(json.dumps(run, indent=2) + "\n")
    with HISTORY_LOG.open("a") as handle:
        handle.write(json.dumps(run) + "\n")


# --- run -------------------------------------------------------------------


def acquire_lock() -> None:
    """Stop two runs overlapping; a stale lock from a crash is ignored after an hour."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        age = dt.datetime.now().timestamp() - LOCK_FILE.stat().st_mtime
        if age < 3600:
            raise SchedulerError("Another scheduler run is in progress.")
    LOCK_FILE.write_text(str(os.getpid()))


def main() -> None:
    args = parse_args()
    today = parse_date(args.today) if args.today else dt.date.today()
    if today is None:
        raise SystemExit(f"--today {args.today!r} is not a valid YYYY-MM-DD date.")

    run: dict[str, Any] = {
        "run_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "published": args.publish,
        "results": [],
        "skipped": [],
        "fatal": None,
    }

    try:
        acquire_lock()
        token = gcloud_token()
        header, rows, row_numbers = read_sheet(token)

        for row, row_number in zip(rows, row_numbers):
            show_id = row.get("show_id", "").strip() or "(no show_id)"
            if args.only and show_id != args.only:
                continue

            due, reason = assess(row, today, args)
            if not due:
                run["skipped"].append({"show_id": show_id, "reason": reason})
                continue

            try:
                flyer = fetch_flyer(row["flyer_ref"].strip(), token)
            except SchedulerError as exc:
                run["skipped"].append({"show_id": show_id, "reason": str(exc)})
                continue

            posted_anywhere = False
            for account in target_accounts(row):
                outcome = publish_story(row, account, flyer, args.publish)
                outcome.update({"show_id": show_id, "account": account, "handle": ACCOUNTS[account]["handle"]})
                run["results"].append(outcome)
                if outcome["status"] == "posted":
                    posted_anywhere = True

            # The contract: last_posted is set once a Story is live on at least one account.
            if posted_anywhere and args.publish:
                try:
                    write_last_posted(token, header, row_number, run["run_at"])
                except SchedulerError as exc:
                    run["results"].append(
                        {
                            "show_id": show_id,
                            "account": "sheet",
                            "handle": "last_posted",
                            "status": "failed",
                            "media_id": None,
                            "error": str(exc)[:200],
                        }
                    )

        if args.only and not run["results"] and not run["skipped"]:
            run["fatal"] = f"No row with show_id {args.only!r}."

    except SchedulerError as exc:
        run["fatal"] = str(exc)
    finally:
        LOCK_FILE.unlink(missing_ok=True)

    subject, body = build_report(run)
    run["email"] = "skipped (--no-email)" if args.no_email else send_email(subject, body)
    record(run)

    print(subject)
    print()
    print(body)
    if run["fatal"] or any(r["status"] == "failed" for r in run["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
