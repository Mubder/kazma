# X Studio — accuracy, safety, reliability, and operator workflow

**Status:** Core text Studio engineering delivered; see the
[execution record](X_STUDIO_EXECUTION.md) for verified behavior and remaining
engineering/rollout gates. Automatic replies remain unqualified without real
human evaluation. The checklist distinguishes delivered engineering from
deployment qualification and subsequent capability-gated features.
**Date:** 2026-10-03 (Asia/Kuwait).
**Scope:** X Settings, subjects/stances, mentions and manual summons, drafts,
approvals, immediate publishing, scheduling, threads, Studio UI, audit,
operations, and the native skill/gateway callers of these capabilities.
**Basis:** Original source review and tests/docs. The findings below describe
the pre-change implementation; isolated regressions now cover the corrections.
A live capability/configuration audit and real evaluation baseline remain
rollout work.

## 1. Intended outcome

Kazma speaks from the operator's declared positions, addresses the correct
target, uses supportable facts, and can explain why a reply was generated,
held, rejected, or published. The Studio preserves composing work, displays
durable publication truth, and gives operators clear recovery actions.

Industrial readiness means enforceable boundaries and tested recovery, rather
than a stronger prompt alone. Model verification reduces risk; it does not
prove factual truth. Local idempotency prevents duplicate local execution;
it cannot guarantee exactly-once remote delivery across an ambiguous network
failure. Such outcomes must remain unknown until sufficient evidence resolves
them, without blind resend.

### Binding product decisions

- Retain operator-defined support/against positions. Define their target,
  scope, exceptions, and factual boundaries precisely; do not replace them
  with mandatory neutrality.
- Facts constrain claims. A stance never authorizes inventing facts, denying
  supported facts, fabricating quotations, or accepting allegations as proof.
- Tone is separate from stance. Emoji cannot override a declared card. A
  summon may choose a one-off side only under an explicit operator permission.
- Missing context, conflicting matches, invalid configuration, unavailable
  required checks, and unsupported material claims prevent auto-publishing.
  Safe candidates may be retained for review with the reason visible.
- Draft is the default for new/changed subjects. Auto is permission per
  subject in addition to the master mode, granted after evaluation.
- Explicit violations remain rejected. Review is not a bypass of universal
  hard lines; edits create a new revision which is checked again.
- No automatic retry of an ambiguous remote write. No notification may say
  "not published" when the outcome is unknown.
- All transports use the same decision and publication services. Preserve
  always-HITL native chat tools, stored-proposal binding, canonical danger
  tool parity, and the operator-click approval contract for the web surface.

## 2. Current findings and required evidence

| ID | Observed source behavior | Required reproduction / resolution |
|---|---|---|
| F01 | `stance._keyword_hit` returns the first card; non-ASCII matching is containment. | Multi-topic, incidental mention, quotation, Arabic boundary/alias cases; collect candidates before deciding. |
| F02 | `reply._build_prompt` mandates unconditional support/criticism and says position wins over conflicting KB notes. | Draft cases with contrary evidence; replace factual override with scoped argument and abstention. |
| F03 | `screen_draft` checks length/emptiness and several English threat patterns, not the subject's complete hard lines. | English/Arabic/custom-rule violations; add independent contextual verification. |
| F04 | `check_stance` receives subject and draft, not original post; catch-all skips it even with a side. | Wrong-target draft and sided catch-all; check applicability and stance against resolved context. |
| F05 | No subjects yields implicit voice; invalid cards are skipped; multiple unmatched branches permit voice or a summon-selected side. | Broken config and overlapping fallbacks; separate explicit voice permission from missing/invalid cards. |
| F06 | `is_summoner` accepts an open marker from parent or summon text, including a stranger's own mention. | Stranger self-opens a closed-by-default thread; authenticate and persist open/close authority. |
| F07 | Schedule fire reads pending without an atomic send claim. | Two processes and crash-after-send; add guarded execution states and uncertain-outcome recovery. |
| F08 | Booking checks count/dedupe separately from insertion; immediate publish checks before network await and records afterward. | Concurrent booking/post/reply at a cap; one transactional reservation authority. |
| F09 | Schedule edit/cancel use an ID without tenant ownership in these handlers; some tenant helpers default on errors. | Verify full middleware and direct service behavior; fail closed and scope ownership in queries. |
| F10 | Immediate publish/delete ledger calls are synchronous on the loop and may fail after remote success. | Database refusal after confirmed remote result; offload IO, preserve outcome, repair projections. |
| F11 | X client folds reset epoch into Retry-After prose; scheduler extracts digits as seconds. | Seconds/date/reset-epoch cases; typed rate-limit metadata. |
| F12 | Queue/drafts/audit fetch failures can render empty; Posted uses API audit events. | API outage, stale responses, failed writes/deletes; durable content views and explicit freshness. |
| F13 | Approval reads a held record then publishes without atomic approval claim; republishability uses reason substrings. | Double approval, approve/deny/retry races, uncertain failures; revision-bound claims and typed outcomes. |
| F14 | KB lookup falls back from tenant lookup to unscoped library lookup and silently drafts without notes on failure. | Verify library sharing policy and evidence-required subjects; no cross-tenant fallback without explicit authorized sharing. |

