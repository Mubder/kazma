# Web acquisition implementation progress

Date: 2026-10-10; final implementation and local qualification follow-up: 2026-10-12. Status: hardening delivered for the supported routes; release limits remain explicit below.

The binding objective is an industrial, fail-safe scraper/crawler that runs
locally wherever possible and visibly reports failures and third-party usage
and credits. This document records the implemented controls, measured qualification and
remaining release limits. The [baseline audit and full plan](./AUDIT_WEB_ACQUISITION_2026-10-10.md)
preserve the original findings. [Supported-route release acceptance](#supported-route-release-acceptance-2026-10-12) is the current qualification; earlier progress entries are historical.

## Recovery and operator controls added on 2026-10-12

Background session rows record their process owner and durable pipeline ID.
A dead owner becomes `interrupted`; recovery requires an explicit Resume action
in the Research page or `start_deep_research(resume_session_id="…")`.
`research_session_status` and `cancel_research_session` use the same store in
chat transports. Completed terminal output is restored from verified artifacts;
uncertain planning, search, synthesis or publication is refused without replay.
Partial output remains partial. History admits at most 256 rows/128MiB; archive
does not free that allowance. Delete settled rows explicitly when appropriate.

Origin leases, spacing and cooldowns now use conditional ConfigStore writes,
including synchronous fixed HTTP search and native browser HTTP. Robots rules
and unavailable checks persist in bounded tables. Interrupted policy lookups
remain unavailable until their retry deadline. Opaque DDGS origin fanout is
skipped visibly; search continues through fixed, bounded HTTP backends.

Settings, CLI (`kazma acquisition credits`) and TUI offer an explicit Firecrawl
credit refresh. Status reads remain cached/local. Jina has a finite
`KAZMA_JINA_TOKEN_BUDGET` (500–50000; default 50000). No response token count is
presented as a charge. Setting `KAZMA_ACQUISITION_MAX_COST_USD` refuses remote
requests until an adapter can prove a monetary upper bound; it never guesses.
Research freezes explicit-key/local model clients and fallback clients. Ambient
AWS credential chains and Google ADC are refused for durable research because
their rotating identities are not frozen by this implementation.

Native interactive tools now use the contained owner and HTTPX/CDP broker,
with supported UTF-8 form bodies capped at 1MiB. Opaque uploads are refused.
Workspace/tenant changes refuse reuse of a foreign browser; close it first.
Crawler rendering stays read-only. Strict browser network isolation is required
by default (`KAZMA_BROWSER_NETWORK_MODE=required`). Linux launches require the
existing cgroup controls plus a verified isolated network namespace. Windows
has no qualified OS network sandbox here and refuses strict browser launch;
plain local HTTP extraction continues. Explicit `compatibility` mode retains
CDP/deny-proxy defenses and reports that OS network isolation is absent.

Retained pipeline reports are inventoried before admission in a bounded child,
including legacy output in the encountered workspace. A separate 2GiB registry
reserves 512MiB per active pipeline and survives journal eviction. Existing files
are preserved; removing settled reports releases their allowance on the next
inventory. This is a report allowance, not a whole-install disk quota.

The owner authorized the full test on 2026-10-12 after implementation. Use one
serial worker, two math-library threads, lowered priority and owned process-tree
cleanup. Earlier crash/timeout receipts remain failures, not passing certificates.
Strict Linux namespace/browser/resource/cleanup fixtures passed on this host;
four disposable PostgreSQL checks passed, including process death with retained
cooldown and unavailable robots policy. These are bounded fixtures, not a soak.
Windows strict sandbox implementation, provider smoke calls, broad vector/WAL
disk quotas and sustained load remain release limitations until measured. Unsupported
routes must stay visibly refused. Do not claim universal industrial readiness.

## Deep-research durable stages (2026-10-11)

`web_acquire.research_pipeline_job` extends the common ConfigStore CAS journal
with kind `research_pipeline`, sharing its four-active/24-retained/8MiB bounds.
The journal owns directory creation, planning, search, source acquisition, digest,
gap checks, synthesis, immutable publications, export and paper registration
before dispatch. It reserves at most 512MiB of retained artifacts per job before
stages start (128 stages, 512KiB cached values, 24MiB per published artifact).
Completed stage values live in private files with SHA256/size receipts; settings
hold no source bodies. These bounds do not garbage-collect old report folders.

Resume uses the returned durable job ID and identical topic/options/workspace.
Verified completed results are reused. Process death or cancellation makes an
unfinished stage uncertain: source uncertainty preserves any existing file and
receipt, skips that source and visibly produces a partial report; uncertainty in
planning/search/synthesis/publication/registration refuses automatic replay.
A missing or edited completed artifact refuses reuse. Source acquisition receipts
commit before source file publication. Concurrent searches/acquisitions join every
owned child before terminal settlement. Cached status exposes stage uncertainty,
blocking stages and earlier acquisition receipts without remote probes.

Configuration digests cover selected model, account settings/environment and
acquisition inputs, retaining hashes only. Every stage rechecks the digest.
The 2026-10-12 route snapshot, explicit session recovery and retained report
allowance supersede these earlier implementation gaps. Ambient credential
routes remain visibly refused; no automatic destructive cleanup is performed. Publication
uses the existing exclusive writer; registration now propagates storage failure
and updates the shared paper index atomically. Its workspace index is a derived
snapshot and may lag concurrent registrations. Optional DOCX generation still
requires its separate native output/containment qualification.

`evidence/deep_pipeline_frozen_gates_2026_10_11.log` records **262 passed,
1 skipped** in 115.16 seconds, plus an existing Starlette deprecation warning.
This selected run covers fresh-process SQLite crashes, concurrent resume,
changed/missing artifacts, invalid/mismatched scope, receipt/publication failures,
held-worker cancellation, prior research/session/KB behavior, accounting/credits,
settings restore and debt/orphan gates. The original unowned planner boundary is
a negative control: it repeats planning after a crash. Cached status distinguishes
a successful fetch from a failed source publication. Exception debt fell from
3506 to 3500. `deep_pipeline_imports_2026_10_11.log` records **4/4** separate
bounded fresh-process imports. `deep_pipeline_metrics_docs_gates_2026_10_11.log`
records **61 passed** in 23.99 seconds across metrics/receipt and documentation
reference/anchor/sidebar/catalog gates. Bounded collection after source/test
freeze records **14,477 collected cases** in 8.82 seconds
(`deep_pipeline_collection_2026_10_11.log`); this is not a passing-test result.
The shipped metrics receipt matches the qualified collection inputs.

Earlier failing selections remain saved, including the compatibility fixtures,
required debt reductions/internal naming fixes and a 60-second static-scan timeout
in `deep_pipeline_qualified_gates_2026_10_11.log`. The final selected run allows
that scan 120 seconds in the same single low-priority worker; this is not a
full-suite execution or a general removal of timeouts. Full test execution stays
deferred at the owner's request. No live installation restart, paid request,
automatic session recovery or industrial release is claimed.

## Cancellation and crash-recovery follow-up (2026-10-11)

Background cancellation persists `cancelling` before requesting loop-owned task
cancellation. Joined pipeline workers finish before terminal cancellation is
published. Active deletion requests cancellation and returns typed HTTP 409,
retaining the session row, subscriber and capacity lease. A SQLite writer
transaction latches cancellation/terminal status against stale progress; archiving
remains available. A task cancelled before its first coroutine step transfers
ownership to its terminal writer, including the admission lease. Cleanup writers
are not cancelled by a subsequent Delete request. English/Arabic UI keeps the
stream open while cancelling and reconnects to a cancelling session. Monotonic
update timestamps let the UI discard progress delivered after a newer commit.
The original premature status/deletion and early stream-close boundaries remain
negative controls in the new real SQLite/held-worker and EN/AR JavaScript gates.

The corrected full-suite run was interrupted by the PC reboot and produced only
its startup line: it has no measured result. Windows event 1001 reported bugcheck
`0x000000d1`; the cause has not been established. The interruption receipt is
`evidence/acquisition_full_repository_interruption_2026_10_11.json`. All 23 files
from the previous operations promotion survived the reboot. The full suite stays
deferred until the owner confirms readiness after closing resource-heavy apps.
No production restart, paid request or release qualification is claimed.

Focused validation: `research_cancel_truth_final_gates_2026_10_11.log` records
**86 passed, 1 skipped, 57 deselected** in 59.56 seconds, with one existing
Starlette deprecation warning. This is a selected run, not the full suite.
It includes the real SQLite/held-worker and pre-start ownership checks, existing
session/runtime/thread/report-path behavior, code debt/orphan checks and the
three relevant JavaScript suites. The new UI suite runs six EN/AR cases,
including the original early-close negative controls and reordered progress.
`research_cancel_truth_imports_2026_10_11.log` records **4/4** separate bounded
fresh-process imports. Initial failed selections remain in
`research_cancel_truth_behavior_2026_10_11.log` (cleanup writer cancellation)
and `research_cancel_truth_gates_2026_10_11.log` (the enforced exception debt
reduction from 3507 to 3506); both defects/required updates are addressed.
`research_cancel_truth_metrics_gates_2026_10_11.log` records **31 passed** in
16.13 seconds. Single-process bounded collection records **14,445 collected
cases** in 8.74 seconds; this count is not a passing-test result. Source/test
files were frozen before regenerating the collection receipt and repository
metrics. No full execution was started after the owner's deferral.

## Implemented foundation

| Boundary | Result | Behavioral evidence |
|---|---|---|
| Retained KB data | Discovery absence no longer prunes previously indexed pages, including capped/sparse/failed refreshes. Explicit deletion remains separate. | Real temporary SQLite KB/index with retained pages across refreshes |
| Terminal failures | All-page ingest failure ends in error and retains every page reason; cancellation joins discovery work. | Failed-page and cancelled-generator tests |
| HTTP network | Shared raw GET acquisition validates/pins each actual redirect hop, checks peer before body reads, retains real httpcore peer evidence and refuses unverifiable peers/missing pins/embedded credentials. | Real HTTPX streams, httpcore adapter and transport-dispatch spies |
| Target DNS | Public-target validation runs native resolution in bounded credential-free children, preserves all-address rejection, joins cancellation and quarantines uncertain cleanup. Proxy/hosted transport DNS remains separate work. | Real Windows children and six Linux probes: native resolver, mixed private answer, memory, cancellation, kernel deadline and parent death |
| TLS identity | Pin at the TCP backend boundary while preserving logical connection-pool origins, Host and SNI. Retain the actual connection's pin across TLS and reuse. | Real HTTPX/httpcore keep-alive pool: two hosts sharing an IP require separate TLS handshakes, returning to the first reuses only its own connection |
| Crawl scope | Research and KB propagate explicit seed scope into direct GET redirects and every brokered top-level browser HTTP hop. Text and links use one acquired document. Scoped reads bypass cache; hosted scoped readers/discovery are refused. Metadata redirect targets remain on the seed host. | Direct hop dispatch spies and actual Chromium HTTP/JS redirects; shared typed KB/BFS tests |
| Body resources | Bounded raw gzip/deflate decoding, wire/decoded caps, exact-boundary truncation, explicit unsupported/corrupt compression errors. | Ten-megabyte expansion witnesses with measured allocation below two megabytes at a 1KB cap |
| Saved files | Worker-thread atomic exclusive publication refuses existing files and cleans owned temporary files; crawl indexes retain safe per-page failure reasons. | Existing user edits, simulated publication failure and actual file contents |
| Browser ownership | KB contexts are job-owned and launched lazily; duplicate launch within a job is serialized. Partial launch closes owned resources. Closing one job leaves another usable. | Actual Chromium contexts/cookies and asynchronous ownership tests |
| Browser execution | One shared job-owned Chromium launch with explicit sandbox, service workers blocked, CDP HTTP fulfillment and a native deny proxy. HTTP uses the same pinned/bounded factory; native DNS/UDP flags provide defense in depth. | Actual Chromium HTTP redirects/relative scripts/repeated cookies, private subresource redirects, denied POST/WebSocket, request fanout, compressed resource limits and binary-document refusal |
| Shared admission | FIFO process-wide admission: 2 browser owners/8 waiting, 6 ordinary acquisitions/24 waiting and 2 reserved browser-owner acquisitions/8 waiting. Waits expire at 30 seconds within the execution deadline; full queues fail before dispatch. Cache hits bypass admission. | Cross-loop grants, cancelled/racing grants, FIFO, timeout, saturation and reserved-owner progress tests |
| Cancellation ownership | The browser bridge waits for the actual task's asynchronous finalizers, deferring repeated cancellation. Failed teardown retains/quarantines its browser capacity and native deny endpoint; readiness exposes it. Close the page before CDP detach to end paused navigations. Native deny handlers are capped at 16 per owner and joined before releasing capacity. | Baseline bridge returned before cleanup in two negative regressions; cooperative finalizer, quarantine and real Chromium follow-ups below |
| Browser process resources | Contain Node and Chromium before launch in a 2GiB Windows Job Object or Linux cgroup v2. Bound CPU, process/task count, protocol frames/queue/total, owner lifetime and graceful cleanup. Independent termination works while the browser event loop stalls and on parent death. Verify no live descendants before releasing capacity; retain failed OS proof for retry. | Real Windows descendants, driver protocol adversaries, aggregate allocation and crash tests; seven Linux kernel/Chromium probes below |
| Knowledge extraction | Shared knowledge profile retains hidden panels, exact code whitespace and table cells; captures each supported lazy tab while present. No independent KB text fallback after a failed/partial result. BFS uses acquired links and preserves query values. | Pure EN/AR/code/table/limit fixtures plus real lazy replaced-panel Chromium fixture and scoped BFS |
| Local policy | Default `local_only`; remote recovery/proxies require explicit mode and named allowlist entries. Keys and legacy flags cannot authorize remote calls. Cache separates policies; ambient HTTP proxies are disabled. | Direct helper/cascade/factory tests with deliberately configured old keys and switches |
| Robots policy | Default-on KazmaBot enforcement for automated crawl/KB/research saves, before cache reuse and every content/browser request. Missing 404/410 differs from denial and failed checks; explicit opt-out is visible. Separate bounded raw metadata capacity avoids nested admission deadlock. | Actual bounded HTTP loader, disallowed redirects, Chromium assets/navigation, policy/cache/route changes, cancellation and KB progress ownership |
| Proxy route | One live account snapshot; invalid selected routes refuse direct fallback with `proxy_routing_error`. Local-only policy avoids credential resolution and paid probes. Settings Test probes the resolved URI under admission/15s/128 decoded bytes. | Unavailable store/vault, malformed config, atomic account changes, thread isolation, actual HTTPX and Chromium route refusal, bounded probe fixtures |
| Search SDK worker | DDGS gets an explicit route over stdin in an isolated clean-environment child, with 30s execution and 256KiB combined output limits. Windows inherits the shared aggregate 2GiB/32-process Job Object budget; POSIX uses per-process inherited limits and group cleanup. Search admission is bounded; cancellation joins cleanup before return. | Real controlled SDK child verifies exact route, stripped ambient proxies/API keys, result bounds, deadlines/output refusal and cancellation; no network or paid calls |
| Deadline/recovery | The complete text ladder has a bounded deadline; real HTTPX connect/read/protocol/timeout errors reach local recovery. | Cancellation-finalizer and actual MockTransport exception tests |
| Local discovery | Recursively fetch leaf sitemaps, refuse unsafe XML, prevent cycles, cap 64 documents/4 index levels/20MB XML/60s recursive traversal. Handle raw `.gz` and already decoded HTTP gzip. | Nested indexes, cycles, oversized frontier and gzip variants |
| Readiness | Reports policy, workers, retained jobs and request allowances. Provider charges remain unknown; explicit Firecrawl balance observations carry account scope and freshness. Status reads use cached evidence. | Local-only policy, unavailable/zero/stale balances, cleanup quarantine and storage-refusal fixtures |
| Typed outcomes | `FetchResult` carries stable error codes, actual final URL/status/backend, per-call attempts, observed bytes, clipping, link completeness, quality and cache status. Content beginning `Error:` is not a failure discriminator. | Real HTTPX redirects/streams, JSON 404, literal content, actual Chromium loop bridge, failed provider receipts |
| Consumer contract | Chat reads/saves and crawl indexes show acquisition receipts, failures included. Research writes typed evidence into `sources.json`, refuses partial automatic saves; KB refuses partial content and permanent acquisition refusals. | Pipeline reaches acquisition and persists coded errors/unknown credits; atomic file content/existing-file/partial refusal tests |
| Hosted response resources | Jina/Firecrawl scrape responses use the same bounded raw decoder. Reject clipped service payloads, request only needed Firecrawl markdown, cap output and clamp response settings to 20MB. | Real HTTPX compressed nine-megabyte service responses retained at 1KB; stream closure and typed limit refusal |
| Login quality | Require a real password input plus an explicit EN/AR login document title; reject observed login screens and avoid further hosted recovery. | Static HTTP and actual Chromium login fixtures; escaped code, article titles and ordinary links as negative controls |

The raw HTTP layer is not a second extraction ladder. `web_acquire.fetch_text`
uses the public `read_url.fetch_result` typed entry. Public legacy
`read_url.fetch_full_text` is a text adapter over the same implementation.
`FetchResult.evidence()` excludes extracted bodies and full link lists.
Provider service status is not fabricated into origin status; unsupported
final URLs and per-request charges remain unknown. Fetch attempts are call-local
observations. The later allowance follow-up separately persists request
reservations; neither is a durable invoice or proof of provider charges.

## Operator contract in this checkout

- `KAZMA_ACQUISITION_MODE=local_only` is the default. HTTP fetching, extraction
  and local Chromium are allowed; hosted Jina/Firecrawl and scraping proxies
  are refused even when old credentials/settings exist.
- `local_first_with_remote_fallback` plus
  `KAZMA_ACQUISITION_REMOTE_PROVIDERS=jina,firecrawl` admits those hosted
  fallbacks after local recovery fails. A deliberate `proxy` entry additionally
  admits the configured scraping proxy route for HTTP/browser clients.
- Legacy backend/reader switches may narrow permission. Invalid policy values
  fail visibly. This interim operator interface is environment-based; Settings
  controls and financial spending caps are still outstanding; finite atomic
  request allowances now exist as described in the later follow-up.
- Browser rendering requires Windows Job Objects or a writable delegated Linux
  cgroup v2 with memory/swap/pids/CPU/group-kill controls. Linux tries its current
  cgroup or absolute `KAZMA_BROWSER_CGROUP_ROOT`; Kazma does not configure host
  controllers. Missing controls refuse rendering. Node/Chromium share 2GiB
  aggregate memory and approximately two CPUs, with 32 processes on Windows or
  256 tasks/no swap on Linux. Frames and queued commands are each 32MiB, total
  protocol traffic 512MiB. An independent watchdog ends the tree at cancellation,
  1800-second owner lifetime or five-second cleanup grace. The driver receives a
  minimal environment. The isolated private Playwright Connection/stdio seam is
  qualified against 1.61.0; future versions need the same boundary tests.
- `KAZMA_CRAWL_RESPECT_ROBOTS=1` now defaults automated crawl/KB/research saves
  to enforcement. Explicit `0` or `crawl_site(respect_robots=False)` is visible
  in receipts. Manual chat reads keep their existing single-page behavior.
  Metadata is capped at 500KiB/15s/five same-origin redirects, with two active
  metadata slots/eight queued; cross-origin redirects refuse crawling. Policies
  cache for one hour, denials ten minutes, failures thirty seconds, partitioned
  by origin/acquisition policy/resolved route. There are at most 128 cached
  policies/16MiB estimated policy memory, eight in-flight keys/32 waiters each,
  and 64 checked origins per job. Rule count/length are bounded separately.
  Hosted readers are refused during enforcement because their individual
  navigation requests cannot be checked. Full standards/release qualification
  and durable policies remain required work.
- `KAZMA_FETCH_DEADLINE_SECONDS` defaults to 90 seconds, with a 1–300 second
  operator range, for one full text acquisition attempt. It is not yet a
  complete multi-page job deadline or a hard wall-clock bound on finalization.
  Cancellation/deadline joins cooperative asynchronous cleanup; a hung browser
  close still needs the planned hard process-tree termination boundary.
- Admission is shared only within one Python process. Ordinary acquisitions
  have six active slots and 24 waiting; browser-owning jobs have two reserved
  acquisition slots and eight waiting. Browser launches have two active slots
  and eight waiting. Each admission wait expires at 30 seconds. Quarantined
  browser capacity stays occupied until cleanup succeeds, and readiness fails
  visibly. This does not enforce an aggregate limit across server processes.
- Readiness does not make paid provider requests. It exposes unknown accounting
  honestly. Chat/crawl receipts now identify attempted services and unknown
  accounting; there is no durable usage ledger or credit dashboard yet.

No live installation was modified or restarted. No third-party credits were
spent or balances inspected for these tests. No industrial certification,
Linux runtime qualification, production rollout or website publication is claimed.

Removing a `--no-sandbox` argument alone was insufficient: Playwright defaults
`chromium_sandbox` to false. All three scraper launches now set it explicitly to
true, with actual process argument verification on this Windows host. Those
three callers now delegate to one shared launch. This is
not an OS sandbox attestation or Linux container qualification. See the
[official launch option](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-option-chromium-sandbox).

## Verification recorded so far

- 173 passed in a targeted run including acquisition behavior, real Chromium,
  import integrity, static gates, existing deep-audit regressions and façade
  compatibility. Later sitemap and ownership additions require their final
  follow-up run below; this count is not a cumulative count across runs.
- 115 passed in the subsequent acquisition/KB/research compatibility run,
  including recursive sitemap and gzip cases. Subsequent import cleanup and
  same-job launch serialization are awaiting the final follow-up run.
- `git diff --check` has reported no whitespace errors.
- 239 passed in the wider acquisition/import/static/docs run; its one failure
  was a renamed documentation anchor. The repair preserved the published anchor
  and passed a 53-test safety/anchor follow-up.
- 46 safety tests passed after explicit sandbox enablement, including actual
  process command lines for scoped KB, standalone KB and read-url Chromium.
- A new shared-IP TLS test first failed against the URL-rewriting transport:
  only the first hostname received a TLS handshake. The socket-level fix passed
  an 88-test acquisition/proxy/attachment/transport follow-up. Connection
  provenance and non-TCP refusal are covered by the subsequent follow-up.
- 93 tests passed across acquisition/scope/transport/browser/research behavior.
  Eight scope tests then passed after adding actual crawler propagation and
  refusal to silently retrofit scope onto an already guarded context.
- Nine scope tests passed including an actual Chromium JavaScript navigation
  refused before DNS across the Windows browser loop. The first scope failure
  is retained instead of being replaced by Chromium's subsequent error-page URL.
- Required full repository run completed in 1432 seconds: 13,910 passed,
  6 failed, 59 skipped, 2 deselected. One shutdown-timeout chunk was recovered
  by the runner. Output: `evidence/web_acquisition_full_suite_2026_10_10.log`.
  Three failed tests used obsolete GET-only mocks; two ratchets required locking
  in reduced debt; the metrics collection receipt needed regeneration. All six
  were repaired and passed focused follow-ups; this is not a green full run.
  It began before the final sandbox/terminal/pinning/scope refinements, which
  have separate focused runtime evidence above.
- Final acquisition/SSRF/quick-win/debt/import/static follow-up: **246 passed**
  in 142.51 seconds, with one dependency deprecation warning. Output:
  `evidence/web_acquisition_followup_2026_10_10.log`.
- Transport/metrics/documentation follow-up: **37 passed**. Metrics were
  regenerated through the standard generator using a killable, lowered-priority
  collection process; the receipt records 13,989 collected cases, not passing
  cases. New Python files have intent-to-add entries for the tracked-input
  metrics scanner; no file contents were staged for commit.
- All ten selected changed/new acquisition modules imported independently in
  fresh interpreters. Environment reference: 268 variables, zero undescribed.
  Final whitespace check passed.
- Typed-contract compatibility: **256 passed** in 74.12 seconds (one dependency
  deprecation warning), covering acquisition, research/KB jobs/routes, proxy and
  SSRF consumers. Output: `evidence/web_acquisition_typed_consumers_2026_10_10.log`.
  Subsequent hosted-streaming/login/body-ceiling changes have separate follow-up
  evidence below and are not covered by that earlier result.
- Hosted-streaming follow-up: **88 passed** in 28.78 seconds, including real
  compressed service streams and retention of HTTP status after a body drop.
- Login/Chromium/pipeline follow-up: **39 passed** in 10.04 seconds before the
  later operator-ceiling assertion and required-import cleanup.
- Wider typed-result qualification: **421 passed** in 174.66 seconds, one
  dependency deprecation warning. Includes acquisition/KB/research/proxy/SSRF,
  debt/static/import gates, environment/tool catalog/documentation checks and
  current metrics receipt. Output: `evidence/web_acquisition_typed_final_2026_10_10.log`.
  Malformed-base/duplicate-attribute link parsing was repaired during this run;
  it receives a separate focused follow-up, not retroactive coverage.
- That final link/result/receipt/anchor follow-up passed **33 tests** in 9.94
  seconds. Output: `evidence/web_acquisition_typed_link_followup_2026_10_10.log`.
  Final whitespace check and generated environment-index check passed.
- Ten changed/new acquisition modules passed independent fresh-interpreter
  imports. Doc/code sync passed. That phase's metrics receipt recorded **14,014
  collected** cases, not passing cases. Exception debt ratchets decreased to
  3,533 blind handlers/441 silent handlers; private-by-use public symbols to
  551. No baseline was raised.
- Actual pre-fix Chromium HTTP redirect witness **failed as expected**: the
  owned out-of-scope destination was contacted before the reader reported
  `scope_blocked`. Archived output:
  `evidence/browser_redirect_before_broker_2026_10_10.log`.
- Shared broker/KB compatibility run: **410 passed** in 217.04 seconds, one
  dependency deprecation warning. Covers actual Chromium, KB/API/jobs/research,
  proxy/SSRF, imports, debt/static, environment/catalog and documentation gates.
  Output: `evidence/browser_kb_broker_final_2026_10_10.log`. Later renderer
  cap/link merging, bodyless HEAD handling and KB error-JSON guards are covered
  by the separate follow-up below, not retroactive coverage.
- Final resource/consumer follow-up: **101 passed** in 40.81 seconds. Output:
  `evidence/browser_kb_broker_limits_followup_2026_10_10.log`. Includes no-body
  HEAD stream, preserved representation metadata, bounded callback output and
  HTTP 404/429/500 JSON refused by KB despite general-reader inspection support.
- Eight acquisition/KB modules imported independently in fresh interpreters.
  Documentation sync and environment-index checks passed. Debt decreased to
  **3,522 blind / 439 silent** handlers; module-local public symbols stay at 551.
  No baseline was raised. That phase's metrics receipt records **14,034 collected**
  cases, not passing cases. These are development/offline gates on Windows,
  not full repository, Linux, provider-account or production qualification.
- Final metrics/receipt/static/environment/document-anchor follow-up: **103
  passed** in 68.56 seconds. Output:
  `evidence/browser_kb_broker_repository_followup_2026_10_10.log`. Final
  `git diff --check` passed. No provider credits were spent and no live install
  was changed, restarted, committed or deployed.

- Admission/cancellation baseline: two regressions failed against the old
  Windows bridge, which acknowledged cancellation/timeout before asynchronous
  cleanup completed. Output: `evidence/browser_cleanup_before_join_2026_10_10.log`.
- Qualification caught an intermediate teardown hang: the HTTP stream closed,
  but CDP detach waited on a paused navigation. Closing the page first fixes it.
  Historical output excerpt:
  `evidence/browser_cdp_detach_before_page_close_2026_10_10.log`.
- Acquisition/KB/research/admission compatibility: **242 passed, 1 skipped** in
  116.65 seconds, one dependency deprecation warning. Output:
  `evidence/acquisition_admission_final_2026_10_10.log`. Later native endpoint
  ownership and final cleanup-boundary refinements have their own runs below.
- Native deny endpoint/browser/admission/static/debt follow-up: **150 passed**
  in 102.79 seconds. Output:
  `evidence/acquisition_admission_native_cleanup_2026_10_10.log`.
- Actual Chromium/HTTP-stream cleanup plus admission follow-up: **33 passed**
  in 33.94 seconds. Output:
  `evidence/acquisition_admission_final_cleanup_2026_10_10.log`. The later
  partial-launch quarantine safeguard has a separate follow-up below.
- Partial launch/admission/ownership compatibility: **66 passed** in 14.74
  seconds. Output: `evidence/acquisition_admission_partial_launch_2026_10_10.log`.
  Final endpoint-error/required-module boundary refinements are qualified below.

- Final cleanup-boundary qualification: **186 passed** in 122.81 seconds.
  Covers real Chromium cancellation/admission, incomplete cleanup, native
  endpoint bounds, partial launch, required-module failures, scope/policy,
  hard-page recovery, static/import and debt gates. Output:
  `evidence/acquisition_admission_cleanup_boundary_2026_10_10.log`.
- Eight acquisition/KB modules imported independently in isolated fresh
  interpreters. Documentation sync passed; environment index still has 268
  variables and zero undescribed. Debt remains **3,522 blind / 439 silent**
  handlers and 551 module-local public symbols; no baseline increased.
  Regenerated metrics record **14,059 collected** cases, not passing cases.
  No credits were spent and the live installation was not changed.

- Final metrics/receipt/environment/catalog/documentation checks: **56 passed**
  in 19.36 seconds. Output:
  `evidence/acquisition_admission_repository_2026_10_10.log`.
  Final `git diff --check` passed. These remain Windows development/offline
  gates, not Linux, paid-provider, full-suite or industrial release qualification.

- Proxy routing baseline: **13 expected failures / 2 passes** against the
  previous resolver exposed direct fallback after config/provider failure,
  incoherent repeated config reads and false-ready status. Output:
  `evidence/proxy_routing_before_fail_closed_2026_10_10.log`.
- Proxy/browser/search/Settings/process-budget/static/import qualification:
  **300 passed** in 128.01 seconds, one dependency deprecation warning.
  Includes real Chromium route refusal and controlled real SDK subprocesses;
  no provider calls. Output: `evidence/proxy_sdk_final_2026_10_11.log`.
  Earlier intermediate qualification had 246 passes and only the expected
  decreased-debt lock failure; it is not claimed as a green run.
- Final SDK fixture/child-environment follow-up: **27 passed** in 6.76 seconds.
  The cancellation fixture now publishes its PID receipt atomically and checks
  process termination through psutil. Output:
  `evidence/proxy_sdk_cleanup_followup_2026_10_11.log`.
- Eight proxy/search/acquisition modules imported independently in isolated
  fresh interpreters. Documentation sync and environment checks passed (268
  variables, zero undescribed). Broad exception debt decreased to **3,514 blind
  / 439 silent** handlers; no baseline was raised. Windows development/offline
  evidence does not qualify Linux limits, real provider contracts or a full
  industrial release. No provider credits were spent; the live installation
  was not changed or restarted.

- Final metrics/receipt/environment/catalog/docs checks: **56 passed** in
  19.81 seconds. Output: `evidence/proxy_sdk_repository_2026_10_11.log`.
  Refreshed receipt records **14,107 collected** cases, not passing cases.
  Documentation sync, environment index and final `git diff --check` passed.

Historical `evidence/web_acquire_2026_10_10.py` asserts the original bugs against
the audited revision. Preserve it as historical evidence, outside normal test
discovery. It is not a remediation suite and is not expected to stay green on
the fixed implementation. The new safety/policy tests assert the desired behavior.

## Historical implementation sequence (2026-10-11)

Origin scheduling qualification (2026-10-11): two before-fix regressions
confirmed ignored 120-second Retry-After values for an independent client and
a redirected request. Evidence: `evidence/origin_retry_after_before_scheduler_2026_10_11.log`.
The browser/KB/research/static/import compatibility run passed **244 tests** in
136.09 seconds (`evidence/origin_scheduler_qualification_2026_10_11.log`).
A further **82 tests** passed in 22.07 seconds for origin/proxy/debt boundaries,
including invalid delay policy, cancelled/queued requests, deadline cleanup,
typed failures and hosted retry refusal
(`evidence/origin_scheduler_boundary_followup_2026_10_11.log`).
Final Chromium/origin/static/import/debt qualification passed **130 tests** in
100.53 seconds (`evidence/origin_scheduler_chromium_final_2026_10_11.log`).
The real Chromium cooldown test proves that a browser job honors another job's
cooldown before HTTP dispatch and closes its context, browser, runtime, native
deny endpoint and admission lease.

HTTPX streams and hosted source/service reservations now share two active and
32 queued reservations per origin, a 512-origin table and a 30-second
admission/spacing bound. Crawl/KB and KB metadata starts have a 300ms floor.
Complete integer/HTTP-date Retry-After values are retained when headers arrive,
including redirect destinations and subsequent body failure/cancellation.
Cooling requests fail visibly before dispatch. Firecrawl no longer shortens
the server's delay to 20 seconds. Origin state remains process-local and
non-durable; synchronous search and native browser automation need integration.

Eight origin/HTTP/browser/research/KB modules imported independently in isolated
fresh interpreters. Documentation/environment sync passed. Exception baselines
remain 3,514 blind / 439 silent. No paid calls or live-install changes.

Final metrics/receipt/environment/catalog/docs verification passed **56 tests**
in 20.18 seconds (`evidence/origin_scheduler_repository_2026_10_11.log`).
The refreshed receipt records **14,145 collected cases**, not passing cases.
Documentation sync, environment index (268 variables, zero undescribed) and
final `git diff --check` passed. These remain Windows development/offline
checks; full repository, Linux, provider-account and industrial release
qualification are still required.

HTTP delay parsing follows [RFC 9110 section 10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#name-retry-after).
Robots remediation now replaces the legacy opt-in/fail-open page-extractor
checker with bounded raw checks. It distinguishes successful, missing, denied
and failed policies, covers redirects and brokered assets, freezes each job's
setting and records call-local metadata receipts. Unknown proxy usage/credits
remain unknown. Matching covers combined KazmaBot groups, wildcard fallback,
longest match/allow ties, query paths and percent-encoded UTF-8. Requirements
are grounded in [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html).
The conservative same-origin control redirect boundary and strict failure/body
limits are documented; this is not a full RFC conformance or release claim.

Robots qualification (2026-10-11):

- Historical checker witness: **one expected failure** reproduced permission
  after a failed robots fetch at audited commit `9796e53`.
  `evidence/robots_before_fail_closed_2026_10_11.log` preserves the observation;
  its Python witness stays outside normal test discovery.
- Final HTTP/Chromium/KB/proxy/origin/research behavior run: **203 passed** in
  83.86 seconds (`evidence/robots_final_behavior_2026_10_11.log`). Actual
  Chromium refused disallowed navigation and assets before dispatch; a denied
  script made the page partial. Dedicated metadata capacity completed while
  every ordinary acquisition slot was occupied. Cancellation, changed rules,
  route partitioning, policy freezing, call-local receipts and bounded waiters
  are covered. KB progress yields leave no robots ContextVar in the subscriber.
- Safety/policy/research/import/static/debt compatibility: **166 passed** in
  99.06 seconds (`evidence/robots_static_compatibility_2026_10_11.log`).
  Eight changed modules also imported independently in isolated fresh processes
  (`evidence/robots_fresh_imports_2026_10_11.log`).
- Metrics/receipt/environment/catalog/docs verification: **67 passed** in
  21.00 seconds (`evidence/robots_repository_2026_10_11.log`). The refreshed
  receipt records **14,198 collected cases**, not passing cases. Environment
  inventory remains 268 variables, zero undescribed. Doc-code sync and final
  whitespace checks passed.

Early qualification exposed Windows test-name overflow from an oversized
parameter value and shared page-cache contamination between fixtures; bounded
parameter IDs and isolated caches repaired those fixtures. The failed logs
remain historical observations, not final qualification. No provider credits
were spent and no live installation was changed/restarted. Windows offline
tests do not establish Linux, provider-account, full-suite or industrial release
qualification. The full sequence below remains required.

Browser process qualification (2026-10-11):

- Final broader acquisition behavior run: **295 passed** in 73.14 seconds
  (`evidence/browser_driver_qualified_behavior_2026_10_11.log`), covering browser
  limits, HTTP/scope/policy/result contracts, robots, origins, KB and proxy/search
  workers. The qualification runs overlap; their counts are not additive.
- Windows final browser/admission/process-budget/import/static/debt run:
  **188 passed** in 124.46 seconds
  (`evidence/browser_driver_final_boundaries_2026_10_11.log`). Real descendants
  end on parent-process death, stalled browser event loops, hung close and
  aggregate memory pressure. Oversized protocol frames, blocked command writes,
  stderr, startup deadlines, failed OS proof/retry and typed failure receipts
  are covered. Cgroup directory-open failure leaves no owned directory behind.
- **Seven Linux kernel/browser probes passed** in Ubuntu WSL, Python 3.14.4,
  Playwright 1.61.0/Chromium 149.0.7827.55
  (`evidence/browser_linux_boundary_2026_10_11.log`). They verify aggregate
  memory OOM, task-count refusal, hard lifetime, parent death, sandboxed Chromium,
  forced hung cleanup and tree termination before a stalled Python event loop
  unblocks. All use the product cgroup implementation; kernel controls are read
  back and owned groups are removed only after empty verification.
- `evidence/browser_linux_harness_2026_10_11.py` reproduces the Linux probe.
  Root creates only a unique disposable delegated subtree; tests run as UID1000.
  An isolated runtime under the user's cache supplies Python packages/browser
  binaries. Three missing native libraries were downloaded/extracted there and
  selected through a test-only executable wrapper; no distro packages or global
  controller settings were changed. The Chromium sandbox stayed enabled. The
  final harness reports exit zero and removal of its delegation.
- Linux probing exposed Playwright's one-argument exception reconstruction at
  a forced-close boundary. The private transport exception now preserves typed
  resource errors through that reconstruction. The real API regression checks
  the error code; already-ending owners skip pointless graceful API calls and
  still require OS termination proof. Fixture corrections included adequate
  startup deadlines, an allocation that exceeds the aggregate budget, isolated
  temporary directories and recognizing verified cgroup removal in the stalled
  loop observer. Early failed runs are not release evidence.
- Eight affected modules imported independently in isolated fresh processes
  (`evidence/browser_driver_fresh_imports_2026_10_11.log`). Repository metrics,
  collection receipt, environment, catalog and docs gates: **67 passed** in
  21.89 seconds (`evidence/browser_driver_repository_2026_10_11.log`). The
  refreshed receipt records **14,212 collected cases**, not passing cases.
  Environment inventory: 269 variables, zero undescribed. Doc-code sync and
  whitespace checks passed.

This closes the targeted Node/Chromium process boundary gap, not overall Python
HTTP/parser/transport-DNS cleanup or OS network isolation. The separate native browser
tools still require migration. Whole-install job/storage caps, runtime/storage
readiness, durable recovery, accounting and user-visible operations remain open.
The probes do not establish a Linux distribution/version matrix, full repository
or soak qualification. No provider credits were spent, no live installation was
changed/restarted, and no production rollout is claimed.

### Public-target DNS boundary follow-up (2026-10-11)

The acquisition validator now retains the synchronous SSRF API while placing
native `getaddrinfo` in an isolated child. Direct reads, redirect hops, CDP assets
and permitted hosted target checks use this boundary. It does not replace the
separate transport resolution of proxy/service endpoints or native search/browser
network stacks. No hosted requests or provider credits were used to qualify it.

Limits: four active validators, 16 queued, ten-second admission, five-second
worker runtime, 256MiB memory, 16KiB combined output, 8192-character input URL,
128 unique addresses and 2048 raw DNS records. Overflow is a typed refusal;
never truncate a mixed answer into permission. Child environments exclude
credentials/proxy/runtime overrides. Windows uses the existing verified Job
boundary; POSIX has resource limits and a kernel wall timer, and Linux reads
back parent-death protection before resolving. Existing SSRF checks examine
every address; direct acquisition connects its validated pins.

Per-call executor/process/pipe ownership survives repeated cancellation. Capacity
releases after cleanup proof; uncertainty retains handles and quarantines the
slot in readiness. A later validation starts one retained cleanup retry thread,
with an explicit bounded join. Python 3.14 logs exceptions from cancelled
shielded futures even when their owner retrieves them; DNS shields one
non-raising completion receipt and re-raises actual failures to the caller.
This avoids false cancellation alarms without suppressing acquisition errors.
See the [CPython shield implementation](https://github.com/python/cpython/blob/v3.14.4/Lib/asyncio/tasks.py),
[kernel timer documentation](https://docs.python.org/3.12/library/signal.html)
and [Linux parent-death semantics](https://www.man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html).

Final frozen-source qualification:

- **258 passed** in 79.92 seconds
  (`evidence/dns_worker_final_behavior_2026_10_11.log`): real DNS children,
  memory/output/deadline failures, repeated cancellation, uncertain-cleanup
  quarantine/recovery, shared admission, HTTP/robots/origin limits, actual
  browser broker/driver and KB compatibility. The DNS fixture explicitly joins
  retained cleanup threads and verifies every owned child ended.
- **115 static/import checks passed** in 82.81 seconds
  (`evidence/dns_worker_final_static_2026_10_11.log`), and **195 transport/result/
  policy/scope/SSRF/CORS checks passed** in 31.67 seconds
  (`evidence/dns_worker_qualified_transport_final_2026_10_11.log`).
  The original combined run timed out at app lifecycle creation. Bounded
  diagnostic runs identified unrelated Hugging Face model warm-up and the
  shipped `npx` MCP bootstrap. The CORS-only fixture now replaces those two
  dependencies while retaining real app construction, lifespan and middleware;
  production startup was not changed. Diagnostic timeout logs are not passing
  evidence. Broader runtime/dependency readiness remains required work.
- **Six Linux probes passed**, Ubuntu WSL/Python 3.14.4
  (`evidence/dns_linux_boundary_2026_10_11.log`): native localhost resolution,
  mixed private rejection, real memory limit, repeated cancellation, independent
  SIGALRM deadline and parent death. The orphan was reaped; no loop exception
  warnings occurred. Controlled resolver fixtures use the actual product child
  entry; they do not establish a public DNS/provider availability matrix.
- **Eight fresh isolated imports passed**, with bounded clean-environment
  interpreters and temporary data/workspace paths
  (`evidence/dns_worker_fresh_imports_2026_10_11.log`).
- **130 final ratchet/repository checks passed** in 87.32 seconds after the CORS
  fixture refinement (`evidence/dns_worker_qualified_repository_2026_10_11.log`).
  Refreshed collection: **14,226 cases**, not passes; input fingerprint
  `c93ba1d757ace333f2d67e8afa9ec65efa1088807d16bc3d52b1b3b5c24861b4`.
  Environment inventory remains 269 variables, zero undescribed; doc-code sync
  and whitespace checks passed.

The reproducible `evidence/dns_linux_boundary_2026_10_11.py --wsl` probe reuses
the isolated Ubuntu runtime. It performs no downloads, privileged operations,
host configuration changes or public network requests. Its localhost native
resolution is a raw worker check; public acquisition still rejects localhost.
This phase does not establish all DNS containment, full-suite success, soak,
storage/runtime readiness or production rollout.

### Recovery, allowances, parser/search and endpoint follow-up (2026-10-11)

The development checkout now has a bounded durable KB journal in the existing
ConfigStore backend. Admission/explicit resume claims ownership before the Web
API returns acceptance. Site and refresh use the same background admission and
cleanup path. Four jobs may be active; at most 24 jobs and 8MiB remain retained.
Page effects and BFS pop/inflight/frontier changes persist before dispatch.
Metadata requests retain parsed llms/sitemap results and index children, never
raw XML/HTML bodies. Interrupted recovery preserves completed work, skips
uncertain fetch/indexing effects and continues pending URLs under the identical
frozen policy. Sparse sitemap results survive BFS fallback. Uncertain/limited
discovery produces a partial outcome, not a success-only notification.
Research crawl/pipeline durability and durable origin/robots scheduling remain
separate required work.

Settings backup/restore now excludes the allowance/crawl journals and their CAS
revisions; restoring old settings cannot reopen spent requests or replay effects.
Browser render finalization executes once, keeps the native watchdog armed on
failure, and observes/joins failed Playwright initialization. The broker fixture
also uses the owned driver for runtime readiness, rather than an unbounded native
driver startup. The initial full-run timeout was not reproduced in isolation or
file order; its cause remains unproven pending full-run requalification.
Sitemap XML now shares the owned parser capacity, memory/deadline/output and
cancellation boundaries. Unsafe XML is refused; exceeding 4000 leaf URLs or 64
index children produces a typed resource failure instead of silent clipping.

Identity preserves `.md`, trailing slash, matrix parameters, escape octets,
query order/duplicates and the empty-query marker. Only the fragment is removed;
authority case may normalize. Do not guess that different resources are aliases.
SQLite first-writer tests exposed `_stored_raw` releasing the ConfigStore lock
before its native SELECT. It now holds the existing RLock through that statement,
including secret/dict preparation before a conditional batch. The regression
uses the original lock boundary as a negative control.

Remote proxy/Jina/Firecrawl HTTP effects reserve finite requests durably before
transport dispatch, including retries, redirects, metadata and explicit probes.
Failed/unknown launch/cancellation is never refunded. Defaults are 256/job and
1024/UTC day, bounded operator ranges 0–1024 and 0–4096. Zero denies dispatch;
active allowances may narrow only. Shared SQLite/PostgreSQL CAS protects first
writers. Retention is 31 days, 4096 job counters, 256 recent attempts and 16
balance accounts, with a 4MiB ledger cap; clipping recent evidence does not
reset aggregate counters. Reservations are an upper bound, not provider charges
or financial spending caps. Fixed HTTP search reserves six requests before its
child; opaque DDGS proxy requests are refused before dispatch. Selected routes
never silently become direct fallback.

Firecrawl's explicitly requested balance refresh uses
[the documented credit-usage endpoint](https://docs.firecrawl.dev/api-reference/endpoint/credit-usage).
It is metered, bounded and account scoped; cached observations are fresh for five
minutes, with a durable 60-second query cadence. Ordinary status/readiness reads
never poll a provider. Observed zero differs from unavailable/stale. Jina/proxy
balances without a verified adapter and per-request charges stay unknown, with
provider dashboard links where supported. No real paid provider was queried in
qualification.

HTML/sitemap XML extraction and fixed HTTP search now execute in bounded owned children,
with clean environments, capped input/output/memory/deadlines and cancellation
joins. Async and sync proxy endpoints and hosted async transport use the owned
resolver. Logical HTTP/CONNECT authority and verified TLS SNI survive literal-IP
connection. Only an operator-selected proxy endpoint may be private; content
targets still require independent public validation. Alternative proxy transport
mounts are refused. Legacy synchronous target validation/XML parsing and native
browser automation remain outside the complete boundary.

The Web page reconnects to durable jobs and shows attempts, uncertain work,
request reservations, cached account balances and explicit resume/credit refresh.
Poll/resume/refresh calls have deadlines, single-flight state and teardown.
Real Chromium/Alpine in EN/AR caught an uninitialized refresh-button field that
static checks missed; the fixed state and original failing shape are both tested.
Chat `/kb status` reports retained services, limits and balance freshness. Agent
ingest admission failures remain errors rather than finished-site messages.
Full CLI/TUI/Settings operations coverage is still required.

Evidence (counts overlap; do not add them):

- `evidence/parser_behavior_2026_10_11.log`: **92 passed**; parser failure,
  cancellation and hostile input qualification.
- `evidence/search_http_behavior_2026_10_11.log`: **122 passed**; owned HTTP
  search/DNS/parser integration.
- `evidence/remote_allowances_integration_2026_10_11.log`: **128 passed**.
- `evidence/endpoint_allowances_behavior_2026_10_11.log`: **121 passed**.
- `evidence/raw_read_crawl_accounting_2026_10_11.log`: **65 passed** after the
  traced SQLite statement-lock fix.
- `evidence/acquisition_postgres_2026_10_11.log`: **2 passed**, disposable
  PostgreSQL 16, separate ConfigStore instances/connections, allowance and crawl
  first-writer CAS/recovery. The owned fixture was removed.
- `evidence/metadata_ui_endpoint_2026_10_11.log`: **130 passed**; real EN/AR
  browser rendering, metadata interruption/recovery and sync proxy routing.
- `evidence/tls_metadata_accounting_ui_2026_10_11.log`: **68 passed**; actual
  certificate/SNI validation for sync/async transport, independent SQLite
  process allowances, metadata and UI integration.
- `evidence/acquisition_surfaces_final_2026_10_11.log`: **40 passed**; real
  EN/AR Alpine fixed/negative controls, terminal failure and cached evidence.
- `evidence/recovery_parser_fullrun_repairs_2026_10_11.log`: **287 passed**;
  restore semantics, real browser/driver failure and cleanup, HTML/XML parser,
  EN/AR recovery rendering, and repaired repository regressions.
- `evidence/owned_xml_accounting_final_2026_10_11.log`: **144 passed**; owned
  HTML/XML parsing, durable metadata, allowances, import/debt and settings gates.
- `evidence/acquisition_fresh_imports_followup_2026_10_11.log`: **11/11**
  changed modules import independently in bounded fresh interpreters.
- `evidence/browser_linux_boundary_2026_10_11.log`: **7 kernel/browser cases
  passed** on the follow-up; the owned cgroup was verified empty and removed.
- `node --test tests/js/test_kb_recovery.js`: **7 passed**, including incomplete
  discovery never emitting a success toast.
- `evidence/dns_linux_boundary_2026_10_11.log`: **9 Linux probes passed** after
  endpoint additions; actual native resolution, mixed-private hosted refusal,
  selected private proxy, memory/runtime/cancellation/parent-death cleanup.
- `evidence/parser_search_linux_boundary_2026_10_11.log`: **10 Linux probes
  passed** for isolated HTML/XML parser/search entry points, XML entity refusal
  and independent kernel deadlines, plus native process cleanup.

Refreshed collection: **14,324 cases**, not passing tests; fingerprint
`61f84f7a86d70db06eff2e4e92452c4e8ce625cf7a6b28dc78336b5888b56d86`.
Environment inventory: 271 variables, zero undescribed. Full repository
qualification is recorded separately below when the run settles; historical
partial/failed runs are not green release evidence.

The first full repository run (`evidence/acquisition_full_repository_2026_10_11.log`)
finished with **14,240 passed, 8 failed, 61 skipped, 2 deselected** and an
unmeasured browser-broker file terminated by a test timeout. It is failed release
evidence. Follow-up checks repair the restore classification, reachability,
conditional UI visibility, provider labels and outdated extraction test seams;
the collection receipt is regenerated after final source changes. A full rerun
is still required after these repairs.

Follow-up repository gates (`evidence/acquisition_repository_static_followup_2026_10_11.log`)
passed **187 checks** against the refreshed 14,324-case collection receipt.
The second full run (`evidence/acquisition_full_repository_followup_2026_10_11.log`)
terminated after 2689 seconds with **14,238 passed, 7 failed, 61 skipped and 2
deselected**. Its browser file again lacked a terminal tally; the reporting gap
described below means that file is unmeasured, not green. Four chunks exhausted
their initial budgets and were recovered in smaller pieces. Failures include
browser metadata/launch, sitemap parsing/budget expectations and native process
budgets. No passing full-run claim is made. New phase changes below were qualified
separately and require a fresh repository run after remaining failures are resolved.

### Provider observations, research bounds and runner reporting (2026-10-11)

This phase was qualified in an isolated managed worktree while the primary checkout
stayed frozen for its second full-suite run, then promoted with baseline-content
checks and the primary files' existing line endings. Jina's strictly validated `X-Usage-Tokens`
header now reaches attempt receipts, the durable allowance ledger, crawl projection,
chat status and EN/AR Web rendering before body consumption. Zero is an observation;
absent, malformed or duplicate metadata remains unknown. Body failure/cancellation
retains observed units. Cache reads create no new requests or usage. Retained
history may be clipped, so these are scoped response observations, never a complete
bill, money charge or remaining account balance.

Research crawling now deduplicates pending URLs and bounds retained URL identities
to four times the page cap, with an 8192-character URL ceiling. Coverage limits are
visible in the returned index. Validation failures retain their typed cause instead
of becoming a generic SSRF block. Invalid page/depth/delay values refuse execution;
automatic publication refuses partial extracts. Saves stay bound to the starting
workspace through the existing path scope if the active repository changes.
This does not establish research restart recovery or a whole-install storage cap.

The second full run exposed an isolation-reporting defect: pytest-timeout exits 1
without a terminal tally, and per-file recovery treated that as a benign outcome.
Both isolation attempts now require a parsed tally for exits 0/1; exit 5 remains
an explicit no-tests skip. Missing evidence becomes POISON rather than disappearing
from totals. Regression tests execute the original boundary as a negative control
and demonstrate its false-green result beside a healthy chunk. That full run
used the earlier runner and cannot establish complete repository qualification.

Evidence (overlapping counts):

- `evidence/provider_usage_research_final_worktree_2026_10_11.log`: **136 passed**;
  reported/missing/zero/quota/redirect usage, body failure/cancellation, policy
  refusal, real cached reads, durable projections, EN/AR rendering, crawler bounds,
  partial publication refusal, workspace switching and owned sitemap integration.
- `evidence/runner_terminal_tally_worktree_2026_10_11.log`: **27 passed**, including
  original-boundary controls for unmeasured exits and explicit no-tests skips.
- `evidence/provider_crawl_static_worktree_2026_10_11.log`: **65 passed**; imports,
  reachability, debt ratchets, localization and install-data isolation.
- `evidence/provider_usage_fresh_imports_worktree_2026_10_11.log`: **8/8** changed
  modules import independently in bounded fresh interpreters.
- `evidence/browser_order_diagnostics_2026_10_11.log`: **256 passed**, preceding
  file order plus the actual broker suite. Full-chunk collection with the same
  selected tests also passed **256**, with **1579 deselected**, in
  `evidence/browser_collection_diagnostics_2026_10_11.log`.
- `evidence/browser_primary_capture_diagnostics_2026_10_11.log`: **18 passed**
  in the frozen primary checkout with the full runner's math-thread share.

Collection after this phase: **14,360 cases** (not passing tests), fingerprint
`0a7b1aba7af91d0084bcb217d94481366b244ef2d9a504d4242faf6558524b8d`.
Metrics/surface gates passed **44 checks** against that receipt
(`evidence/provider_metrics_surfaces_worktree_2026_10_11.log`).

The full-suite browser timeout remains unproven; isolated passes do not close it.
No real paid provider was called. This phase is not an industrial release claim.

After promotion, `evidence/acquisition_primary_followup_failures_2026_10_11.log`
passed **108 checks**, covering all seven failed cases plus the actual broker suite
and the current receipt. The unresolved full-run condition still requires
requalification. A shared lower-level gap was then fixed: `run_bounded_async`
signaled cancellation but returned before its native worker finished cleanup.
It now shields and joins the worker receipt through repeated cancellation.
`evidence/process_cancel_join_final_2026_10_11.log` passed **120 checks**, including
real-child fixed/original controls with cleanup deliberately held open, child
environment/shell compatibility, owned acquisition/parser/DNS boundaries. Its
module also imports independently in a bounded fresh interpreter.
Non-deadline process fixtures allow fifteen seconds for startup under full-suite
load while retaining their actual memory/tree/cancellation assertions; production
execution budgets did not change. The browser diagnostic plugin now requires an
explicit selection flag for its historical file-order reproductions. Loading it
for timeout evidence alone never deselects qualification tests.

Final primary collection for that subprocess phase was **14,362 cases**, fingerprint
`a6c6d384e564b7a9db71a5d0e6aca1749e9ce4d0ce340136d57b8981ea5fa900`.
The final repository/surface gates passed **133 checks**
(`evidence/acquisition_repository_gates_final_2026_10_11.log`).

The third frozen full run (`evidence/acquisition_full_repository_qualified_2026_10_11.log`)
finished in 1141 seconds: **14,295 passed, 4 failed, 2 errored, 61 skipped,
2 deselected**, with all eight chunks measured and no poison file. The browser
broker completed this time. All six failure identities trace to the audit's
`PYTEST_PLUGINS` diagnostic module being inherited by nested pytest processes in
unrelated workspaces, where `docs` is not importable. This is a failed qualification,
not a passing suite. The plugin now removes only its own inherited environment
entry after loading; bounded fresh-child original/fixed controls demonstrate
startup failure versus a passing nested test
(`evidence/browser_diagnostic_inheritance_2026_10_11.log`). Final full qualification
must run without the diagnostic plugin. Earlier intermittent browser timeouts
remain recorded; this pass alone does not prove their historical cause.

### Durable research crawl follow-up (2026-10-11)

Implemented in a separate worktree while the third primary run stayed frozen.
`crawl_site` now uses the shared ConfigStore/CAS crawl journal with a research
kind, frozen workspace/output options/limits and acquisition/robots policy.
It persists ownership before fetch/publication and the acquisition receipt
before saving. A page's bounded summary and newly pending frontier settle in
one write; bodies remain out of settings. Credentialed discovered links are
refused before retention. Installation admission/retention remains shared:
four active, 24 retained jobs and 8MiB; research URLs retain the existing
four-times-page-cap bound.

Explicit `resume_job_id` requires identical scope and continues pending pages
only. Completed/failed/ambiguous effects are not replayed, existing files are
preserved and capped/uncertain coverage stays partial. Publication and terminal
commits join repeated cancellation; a successfully committed terminal outcome
is not overwritten during cancellation cleanup. KB list/status/resume routes
filter research jobs; acquisition readiness can inspect both kinds. Research
pipeline/session recovery and durable scheduling remain required work.

Evidence (overlapping runs, never summed):

- `evidence/research_durability_final_behavior_2026_10_11.log`: **125 passed,
  1 skipped**; public SQLite child crashes before/after publication, pending-only
  recovery, publication/terminal cancellation, changed scope/policy, concurrent
  resume, invalid state, finite retained frontier, existing KB/robots/scope/files.
- `evidence/research_durability_behavior_2026_10_11.log`: **81 passed**, including
  shared provider observations/allowances and KB route compatibility.
- `evidence/research_durability_postgres_2026_10_11.log`: **3 passed** against
  a new disposable local PostgreSQL 16 container: actual independent connections,
  shared first-writer caps and public research exclusive resume/settlement.
  The owned container was removed. SQLite process-death evidence does not imply
  a PostgreSQL process-death drill.
- `evidence/research_durability_fresh_imports_2026_10_11.log`: **6/6 modules**
  imported independently in bounded fresh interpreters.
- `evidence/browser_diagnostic_failed_files_2026_10_11.log`: **57 passed**
  with the repaired diagnostic plugin, covering every third-run failure identity
  and its complete owning test file.
- `evidence/research_durability_primary_gates_2026_10_11.log`: **218 passed**
  after promotion: real recovery/KB/provider behavior plus imports/orphans,
  structural/static gates, collection/metrics, i18n and template/hydration checks.
  Doc-code sync and whitespace checks also pass.

The 19 changed files and 11 evidence logs were promoted after zero-conflict raw
baseline checks; primary collection matches **14,380 cases**, fingerprint
`108cba6e65ebadd810087b0baf02456cd15d092226f349f3b92750326db54866`.
This is a collection receipt, not a pass certificate. The primary source is
frozen for a fourth full run with no diagnostic plugin.

The fourth frozen run (`evidence/acquisition_full_repository_clean_2026_10_11.log`)
finished after 1196 seconds: **14,317 passed, 1 failed, 62 skipped and 2
deselected**, all eight chunks measured, no poison file. Broker and research
recovery tests completed. The failure was the real Alpine Arabic recovery
fixture's 20-second outer deadline, including contained Node/Chromium startup,
rendering, screenshot, refresh and teardown. Its default refresh wait alone
could run for 30 seconds. The unchanged fixture and its owning surface checks
then passed **7 tests** in 14.97 seconds
(`evidence/kb_browser_full_failure_recheck_2026_10_11.log`). This isolated pass
does not erase the failed full run or establish the historical timeout cause.
The fixture now has explicit UI step bounds, a 60-second outer budget and phase
timing receipts for future full-load evidence; production limits are unchanged.

### Research execution admission and worker ownership (2026-10-11)

Implemented separately while the fourth primary source stayed frozen. Direct
pipelines now admit before planning/output; background sessions admit before
creating a row/task. Shared process-wide pools allow four active pipelines and
four background sessions with no waiting queue. Session leases transfer to the
actual task and release on its completion, including cancellation before its
first step. Web rejects saturation with a typed 503 instead of accepting work.

Execution freezes starting workspace and acquisition/robots policy. Distinct
per-run directories prevent same-topic/same-second collisions. A cooperative
30-minute pipeline deadline joins asynchronous cleanup. Pipeline preflight now
runs off the event loop; session/store/scoring and pipeline path/registration
workers join cancellation. Standalone extract saves freeze workspace before
fetch and join path/publication work. These changes do not supply durable
pipeline effects/resume, retained-history/storage limits or hard native I/O
deadlines; those remain required work.

Evidence (overlapping, never summed):

- `evidence/research_runtime_worker_join_2026_10_11.log`: **172 passed**,
  covering original/fixed admission boundaries, actual API and SQLite,
  cross-thread direct execution, workspace/policy snapshots, preflight/session
  creation/publication cancellation, cooperative deadline/inner-timeout
  distinction, existing research/KB and structural/static gates.
- `evidence/research_runtime_final_imports_2026_10_11.log`: **8/8 changed
  modules** imported independently in bounded fresh interpreters.
- `evidence/research_runtime_ui_final_2026_10_11.log`: **104 passed**, including
  the bounded/timed real EN/AR browser fixtures and original Alpine-state controls,
  provider/surface compatibility and static/structural gates.
- `evidence/research_runtime_final_lifecycle_2026_10_11.log`: **15 passed**,
  including cancellation before the background task's first step. No lease or
  task-registry entry survives that ending.
- `evidence/research_runtime_final_gates_2026_10_11.log`: **237 passed**, covering
  current collection/metrics, imports/orphans, static/structural gates,
  i18n/template/hydration, runtime/crawl recovery, providers/KB routes and real
  EN/AR browser controls.

Final collection: **14,395 cases**, fingerprint
`896762464c49b425d2fde2c3ba147d0fc57da66549dc35006da7a4ba7b4cdb2f`.
The 18 changed files were promoted with raw baseline checks. The fifth full
run uses that frozen primary checkout, with no diagnostic plugin and the
required default eight chunks. Collected cases and targeted passes do not
replace full qualification.

### Publication and readiness follow-up (2026-10-11, isolated qualification)

The concurrent source-save guard ran after publication: four admitted fetches
could publish more than a two-source cap, while the manifest retained only two.
The held-fetch negative control reproduces at least four actual files under the
original dispatch boundary. Source batches now reserve remaining publication
slots before dispatch; failed candidates can be replaced without exceeding the
cap. Gap fills use a monotonic attempt index, preserving earlier filenames.

The source manifest previously omitted gap-fill results. It now has a 4MiB
bound, atomic replacement and joined filesystem workers. An empty manifest is
written before acquisition and every batch settles its receipts before the next
batch starts. Disk/replacement failure stops further acquisition and preserves
the last complete JSON file. This does not resolve uncertain effects inside an
interrupted batch or provide a durable pipeline restart journal.

Preflight errors and malformed readiness receipts now refuse work before
planning/output creation. Readiness also retains explicitly requested historical
remote-job observations after switching to local-only, including reported zero;
cached inspection grants no egress and does not infer charges or balances.

The first behavior/control run recorded **28 passed, 1 failed**: the original
dispatch published five files, exceeding the test's exact-four control assertion.
The control now allows the original scheduler's four-to-six files while fixed
cases require the exact publication cap and matching receipts. The next run
recorded **116 passed, 2 failed** on the debt ratchet, and the broader run
recorded **200 passed, 1 skipped, 1 failed** on the remaining complexity gate.
Preflight/status projection were extracted and the improved exception baseline
was lowered. Preserve those failed logs. Final gates then passed:
`evidence/research_pipeline_publication_gates_2026_10_11.log` records
**250 passed, 1 skipped** in 236.91s, covering publication/preflight/storage
controls, cached historical usage, research/recovery compatibility, imports,
metrics receipts, static checks and the debt ratchet. These suites overlap
earlier runs; do not sum their passing counts.

Final collection contains **14,413 cases**, fingerprint
`02350e25d9ccbf7a87751d08ce66c43c9b1a2a99d97f0e44a8a7633731d640d6`.
Both changed modules pass fresh bounded imports. After the frozen primary run
finished, promotion verified unchanged primary baselines for 13 changed tracked
files and copied their qualified bytes. A new full run is required after promotion.

### Cached operations and qualification counting follow-up (2026-10-11)

`kazma acquisition status [--job ID] [--json] [--language en|ar]` and the TUI
Settings cached-status panel use one shared read-only projection. Show saved
job failures, reported response tokens (including zero), request reservations,
unknown charges and cached account freshness even after returning to local-only
mode. Capacities are explicitly limited to the inspecting Python process.
The terminal panel owns one joined read through repeated cancellation and
relabels cached results when the operator changes language. Provider text is
rendered literally. Neither surface polls providers or refreshes balances.

Cached readiness also had an unintended live-search route: the legacy
`KAZMA_RESEARCH_PREFLIGHT_LIVE` flag overrode `probe_search=False`. Only an
explicit `probe_search=True` now performs that probe; default/health/API reads
open the read-only diagnostic scope. Configured SearXNG credentials are omitted
from readiness. Real API, SQLite reopen, CLI dispatch and headless Textual tests
cover these boundaries, with original environment/write-scope controls.

The previous full run, `evidence/acquisition_full_repository_final_2026_10_11.log`,
exited 0 after recovery in 1647s. Its raw **16,159 passed, 67 skipped,
4 deselected** total is inflated: chunk 00 printed **1,826 passed, 5 skipped,
2 deselected**, then failed to exit before its 1260s bound. Recovery counted
those files again. Subtracting that duplicate receipt gives a derived
**14,333 passed, 62 skipped, 2 deselected**, not a new clean-run certificate.
The runner now excludes a recovering chunk's preliminary tally while retaining
its observed failure identities. Deterministic timeout/crash controls execute
the actual runner entry point and demonstrate both the original overcount and
the requirement that a clean retry cannot erase an observed failure.

The first cached-surface run recorded 47 passed and 2 failed because its test
queried a nonexistent Textual `Static.markup` property. The replacement checks
the actual rendered literal markup. The first import check also named a
nonexistent `kazma_tui.widgets.settings_panel`; the corrected check imports
the real `kazma_tui.settings_panel`. Preserve those failed evidence logs.
The first broader run recorded 161 passed and 1 failed: a new env-loader patch
target exposed a copied value import in the Hub entry point. The entry point now
looks the loader up on its module; no debt baseline was raised. Failure
preservation also covers nested split pieces, minus-suspect recovery and
per-file retries, not just the initial chunk.

Final owning gates: `evidence/acquisition_cached_status_final_gates_2026_10_11.log`
records **244 passed** in 142.45s; metrics/reference gates record **42 passed**
in 22.16s in `evidence/acquisition_cached_status_metrics_gates_2026_10_11.log`.
These selections overlap; do not sum them. All eight changed CLI/TUI/readiness
modules pass fresh bounded imports in
`evidence/acquisition_cached_status_imports_final_2026_10_11.log`.
Final collection contains **14,438 cases**, input fingerprint
`079d6fe595b60d4c44d0ad1977c0c2344326eea583adcca17c9d138af2524344`.
That receipt records collection, not passing tests. Promotion checks cover 23
tracked files against unchanged primary baselines. Full-run qualification uses
`evidence/acquisition_full_repository_counted_2026_10_11.log` after promotion.

This phase closes cached CLI/TUI inspection, not pipeline effect recovery,
durable origin/robots state, browser OS network isolation, whole-install limits,
all Settings/chat actions or release/soak qualification.

### Historical next implementation details (2026-10-11; superseded)

- **Jina reported usage contract.** Its maintained Reader implementation emits
  `X-Usage-Tokens` and accepts `X-Token-Budget`. The observation adapter above captures strictly validated
  provider-reported token observations on every attempt, including unusable
  content and cancellation after headers. Preserve absent/invalid observations
  as unknown; never infer cash charges or a remaining balance from tokens.
  Durable receipts and consumer projections now carry those observations.
  Provider-enforced token budgets and defensible financial caps remain open.
  [Reader implementation](https://github.com/jina-ai/reader/blob/main/src/api/crawler.ts),
  [options contract](https://github.com/jina-ai/reader/blob/main/src/dto/crawler-options.ts).
- **Firecrawl charging and response versions.** The current billing contract
  distinguishes returned documents from outright scrape failure; HTTP success
  alone is insufficient. PDF and provider-routed targets can add costs. The
  current v2 scrape schema does not establish a universal total `creditsUsed`
  field; an optional provider cost component cannot be treated as the complete
  bill. Keep v1/self-hosted capability handling separate, preserve unknown
  charges, and qualify explicit reported origin error statuses before accepting
  hosted content. Financial caps require a defensible bound or provider-enforced
  limit; do not relabel request reservations as such a cap.
  [Billing](https://docs.firecrawl.dev/billing),
  [v2 scrape contract](https://docs.firecrawl.dev/api-reference/endpoint/scrape).
- **Research durability.** `research_session` persists panel rows; it does not
  journal every search/acquisition/save effect. `research_pipeline` saves its
  acquisition manifest after each bounded batch, including gap fills. `crawl_site` now has the shared durable
  ownership/frontier/receipt/publication contract described above. Extend the
  contract to pipeline search/acquisition/synthesis effects and bounded session
  scheduling. Preserve frozen output/policy, scoped surfaces and pending-only
  explicit resume. PostgreSQL process-death and broader publication/storage
  failure qualification remain required; a retained panel row is insufficient.
- **Native browser migration.** The native automation skill still owns a
  separate long-lived Playwright context and requires its own migration to the
  contained owner and metered broker. Preserve approved interactions and
  per-session state while closing native egress gaps; the scraper's read-only
  GET/HEAD broker cannot simply replace interactive POST behavior. OS network
  isolation remains separate from deny-proxy and browser flag defenses.

### Acceptance sequence (2026-10-12; final receipts below)

The earlier sequence is historical. Session reconciliation/retention, durable
origin and robots scheduling, frozen explicit-key/local research routes,
native browser migration, Settings/CLI/TUI credit actions and retained output
allowances are implemented in this checkout. The acceptance sequence was:

1. Complete the frozen repository suite, separate browser E2E checks, final
   compile/import/static/documentation checks and guarded primary transfer.
2. Measure additional PostgreSQL deep-pipeline process-death recovery and a
   bounded repeated-browser lifecycle profile, preserving failed receipts.
3. Keep unsupported configurations visibly refused: strict Windows networking,
   ambient research credentials and remote dispatch with unprovable monetary
   caps. Compatibility mode is an explicit weaker option.
4. Retain explicit release limits: no paid-provider contract smoke calls,
   no universal hostile-site/robots compatibility certification, no volume-wide
   vector/WAL/temp/user-file quota, and no production workload soak or rollout.

Self-hosted Firecrawl compatibility requires trusted-local endpoint/capability
fixtures before any relaxation of the current private-service refusal.

Standalone extract/screenshot publication has a conservative shared lifetime
allowance of 256 files/512MiB and 24MiB per file. Reservations precede writes
and are retained on failure; no automatic refund or destructive cleanup is
performed. Existing standalone output is preserved and is not retroactively
inventoried by this ledger. Knowledge SQLite text/FTS/history has a 1GiB file
ceiling; a full database refuses writes. These controls do not bound all Chroma
vector files, SQLite temporary/WAL space or unrelated user files.

## Qualification receipts for the 2026-10-12 follow-up

- Recovery/native integration: **120 passed** in 57.60s;
  `remaining_research_native_retry2_2026_10_12.log`.
- Expanded initial run: **739 passed, 6 failed, 4 skipped**; retained as a
  failure receipt. Repairs preserve the original SQLite full error, existing
  extracts, identical child policy and explicit legacy-worker qualification.
- Repair run: **235 passed, 2 failed** (debt gates); new broad cleanup was
  replaced with a finally block, and retired production helpers made private.
- Real PostgreSQL: **4 passed** in 13.32s;
  `remaining_postgres_2026_10_12.log`. Owned ephemeral container removed.
- Strict Linux namespace fixture passed; initial sysfs namespace check refusal
  retained in the first receipt, then fixed to query kernel interfaces. The
  first strict Chromium root-mapping failure is retained; current-user mapping
  preserves Chromium sandboxing. Seven final kernel/Chromium checks passed in
  `remaining_linux_browser_strict_retry_2026_10_12.log`; delegation removed.
- Frozen acquisition regression: **823 passed, 5 skipped** in 240.09s;
  `remaining_frozen_regression_2026_10_12.log`.
- Long Windows command-line handling uses an owned pytest argument file; the
  real-child regression passed. New chat-tool safety/catalog checks passed
  **101 tests** in 7.97s (`remaining_tool_tiers_2026_10_12.log`).
- Full serial repository unit run completed in **58m40s** with **14,435 passed,
  13 failed, 63 skipped and 2 deselected**. Its input fingerprint was
  `e7e3dbdffa427b69afdc3f1e5552a46cb8b72d0ba1324ddbe98a8cc1a446ed28`.
  This is a pre-repair failure receipt, retained in
  `remaining_full_repository_retry_2026_10_12.json` and
  `remaining_full_retry_chunk_000_2026_10_12.log`; it is not a green full-suite claim.
- All thirteen failures were repaired. **361 tests passed** in 169.98s,
  covering every failed file, import integrity, real browser contexts and
  artifact recovery (`remaining_full_failures_repaired_2026_10_12.log`).
  Repairs make the credits POST literal, declare dynamic runtime writes and
  cancellation readback, provide both tool-description languages, explicitly
  roll back session transactions and keep recovery fixtures on identical policy.
  The ordinary-command fixture uses a tiny isolated Git workspace. The unused
  legacy browser guard and its implementation-only tests were retired; real
  fresh-context broker coverage remains.
- Artifact restore rejects non-regular descriptors before reading and decodes
  the same bounded bytes whose hash was checked. A real Linux FIFO blocks at
  the former boundary and is immediately refused at the repaired boundary
  (`remaining_fifo_boundary_2026_10_12.log`). Report inventory refuses special
  files too. This repair was added after the full unit run and is qualified by
  the subsequent focused receipt, not its earlier tally.
- PostgreSQL: **4 tests passed plus two deep-pipeline process-death drills**,
  planning and partially published source recovery, through an owned disposable
  local container (`remaining_postgres_deep_2026_10_12.log`). Initial standalone
  harness retries used a sanitized status row where the raw journal was needed,
  then hit a Windows temporary-file cleanup lock. Containers were removed;
  temporary scratch was retained where removal was blocked. These harness
  failures are preserved and do not count as passes.
- Final Python/JavaScript syntax check: **2,119 Python files and 122 JavaScript
  files, zero errors** (`remaining_compile_static_final_2026_10_12.log`).
- Refreshed collection: **14,502 cases**, not passing tests; fingerprint
  `fdd7fe9b309f83bba34685aaef1d3cfd38753bfb4cd48a9f0a028ae9a8d0b829`.
  This collection predates the archive and credit-error regressions. The final
  collection and remaining terminal receipts are recorded below.

The public provider contracts were rechecked on 2026-10-12 without account
calls: [Firecrawl credit usage](https://docs.firecrawl.dev/api-reference/endpoint/credit-usage)
documents the authenticated team's `remainingCredits` field at
`GET /v2/team/credit-usage`; [Jina Reader](https://jina.ai/reader/)
documents `X-Token-Budget` as a per-request token ceiling. Neither contract makes
Kazma's local reservations a monetary charge.

Paid provider smoke calls were not made. Cached status and deliberate refresh
adapters are qualified with fixtures; no live account charge or balance is
claimed. The strict Windows route remains refused, and a compatibility-mode
Chromium pass must never be presented as strict Windows sandbox certification.


### Browser E2E follow-through and archive repair

The serial 28-file E2E run completed in 26.10 minutes with **83 passed and
1 failed**, on unchanged input
`fdd7fe9b309f83bba34685aaef1d3cfd38753bfb4cd48a9f0a028ae9a8d0b829`.
The failure was the populated Research archive in the Arabic page suite, not
the session API: an accidentally duplicated resume-button line referenced
`isSession` and `actions` outside their scope, and the archive loader's catch
left the list blank. Read-only browser/API diagnostics are retained in
`remaining_arabic_research_diagnostics_2026_10_12.log`.

The stray archived-renderer line was removed. Restore remains the explicit
archived action; interrupted active sessions keep their Resume control.
`tests/js/test_research_archived.js` drives the real loader with saved and
interrupted rows in English and Arabic. Its two negative controls reproduce
the former blank archive. All four Node cases passed. The full five-test Arabic
browser file then passed in **96.65s**
(`remaining_arabic_research_repaired_2026_10_12.log`). The original 83/1 receipt
is preserved in `remaining_e2e_2026_10_12.json`; it has not been rewritten as a
fresh green 28-file run. Together the original unaffected files and repaired
file cover all 84 E2E cases; no duplicate passes are added to that case count.

## Final local acceptance (2026-10-12)

The implementation is ready for review in the primary checkout. Default
acquisition remains local-only. Third-party use requires explicit policy;
cached status shows errors, usage and unknown/stale credits without making
account calls. Credit-refresh HTTP failures now include their status and an
actionable hint. Secret provider response bodies are excluded. The shared
credit/operations/JavaScript qualification passed **98 tests** in 12.30s
(`remaining_credit_errors_and_js_final_2026_10_12.log`).

The final 67-file acquisition, recovery, browser, JavaScript, import, static and
documentation regression completed: **1101 passed, 5 skipped, 1 warning in 345.23s (0:05:45)**
(`remaining_regression_final_2026_10_12.log`). Its collection-input fingerprint is
`6f500d98f9b42db9b44c9e3abe238c3bc18805ceba655056ba4b4674dda394e7`. Collection found **14,508 cases**;
collection is not a passing-test count. The initial 67-file run recorded
1,100 passed, 1 failed and 5 skipped in 405.62s: the HTTP cancellation fixture
timed out waiting for its child-ready marker. Its isolated file passed 13/13,
and the preceding origin/proxy/research ordering passed 279 tests with 1 skip.
The final repeat adds a failure-only task-state diagnostic hook. No product
change or timeout increase was made to hide the earlier failure; its cause
was not established. The original receipt is retained as
`remaining_regression_initial_2026_10_12.log`. The full repository run's original
13 failures and the E2E run's original failure remain recorded above. Each
failed file passed after repair; no fresh green whole-repository run is claimed.

Every product module was also imported in its own fresh interpreter, serially:
**985/985 passed** in 40 bounded batches
(`remaining_fresh_imports_all_2026_10_12.log`). Final syntax qualification covered
**2,119 Python and 123 JavaScript files**, with zero errors
(`remaining_compile_qualified_2026_10_12.log`).

Repeated browser ownership checks used local in-memory pages, with no external
requests. Windows compatibility mode completed **69 cycles /
138 pages** in 243.5s; strict Linux completed
**60 cycles / 120 pages** in
241.3s. Maximum sampled owned RSS was
395.3MiB and
608.9MiB respectively.
Each cycle verified closed child processes, released capacity and no
quarantine; Linux also verified removed cgroups. Positive-control reachable
host IPv4 and IPv6 loopback listeners were refused from the isolated namespace.
These four-minute checks are bounded lifecycle evidence, not a production soak.
The initial five-minute Windows fixture exceeded the runner's 300s ceiling
including startup/cleanup; that failed attempt remains recorded separately.
The first Linux probe expected a normal launcher exit, although its contained
guard deliberately group-kills itself when the child exits. Both socket refusal
records were present. The corrected harness requires SIGKILL and both explicit
refusal records; the initial failure receipt is retained.

The transfer preserves unrelated primary changes and existing line endings,
with a baseline guard and an external recoverable snapshot. No commit, push,
live-install restart or deployment was performed.

### Remaining release limits

- Strict browser network isolation is qualified on Linux; Windows strict mode
  refuses execution. Windows compatibility mode is explicitly weaker.
- Live paid-provider balance/charge smoke checks and production workload soak
  remain release validation. Fixtures do not establish a live account balance.
- Policy editing uses documented environment variables; Settings presents
  status and explicit credit refresh, rather than a policy editor.
- Self-hosted Firecrawl private-service support, a volume-wide storage quota
  and ambient-cloud-credential durability remain unsupported extensions.
- Site refusals, unavailable robots policy and partial results stay visible;
  universal hostile-site compatibility is not claimed.

Automatic approval review blocked recursive temporary-fixture cleanup. Scratch
was retained; disposable PostgreSQL containers were removed.


## Release qualification follow-through (2026-10-12)

The first complete primary-checkout run included slow tests and all 1,011
unit-test files, in 21 serial batches. It finished in 3,662.2s with **14,443
passed, 4 failed and 63 skipped**, on unchanged collection inputs
`6f500d98f9b42db9b44c9e3abe238c3bc18805ceba655056ba4b4674dda394e7`.
The terminal receipt is `release_full_final_2026_10_12.json`; batch logs retain
every failure. This is a measured failed run, not a clean release certificate.

One failure was a Windows parent-death fixture whose filtered child environment
dropped the explicitly selected compatibility mode. A separate controlled child
reproduced the resulting strict-mode refusal before Chromium launched
(`release_browser_fixture_diagnostic_2026_10_12.log`). The fixture now explicitly
selects compatibility for its Job Object cleanup proof; production strict mode
is unchanged. All **14 browser-budget tests passed** after that correction
(`release_browser_fixture_repaired_2026_10_12.log`). The structural-debt baseline
was lowered from 551 to 550; all **5 debt checks passed**
(`release_debt_ratchet_2026_10_12.log`).

The document-generation file reported a parser-child timeout, and the KB API
fixture did not reach its terminal progress receipt within five seconds. Both
files passed together unchanged in isolation: **20 passed, 1 skipped in 26.37s**
(`release_failed_files_initial_2026_10_12.log`). Their causes were not established.
The complete run sampled a peak 2.90GiB owned RSS and a minimum 2.92GiB free host
memory. Those observations do not prove memory pressure caused either failure.
The next run adds an external guard: require 8GiB free before launching a batch
and stop only its owned pytest tree after three samples below 4GiB. Such a stop
is an unmeasured failure, never a passing retry. Product timeouts remain intact.

### Actual Firecrawl account observation

One authenticated `GET /v2/team/credit-usage` was made using the configured live
credential in a disposable diagnostic process. The adapter observed a fresh,
finite team credit balance with no error
(`release_firecrawl_live_2026_10_12.log`). The installed acquisition policy and
accounting store were not changed. The published receipt omits the credential,
account identifier, raw response and balance amount. This qualifies the actual
balance adapter; it does not establish a per-request monetary charge. No paid
content crawl was made. Unsupported charges and balances remain unknown.

The fresh 28-file browser E2E run, clean complete-suite repeat, continuous
30-minute Windows/Linux browser profiles and guarded live rollout are in
progress. Final terminal results will be recorded here before publication.


### Fresh browser E2E result

All **84 cases passed across all 28 files** in 35.19 minutes, with unchanged
collection-input fingerprint `74bcdb2a47115eb449e2f14a919722664c89addb88fa27c342b5d7f000013312`
(`release_e2e_2026_10_12.json`). This fresh run includes all five Arabic page
checks and the populated Research archive, all eight navigation/Settings checks,
turn approvals, concurrency and restart recovery. The earlier 83/1 receipt is
retained separately; this terminal result does not rewrite it.


### Complete-suite repeat interrupted for diagnosis

The repeat measured **3,395 passed and 21 skipped** in its first five batches,
including the corrected Windows browser fixture in the original file order.
Batch 6 then aborted on the 120-second pytest deadline while the fourth test
in `test_document_actions_phase8.py` awaited an isolated HTML parse. It emitted
no terminal tally and is **unmeasured**, not a passing batch. The owned run was
stopped before spending further time on a qualification that already failed;
all of its owned children were reaped. The original partial receipt remains
`release_full_verified_2026_10_12.json`. The resource guard did not trip.
This failure has not been attributed to memory pressure or hidden by a larger
product timeout. Original-order child-stack diagnostics are being collected.

The same 50-file batch then passed in its original order: **701 passed and
11 skipped in 611.09s** (`release_document_order_probe_2026_10_12.log`).
External failure diagnostics instrumented child stacks without changing
tracked source or product/test deadlines. No parser timeout recurred. The
original cause remains unresolved; this is recovered full-suite coverage,
not a fresh uninterrupted green full run. Qualification continues with the
remaining 15 batches on the same collection inputs, retaining the original
unmeasured attempt separately and counting the recovered files only once.

An optional external progress hook caused a separate harness interruption in
batch 11: it used the test's current directory while an MCP fixture deliberately
changed it. The internal-error receipt (`release_full_recovered_2026_10_12_batch_010.log`)
is retained and its partial 200-test tally is excluded. The hook now anchors its
output to its own file, tolerates optional progress-write failures, and passed a
controlled changed-directory proof (`release_progress_hook_repaired_2026_10_12.log`).
Tracked production and test source did not change. The first ten terminal green
batches remain qualified; batch 11 and the interrupted following batch are rerun
in full. This instrumentation mistake is separate from the unresolved parser flake.


### Terminal complete-suite sweep and targeted recovery

The serial campaign reached all 21 planned batches and 1,011 unit-test files
on unchanged inputs `74bcdb2a47115eb449e2f14a919722664c89addb88fa27c342b5d7f000013312`.
Its raw terminal manifest is `release_full_completed_2026_10_12.json`: **13,628
passed, 57 skipped, 1 failed**, with one additional 50-file batch unmeasured.
The failure is `test_metrics_receipt_current`: the generated collection receipt
still names the earlier input fingerprint, before the two fixture corrections.
It requires real regeneration and a passing receipt check; it is not silently
removed from the raw result.

The unmeasured batch was stopped by the external guard after host free memory
stayed below 4GiB for three one-second samples. The ten-second sampler observed
falling host headroom while owned RSS was about 2.3GiB; those samples do not
establish which process caused the pressure. Host headroom recovered to about
28GiB, and later batches completed. The interrupted batch's partial results are
excluded. Its separate guarded recovery additionally records owned private
memory and the largest private-memory processes only if headroom falls below
8GiB. No product timeout or test deadline was widened.

The final continuation's 2,745.2s measures its own owner, not the whole campaign: earlier
qualified batches and the documented original-order parser replay have separate
receipts. A derived accepted coverage manifest will retain those raw attempts
and count each recovered file once. An uninterrupted green full run is not claimed.


### Complete repository coverage accepted with recovery

The resource-interrupted 50-file batch passed separately: **818 passed and
6 skipped** (`release_resource_recovery_2026_10_12.log`). Its 35 resource samples
retained at least **22.95GiB** available host memory; maximum sampled owned RSS
was **2.48GiB**, and owned private memory **3.53GiB**. The guard did not trip.
These observations do not identify the cause of the earlier pressure.

The metrics generator performed a real, owned, low-priority collection and
refreshed `tests/fixtures/repository_metrics_collection.json` to the unchanged
current fingerprint. It found **14,508 collected cases**, not a passing count.
The formerly failing one-case receipt check then passed
(`release_metrics_receipt_recovery_2026_10_12.log`).
The campaign's 63 skip outcomes include two collection-level module skips
(batches 2 and 16), which are not collected cases: 14,447 passed plus 61
case-level skips equals the 14,508-case collection. Counts are not added across
these different measurement categories.

The explicitly derived `release_full_accepted_2026_10_12.json` covers all
**1,011 unique unit-test files: 14,447 passed and 63 skipped**. It preserves links
to the raw failed sweep, the interrupted batch and the targeted recoveries; each
file contributes once. The source/test collection fingerprint remains
`74bcdb2a47115eb449e2f14a919722664c89addb88fa27c342b5d7f000013312`.
This is complete serial campaign coverage with recovery, not an uninterrupted
green full run. Earlier document-parser and KB timing failures remain recorded;
passing replays do not establish their causes.

All **98 changed production files**, including the changed UI assets, still
match the qualified worktree (`release_production_mirror_2026_10_12.log`); the
51 promoted production paths also match their immutable transfer snapshot.
The fresh **84/28 browser E2E** result remains valid. Continuous 30-minute
Windows compatibility and strict Linux lifecycle profiles are now running
sequentially before the guarded rollout.


### Long-profile cleanup contract and preserved first attempt

The first 30-minute Windows compatibility attempt stopped after **73.4s** on a
classified five-second graceful-close deadline. Its original receipt is retained
as `release_windows_soak_initial_2026_10_12.log`, with the original pipeline log
beside it. The owning process tree closed; the product reported forced tree
termination and released capacity. That message alone is not sufficient proof
of cleanup, and this failed attempt is not a completed endurance result.

The external endurance harness now distinguishes that exact classified cleanup
deadline from uncertain cleanup or page-work failure. A deadline is counted only
after independent assertions confirm the OS group closed, all observed children
exited, no descendants remain, the driver/lease/Playwright objects and local deny
endpoint are cleared, and active/quarantined capacity is zero. Linux additionally
requires cgroup removal. Every accepted forced cleanup emits a visible event,
and the final receipt counts those events. Unknown failures still abort the run.
The production five-second grace period and all production/test sources are
unchanged. This qualifies bounded lifecycle and cleanup behavior on local pages;
it does not establish uninterrupted graceful closure or a 30-minute public-site
crawl. The revised profile and acceptance harnesses are archived as
`release_soak_runner_2026_10_12.txt` and `release_verify_receipts_2026_10_12.txt`.


The first revised harness stopped after **26.0s** when an immediate psutil
`is_running()` observation still reported a Chromium child after the Job Object
reported zero active processes. That failed receipt remains
`release_windows_soak_exit_observation_2026_10_12.log`. The harness now uses the
same bounded three-second `psutil.wait_procs` exit verification as the existing
real Chromium hung-close regression; all observed processes must be gone before
continuing. This changes the external observation only, not production cleanup
or the five-second grace period. Any persistent child still aborts qualification.


The bounded-exit-wait attempt then failed after **717.6s** with an actual
unverified-cleanup error. Its raw receipt is
`release_windows_soak_unverified_cleanup_2026_10_12.log`. Two earlier graceful
cleanup deadlines had independently verified exits, but the later uncertainty
correctly aborted the run and retained quarantined capacity. The outer owned
process tree closed. This is a failed endurance gate, not a completed profile.
External teardown diagnostics now capture the caught exception and pipe-thread
state; no production timeout, test source or acceptance requirement was relaxed.
Rollout remains withheld while this uncertainty is investigated.


The diagnostic replay failed after **107.5s** and captured the underlying
`subprocess.TimeoutExpired` at the driver's five-second `proc.wait`, after the
Job Object close returned; Node had not completed exit and the stderr reader
remained alive. See `release_windows_soak_diagnostic_2026_10_12.log`. The product
correctly retained quarantined capacity. Its timeouts and source remain unchanged.
Windows compatibility rendering is **not endurance-qualified** and is excluded
from the release's supported qualification. The shipped Windows strict mode
continues to refuse browser launch; local HTTP remains available. A real strict
refusal check must demonstrate zero new child processes and no retained lease.
The release gate now explicitly qualifies strict Linux rendering and local HTTP,
while retaining the failed Windows compatibility profile as a release limit.
This is a supported-route qualification, not universal industrial certification.


The real Windows strict-mode check passed: launch was refused before creating
any child, all browser objects/deny endpoint were cleared, and active/quarantined
capacity was zero (`release_windows_strict_refusal_2026_10_12.log`). Its first
external assertion incorrectly required the literal word Windows in the generic
unsupported-host message; that harness failure remains
`release_pipeline_windows_strict_refusal_expectation_2026_10_12.log`. The corrected
check verifies the actual unavailable code and launch-refusal contract. Product
source remains unchanged. Strict Linux endurance qualification is now running.


The first long strict Linux attempt stopped at **1,783.9s** on an external
RSS-sampling race: a renderer exited between `is_running()` and `memory_info()`,
raising `psutil.NoSuchProcess`. The failed receipt remains
`release_linux_soak_sampling_race_2026_10_12.log`; cgroup removal completed.
Completed cycles had verified teardown and no graceful-close deadlines. Optional
RSS sampling now tolerates only processes that disappeared during sampling;
access failures and mandatory lifecycle/exit assertions still fail. No product
or tracked test source changed. This attempt does not satisfy the 30-minute gate;
a fresh continuous profile is required.


## Supported-route release acceptance (2026-10-12)

The supported routes passed the serial release acceptance contract. The frozen
collection-input fingerprint is
`74bcdb2a47115eb449e2f14a919722664c89addb88fa27c342b5d7f000013312`.
`release_acceptance_2026_10_12.log` requires measured complete coverage across
1,011 unique unit-test files (14,447 passed, 63 skipped), fresh browser E2E
coverage (84 passed across 28 files), strict Linux endurance, real Windows
strict refusal, local public HTTP extraction and the isolated credit-adapter
observation. Recoveries are counted once and original failed/interrupted receipts
remain published. Parser/KB timing causes remain unresolved; this is not an
uninterrupted green campaign or universal industrial certification.

The final continuous strict Linux profile passed **1,801.7 seconds, 522 cycles
and 1,044 local DOM pages**, with zero graceful-cleanup deadlines and zero
external requests. Both host-loopback families were refused inside the network
namespace. Each completed cycle verified all observed children exited and all
browser objects, leases, endpoints and capacity were cleared. Final active,
waiting and quarantined capacity were zero; the outer tree closed and delegated
cgroup was removed. Maximum sampled owned RSS was 649,179,136 bytes (about
619 MiB); owner threads changed from one to two. These are local browser
lifecycle/load results, not a 30-minute public-site crawl. The raw result is
`release_linux_soak_2026_10_12.log`.

Windows compatibility remains **failed and unqualified**. The diagnostic captured
Node exceeding the five-second exit wait and correctly quarantining capacity;
production timeouts and acceptance criteria were not widened. Windows strict
mode is the shipped default and the real refusal check created zero children
and retained no lease. Local HTTP works independently of browser availability.

The real public-page smoke read `https://example.com/` through HTTPX with robots
enforcement, usable untruncated text, HTTP 200 and **zero hosted attempts**
(`release_local_http_smoke_2026_10_12.log`). Its first external setup attempt
passed a mapping to the triple-list ConfigStore API and failed before any
network request. The corrected helper uses `set`; the original setup traceback
is retained in `release_pipeline_public_http_initial_2026_10_12.log`. Product
source was unchanged. The isolated store's generic secret detector also warned
about the internal robots coordination token and vetoed clearing that marker;
policy expiry and saved rules still controlled this successful request. The
raw diagnostic is preserved without claiming it establishes broader site
compatibility.

One real Firecrawl balance observation was made in an isolated store without
changing the live policy. Its freshness refers to that observation time;
unsupported per-request charges remain unknown. Saved reservations are not
billed credits, and a monetary ceiling refuses an unprovable cost.

Final documentation, policy, debt and collection-receipt gates passed: **87
checks across nine files**, with the same frozen fingerprint
(`release_final_gate_summary_2026_10_12.log`). Guarded deployment is recorded
below when completed. Qualification does not enable remote providers or Windows
compatibility mode on the installed instance.
