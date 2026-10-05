# X Studio implementation record

Date: 2026-10-04 (Asia/Kuwait).
Plan: [Industrial improvement plan](X_STUDIO_INDUSTRIAL_IMPROVEMENT_PLAN.md).

This release implements the text Studio engineering scope: publication safety,
subject/evidence checks, independent X models, isolated evaluation, policy review,
ordered threads and operational health/recovery.
Automatic replies remain unqualified without a real human-labeled evaluation.
Production qualification and capability-gated richer content are distinguished
from completed engineering below; no account or human accuracy is certified.

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
  EN/AR mobile composer and ordered-thread reload/overflow checks pass.
  Roving keyboard tab focus, Home/End navigation, labelled subject controls,
  accessible disclosures and ordered-segment controls are covered by the
  bilingual browser/static checks. Manual assistive-technology qualification
  remains a deployment acceptance exercise.
- Policy export contains subject cards only. Import validates and stages an
  unsaved draft with automatic permission disabled. Before/after comparison
  includes actual changed values; saving a staged import explicitly reviews
  it and uses draft mode. Settings revision conflicts prevent overwrites.
- Ordered 2–25 segment threads persist immutable reviewed manifests and reserve
  every segment atomically. Whole-revision approval binds the account and
  credentials. Segments use the ordinary publication service and confirmed
  predecessor IDs; generic operation dispatch cannot bypass whole-thread review.
- Partial threads stop. Fresh approval resumes only known unsent segments,
  skips confirmed posts and blocks uncertain outcomes. Expired/crashed execution
  requires refreshed review. Cancellation retains published/unknown segments.
  Actors/revisions and segment results remain in the review/event history.

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
- The collection CLI exports real observed context with empty human labels.
  The shadow CLI copies settings/knowledge/history into isolated per-case
  child processes, runs the normal authority/check/publication-preflight path,
  and records would-send intent while X transport and alert delivery are blocked.
  Source stores stay unchanged. Calls may reach configured model endpoints.
  Actual token measurements and missing usage are distinguished from budget
  ceilings. No report was installed and no automatic release was qualified.
- Operations health exposes bounded tenant counters, process loop heartbeats,
  unknown outcomes, repair/notification lag and measured model usage. Three
  consecutive loop failures and subsequent recovery generate transition notices
  through the acknowledged alert outbox; superseded notices are suppressed.
- A real isolated bundle restore rehearsal preserves published receipts,
  converts interrupted sends to unknown, holds queued work and exercises the
  older pending-only scheduler's managed-row fence. Thread approvals are
  invalidated on restore. Production RTO/RPO is not inferred from small fixtures.

The operator has no existing dataset. The
[collection guide](../docs/guide/x-evaluation-dataset.md) explains real case
collection, independent human labels and report installation. Synthetic
regression fixtures never qualify automatic publishing.

## Validation

- The foundation X/settings/concurrency/documentation run passed **542 tests**;
  receipt, approval, qualification and rollback checks also passed.
- Completion-wave integration: **106 passed**, covering isolated evaluation,
  policy import, ordered threads, health, recovery, model selection, budgets
  and notification acknowledgement.
- Diagnostics, environment reference, template language/loading and recovery
  fixes: **159 passed**. API/caller/docs/CI-wiring checks: **66 passed, 1 skipped**.
- Final structural, thread, health, language and template checks: **43 passed**.
  After making collection's subprocess entry point explicit, module reachability,
  debt and the real CLI isolation/Arabic checks passed together: **19 passed**.
- The latest full local candidate run passed **12,971 tests**, skipped 45 and
  found one module-reachability failure in the inline collector subprocess.
  The explicit worker above fixes that finding without a baseline exception.
