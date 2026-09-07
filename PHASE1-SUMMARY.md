# Phase 1 Summary — Instagram Graph API Publisher

Context handoff for discussing what to build next. Written 2026-09-07.

## Setup status: COMPLETE — do not redo

Every install, login, and IAM step is already done, on the Mac mini, and
verified. This is stated up front because an earlier reading of this document
led to an attempt to re-run the setup from scratch. None of it needs repeating.

```
gcloud installed   : Google Cloud SDK 583.0.0
logged in as       : linus@giantrockmeetingroom.com
active project     : grmr-instagram
service account    : ig-publisher@grmr-instagram.iam.gserviceaccount.com
SA objectAdmin     : yes
you tokenCreator   : yes
lifecycle age days : 7
venv Pillow        : 11.3.0
token file         : -rw------- (~/.config/grmr/instagram-graph-token)
config real IDs    : 17841407794098173 (filled in, gitignored)
```

Confirmed green by `python3 social/scripts/check_instagram_graph.py --check-upload`,
which passes token, account, Page link, quota, bucket, and a full signed-URL
round trip.

**The only thing left in Phase 1 is publishing a real post.** Everything else is
built and working.

## The goal

Post approved event flyers to Instagram through Meta's official publishing API,
replacing an older setup that drove the Instagram website with a browser robot.
The browser automation still exists and is kept as a fallback.

Accounts: `@giantrockmeetingroom` (Instagram professional account) linked to the
Facebook Page "Giant Rock Meeting Room - Pizza & Bar", both in one Meta Business
account.

## Two decisions made before building

1. Use the **Instagram Graph API** path for a professional account linked to a
   Facebook Page — not "Instagram API with Instagram Login."
2. Host images in a **private Google Cloud Storage bucket** with short-lived
   (~1 hour) signed URLs that Meta fetches, plus a 7-day auto-delete backstop.

## What was built

Five new scripts in `social/scripts/`:

| File | Role |
|---|---|
| `publish_instagram_graph.py` | Orchestrator: the whole publish flow |
| `image_prep.py` | Flyer to Instagram-ready JPEG |
| `gcs_upload.py` | Private bucket upload + signed URLs |
| `instagram_graph_config.py` | Config, Graph API calls, secret redaction |
| `check_instagram_graph.py` | Verifies the setup before posting |

The flow for one approved post: validate the post, convert the image to JPEG,
upload to GCS, hand Meta a signed URL, create a media container, poll until Meta
has fetched the image, publish, then archive the post and record the media ID.

## Design decisions worth knowing

**Images are padded, not cropped.** Instagram only accepts aspect ratios between
4:5 and 1.91:1, and silently crops anything outside that range. A standard
8.5x11 flyer is about 0.77 — just outside — so Instagram would trim the top or
bottom and cut off text. The default pads with white bars instead. `--fit crop`
and `--fit error` exist if wanted.

**Nothing publishes without an explicit `--publish` flag.** Being in the
approved lane never posts on its own. Dry run is the default, and there are
intermediate stages (`--stage upload`, `--stage container`) to test the pipeline
without going public.

**Duplicate protection.** A post already marked posted, or whose image already
appears in the publish ledger, is refused unless `--force` is passed.

**Uploading and signing go through the gcloud CLI, not the Python client
library.** This was forced, not preferred: the Workspace org blocks service
account keys by policy (`constraints/iam.disableServiceAccountKeyCreation`) and
does not allowlist the ADC OAuth client, so both credential paths the Python
library supports are closed. The gcloud CLI's own credentials work. This also
matches the repo's existing pattern of shelling out to the `gog` CLI.

**Secrets never touch the repo or the logs.** The token lives at
`~/.config/grmr/instagram-graph-token` (chmod 600, outside the repo). The real
config file is gitignored. Signed URLs are logged with the signature stripped.

## Infrastructure that is now live

- GCP project `grmr-instagram`
- Bucket `gs://grmr-instagram-publish` — private, public access prevention
  enforced, 7-day delete lifecycle rule
- Service account `ig-publisher@grmr-instagram.iam.gserviceaccount.com` with
  `objectAdmin` on the bucket; `linus@giantrockmeetingroom.com` holds
  `serviceAccountTokenCreator` on it so URLs can be signed without a key file
- Meta: Page `435536763613224`, Instagram user `17841407794098173`, long-lived
  token with all required scopes, publishing quota 0 of 100 per 24h

## What is proven and what is not

**Verified by actually running it:** image conversion across five shapes
(letter flyer, ultra-wide, transparent PNG, square, undersized); every guardrail
(unapproved post, already posted, over-long caption, too many hashtags, missing
asset, missing token, missing config, duplicate, flag conflict); the full GCS
round trip (upload, sign, unauthenticated fetch returns 200, unsigned fetch
returns 403); live Graph API error handling; token, account, Page link and quota
checks.

**Not verified:** the `media` (container create) and `media_publish` calls have
never run. Nothing has ever been posted. This is the one untested seam.

## What is NOT built

- **Scheduling.** `social/calendar/` is an empty folder. The system can only
  publish immediately; there is no way to queue a flyer for a future date.
- **Analytics.** `social/analytics/` is an empty folder. No metrics are pulled.
- **A website.** Despite the repo name, there is no website code here at all.
- **Carousels.** Single image only.
- **Sending DMs / public replies.** Still deliberately staging-only.

## Practical constraints

- The publisher runs only on the Mac mini, from Terminal — directly or through
  Jump Desktop from home. It needs gcloud and Python locally, both of which are
  already installed and authenticated there (see Setup status above).
- The iPhone can review and approve through GitHub but cannot publish.
- Posts made by hand from the Instagram app are invisible to the duplicate
  guard. If a flyer is posted manually, archive it out of the approved lane.
- System Python is 3.9.6, which is past end of life. Google's libraries warn
  about it. Not urgent, but an upgrade is owed.

## Open questions for the next discussion

1. Should Phase 1 be proven with one real flyer before anything else is built?
   The content folders (`intake`, `drafts`, `approved`, `posted`) are all empty —
   the pipeline has never carried a real post end to end.
2. Is **scheduling** the right next thing? It is the most obvious gap for a
   venue posting flyers for dated events.
3. Does "website" in the repo name mean an actual public site is wanted, or is
   that just the repo's name?
4. Is analytics worth building, and what would actually be acted on?
5. Are carousels or stories needed through the API, or is single-image enough?
