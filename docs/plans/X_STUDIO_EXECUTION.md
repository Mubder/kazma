# X Studio implementation record

Date: 2026-10-04 (Asia/Kuwait).
Plan: [Industrial improvement plan](X_STUDIO_INDUSTRIAL_IMPROVEMENT_PLAN.md).

This release implements the publication safety foundation, subject/evidence
pipeline, independent X model selection and principal text Studio workflows.
Automatic replies remain unqualified without a real human-labeled evaluation.
The complete roadmap is not declared finished; pending engineering and
production qualification are listed below.

## Delivered behavior

### Publication and recovery

- Immediate posts, deletes, schedules and replies use one durable publication
  service. Operations bind tenant, verified numeric account, credential
  revision, exact payload and idempotency key.
- SQLite WAL transactions own send claims, duplicate checks, rolling future
  quota reservations and reply limits. Independent instances cannot claim the
  same operation or overbook the tested caps.
- Sending is persisted before network dispatch. Typed rejected/unsent/unknown
  outcomes replace inference from prose. Unknown sends retain reservations
  and cannot be approved, retried or rebooked.
- Valid remote identifiers and explicit deletion confirmation are required.
  Known results survive legacy projection failures. Versioned outbox replay
  repairs projections without sending again; only an exact correlated
  successful receipt can repair interrupted local confirmation.
- Archive retains idempotency and history. Migrated pending work is held when
  account approval cannot be reconstructed; interrupted sends remain unknown.
  Managed rows are fenced from older pending-row scheduler loops.
- Durable notification queues retry unsuccessful deliveries through the
  existing alert infrastructure and require delivery acknowledgement.
  Superseded draft notices are suppressed. Delivery is at least once: a crash
  after alert acceptance can repeat an alert.
- Rate reset epochs, Retry-After seconds and HTTP dates are handled separately.
  Schedules missed beyond five minutes are held for explicit rescheduling.
  Ambiguous/nonexistent daylight-saving times require an explicit offset.
  Recurring identical posts remain refused.

If both local result persistence and the best-effort API receipt fail after X
accepts a write, its outcome remains unknown. Local idempotency does not promise
exactly-once remote delivery. Publishing replicas must coordinate through the
same supported local database; separate-host active-active is outside this
implementation.

### Subjects, context and evidence

- Versioned subject cards carry target, aliases/exclusions, scope, exceptions,
  tone, evidence age, examples/counterexamples, ownership and explicit draft/
  auto permission. Migration cannot grant automatic permission.
- Routing gathers candidates with conservative Unicode/token matching,
  including Arabic normalization. Conflicting cards and mixed/negated summon
  instructions remain held. Structured classifiers must name declared cards
  and valid evidence spans; malformed output is unavailable.
- Stranger open markers cannot authorize replies. Opening/closing requires
  trusted authorship or an authenticated operator action, persisted per tenant.
- Context records authorship, quotes, media dependence, truncation and missing
  content. Missing contextual information prevents unattended publication.
- Retrieval hydrates current authorized chunks after ranking and records
  source/document/version/timestamps/full-content hashes. There is no unscoped
  library fallback. Facts constrain the declared stance.
- Five mandatory typed checks cover context, stance, evidence, universal
  safety and custom hard lines. Outage/malformed verdict holds the work;
  the legacy stance-check toggle cannot bypass these checks.
- Approval and dispatch recheck cited facts for freshness, authorization,
  active version and unchanged content. Changed/retired sources require a new
  draft and review.
- Calls, output tokens, latency, daily usage and concurrent drafting are
  bounded. Budget exhaustion cannot weaken checks.

These controls do not prove that model interpretation is correct. Real
bilingual evaluation remains a release gate.

### Independent X models

- Settings supports global inheritance or an exact enabled provider/model
  without changing Kazma's global profile. Advanced overrides cover
  classification, drafting, evidence and verification roles.
- Explicit selection has no implicit fallback. Unavailable endpoints/models
  hold decisions. Existing native adapters and credential resolution remain;
  scoped clients are cleaned up after use.
- Local-only validates loopback destinations and uses lexical retrieval
  without query embeddings. A loopback endpoint cannot attest that its server
  is not proxying cloud inference; deployment provenance is still required.
- Preview/saved decisions expose actual model identity. Manual publishing
  needs no generation call. AI-assisted composer alternatives use the X model
  and are saved for review without publishing.

### Approval and Studio

- Approval binds the complete stored candidate/decision, tenant, revision,
  account, credentials, pipeline fingerprint and expiry. Stale tokens cannot
  publish. Approve/deny/archive claims are conditional; retries retain prior
  candidates, attempts and revisions.
- Service ownership/backend RBAC enforce reads and mutations. Gateway review
  uses authenticated sender identity and revision tokens; native chat tools
  preserve proposal and HITL gates.
- X reply settings save atomically against an expected revision. SQLite,
  Postgres and volatile ConfigStore share the conditional batch contract.
  Independent SQLite writers are tested; no live Postgres concurrency exercise
  was performed.