Source anchors: `kazma_core/x_api/{stance,reply,reply_store,booking,policy,
schedule,scheduled_fire,client}.py`; `kazma_ui/{x_api,x_reply_api,
scheduled_api}.py`; `static/js/x_studio.js`; Settings subject editor in
`templates/settings.html` and `static/js/settings_integrations.js`.

## 3. Target architecture and durable truth

Keep public wrappers and route contracts compatible where possible. Extract
focused core modules rather than expanding `reply.py` or the Studio component
into larger controllers. Names below are proposed, not existing files.

| Component | Responsibility |
|---|---|
| `x_api/subject_policy.py` | Versioned card schema, explicit permissions, validation, migration. |
| `x_api/context.py` | Resolved source/quote/mention, authorship, missing/truncated/media context flags. |
| `x_api/subject_router.py` | Candidate discovery, contextual applicability, conflicts, target selection. |
| `x_api/evidence.py` | Tenant-authorized retrieval, provenance, freshness, claim/evidence links. |
| `x_api/verification.py` | Typed relevance/stance/factual/hard-line verdicts; required-check policy. |
| `x_api/decision.py` | Shared routing → evidence → draft → checks → hold/reject/ready orchestration. |
| `x_api/publication_store.py` | Durable operations, reservations, attempts, guarded transitions, events and projection outbox. |
| `x_api/publication_service.py` | All X writes, approval validation, send ownership, result classification and reconciliation. |
| Existing stores/services | Compatibility readers/wrappers and replayable projections during migration. |

The publication store is the authority for send execution and quota
reservations. Use one SQLite WAL database under `data_dir()` for operation,
reservation, attempt, and outbox transactions; do not pretend transactions
across separate legacy SQLite files are atomic. Register the proposed
`x_publications.db` in `store_registry.STORES`, migration bundle, readers, and
write declarations before introducing it. Add the decision/draft tables there
where approval and revision state must commit with operation creation.

Legacy post/schedule/reply/artifact stores remain compatible during rollout.
Updates to them are idempotent outbox projections keyed by operation ID, with
repair and lag visibility. No split source of truth for deciding whether to
send. Migrate reads toward authoritative views before retiring old write sites.

Local replicas are supported only if they coordinate through the same database
on a supported local filesystem. Different hosts with separate SQLite files
are not coordinated by this design; require a single publishing owner. Remote
active-active publishing would require a separately tested shared transactional
backend and is outside this release. Existing shared Postgres settings alone
do not make the X stores shared.

### Core records

- **Account binding:** tenant, stable verified X user ID, credential revision,
  capabilities, handle for display. Handle changes are not a new account;
  changing connected user ID cannot redirect old queued work silently.
- **Subject revision:** ID, schema/revision, actual target, side/view, scope,
  aliases/exclusions, exceptions, hard lines, evidence policy, tone range,
  permissions, examples and counterexamples, owner and change reason.
- **Context snapshot:** source and reply-to IDs, parent/quote/mention text and
  authors, conversation, fetch time, completeness, hashes and truncation flags.
- **Decision record:** candidate cards and match evidence, selected target,
  routing result, subject/policy revision, evidence manifest, model identity,
  verification results, draft hash, and final eligibility/reason code.
- **Publication operation:** tenant/account, client idempotency key, exact
  payload hash/revision, origin/proposal/summon/schedule refs, due/expiry times,
  approval actor/hash, current state/version, claim owner and attempt history.
- **Events/outbox:** durable transitions, external result, projection repair,
  notification status. Redact secrets; do not persist hidden model reasoning.

### State contracts

Decision states: `no_match`, `ambiguous`, `context_missing`, `evidence_missing`,
`verification_unavailable`, `rejected`, `needs_review`, `ready`.
Unavailable checks never become a pass or a semantic no-match.

Publication states: `awaiting_approval`, `scheduled`, `claimed`, `sending`,
`published`, `deferred`, `outcome_unknown`, `failed_permanent`, `cancelled`,
`expired`. Use conditional updates with expected revision/state.

- Persist `sending` before the network write. After process loss, interrupted
  sends become `outcome_unknown`, not pending. Claim expiry may reclaim work
  only with evidence it had not entered the send state.
