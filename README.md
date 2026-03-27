# Social Media Ops

This workspace is set up so Codex can help operate your social media with a
browser automation layer, clear approval rules, and a predictable file
structure.

## Structure

- `social/assets/`: images, videos, thumbnails
- `social/content/drafts/`: work in progress posts
- `social/content/approved/`: posts cleared to publish
- `social/content/posted/`: archived published posts
- `social/content/staging/`: staged content manifests before approval
- `social/content/reposts/`: repost-review packages and source manifests
- `social/calendar/`: scheduling files
- `social/inbox/`: pulled comments, mentions, and DM triage
- `social/analytics/`: metrics snapshots
- `social/voice/`: brand, safety, and approval rules
- `social/logs/`: action logs
- `social/operator/`: rendered queue/progress artifacts for automation handoff
- `social/scripts/`: automation scripts
- `social/sessions/`: saved browser session state

Shared logic:

- `social/scripts/policy.py`: trusted-account and repost decision helpers
- `social/voice/reshare-rules.md`: repost policy
- `social/voice/trusted-repost-accounts.json`: trusted account list

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
python3 social/scripts/login.py --platform x --show-browser
```

The login script automatically uses the local browser bundle in
`.playwright-browsers/` after Playwright is installed there once.
Operational scripts run headless by default. Add `--show-browser` only when you
explicitly want to watch the browser.

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

Stage the latest drafted Instagram DM reply without sending:

```bash
source .venv/bin/activate
python3 social/scripts/send_instagram_dm_reply.py
```

Try to reach the Instagram story reshare flow from the latest DM thread:

```bash
source .venv/bin/activate
python3 social/scripts/reshare_instagram_story.py
```

If the story mention is live, prepare the Instagram story reshare flow without posting:

```bash
source .venv/bin/activate
python3 social/scripts/prepare_instagram_story_reshare.py
```

Draft a post from local image/video assets:

```bash
source .venv/bin/activate
python3 social/scripts/draft_asset_post.py --title "Example title" --asset social/assets/example.png --context "Short notes about the asset"
```

Turn the latest asset in `social/assets/` into a draft plus staging manifest automatically:

```bash
source .venv/bin/activate
python3 social/scripts/intake_latest_asset.py --context "Short notes about the asset"
```

Approve the latest content draft so it moves into the ready-to-post lane:

```bash
source .venv/bin/activate
python3 social/scripts/approve_content_post.py
```

Stage the latest Instagram post draft without publishing:

```bash
source .venv/bin/activate
python3 social/scripts/stage_instagram_post.py
```

Build a single operator queue from the latest Instagram workflow logs:

```bash
source .venv/bin/activate
python3 social/scripts/instagram_operator_queue.py
```

Advance one safe, non-public queue action automatically:

```bash
source .venv/bin/activate
python3 social/scripts/run_instagram_queue.py --refresh
```

Refresh human-readable operator artifacts from the current queue:

```bash
source .venv/bin/activate
python3 social/scripts/refresh_operator_artifacts.py
```

Record a manual queue override after handling something directly in Instagram:

```bash
source .venv/bin/activate
python3 social/scripts/record_instagram_operator_override.py --key "story_reshare::stonelevitation" --status done --action manual_story_shared --details "Story repost completed manually in Instagram." --note "Operator completed this in-app."
```

Create a repost-review manifest from the latest Instagram reshare check:

```bash
source .venv/bin/activate
python3 social/scripts/create_repost_candidate.py
```

Create a staging manifest from the latest content draft:

```bash
source .venv/bin/activate
python3 social/scripts/create_staging_manifest.py
```

Approve the latest staging manifest into the approved lane:

```bash
source .venv/bin/activate
python3 social/scripts/approve_staging_manifest.py
```

Attach real assets to a draft or approved post:

```bash
source .venv/bin/activate
python3 social/scripts/attach_post_assets.py --post social/content/approved/example.md --asset /absolute/path/to/image.png
```

Archive a test or retired content item out of the active workflow:

```bash
source .venv/bin/activate
python3 social/scripts/archive_content_item.py --path social/content/approved/example.md
```

This writes:

- `social/logs/instagram-operator-queue.json`: latest generated queue snapshot
- `social/logs/instagram-operator-backlog.json`: latest known state per item
- `social/logs/instagram-operator-history.json`: rolling history of queue updates
- `social/logs/instagram-operator-overrides.json`: manual state corrections for items handled outside automation

## Content workflow

1. Drop assets into `social/assets/` or pass absolute paths directly.
2. Use `intake_latest_asset.py` for the fastest path, or `draft_asset_post.py` if you want more manual control.
3. Review the markdown draft in `social/content/drafts/`.
4. Review the staging manifest in `social/content/staging/`.
5. Move the content into `social/content/approved/` with `approve_content_post.py` or `approve_staging_manifest.py`.
6. Stage it in Instagram with `stage_instagram_post.py`.
7. Only use `--share` when you want to publish publicly.

## Queue runner

`run_instagram_queue.py` is the safe executor for the operator system. It can:

- refresh the inbox and queue
- refresh the human-readable operator artifacts after queue changes
- draft a reply for an unread DM
- prepare a live story reshare flow
- stage an approved Instagram post

It will not send DMs, publish posts, or make public changes on its own.

`refresh_operator_artifacts.py` renders:

- `social/operator/operator-queue.json`
- `social/operator/operator-queue.md`
- `social/operator/progress-latest.md`
- `social/operator/cards/*.md`

These are useful for automations and mobile review because they condense the current state into readable artifacts. The `cards/` directory gives each queue item its own short review file with the current status, next action, and any draft or debug context.

If Instagram’s post composer fails while staging a post, the workflow now writes:

- `social/logs/instagram-latest-post-stage-debug.json`

That debug log includes the current page URL, visible actions, a body excerpt, and a screenshot path so the failure can be diagnosed quickly.

## Initial operating model

Start in approval-first mode:

- Codex can draft content freely.
- Codex prepares posts for review before publishing.
- Codex does not send DMs or public replies until you allow that.

Once the browser session works, we can add posting, inbox checks, and reply
workflows.

## GitHub and iPhone workflow

Recommended setup:

1. Publish this repository to GitHub.
2. Keep browser sessions and generated logs local only.
3. Review source files, queue artifacts, and markdown content from your phone through GitHub or the Codex app.

What should live in GitHub:

- scripts in `social/scripts/`
- voice and policy files in `social/voice/`
- reusable templates in `social/content/templates/`
- approved process docs like this README

What should stay local:

- `social/sessions/`
- `social/inbox/`
- `social/logs/`
- generated operator artifacts in `social/operator/`
- generated intake, staging, and repost manifests

Suggested mobile pattern:

1. Use Codex on desktop to refresh queue artifacts or draft content.
2. Review repo changes or markdown workflow files from iPhone.
3. Approve or request edits, then return to desktop only for browser-driven actions that need the local session.

## Using this project from iPhone

Best app split:

- `ChatGPT`: ask Codex for planning, writing, repo edits, and workflow help
- `GitHub`: read files, review commits, and check the current repo state

What works well from iPhone:

- reviewing `README.md`, voice docs, and templates
- asking Codex to update copy, docs, or scripts
- checking what changed in the repo
- planning captions, approvals, and next steps

What still needs the Mac:

- Playwright and browser automation
- Instagram login or session-dependent scripts
- anything that depends on local files in `social/sessions/`, `social/logs/`, or `social/inbox/`
