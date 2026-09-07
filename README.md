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
- `social/config/`: local publishing config (the filled-in file stays out of git)
- `social/sessions/`: saved browser session state

Shared logic:

- `social/scripts/policy.py`: trusted-account and repost decision helpers
- `social/scripts/instagram_graph_config.py`: config and Graph API request helpers
- `social/scripts/image_prep.py`: flyer to Instagram-ready JPEG conversion
- `social/scripts/gcs_upload.py`: private image hosting with short-lived signed URLs, via the gcloud CLI
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

Find a likely flyer email in Gmail, download its attachments locally, and turn it into a draft plus staging manifest:

```bash
source .venv/bin/activate
python3 social/scripts/intake_gmail_flyer.py --draft
```

Use `--message-id` if you already know the exact Gmail message to intake. The
script currently works fully with Gmail and will attempt calendar matching, but
it records a graceful warning if Google Calendar API access is not enabled for
the current `gog` project yet.

Intake a flyer or event image from the local synced Google Drive mount and turn it into a draft plus staging manifest:

```bash
source .venv/bin/activate
python3 social/scripts/intake_drive_flyer.py --draft
```

By default this scans recent files from the local `linus@giantrockmeetingroom.com`
Google Drive Desktop mount, prioritizing:

- `GRMR Mac Docs/Marketing&Graphics/INSTAGRAM FLYERS`
- `GRMR Mac Docs/Marketing&Graphics/Flyers`
- `GRMR Mac Docs/Events`

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
6. Publish it with `publish_instagram_graph.py`, which uses Meta's official API.
7. Only use `--publish` when you want the post to go public. Browser automation
   (`stage_instagram_post.py`) stays available as a fallback.

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

## Publishing to Instagram (official Graph API)

`publish_instagram_graph.py` is the supported way to post. It publishes to an
Instagram professional account that is linked to a Facebook Page, using Meta's
Content Publishing API. The Playwright scripts stay in place as a fallback for
anything the API cannot do.

The flow for one approved post:

1. Validate the post: approved status, one asset, a caption within Instagram's limits.
2. Convert the flyer to JPEG, the only format the API accepts.
3. Upload it to a private Google Cloud Storage bucket.
4. Hand Meta a signed URL that expires in about an hour.
5. Create a media container and poll until Meta has fetched the image.
6. Publish, then archive the post and record the media ID.

### Why the image gets padded

Instagram only accepts aspect ratios between 4:5 and 1.91:1, and it silently
crops anything outside that range. A standard 8.5x11 flyer is about 0.77, just
outside the limit, so Instagram would trim the top or bottom and cut off text.
The default `--fit pad` adds white bars to bring the flyer into range instead,
leaving every word intact. Use `--fit crop` only when you know the edges are
safe to lose, or `--fit error` to refuse anything out of range.

### One-time setup

**1. Meta side.** In a Meta app with the Instagram Graph API product, grant
`instagram_basic`, `instagram_content_publish`, and `pages_read_engagement`.
Find the two IDs with:

```bash
curl -s "https://graph.facebook.com/v25.0/me/accounts?access_token=$IG_GRAPH_ACCESS_TOKEN"
curl -s "https://graph.facebook.com/v25.0/PAGE_ID?fields=instagram_business_account&access_token=$IG_GRAPH_ACCESS_TOKEN"
```

**2. Private bucket with a 7-day backstop.** Signed URLs expire in an hour; the
lifecycle rule makes sure nothing lingers even if a run fails partway:

```bash
gcloud storage buckets create gs://YOUR_BUCKET --location=us-west1 --uniform-bucket-level-access
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}' > /tmp/lifecycle.json
gcloud storage buckets update gs://YOUR_BUCKET --lifecycle-file=/tmp/lifecycle.json
```

Do not make the bucket public. `check_instagram_graph.py` warns if it is.

**3. Credentials for signing.** Uploading and signing both run through the
gcloud CLI, so it is a runtime requirement, not just a setup tool:

```bash
brew install --cask gcloud-cli
```

```bash
gcloud auth login
```

A signed URL must be signed by a service account. Impersonating one keeps no
private key on disk, which is also the only option when the organization
enforces `constraints/iam.disableServiceAccountKeyCreation`. Check that with
`gcloud resource-manager org-policies describe
constraints/iam.disableServiceAccountKeyCreation --project=YOUR_PROJECT
--effective` before assuming a key file is possible.

