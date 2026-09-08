# Phase 1 Summary — Instagram Graph API Publisher

Context handoff for discussing what to build next.
Written 2026-09-07, updated 2026-09-08.

## Status: Phase 1 is COMPLETE and proven — do not redo setup

Every install, login, and IAM step is done and verified on the Mac mini, and the
pipeline has now published real posts. An earlier version of this document led a
reader to try re-running the setup from scratch. None of it needs repeating.

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

**Published through this pipeline on 2026-09-08:**

- Psycho Cycle Rock & Roll Freek Out (Sat Sept 19) —
  https://www.instagram.com/p/DdCfLXQAfGv/
- Kal-El with Goya and Black Moon Cult (Fri Sept 25) —
  https://www.instagram.com/p/DdCkambgU-K/ — with collaborator invites to
  kalelband, goyaaz, blackmooncult

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

Five scripts in `social/scripts/`:

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
and `--fit error` exist if wanted. In practice both flyers posted so far were
already 4:5 and needed no padding.

**Nothing publishes without an explicit `--publish` flag.** Being in the
approved lane never posts on its own. Dry run is the default, and there are
intermediate stages (`--stage upload`, `--stage container`) to test the pipeline
without going public. The container stage is the useful rehearsal: Meta fetches
and validates the image, but the post stays invisible and expires in 24 hours.

**Duplicate protection.** A post already marked posted, or whose image already
appears in the publish ledger, is refused unless `--force` is passed. Note the
ledger lives in `social/logs/` and is gitignored, so it does not travel with the
repo; the `status: posted` frontmatter in `social/content/posted/` does.

**Collaborators are supported.** Up to three Instagram usernames via
`--collaborator` or a `collaborators` list in the post frontmatter. They must be
set when the container is created, so they cannot be added to an already
published post through the API. Invited accounts show `invite_status: Pending`
until they accept.

**Uploading and signing go through the gcloud CLI, not the Python client
library.** This was forced, not preferred: the Workspace org blocks service
account keys by policy (`constraints/iam.disableServiceAccountKeyCreation`) and
does not allowlist the ADC OAuth client, so both credential paths the Python
library supports are closed. The gcloud CLI's own credentials work. This also
matches the repo's existing pattern of shelling out to the `gog` CLI.

**Secrets never touch the repo or the logs.** The token lives at
`~/.config/grmr/instagram-graph-token` (chmod 600, outside the repo). The real
config file is gitignored. Signed URLs are logged with the signature stripped.

## Infrastructure that is live

- GCP project `grmr-instagram`
- Bucket `gs://grmr-instagram-publish` — private, public access prevention
  enforced, 7-day delete lifecycle rule
- Service account `ig-publisher@grmr-instagram.iam.gserviceaccount.com` with
  `objectAdmin` on the bucket; `linus@giantrockmeetingroom.com` holds
  `serviceAccountTokenCreator` on it so URLs can be signed without a key file
- Meta: Page `435536763613224`, Instagram user `17841407794098173`, long-lived
  token with all required scopes, publishing quota 100 per 24h

## What is proven

Everything, with real posts. Image conversion across five shapes; every guardrail
(unapproved post, already posted, over-long caption, too many hashtags, missing
asset, missing token, missing config, duplicate, flag conflict, too many
collaborators); the full GCS round trip; container creation; publishing;
collaborator invites.

The failure path is proven too. A bad collaborator handle (`kalel_official`) was
rejected by Meta at container creation: nothing was published, the orphaned GCS
upload was deleted automatically, and the post stayed in the approved lane for
retry. The correct handle turned out to be `kalelband`.

## What is NOT built

- **Scheduling.** `social/calendar/` is an empty folder. The system can only
  publish immediately; there is no way to queue a flyer for a future date.
- **Analytics.** `social/analytics/` is an empty folder. No metrics are pulled.
- **A website.** Despite the repo name, there is no website code here at all.
- **Carousels.** Single image only.
- **Sending DMs / public replies.** Still deliberately staging-only.

## Practical constraints

- **gcloud credentials expire about daily.** The Workspace account enforces a
  session length policy. When it lapses, the publisher fails at the GCS step
  with "Reauthentication failed. cannot prompt during non-interactive
  execution," which looks like a bucket problem but is purely auth. Run
  `gcloud auth login` first in any publishing session.
- The publisher runs only on the Mac mini, from Terminal — directly or through
  Jump Desktop from home. gcloud and Python are already installed there.
- The iPhone can review and approve through GitHub but cannot publish.
- Posts made by hand from the Instagram app are invisible to the duplicate
  guard. If a flyer is posted manually, archive it out of the approved lane.
- All shows are all ages unless the event says otherwise (the Gala is an
  exception). Flyers usually do not print this, so it comes from the operator.
- Captions should not state pricing or ticket details unless the operator
  confirms them; the flyer alone is not always enough.
- System Python is 3.9.6, which is past end of life. Google's libraries warn
  about it. Not urgent, but an upgrade is owed.

## Open questions for the next discussion

1. Is **scheduling** the right next thing to build? It is the most obvious gap
   for a venue posting flyers for dated events, and the only one that changes
   day-to-day work. Right now every post has to be published by hand at the
   moment it should go live.
2. Does "website" in the repo name mean an actual public site is wanted, or is
   that just the repo's name?
3. Is analytics worth building, and what would actually be acted on?
4. Are carousels or stories needed through the API, or is single-image enough?
5. Should anything handle the recurring gcloud reauth more gracefully — for
   example detecting it early and prompting — or is running `gcloud auth login`
   first simply part of the routine?