- Confirmation from X requires a valid response and expected identifier;
  malformed success bodies are not normal success. Persist known results and
  replay local projections without sending again.
- A DB outage after remote acceptance still leaves a crash uncertainty window.
  Best-effort API audit can aid reconciliation but is not the correctness
  authority. Expose this limitation; do not claim the outbox eliminates it.
- Same idempotency key and payload returns the existing operation. Same key
  with a different payload returns conflict. Exact operation replay prevention
  is distinct from text-content duplicate policy.
- Cancel/reschedule succeeds only before send ownership is acquired. After
  that return a clear conflict; never report cancellation while a write can
  still proceed. Recheck live kill-switches immediately before dispatch.

## 4. Implementation waves

Each wave ends with a reviewable change, behavior tests, relevant static gates,
and an updated execution checklist. Do not enable subsequent auto-publishing
capabilities merely because their code exists.

### Wave 0 — baseline, contracts, and reproductions (P0)

1. Enumerate every X read/write caller across web, native skill, gateway,
   scheduler, poller, and retry/approve actions; include router middleware,
   tenant binding, RBAC, secrets and startup/shutdown wiring.
2. Add isolated reproductions for F01–F14 before correcting their behavior.
   Use fake X responses and temporary stores; no live posting for CI.
3. Record current status/response shapes and migration fixtures. Inspect live
   subject configuration read-only if available, outputting cards and metadata
   only; never credential values or unrelated private state.
4. Confirm current X capabilities from official docs and account test results.
   Replace obsolete fixed plan/tier assumptions with capability diagnostics.
5. Establish evaluation fixtures and baseline metrics (Wave 4). Preserve the
   baseline report so improvements can be compared.

**Exit:** call-site map, failing regressions, schema/state contract, and baseline
report. No production switches changed by the audit.

### Wave 1 — stop unsafe ambiguity and authority expansion (P0)

1. Distinguish unknown publication from rejected/unsent errors in all surfaces.
   Remove rebooking advice and approval/retry eligibility for unknown outcomes.
2. Enforce tenant/account ownership in services and store queries, including
   edit/cancel, conversations, approval, retry, audit and KB libraries. Verify
   authenticated web, gateway identity, and headless fail-closed behavior.
3. Persist open/close permission from trusted authors/actions only. A marker
   on arbitrary content is not authority. Closed state and trust exceptions
   follow an explicit policy shared by poller, manual path and preview.
4. Treat invalid subject config separately from intentional empty config;
   invalidate auto eligibility rather than falling into voice-only mode.
   Make voice/catch-all/summon-side auto permission explicit and draft-only
   during migration until reviewed.
5. Type X errors and rate-limit fields; parse reset epoch, Retry-After seconds
   and HTTP dates correctly. Bound attempts and validate wait times. Do not
   infer safe retry solely from a human-readable error string.
6. Offload sync IO; preserve a confirmed X result when local projections fail.
   Emit repair-needed information and durable operator alerts where possible.

**Exit:** no stranger self-opening, no cross-owner mutation, no unknown-outcome
resend, no invalid-config permission expansion, correct rate-limit timing.

### Wave 2 — durable publication execution and reservations (P0)

1. Introduce the publication store, schema migrations, operation/event API,
   idempotency keys, per-account reservation transaction and outbox.
2. Route immediate post/delete, schedule fire and summon approval/auto publish
   through `publication_service`; retain existing wrappers and approval gates.
3. Atomically check and reserve duplicates, account caps, reply daily/target/
   conversation limits, then create operation. No independent check-then-send
   or check-then-insert path may remain.
4. Use the actual future fire window for scheduling reservations. Define
   rolling 24h/30d Kazma caps separately from X endpoint limits. For each
   reservation candidate, validate affected future windows and reservations;
   rescheduling moves reservations atomically. Do not count every future post
   against today's cap. Immediate posts must respect commitments due in
   overlapping windows. Unknown sends retain conservative reservations.
5. Add expected-state send claims and version-bound approval claims. Race
   approve/deny/retry/cancel/reschedule and two-process scheduling in tests.
6. Reconcile using confirmed tweet IDs/API audit and authorized reads where
   available. Text/time similarity yields a candidate for operator review,
   not automatic proof of success or absence. Unresolved operations stay held.
7. Repair projections and notifications from the outbox. Count deleted
   publications toward consumed local quota; cancellation releases only
   unconsumed reservations. No retry counter reset by restarting the server.

**Exit:** one local send owner per operation under concurrency, no blind resend
after crash, transactional cap/dedupe enforcement, projection replay proven.

### Wave 3 — precise subject policy and routing (P0/P1)