```bash
gcloud services enable iamcredentials.googleapis.com
```

```bash
gcloud iam service-accounts create ig-publisher --display-name="Instagram publisher"
```

```bash
gcloud storage buckets add-iam-policy-binding gs://YOUR_BUCKET --member=serviceAccount:ig-publisher@YOUR_PROJECT.iam.gserviceaccount.com --role=roles/storage.objectAdmin
```

```bash
gcloud iam service-accounts add-iam-policy-binding ig-publisher@YOUR_PROJECT.iam.gserviceaccount.com --member=user:YOUR_EMAIL --role=roles/iam.serviceAccountTokenCreator
```

The service account needs `objectAdmin` because a signed URL is authorized as
whoever signed it: you upload as yourself, but Meta's fetch is checked against
the service account's permissions.

Then set `impersonate_service_account` in the config and leave
`service_account_key_file` null. Also set `gcs.region` to the bucket's location
(`US` for a multi-region bucket). The signer only holds object permissions, not
`storage.buckets.get`, so it cannot auto-detect the region while impersonating.

Application Default Credentials are deliberately not used. Some Workspace orgs
do not allowlist the ADC OAuth client, which makes `gcloud auth
application-default login` fail with a "scope is required but not consented"
error even when `gcloud auth login` works. Going through the CLI sidesteps that
entirely.

**4. Config and token.** Copy the example and fill it in. The real config file
is gitignored:

```bash
cp social/config/instagram-graph.example.json social/config/instagram-graph.json
```

Keep the access token out of the repository. Either export it:

```bash
export IG_GRAPH_ACCESS_TOKEN='your-long-lived-token'
```

or store it in a file outside the repo and point `access_token_file` at it:

```bash
mkdir -p ~/.config/grmr && chmod 700 ~/.config/grmr
printf '%s' 'your-long-lived-token' > ~/.config/grmr/instagram-graph-token
chmod 600 ~/.config/grmr/instagram-graph-token
```

A token from the Graph API Explorer lasts about an hour. Exchange it for a
long-lived one (needs `app_id` and `app_secret` in the config):

```bash
python3 social/scripts/check_instagram_graph.py --exchange-token --output ~/.config/grmr/instagram-graph-token
```

### Check the setup

```bash
source .venv/bin/activate
python3 social/scripts/check_instagram_graph.py --check-upload
```

This reports token validity, missing permissions, token expiry, the linked
account, remaining publishing quota, and whether the bucket is private and has
the 7-day rule. `--check-upload` round-trips a small test object through a
signed URL and deletes it afterwards.

### Publish a post

Every run is a dry run unless `--publish` is passed. Being in the approved lane
is never enough on its own. Walk it up one stage at a time:

```bash
python3 social/scripts/publish_instagram_graph.py
```

```bash
python3 social/scripts/publish_instagram_graph.py --stage upload
```

```bash
python3 social/scripts/publish_instagram_graph.py --stage container
```

```bash
python3 social/scripts/publish_instagram_graph.py --publish
```

- default (no flags): converts the image and reports what it would do. No upload, no API calls.
- `--stage upload`: also uploads and verifies the signed URL is fetchable.
- `--stage container`: also creates the media container at Meta. Still not public. Meta discards an unpublished container after 24 hours.
- `--publish`: publishes, archives the post to `social/content/posted/`, and records the media ID and permalink.

Useful flags: `--post` to pick a specific file, `--caption` to override the
caption, `--fit`/`--pad-color` for image handling, and `--force` to override the
duplicate guard.

### Safety behavior

- Nothing publishes without `--publish`. There is no automatic path from approved to posted.
- A post already in `social/content/posted/`, or whose image already appears in the publish ledger, is refused unless `--force` is passed.
- The publishing quota is checked before posting.
- If a run fails before publishing, the uploaded object is deleted. Pass `--keep-remote` to keep it for debugging.
- Access tokens are never written to logs, and signed URLs are logged with the signature stripped.

Written to `social/logs/` (all gitignored):

- `instagram-graph-latest-run.json`: the most recent run at any stage
- `instagram-graph-latest-error.json`: details of the most recent failure
- `instagram-graph-publish-ledger.json`: every post published through the API
- `instagram-graph-check.json`: the most recent setup check

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