- Composer server autosave/revisions survive reload and protect navigation.
  Payloads freeze before asynchronous confirmation; successful sends do not
  erase edits made while a request was running.
- Server-authoritative weighted counting uses a vendored, licensed
  twitter-text parser for normalization, URLs and recognized emoji. Newer
  emoji absent from its data can be conservatively overcounted.
- Drafts, conversations and publication history have tenant-scoped search,
  filters, store totals and keyset paging. Full candidate text, verification
  reasons and retained conversation history are visible.
- Loaders preserve the last good data on failure and reject stale responses.
  EN/AR mobile composer reload/overflow checks pass. Complete keyboard/focus
  accessibility qualification remains pending.

### Restore and automatic qualification

- New stores participate in registry/migration bundles. Restore pauses
  publishing globally, holds queued work and converts interrupted sends to
  unknown. Settings → X → Test must freshly verify the restored account before
  resume. Resume cannot silently release held work.
- Automatic eligibility needs a human-labeled report matching the exact
  pipeline, policy, models, evidence configuration and account. Reports expire
  after 14 days; minimums include 200 held-out bilingual cases, category
  coverage, 98% eligibility precision and zero critical/wrong-target/
  unsupported/unchecked automatic decisions.
- Changing only enabled/mode preserves an otherwise identical evaluated
  pipeline. Relevant policy/model/code changes invalidate it.
- The CLI and protected Studio upload validate recorded reports. They do not
  manufacture labels or run a full network shadow evaluation. No report was
  installed and no automatic release was qualified.

The operator has no existing dataset. The
[collection guide](../docs/guide/x-evaluation-dataset.md) explains real case
collection, independent human labels and report installation. Synthetic
regression fixtures never qualify automatic publishing.

## Validation

- The initial integrated repository run exposed 14 failures; applicable
  compatibility fixtures and repository gates were corrected.
- Focused suites exercise independent send/reservation claims, unknown
  outcomes, approvals/source changes, notification replay, settings races,
  paging, restore guards and weighted counting.
- Latest source/approval/qualification verification: **107 passed**.
- Final X-focused, settings concurrency and documentation/debt run:
  **542 passed**. Restore rollback/import atomicity/debt recheck: **23 passed**.
- Latest combined API-caller, Settings restore, i18n, Alpine and static gates:
  **147 passed**. Debt gates also pass with lowered baselines.
- Full-app browser navigation: **1 passed**. Bilingual mobile composer
  autosave/reload: **1 passed**. JavaScript behavior checks passed.
- Full repository run: **12,932 passed, 44 skipped, 3 failed** in 1,121 seconds.
  The failures were the newly added guide's sidebar/example-file references
  and duplicate Settings write-declaration keys. All were corrected; the exact
  failed checks and affected static/API caller gates then passed together:
  **140 passed**. The complete suite was not rerun after these metadata fixes.
- All **114 changed Python files** compile. Ruff passes on changed first-party
  Python files (existing N818 exception names excluded); vendored upstream
  code is compile-checked. All six changed JavaScript files pass syntax checks;
  both X JavaScript behavior checks pass. Git whitespace checks pass.
- A built wheel contains the parser resources/license and passes emoji/CJK
  weighted-text smoke checks from the wheel.

Tests use fake X transports and isolated stores. No live post/delete, live
setting mutation, production restart or deployment was performed. Browser
tests emitted dependency deprecation warnings and a Windows async-pipe cleanup
warning without test failures.

## Remaining roadmap and rollout gates

| Work | Status |
|---|---|
| W0 baseline | Source/callers and isolated reproductions delivered; connected-account capabilities and real evaluation baseline need operator data. |
| W1 containment | Engineering delivered and regression-tested. |
| W2 publication | Local execution/reservation/projection/notification delivered; production crash/filesystem topology exercise remains. |
| W3 routing | Schema/conservative router delivered; real target/scope accuracy remains unqualified. |
| W4 evaluation | Checks/budgets/report gate delivered; collection tooling and a full live-equivalent shadow runner remain engineering work, then human evaluation. |
| W5 approvals | Revision/role/account/source controls delivered; production operator workflow exercise remains. |
| W6 Studio | Core text workflows delivered; policy import/export, revision comparison/staged activation and full keyboard/focus QA remain. |
| W7 threads | Safe scheduling/late recovery delivered; approved ordered multi-segment threads and partial-thread recovery are not implemented. |
| W8 rollout | Restore pause/diagnostics/alerts/runbook delivered; live restore/rollback drill, measured recovery objectives and draft/shadow canary remain unqualified. |
| W9 richer content | Media/alt text, richer campaigns and analytics remain capability-gated P2 work after core gates. |

Keep production in draft/review during data collection. Investigate unknown
sends against exact operation evidence; a timeout or similar text is not proof
of absence. Automatic rollout needs a small qualified subject allowlist before
expansion.