1. Add the versioned schema described in Section 3. Retain legacy `id`,
   `side`, `match`, `view`, `register`, `hard_lines`, and `examples` imports.
   Separate internal ID from target text; reserve synthetic IDs to prevent
   user cards colliding with voice/post subjects.
2. Validate card conflicts, duplicate IDs/case variants, excessive field
   lengths, empty targets, catch-all precedence, unsafe contradictory rules,
   invalid aliases, and incompatible auto/evidence/check settings on save.
3. Normalize matching text safely: Unicode normalization, Arabic diacritics/
   tatweel handling, explicit alias variants and token boundaries. Keep the
   original for display and evidence. Avoid broad folding that merges distinct
   entities. Negation/quotation is contextual analysis, not string cleanup.
4. Gather all candidates. Resolve primary target, quote vs author stance,
   incidental mentions, scope/exclusions and conflicts. Deterministic clear
   cases need no model; ambiguous cases may use a closed-schema classifier
   with evidence spans. Unsupported or conflicting selections mean review.
5. One shared routing function for live summons and full-policy preview;
   card-only preview remains clearly labeled and cannot imply auto eligibility.
6. Remove unconditional escalation/denial prompts. Express the configured
   position within scope and exceptions. It is permissible to concede a
   supported fact while retaining the declared policy position.
7. Emoji may select permitted tone, not override scope/evidence/stance.
   Parse summon intent with negation/conflict handling; "don't support" must
   not become support by keyword order. Mixed signals mean review.

**Exit:** topic selection is explainable, card order does not silently resolve
conflicting sides, bilingual routing regressions pass, migration cannot widen
permission.

### Wave 4 — evidence and independent verification (P0/P1)

1. Require context completeness flags. Media-dependent claims, unavailable
   quotes, deleted source context, unresolved authorship or truncation cannot
   pass unattended simply using the remaining text.
2. Retrieve authorized knowledge with source/document/chunk ID, URL where
   available, published/retrieved times and passages sufficient for the claim.
   Preserve numeric qualifiers and attribution; arbitrary 280-character cuts
   are not adequate evidence. Record unavailable vs irrelevant vs no results.
3. Separate opinions/value judgments from material factual assertions.
   Apply per-subject evidence/freshness policy. KB alone may be stale for
   recent claims; authorized current retrieval is bounded by cost and time.
   Unsupported allegations/quotes/numbers require review or omission.
4. Generate a structured candidate with draft and claim/source links stored
   privately. Only the approved public text is posted. Do not invent citations;
   allow source links where configured and validated.
5. Verify context/target relevance, scoped stance, factual support, universal
   and custom hard lines, harassment/escalation and deterministic post policy.
   Catch-all with a side receives stance checks; voice receives relevance,
   factual and hard-line checks. Supply original context to every contextual
   verifier; fence posts, drafts and retrieved content consistently.
6. Typed verdicts include `pass`, `fail`, `unknown`, check identity and concise
   evidence. Never accept arbitrary first-word output as a complete validation
   record. Malformed output/outage means unavailable. A second model is useful
   only when measured; correlated errors must remain a known limitation.
7. Auto requires every mandatory check. Draft may retain a safe candidate
   with unavailable checks explicitly shown; rejected content is not
   approvable. Bounded regeneration can fix wording once; never loop until a
   failed verifier happens to pass.
8. Limit calls/tokens/latency per summon, daily model/read budgets, retrieval
   depth, and concurrent drafting. Coalesce duplicate work. Budget exhaustion
   holds work; it never weakens checks.

**Evaluation release gate:** initially at least 200 held-out bilingual cases,
balanced across declared positions and failure categories, with human labels
for applicability, target, factual support and constraints. Include Kuwaiti/
Gulf Arabic, mixed scripts, sarcasm, negation, quotes, multiple entities,
injection and source contradictions. Keep tuning cases separate.

- Zero critical hard-line violations or wrong-target auto-publishes in the
  release corpus; any discovered one blocks release and becomes a regression.
- At least 98% precision of auto eligibility and correct selected target on
  labeled eligible cases; report denominators and uncertainty. These are
  initial engineering acceptance targets, not guarantees in the wild.
- Every unsupported material factual claim and every deliberately injected
  required-check outage in the corpus is held/rejected.
- Report hold/false-rejection rate, supported-claim rate, model/call cost and
  latency; do not optimize pass rate at the cost of the first three gates.
- Model/prompt/retrieval changes rerun the affected held-out evaluation before
  gaining auto eligibility. Human review remains available.

### Wave 5 — approvals, audit, and revision integrity (P1)

