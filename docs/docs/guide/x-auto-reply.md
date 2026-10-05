---
id: x-auto-reply
title: X auto-reply
sidebar_label: X auto-reply
description: Mention the account and Kazma drafts a reply — declared sides, summon emoji, quotes, and Conversations.
---

# X auto-reply

X Studio composes and reviews posts, schedules approved text, and keeps
conversations with their original sources. Automatic replies are off by
default. Start in draft mode. Publishing uses the official X API and the
same connector credentials and local caps as [X publisher](./x-publisher.md).

## How a summon is decided

Kazma gathers all matching subject cards before choosing a target. A specific
card takes precedence over a catch-all, but card order never resolves two
matching topics. Conflicting matches, exclusions, contextual scope or
exceptions are held for review. Match evidence contains original passages and
offsets. Unicode matching preserves distinct Arabic letters, removes
vocalization/tatweel, and checks token boundaries; configure explicit aliases
for other spellings.

A matching card declares the target and position. Facts constrain the public
argument: conceding a supported fact or rejecting violence does not reverse a
position. Emoji may select a permitted tone. Negated or conflicting summon
instructions require review. Synthetic voice/summon cards remain draft-only;
missing or invalid configuration never grants auto-publishing permission.

## What counts as a summon

The mention is not proof that its author can open a conversation. Trusted
operator/account actions establish open/closed permissions. A stranger's own
`#Open` cannot authorize replies in a closed thread. `#Close` prevents further
untrusted summons. Manual commands and polling share this authority policy.
The resolved original, quotation, authorship and reply target are retained.

Incomplete source context is shown explicitly. Missing quotations,
media-dependent context, unresolved authorship, truncation and fallback text
prevent unattended posting. X account read capabilities must be tested for
the connected account; a historical pricing tier is not a capability check.

## Two ways to fire it

Mention polling and manual summon commands feed the same drafting pipeline.
Studio's Conversations view loads the local log; explicit polling fetches X
mentions and consumes the daily read budget. Previewing pasted text does not
post to X. It can send that text to the selected model and consume model
budgets, so model selection and locality still matter.

## Subjects

Settings → X lets administrators edit targets, aliases, exclusions, positions,
scope, exceptions, custom hard lines, tone permissions and evidence policy.
Imported legacy cards preserve their text and side but default to draft-only.
New cards do not silently default to an against position. Save validates the
whole policy; malformed entries remain visible errors.

Each content change increments the card revision. Per-card preview tests an
unsaved card; complete-policy preview applies routing to all configured cards.
The preview labels these scopes and shows the actual models and check results.
A preview is never publication approval or auto qualification.

### Simple setup and advanced policy

Use the Connection, Model, Subjects, Reply style and Publishing links in
Settings → X. Add a subject, enter its target and choose Support or Against.
The card generates a stable internal ID and initially matches the exact target
you entered. Confirm additional matching terms in Advanced before saving;
Kazma does not guess or silently enable aliases. Changing an existing target
does not erase its matching terms, exceptions or permissions.

Support defends the scoped target and challenges unsupported criticism while
acknowledging substantiated failings. Against challenges the target's actual
claim or public conduct while conceding supported facts. Neither side permits
invented claims, denial of evidence or criticism redirected at an incidental
entity. Quotes, sarcasm, negation and competing targets can produce a hold.
Additional instructions are optional. Explicit commands to reverse the chosen
side are rejected; contextual verification checks subtler conflicts.

Advanced retains scope, exceptions, evidence requirements, aliases, exclusions,
examples, hard lines, tone permissions and publication permissions. Hiding
these fields does not reset them. Unsaved changes are indicated beside Save;
validation failures stay visible. If you edit while a save is running, the
newer local edits remain unsaved instead of being overwritten by its response.

### Reply style and language

Choose Professional, Friendly, Humorous, Roast, Angry, Dry, Deadpan or Supportive
on the card. Supportive means constructive wording; it never turns Against
into Support. Global Reply style sets source/English/Arabic language, optional
dialect, slang intensity and short/standard length. The card can override
language settings for one card; otherwise it inherits the global defaults.
Permitted summon emoji can override tone, subject to the card's allowed tones.

**Allow uncensored language** is off by default. Enable it and select None,
Mild or Strong profanity to allow ordinary strong language; it never requires
profanity. A local model may still refuse particular wording. Turning it off
restores clean language. Dialect and slang are separate from profanity.
Hard lines, factual checks, threat/slur restrictions, approvals, weighted X
length limits and publication safeguards remain active at every level.
Known-term screening and contextual model checks can hold a candidate; they
cannot guarantee detection of every slang or disguised expression.