- Release acceptance requires the full repository and PostgreSQL suites,
  blocking bilingual browser check, wheel/import checks and all other checks
  for the release commit in [CI](https://github.com/Mubder/kazma/actions/workflows/ci.yml).
  The newly added PostgreSQL race test runs against CI's isolated real service.
- Release candidate `3f25172f` passed the full CI unit run (**12,904 passed,
  112 skipped**), real PostgreSQL suite (**300 passed, 4 skipped**) and all
  browser, wheel, security, import and syntax jobs. Its README metrics gate
  found stale headline counts; those were regenerated before final deployment
  acceptance. The final correction must also pass CI.
- Bilingual mobile browser: **1 passed**, including composer/thread reload,
  keyboard focus and health display. Both X JavaScript behavior checks pass.
  The completion wave's **33 changed Python files** compile and pass Ruff
  (existing N818 exception names excluded); all three changed page scripts
  pass syntax checks. Git whitespace checks pass.
- The foundation wheel contains the weighted parser resources/license and
  passes emoji/CJK smoke checks from the wheel.
- A read-only pre-deployment snapshot includes the live PostgreSQL settings,
  X SQLite stores and vault/configuration. Snapshot hashes verify. The first
  collection contains **14 actual stored cases** with empty human labels;
  it is kept locally outside Git and does not qualify automatic publishing.

Tests use fake X transports and isolated stores. Live acceptance uses the
guard's idle reload and browser inspection; no test post/delete is required.
Live inspection found legacy keyword inventories of 101 and 62 entries held
by the original 40-item bound. The bound is now 256 for literal keywords and
aliases; all four actual policies retain their full inventories and draft-only
legacy permissions. Compatibility/status tests passed with their owning suites
(85 tests), and the bilingual browser check passed with planner interpolation
coverage. The guard served the merged release and the live UI retained all
four cards, displayed the model overrides and enforced missing-evaluation holds.
Account-read success does not attest write access. Browser dependency
deprecations and Windows pipe cleanup warnings did not fail the browser check.
Production recovery objectives and human accuracy qualification remain external
acceptance work, as recorded below.

## Remaining roadmap and rollout gates

### Deferred production qualification (2026-10-05)

The operator asked to save these next steps for later: label genuine
English/Arabic cases and build at least 200 held-out evaluation cases; run
shadow evaluation for the selected X models and subject policies; rehearse
backup, restore, rollback and interrupted-publication recovery on the actual
deployment; verify connected-account capabilities and perform manual
accessibility/operator acceptance. Richer media, alt text, campaigns and
analytics follow text readiness and verified account capabilities. Keep X in
draft/review during qualification and do not publish live X content as testing.

This records the earlier deferred work list, not completed acceptance or
automatic-reply qualification. The 2026-10-05 request to proceed through S1–S10
reactivates it; engineering tests cannot replace its human/deployment evidence.

The follow-up dataset workspace supplies the missing attended editor: collection
creation/import, local observation collection, case labels, candidate inspection,
human attestation and export. Tenant isolation and revision checks protect writes;
review resets and durable conversation provenance protect release preparation.
English/Arabic mobile coverage now includes create, save, reload and edit.
Settings presents subject scope/exception controls visibly and explains that
support/opposition must respect facts. Actual scope, exceptions and negative
examples now reach the drafter as well as verification. These engineering checks
do not replace the real human-labeled release evaluation.

Follow-up validation: **662 focused X/JavaScript/storage tests passed**; the
dataset-aware bilingual mobile browser passed create/save/reload/edit; translation,
template, API caller and documentation gates passed. The full suite and release
CI remain required for the commit that carries this follow-up.
Live acceptance collected 14 real records into an unassigned starter collection
with no human labels and 11 conversation groups. It exposed a saved-tone display
mismatch in dynamic options; explicit option selection fixes it, with English and
Arabic browser assertions for support/opposition cards and visible scope fields.
The control styling follow-up adds Kazma blue checkboxes, themed fields and file
pickers across both X surfaces. Bilingual mobile acceptance checks both light and
dark modes, keyboard checkbox selection/focus and preservation of toggle switches.
The Studio and Conversations sub-tabs also apply these shared controls to draft
count, reply/schedule fields and all searches, with a labeled responsive status
filter. Both themes and languages pass the mobile interaction check; 53 focused
template/translation/Studio checks pass. Final release CI remains required.

| Work | Status |
|---|---|
| W0 baseline | Source/callers and isolated reproductions delivered; connected-account capabilities and real evaluation baseline need operator data. |
| W1 containment | Engineering delivered and regression-tested. |
| W2 publication | Local execution/reservation/projection/notification delivered; production crash/filesystem topology exercise remains. |
| W3 routing | Schema/conservative router delivered; real target/scope accuracy remains unqualified. |
| W4 evaluation | Checks/budgets/report gate, real-case collector and isolated full-path shadow runner delivered; genuine human evaluation remains required. |
| W5 approvals | Revision/role/account/source controls delivered; production operator workflow exercise remains. |
| W6 Studio | Text workflows, policy import/export, staged review/value comparison and EN/AR keyboard/mobile acceptance delivered; manual assistive-technology acceptance remains deployment-specific. |
| W7 threads | Scheduling/late recovery and approved ordered multi-segment threads with partial recovery delivered. |
| W8 rollout | Health, transition alerts, restore/rollback fences and isolated bundle recovery rehearsal delivered. Live topology recovery objectives and real shadow canary require deployment data. |
| W9 richer content | Disabled capability boundaries and subsequent roadmap documented. Media/alt text, richer campaigns and analytics require official account capability verification after text readiness; they are not text Studio prerequisites. |

Keep production in draft/review during data collection. Investigate unknown
sends against exact operation evidence; a timeout or similar text is not proof
of absence. Automatic rollout needs a small qualified subject allowlist before
expansion.


## S1–S10 implementation and acceptance (2026-10-05)

The latest operator request reactivates all ten sprints. This release delivers
the following engineering increments without declaring external acceptance:

| Sprint | Delivered or remaining |
|---|---|
| S1 settings | Section navigation, target-first cards, stable generated IDs, basic tone/language overrides, advanced policy preservation, inline failures and saved-state indicators. Initial matching uses the explicitly entered target; aliases remain operator-confirmed. |
| S2 stance | Shared versioned Support/Against contract in replies/posts/checkers; explicit opposite-side additions rejected. Existing ambiguity, quotations, exceptions and evidence holds remain active. Actual accuracy needs genuine evaluation. |
| S3 language | Eight tones, language/dialect/slang/length defaults and overrides; Allow uncensored language off by default, explicit profanity levels, independent safety/factual/publication gates. Effective style enters audit and qualification identity. |
| S4 preview | Saved/proposed comparison, illustrative opinion/quotation inputs, actual effective-policy/model/check display and stale-result rejection. No preview writes to X or installs qualification. |
| S5 models | Bounded unsaved-binding compatibility test through drafting and structured verification; existing role snapshots/local-only/no-implicit-fallback retained. Passing a sample is not production qualification. |
| S6 review | Effective position/tone on generated drafts and conversations, tenant-scoped filters before keyset pagination, preserved composer/review/thread recovery. Edits during a settings save are retained. |
| S7 release | EN/AR UI and guides, matching website translations, compile/syntax/lint and browser checks; final commit, CI and guard deployment evidence recorded after release. No shipped YAML default was changed; additive style fields preserve old cards and old-client saves preserve owner style choices. |
| S8 evaluation | Collection/editor/shadow/report tooling remains available. Live collection: 14 genuine observations, zero human reviews. No 200-case held-out release exists; accuracy and automatic canary remain unqualified. |
| S9 acceptance | Read-only live health showed both loops running, zero pending repairs/notices/unknown outcomes. Isolated recovery regressions pass. This does not measure production recovery objectives, prove all account capabilities or replace manual assistive-technology acceptance. |
| S10 richer content | Media/alt-text uploads, richer campaigns and external analytics are not implemented. Their prerequisite text qualification and verified account capabilities remain unmet; the existing disabled boundaries are retained. |

Validation: the full runner completed with 13,054 passed, 46 skipped and one
structural-helper exposure failure; making the module-only helper private fixed
that gate, with 91 release checks and 42 structural/style checks passing.
The final X/UI/docs run passed 665 checks. The expanded EN/AR mobile browser
flow passed creation, save/reload, both themes and preserved advanced policy.
JavaScript behavior tests cover stable identity, explicit model binding and
delayed-save/test races. Browser dependency/Windows pipe-cleanup warnings did
not fail the browser run. These are engineering results, not human accuracy
or production capability attestation.

Live model acceptance exposed verifier truncation and a hidden 15-second timeout.
The follow-up delegates timing to the configured X limits, requests JSON output,
adds safe diagnostic reasons and requires four passing content checks before
reporting sample compatibility. Opinion-only drafting now receives its evidence
restriction. Unknown verification still holds publication. The first release CI
also identified missing initial-cloak attributes; both X templates now preserve
the hydration gate. Final follow-up CI remains required.

## Authorized live acceptance follow-up (2026-10-05)

The operator authorized neutral public test posts with a visible automated-test
disclaimer and cleanup after testing. A WSL shutdown interrupted the live
installation before testing. Ubuntu was started; restarting the existing database
container restored its host port, and the guard recovered Kazma. The existing
tunnel returned HTTP 200, and authenticated X Studio loaded normally. No server
process was started or killed by hand.

Test XS-20261005-1253 published successfully through the normal Studio approval
and durable publication path, with receipt 2107091849777201632. Its exact text
and Published state survived a browser reload. Permanent cleanup confirmation
is pending; do not treat this post as human evaluation or automatic qualification.

The latest live PostgreSQL archive (pg_shared_1791195707.dump) restored with
full data into a prefixed scratch database on the actual database server in
21 seconds: 20 tables, 47 indexes, one extension. The scratch database was
removed afterwards. Production rows were not restored or replaced. This proves
that archive and PostgreSQL path, not universal-bundle recovery or a production
RTO/RPO guarantee. The live Backup page also showed two completed offsite
backups with PostgreSQL included. No new offsite upload was triggered.

Verifier prompts now supply explicit per-role check names rather than a
placeholder. Controlled schema errors expose the exact validation failure to
the operator without returning provider output or source content. All rejected
responses still hold publication. Genuine human reviews and S10 capability
acceptance remain open.

Real trusted summon 2107095391284220057 reached the test post, selected Support
and the configured heart-emoji supportive tone, and produced an English draft
held for approval. The operator phrase requesting professional wording did not
replace the emoji mapping. No automatic reply was sent. Diagnostics showed
claim rows in non-evidence checks and invalid decisions in the remaining checks;
role-specific prompts now request claims only for factual verification, and
validation failures identify the offending field category without exposing
provider content. The human-created summon remains unreviewed evaluation data.

The retry passed stance but revealed invented citation IDs and lost source
verification flags. Prompts now enumerate the only permitted independent IDs,
requiring empty citation arrays when none exist. Retry reuses an exact matching
stored context snapshot (including missing-media/quote flags), with history
recovery for older text-only retries; text alone never establishes verification.
Regression coverage rejects mismatched snapshots and retains incomplete-context
holds. The starter collection now contains 15 observations and zero human
reviews. The live test reply remains held while these changes are validated.