1. Approval binds tenant/account, target, exact payload hash, subject/policy
   revision, context snapshot, check results, actor and expiry. Any content,
   target or account change invalidates it. Relevant policy/evidence changes
   require revalidation and show differences; do not silently rewrite content.
2. Extend existing RBAC resources for author/reviewer/publisher/admin actions;
   do not build a separate identity system. Single operators can hold all
   roles. Backend enforcement must match UI permissions.
3. Keep append-only application events and the API audit with documented
   access/retention/export/redaction. Remove from inbox means archive/tombstone;
   deleting on X is a separate operation and does not erase execution history.
4. Record why a subject matched, final verification results and source links;
   avoid raw secret-bearing errors and hidden reasoning. Bound input sizes and
   exports, and test authorization on every read.
5. Surface stale approval and projection/notification failures with concrete
   recovery actions. Success toasts cannot hide incomplete local bookkeeping.

**Exit:** double-clicks are idempotent, stale approvals cannot publish, roles
are enforced server-side, history survives UI removal and failed delivery.

### Wave 6 — Studio and Settings operator workflow (P1)

1. Studio views: Compose, Drafts/Review, Calendar/Queue, Conversations,
   Published, and Activity/Health. Published reads publication truth; Activity
   contains successes, failures, reads and deletes without posing as content.
2. Composer autosave and revisions, navigation protection, source draft
   linkage, account/reply target preview and exact final confirmation. Keep
   editing available while posting is disabled; disable send actions only.
3. Shared X weighted-length validation in server/client with one fixture
   corpus for URLs, emoji/graphemes, normalization, Arabic and mixed text.
   Server is authoritative; display counts and policy reasons immediately.
4. Loaders distinguish loading/empty/error/stale. Preserve last good data,
   display last refreshed time and retry. Abort or sequence preview requests
   so old responses cannot overwrite current input. Bind busy states before
   async confirmations; backend claims remain the concurrency protection.
5. Search, filters and cursor pagination for drafts, published content and
   conversations. Counts come from stores, not truncated client lists. Do not
   allow frequent mention polls just to refresh the local log.
6. Settings card editor adds target/scope/exceptions/evidence/permission,
   warnings, revision comparison, examples/counterexamples, import/export and
   staged activation. New cards do not default to against without a choice.
7. Try it shows candidates, matched evidence, resolved target, selected
   position, completeness, sources, check outcomes and publication eligibility.
   Test one card vs complete live policy are visibly separate modes. Unsaved
   policy preview cannot change active configuration.
8. EN/AR translations for every state/error/action; preserve text's own
   direction. Accessible labels, keyboard tabs/actions, focus management,
   announcements, mobile layout and readable confirmation dialogs.

**Exit:** outage cannot appear as an empty healthy queue, composing survives
reload, preview/live routing agrees, Arabic/mobile/keyboard E2E checks pass.

### Wave 7 — scheduling and thread lifecycle (P1/P2)

1. Calendar with explicit account timezone, absolute UTC instant and local
   display; daylight-saving ambiguity/nonexistent-time validation. Use one
   parser and validation path for create/edit/manual/gateway.
2. Late-post policy with operator-visible migration: hold after missed time by
   default, configurable grace/expiry or explicit publish-late permission.
   Never silently release old campaigns after an outage. Server-owned clock
   and best-effort timing remain clearly explained.
3. Rescheduling recalculates reservations and preserves approval semantics;
   concurrency conflicts are actionable. Error/deferred/unknown/expired work
   remains visible in the queue with the correct recovery action.
4. Thread composer stores ordered segments and dependencies, approves the
   whole exact revision, and records each segment's remote result. Partial
   publication stops on failed/unknown segments; resume sends only confirmed
   unsent segments after required approval. No automatic rollback/delete of
   already-published segments and no illusion of atomic remote threads.
5. Keep recurring identical-post scheduling refused. Recurring campaign work
   creates new drafts for review; it does not bypass duplicate policy.

**Exit:** timezone/restart/race tests pass; partial threads are accurately
represented and cannot restart from segment one by accident.

### Wave 8 — operational readiness and rollout (P1)

1. Health: poller/scheduler heartbeat and last successful cycle, oldest due
   operation, unknown outcomes, deferred sends, held approvals, outbox lag,
   DB refusal, source/check availability and account capability status.
2. Metrics: routing reasons, holds/rejections, wrong-target human corrections,
   unsupported-claim corrections, send lateness, projection lag, rate windows,
   model/read costs and notification delivery. Metrics must not expose text,
   secrets, or unbounded identifiers as labels.
3. Alerts only on actionable changes, completion/failure or operator action;
   deduplicate with the existing alert infrastructure. Include a short
   runbook for X auth denial, classifier outage, DB outage, rate limiting,
   unknown sends, stopped loops and rollback.
