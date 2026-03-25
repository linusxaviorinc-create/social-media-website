# Social Media Ops

This workspace is set up so Codex can help operate your social media with a
browser automation layer, clear approval rules, and a predictable file
structure.

## Structure

- `social/assets/`: images, videos, thumbnails
- `social/content/drafts/`: work in progress posts
- `social/content/approved/`: posts cleared to publish
- `social/content/posted/`: archived published posts
- `social/calendar/`: scheduling files
- `social/inbox/`: pulled comments, mentions, and DM triage
- `social/analytics/`: metrics snapshots
- `social/voice/`: brand, safety, and approval rules
- `social/logs/`: action logs
- `social/scripts/`: automation scripts
- `social/sessions/`: saved browser session state

## Quick start

1. Create and activate a local virtualenv.
2. Install Python dependencies.
3. Install a Playwright browser.
4. Run the login script for your first platform.

Commands:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 social/scripts/login.py --platform x
```

The login script automatically uses the local browser bundle in
`.playwright-browsers/` after Playwright is installed there once.

Instagram activity snapshot:

```bash
source .venv/bin/activate
python3 social/scripts/check_instagram.py
```

Draft a reply for the top unread Instagram DM:

```bash
source .venv/bin/activate
python3 social/scripts/draft_instagram_dm_reply.py
```

## Initial operating model

Start in approval-first mode:

- Codex can draft content freely.
- Codex prepares posts for review before publishing.
- Codex does not send DMs or public replies until you allow that.

Once the browser session works, we can add posting, inbox checks, and reply
workflows.