Preview shows the effective target, side, tone, language controls, model roles
and verification results. Compare saved settings with proposed changes on the
same input without saving or posting. Changing input/settings while it runs
discards the stale result. Conversations show recorded effective positions and
tones and can filter them; historical rows without those snapshots remain in
the unfiltered view. Policy/style changes invalidate automatic qualification.

### Choose an X-specific model

Use the global Kazma model or pin an enabled provider and exact model ID for
X work. Advanced bindings can separately select classification, reply drafting,
post drafting, context/target verification, stance verification, factual
verification and safety verification. Bindings are fixed throughout each
operation; changing X selection never switches Kazma's global profile.

Test the X model runs a bounded, fixed opinion-only sample through drafting
and structured verification using the selected unsaved binding and language
controls. It changes no settings and posts nothing. Passing proves that sample's
compatibility, not model accuracy or production qualification.

Specific bindings have no implicit fallback. A stopped local server or missing
model produces a clear hold/error. Local-only checks the configured endpoint
and all role bindings and uses lexical knowledge retrieval without cloud query
embeddings. A localhost server can itself proxy cloud inference: verify its
installed model and server configuration before treating it as offline.
X publication still uses the remote X API.

### Modes

Off disables automated drafting. Draft retains candidates for review. Auto
requires explicit permission on the card, complete context, every required
check passing, live posting permission, budgets and a current evaluation report.
Changing policies, models, relevant knowledge or pipeline code invalidates the
report. Missing qualification retains drafts for approval rather than silently
posting them. Installing a report never changes draft mode to auto.

## Stance check

The pipeline runs five typed checks: context completeness, correct target,
scoped position, factual evidence and universal/custom safety rules. Results are
pass, fail or unknown, with observed passages and concise reasons. Malformed
JSON, missing checks, fabricated source IDs, unsupported assertions or checker
outages never become passes. Explicit failures are rejected; unavailable checks
can retain a candidate with review reasons. The legacy stance-check toggle
cannot disable the independent checks or make unattended work eligible.

These model checks reduce risk; they do not prove truth. Correlated model
errors remain possible and human qualification is required for unattended use.

## Knowledge Base (optional)

Only active libraries authorized for the current tenant can supply passages.
There is no unscoped fallback. Records retain library/document/chunk and version
identities, source URLs where available, content hashes, publication dates,
retrieval times and truncation flags. Opinions are distinguished from factual
assertions. Missing, stale, truncated or unbound factual support requires review.
The per-card evidence age limit defaults to 30 days. A subject position cannot
override contrary evidence or turn the source post's allegation into proof.

## Conversations

Review the full source, candidate, check reasons and exact target. Approve binds
the stored text and monotonic revision to the tenant, verified account,
credential revision and current pipeline. Approvals expire after 24 hours.
Changed content, policy, account, models or relevant evidence requires a fresh
decision. Simultaneous approve/deny/retry operations use conditional claims.
Rejected and uncertain sends cannot be approved into a blind retry.

Decision history retains prior candidates, checks and approval/denial actors.
Removing a row archives it; idempotency evidence remains. Deleting a posted
reply requires confirmed deletion from X before archival. A 404 or unavailable
post is not proof that deletion succeeded.

## Guardrails

All writes use a durable publication operation and one local database claim.
Reservations atomically enforce rolling daily/30-day and reply limits, text
duplicates and future scheduling commitments. Confirmation requires a valid
numeric X identifier. A missing response or interrupted send remains unknown
and retains its reservation; never resend it automatically.

Compatibility projections replay without sending again. Review and scheduled
result notices are persisted beside their state transition, leased, retried
with backoff and acknowledged only by a real ops delivery route. Notification
delivery is at least once; a crash can repeat a notice, not its publication.

Weighted character validation is shared by composer previews and publishing,
including NFC, transformed URLs and recognized emoji sequences. Parser assets
are pinned; newer unrecognized emoji may be conservatively overcounted.

## Turning it on

Configure and test the connected account in Settings. Choose the X models,
review subject permissions, then start in draft mode. Composer autosave is
scoped to the operator and tenant and detects conflicting browser tabs.
Sending remains an explicit action. Scheduled work binds the account and exact
payload. Local clock changes with missing/ambiguous civil times are refused;
use an explicit UTC offset where supported. A schedule missed by more than
five minutes is held for review and rescheduling instead of catching up on boot.