4. Back up/restore/migrate all new data through the registry; restores must
   enter a paused publishing mode until account binding and old send states
   are checked. Prove restoration cannot replay published/unknown operations.
5. Roll out draft-only, then shadow decision evaluation without remote writes,
   then explicitly enable a small subject allowlist with tight budgets.
   Audit false approvals before expanding. Kill-switches remain live.
6. Rollback uses compatible schema and disables writes first. An old build
   must not resume legacy pending rows already migrated into sending/unknown;
   maintain a migration fence/paused legacy scheduler. Test old/new version
   behavior instead of assuming additive columns make rollback safe.

**Exit:** disaster restore, crash recovery, safe rollback and incident runbooks
are exercised; operational dashboards explain every nonterminal operation.

### Wave 9 — capability-gated richer content (P2, after core gates)

Plan media/alt text, link preview, quote posts, richer campaigns, source-linked
replies and permitted analytics only after official endpoint/account capability
verification. Each feature uses the same approval, evidence and publication
contracts. Media-dependent subjects need supported image understanding or must
remain draft-only; uploads/processing become durable dependencies. Analytics
should distinguish engagement from factual accuracy. This wave is part of the
product roadmap, not a prerequisite for text Studio readiness.

## 5. Dependencies and reviewable delivery units

| Unit | Prerequisite | Main deliverable |
|---|---|---|
| W0 | None | Reproductions, caller map, contracts, evaluation baseline. |
| W1 | W0 | Authority/error/fallback containment and immediate corrections. |
| W2 | W0–W1 | Transactional operation service, claims, recovery and reservations. |
| W3 | W0–W1 | Subject schema/routing and compatibility migration. |
| W4 | W3 | Evidence/verification policy, bounded execution, evaluation gates. |
| W5 | W2–W4 | Revision-bound approvals, RBAC and retained history. |
| W6 | W3–W5 | Complete Studio/Settings workflow on authoritative APIs. |
| W7 | W2/W5, W6 UI foundation | Calendar, late policy and partial thread lifecycle. |
| W8 | Operational hooks begin W2; exit after W5–W7 | Restore/rollback/rollout qualification. |
| W9 | W8 text readiness | Individually reviewed richer-content increments. |

W2 and W3 are independent implementation streams after W1; this is a
dependency observation, not authorization to spawn agents. Split large waves
into focused PRs: schema/store → compatibility wrappers → callers → behavior
gates → UI. Estimate effort after W0 rather than promising dates before
migration and authorization details are known.

## 6. Compatibility and migration checklist

- [x] Inventory installed cards/schedules read-only and take a recoverable
  backup before activation. Preserve original text and identifiers.
- [x] Use `get_config_store()` and `batch_set`/transactions; do not rewrite
  tracked `kazma.yaml` from runtime Settings. Declare changed shipped defaults
  via `config_defaults.py`, refresh fixtures with the prescribed script, and
  record migration markers. No undeclared equality-based default updates.
- [x] Use `add_missing_columns`, WAL/busy timeout, explicit connection closing
  and transaction rollback. Propagate real migration failures.
- [x] Map legacy subject ID to initial target only as a flagged imported value
  for review. Preserve side/view/hard lines and mark policy version. Invalid
  entries become visible errors, not implicitly empty configuration.
- [x] Hold migrated auto replies until policy validation; distinguish an
  explicitly requested voice mode from lack of cards. Explain changed behavior
  in Settings and release notes, with staged activation.
- [x] Backfill known published/scheduled work idempotently. Unresolved account
  binding and ambiguous historical send failures remain review-needed. Never
  infer a publication or approval from a UI label.
- [x] Reconcile existing ledger history into reservation accounting. Avoid
  double-counting projections and preserve post-delete quota consumption.
- [x] Carry per-item proposal usage/discard state; mark consumption only on
  actual successful booking/publication. Repair failed projection updates
  without consuming sibling drafts or making used drafts publishable again.
- [x] Credential disconnect/rotation is atomic; account rebind pauses existing
  work and invalidates affected approvals instead of rerouting it.
- [x] Keep native tool readback and store registry declarations aligned; new
  tools obey HITL tier/canonical YAML parity and migration disposition gates.
- [x] Provide compatible status mappings for legacy clients without hiding
  unknown/review-needed outcomes; new APIs expose canonical states.

## 7. Verification matrix and release definition

Extend existing tests rather than replacing them: `test_x_studio.py`,
`test_x_scheduled.py`, `test_x_auto_reply.py`, `test_x_reply_settings_api.py`,
`test_x_publisher.py`, `test_x_audit.py`, `test_x_post_proposal_gate.py`,
`test_x_quota_counts_deleted.py`, `test_saved_drafts_readback.py`, and
`test_draft_discard.py`. Proposed suites cover publication races/crashes,
subject routing, contextual verification, migration and Studio E2E.

