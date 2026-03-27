# Reshare Rules

Use this file to decide whether tagged content should be reshared automatically,
held for review, or ignored.

## Decision model

- `trusted_auto_repost`:
  Accounts in the trusted list whose story or post fits the venue's voice.
  These can move into the reshare flow automatically, but still stop before
  posting unless approval is explicitly allowed.
- `review_first`:
  Accounts that are welcome but not automatically reposted. Draft a recommendation
  and wait for approval.
- `never_auto_repost`:
  Accounts or content categories that should never be reposted without explicit
  human review.

## Trusted signals

- Bands that played the venue
- Recurring collaborators
- Local artists or organizations the venue actively supports
- Content clearly tied to events at Giant Rock Meeting Room

## Review-first signals

- First-time tags from unfamiliar accounts
- Personal attendee stories
- Posts without clear venue/event context
- Content that is on-brand but still uncertain

## Never-auto-repost signals

- Political content
- Personal drama or callout posts
- Off-brand or unrelated material
- Low-quality repost bait
- Anything that conflicts with the venue's warm community-host voice

## Operational rule

- If a story mention is live and the account is `trusted_auto_repost`, open the
  reshare flow and stop before posting unless direct posting approval exists.
- If the story mention is unavailable, draft or send a reshare-request reply.
- If the account is `review_first`, draft a recommendation instead of reposting.
- If the account is `never_auto_repost`, do not repost and do not auto-engage.