A migration restore pauses publishing. Verify the account, explicitly resume
new publishing in Studio, then separately review old held work. Resume never
releases unknown or restored queued operations. Legacy account-unbound
bookings remain held and must be cancelled/rebooked after review.

## Commands

`/x list` shows full held candidates and revision tokens. Copy the command
for the revision you reviewed:

- `/x approve <summon_id> <revision>` publishes the exact stored candidate.
- `/x deny <summon_id> <revision>` denies that held revision.
- `/x retry <summon_id>` generates a new candidate for review.
- `/x delete <summon_id> <revision>` confirms deletion where applicable and archives.
- `/x poll` fetches mentions; `/x roast <post>` creates a manual candidate.

Native agent publishing tools retain their existing HITL and stored-proposal
requirements. Administrators control credentials, ongoing policy grants,
restore resume and qualification reports; operators review individual work.

## Kill switches

`KAZMA_X_POST=0` disables publication. `KAZMA_X_SCHEDULE=0` disables scheduled
execution. `KAZMA_X_REPLY=0` disables automatic replies. Switches are checked
live before dispatch, independently of previously saved approval.

## Config keys

X settings are stored under `connectors.x`, including `ai`, `ai_limits`,
`reply.subjects`, `reply.mode` and `reply.qualification`. Daily AI/read budget
usage is durable and resets by UTC day. Failed calls still consume reservations.
The global restore pause is `system.x.restore_paused`. Runtime settings do not
rewrite tracked `kazma.yaml`.

## Limitations

SQLite coordination requires one publishing owner or processes sharing the same
supported local database. Separate hosts with separate databases do not
coordinate. Remote writes cannot be made exactly once across an ambiguous
network failure. If both result persistence and audit fail after remote
acceptance, operator investigation remains necessary. Similar text/timing is
not proof of publication or nonpublication.

Local endpoint spelling does not establish offline inference. X endpoint
capabilities and real bilingual model accuracy remain deployment-specific.
Rich media/campaign analytics remain capability-gated. The Studio Threads tab
supports ordered text segments, whole-revision approval and partial recovery;
see [X publisher](x-publisher.md). Settings supports policy import/export and
before/after comparison. Import stages cards for review with automatic permission
disabled; saving an imported policy uses draft mode. Relevant policy changes
invalidate automatic qualification.

## Testing it safely

Preview and isolated fake-transport tests do not publish. Test corpora generated
by code verify mechanics; they are not human accuracy certification.

Unattended qualification needs at least 200 held-out human-reviewed cases,
including at least 40 English and 40 Arabic cases and at least five examples
of each required category: Gulf Arabic, mixed scripts, sarcasm, negation,
quotation, multiple entities, injection, source contradiction and checker outage.
At least 50 cases must be predicted auto-eligible, with precision at least 98%.
Any critical violation, unsupported fact, wrong-target or unchecked auto outcome
blocks release. Reports expire after 14 days and bind the current fingerprint.

A reviewed JSON report has `fingerprint`, `evaluated_at` (UTC epoch) and `cases`.
Each case records `id`, `language` (`en`, `ar`, `mixed`), `labeler`,
`human_reviewed: true`, `held_out: true`, `categories`, `expected` and `actual`.
Expected labels contain `auto`, `target`, `evidence`, `safety`. Actual outcomes
contain `auto`, `target`, `critical_violations`, all five `checks` with typed
verdicts, `latency_ms`, `model_calls`, and `output_tokens`. Record actual shadow
outcomes; never invent successful observations or label tuning data held-out.

Studio exposes the current fingerprint and administrator report upload.
Validate a collected report without installing it:

```powershell
python scripts/x_qualification.py --report reviewed.json
```

Add `--install` only to install a qualified report. With no report,
the script prints readiness and required coverage. It neither labels cases nor
posts. Metrics include denominators, hold/false-hold rates, p95 latency and
model usage; token reservations are not billed monetary cost.

For unknown sends, retain the operation and inspect correlated API receipts
and the connected X account. Only exact validated operation receipts repair
confirmation automatically. For disabled credentials, checker/DB outage or
rate limiting, restore the dependency first; do not weaken policy to clear a
hold. For notification failures, check ops routing and delivery status. For
rollback, disable writes first and preserve the publication/reply databases;
older builds must not fire managed or restored legacy rows.

For collection and annotation instructions, see [Building an X evaluation dataset](x-evaluation-dataset.md).