| Layer | Mandatory evidence |
|---|---|
| Routing | English/Arabic aliases, boundaries, negation, quotes, incidental entities, multiple/conflicting cards, catch-all with side, invalid config and hostile summons. |
| Evidence | Cross-tenant library denied, freshness/attribution, contradictory notes, missing source, injected snippets, unsupported numbers/quotes and retrieval outage. |
| Verification | Required checker outage/malformed verdict holds; contextual wrong target caught; custom/universal hard lines checked in both languages; no auto bypass. |
| Publication | Independent processes at a cap, duplicate booking, double approval, cancellation/reschedule races, kill-switch, malformed response, timeout, and crashes before/after send/result persistence. |
| Persistence | DB refusal, projection replay, idempotent migration, backup/restore, compatibility reads, old-build rollback fence, credential/account change. |
| Web/gateway | Auth/CSRF/ownership/role matrix at full app and service layers, stored draft wins, no parallel publishing path, retry/deny/approve behavior. |
| UI | API 500/timeouts, stale preview order, autosave reload, paginated content, review reasons, keyboard/mobile, EN/AR and partial thread recovery. |
| Operations | Heartbeat loss, overdue posts, rate reset timing, outbox lag, undelivered alert, paused restore and unknown-send runbook. |

Run focused suites per change plus the applicable existing industrial gates:
store registry, SQLite column migrations, event-loop blocking, shipped defaults,
HITL/tool wiring, API caller parity, i18n keys and pages surviving API failure.
Compile Python changes with `py_compile`; syntax-check JS with `node --check`.
Run the broader suite when integration/migration changes justify it. Live
publishing validation requires explicit authorization for the exact test
content; fake transport and dry-run checks do not publish.

Text Studio is ready when W0–W8 exit criteria are met, migrations and rollback
are proven, the held-out evaluation gate passes, and all publication paths
obey the same durable execution contract. Remaining unknown external outcomes
are honestly represented; operators can resolve them without unsafe retries.

## 8. Official references and implementation-time rechecks

- [X rate limits](https://docs.x.com/x-api/fundamentals/rate-limits):
  `x-rate-limit-reset` is a Unix reset timestamp, not a duration. Keep account
  capabilities and headers separate from Kazma's own conservative budgets.
- [X character counting](https://docs.x.com/fundamentals/counting-characters):
  implement weighted validation rather than Python/JavaScript string length.
- [X create posts](https://docs.x.com/x-api/posts/create-post): validate request
  fields, user authorization and current account restrictions before adding
  richer content. Do not promise provider-side idempotency or scheduling
  without verified endpoint support.

These references were checked on 2026-10-03. Recheck relevant endpoints when
implementing; do not freeze provider prices, plan names or quotas in the plan.

## 9. Execution checklist

- [x] W0 source baseline and isolated reproductions.
- [x] W1 ambiguity/authority containment.
- [x] W2 durable publication and transactional reservations.
- [x] W3 subject policy and conservative routing engineering.
- [x] W4 evidence/verification, collection tooling and isolated full-path evaluator.
- [x] W5 revision-bound approval, authorization and history.
- [x] W6 Studio/Settings workflows and automated bilingual keyboard/mobile checks.
- [x] W7 scheduling and approved ordered-thread recovery.
- [x] W8 health, alerts, registered stores and isolated restore/rollback rehearsals.
- [x] W9 disabled capability boundaries and subsequent richer-content roadmap.
- [ ] Deployment-specific account capability verification and manual assistive-technology acceptance.
- [ ] Genuine human-labeled held-out evaluation and automatic-reply canary qualification.
- [ ] Production topology restore/rollback drill and measured recovery objectives.
- [ ] W9 richer-content implementations after their account capabilities and text rollout gates pass.

The implementation record is [X Studio execution](X_STUDIO_EXECUTION.md).
Completed engineering does not certify model accuracy or grant account capabilities.

## 10. X-specific model selection (added 2026-10-03)

**User requirement:** X post/reply work can use a selected local or remote
model independently of Kazma's globally active model. Include this in W3/W4
backend work and W6 Settings/Studio work; it is required scope, not an optional
later feature. Changing an X selection must never change the global profile.

### Settings and role resolution

Add **X AI model** with `Use global model` (compatibility default) and
`Use a specific provider/model`. The latter selects an enabled configured
provider plus an explicit model ID. Support existing local OpenAI-compatible
providers, including Ollama and LM Studio, without duplicating credentials or
creating a second provider registry.

The simple selection applies to all X generative/checking roles. An expandable
advanced section allows overrides for **subject/context classification**,
**post/reply drafting**, and **verification**. Operators can run everything
locally, or knowingly select a different verifier. One X draft model serves
replies and future AI-assisted composer actions; manual publishing and
deterministic scheduled execution do not need a model call.

Suggested config shape under `connectors.x.ai`:

```json
{
  "selection": "specific",
  "provider": "configured-local-provider-name",
  "model": "exact-installed-model-id",
  "fallback": "none",
  "local_only": true,
  "roles": {
    "classification": null,
    "drafting": null,
    "verification": null
  }
}
```

`null` role bindings inherit the X selection. Role-specific bindings contain
an explicit provider/model pair. Do not infer a provider solely from model
name: two endpoints can expose the same ID. A turn snapshots its effective
bindings so concurrent Settings/global-model edits cannot switch provider
mid-decision. Record actual resolved provider/model and selection revision in
the decision, preview and audit; model IDs are not proof of immutable model
weights, so retain an artifact/version identity if the local server supplies it.

### Client construction and failure policy

Current drafting, classification and stance-check call
`get_model_registry().get_client()` and therefore use the active global
profile. The registry already has `get_client_by_provider(provider, model)`;
build a shared `x_api/model_selection.py` resolver around explicit pairs,
existing provider dispatch and the shared credential-resolution helpers.
Validate provider enabled state, endpoint, model availability and required
capabilities; do not assume the existing named-client method enforces all of
those itself. Resolve sync registry/store work off the loop, retain the tenant
context, and pass immutable client bindings through the X decision pipeline.

- Never call `set_active_model`/`set_active_provider` for X overrides and never
  temporarily swap global settings around an await.
- Default fallback for a specific selection is **none**. Local server outage,
  missing model or unavailable verifier means actionable hold/failure, not
  substitution with the global/cloud model.
- Optional fallback is an explicitly named provider/model binding with visible
  operator consent. `local_only` forbids nonlocal bindings and fallback.
  Validate configured local destinations with existing endpoint policy; a
  provider name containing "local" is not proof of locality.
- A loopback endpoint can proxy cloud inference (including cloud models
  exposed through a local Ollama server). Local-only also requires a verified
  locally installed model/server configuration, not just a localhost URL.
  Show the boundary as unverified when deployment provenance cannot be
  established; do not promise offline inference from endpoint spelling.
- Local-only must cover all model calls, including verifiers and retrieval
  embeddings/reranking. Use compatible local retrieval or lexical-only
  retrieval, or hold evidence-required work. Merely selecting a local drafter
  does not establish that KB processing stays local. X publishing itself still
  requires X's remote API.
- Keep ordinary calls on `provider.chat()` and preserve provider-specific
  dispatch, transient errors, strict local system-message hoisting and client
  lifecycle/shutdown management. Tool calling is not required for the existing
  drafting path. Models failing structured verdict validation are unavailable
  checkers, not implicit approvals.
- Expose bounded timeout, concurrency, output-token and supported generation
  settings. Do not promise auto eligibility because a model can generate text;
  run the bilingual evaluation and required verification gates for that binding.

### Operator workflow and acceptance checks

Settings shows configured provider/model options and offers **Test X model**:
a bounded local draft plus sample structured check, without posting, changing
the global model, or sending content to an unauthorized fallback. Try it and
review cards show which models actually ran and whether fallback occurred.
An advanced verifier choice that leaves the local-only boundary is rejected
or requires the operator to change the explicit boundary setting first.

Tests must prove:

- X-specific calls reach the pinned endpoint/model while ordinary Kazma chat
  keeps its global binding; concurrent decisions do not mutate one another.
- All drafting/classification/verification/preview callers share the resolver.
- Missing/disabled model, stopped local server and malformed verdict hold
  safely with zero unapproved cloud fallback calls.
- Role inheritance and explicit overrides resolve consistently; local-only
  also constrains retrieval model calls and rejects incompatible overrides.
- Tenant-scoped credentials resolve normally; tests and responses never
  expose them. Saving selection is atomic and settings reads do not stall
  the event loop.
- Global inheritance preserves legacy behavior, while a changed specific
  model invalidates relevant auto qualification and triggers revalidation.

Provider setup references:
[Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
and [LM Studio OpenAI compatibility](https://lmstudio.ai/docs/developer/openai-compat).
Recheck provider features at implementation time; use exact installed model
IDs rather than recommending a model without testing it on this workload.

- [x] X-specific selection and shared resolver (W3/W4).
- [x] Settings selection/test and actual-model display (W6).
- [x] Local-only/fallback, concurrency and qualification acceptance checks.
