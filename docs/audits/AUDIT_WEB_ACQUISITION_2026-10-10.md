# Web scraper/crawler audit and local-first remediation plan

Current implementation and measured qualification are recorded in the
[implementation report](./WEB_ACQUISITION_IMPLEMENTATION_2026-10-10.md).
The findings, proposed waves and remaining-work statements below preserve the
2026-10-10 baseline; consult that report for the 2026-10-12 fixes, receipts and
explicit release limits.


Date: 2026-10-10 (Asia/Kuwait). Audited revision: `9796e53fbd66bb1ce827f9ccb90bf1e11f58fe53`.
Status: baseline audit with remediation in progress in the development checkout.
See [implementation progress](./WEB_ACQUISITION_IMPLEMENTATION_2026-10-10.md) for verified changes and remaining acceptance gates. Findings and line references below describe the audited revision, not a claim that every defect remains unchanged.

## Verdict

The current stack is a useful bounded research crawler, but it does **not yet meet the requested industrial, fail-safe, local-first standard**. Its own research crawler describes itself as not an enterprise crawler. The most urgent problems are potential deletion of retained Knowledge Library content during incomplete refreshes, ineffective parts of the connected-peer checks, and third-party recovery without a single enforced policy or usage ledger.

Keep and harden the existing stack. Replacing it with another crawler would not automatically fix retention, network policy, cancellation, billing, or presentation. No crawler can guarantee access to every site: an explicit blocked/partial outcome must be acceptable.

“Local” in this plan means that HTTP fetching, extraction, rendering, scheduling, caching, and storage run on the operator's machine. Fetching public sites still requires network access. A locally hosted SearXNG still sends searches to upstream engines. LLM synthesis and KB embedding are separate provider choices; local crawling must not be advertised as making those local automatically.

## Scope and evidence

Reviewed:

- Chat tools: `tools/read_url.py`, `tools/web_research.py`, and the native `advanced_web_crawler` aliases.
- Shared acquisition: `web_acquire/{fetch,serp,profiles}.py`, proxy factories/providers, SSRF validation/pinning, browser egress, and the Windows browser-loop bridge.
- Knowledge ingestion: discovery, sitemap/llms.txt/BFS paths, rendering, indexing/pruning, durable job snapshots, Web API and progress UI.
- Research pipeline acquisition, readiness, search fallbacks, and current documentation.
- Binding repository guidance, including AGENTS.md §§13, 24B, 26, 32 and server-management restrictions.

Validation:

- **77 existing tests passed** in 98.23 seconds: web acquisition, read-url hardening, knowledge discovery, smart reindex, KB jobs, proxy coverage, browser-context guards, pin authority, and audit-wave-8 gates. This included the existing real Chromium check that successive contexts block literal loopback navigation.
- **15 offline baseline witnesses passed** in 2.30 seconds, after correcting a witness's expectation for trailing-slash canonicalization. See [archived audit witnesses](evidence/web_acquire_2026_10_10.py). These deliberately assert the audited defects; they are evidence, **not passing remediation acceptance tests**. They run explicitly, outside normal test discovery paths.
- Reproductions use mocked HTTP/provider/browser/index boundaries and isolated test stores. The real httpcore stream adapter is exercised without opening a socket for the peer-info-key witness.
- No live-install changes, server restart, deployment, real credential inspection, provider billing calls, or paid scrape requests were performed. This is a code-and-targeted-test audit, not a production load certification or proof of an exploited SSRF.

Existing strengths to preserve: URL validation with unresolved-host refusal, direct-path IP pinning in `read_url`, browser-context egress routes, safe XML parsing, text extraction, a streamed response cap on the main HTTP path, page/depth presets, research paging/cache, workspace containment, untrusted-content fences, KB content deduplication, progress reporting, and restart-interrupted job snapshots. Native `crawl_page` delegates to core `read_url`; it is not a separate scraper implementation.

## Findings

P1 means high priority before industrial qualification. P2 means reliability/coverage work required for that qualification. “Reproduced” describes a deterministic baseline witness, not a live incident.

| ID | Priority | Finding and consequence | Evidence |
|---|---|---|---|
| WA-01 | P1 | **Incomplete/capped refresh can prune good KB content.** `ingest_site` slices discovery to `page_cap`, then unconditionally passes that sliced list to `_prune_gone_urls`. Previously indexed, in-scope URLs outside the cap become deletion candidates. Sparse fallback discovery has the same absence-versus-deletion ambiguity. | `stores/knowledge_ingest.py:1518`, `:1613`, `:1685`; `knowledge_index.py:369`. Reproduced: a three-page library refreshed with cap=1 sends the other two pages to pruning. |
| WA-02 | P1 | **Connected-peer protection has two independent defects.** The shared reader asks httpcore for `peername`, while installed sync and AnyIO adapters expose `server_addr`. Missing info is accepted. Separately, `_get_capped` constructs a replacement `httpx.Response` without the original extensions, dropping peer metadata before the caller checks it. | `security/ssrf.py:197`, `:224`; `tools/read_url.py:182`. Both reproduced. Main direct reads still have IP pinning, so this does not establish that every direct read is exploitable. |
| WA-03 | P1 | **Research link HTML and KB discovery lack direct-path IP pinning.** They discard `validate_url`'s resolved addresses, use a non-pinned client, and rely on the defective peer check. The HEAD redirect resolver validates DNS but does not assert the actual peer on each request. Browser routes also validate before connection; they do not pin browser DNS. | `tools/web_research.py:139`; `stores/knowledge_ingest.py:473`; `security/ssrf.py:279`; `security/browser_egress.py`. Static control-flow review. |
| WA-04 | P1 | **Local-first is not the effective policy.** A Firecrawl key enables remote pre-fetch and early KB mapping. Recovery tries Jina before the local browser unless explicitly disabled. `KAZMA_FETCH_BACKEND=httpx` only skips pre-backends; it does not prohibit remote recovery. KB has an additional default-on Jina policy. | `tools/read_url.py:772`, `:804`; `stores/knowledge_ingest.py:181`, `:715`. Three policy witnesses reproduced. |
| WA-05 | P1 | **No acquisition usage/credit accounting.** Firecrawl/Jina helpers return only text or `None`; `FetchResult.backend` stays empty and attempt metadata is not supplied. No acquisition ledger, credit balance retrieval, per-job provider budget, or proxy-bandwidth billing reconciliation was found in the reviewed paths. Failure of a paid service can be hidden by successful fallback. | `tools/read_url.py:618`, `:655`; `web_acquire/fetch.py:21`; proxy and KB job records. Static review. Existing LLM token/cost accounting does not account for scraper services. |
| WA-06 | P1 | **Browser ownership is process-global.** KB jobs write `_pw_shared`; overlapping scopes can replace and close another job's context, sharing cookies/storage and leaving the earlier context unowned. Browser launches explicitly disable Chromium's sandbox. Contexts do not block service workers, which are a documented limitation of route interception. | `stores/knowledge_ingest.py:1076`, `:1079`, `:1186`; `tools/read_url.py:533`. Overlapping ownership reproduced. Sandbox/service-worker concerns verified against official Playwright docs; bypass exploitation was not tested. |
| WA-07 | P1 | **Resource limits cover only part of the stack.** Research HTML discovery uses buffered GET; KB discovery buffers bodies and calls unrestricted `gzip.decompress`; Jina/Firecrawl responses and browser DOM/text have no comparable cap. BFS queues have no frontier/link bound. There is no shared whole-job deadline or global browser/job quota in these paths. | `tools/web_research.py:139`, `:298`; `stores/knowledge_ingest.py:473`, `:912`; provider helpers and browser launch paths. Static review. A page cap alone is not a byte, request, queue, or runtime cap. |
| WA-08 | P1 | **Cancellation does not consistently stop work.** `ingest_site` starts a discovery task but cancels it only under `except Exception`; cancellation/async-generator close bypass this cleanup. The Windows browser bridge similarly does not catch cancellation when awaiting a cross-loop future. Restart restores job snapshots as interrupted, not the page frontier. | `stores/knowledge_ingest.py:1589`; `playwright_loop.py:79`; `stores/kb_jobs.py`. Discovery continuing after cancellation reproduced; cross-loop behavior and restart semantics reviewed statically. |
| WA-09 | P1 | **Research crawling silently overwrites saved extracts.** It uses deterministic URL-derived paths and unconditional `write_text`, so a subsequent crawl destroys edits to the saved file. Unlike `read_url_to_file`, it has no existing-file refusal. Save errors can terminate the crawl without a final page summary. | `tools/web_research.py:344`; compare `tools/read_url.py:1197`. Overwrite reproduced using a temporary workspace. |
| WA-10 | P2 | **Errors lose their useful detail at delivery.** Research crawling collects an `error` field but omits it from its returned index. KB emits error details during fetching, then its final `ProgressUpdate` defaults `errors` to an empty list; API snapshots overwrite the visible list with that empty value. A fully failed site still reaches `phase='done'`; the UI warns, but completion is not a typed failed outcome. | `tools/web_research.py:337`, `:389`; `stores/knowledge_ingest.py:1704`; `kb_api.py:395`; `static/js/kb.js:196`. Missing index reason and cleared terminal errors reproduced. |
| WA-11 | P2 | **Network exceptions miss intended recovery.** Main fetch catches built-in `ConnectionError`/`TimeoutError`; normal `httpx.ConnectError`/timeout/read exceptions reach the generic branch instead. Main HTTP retries ignore `Retry-After`, and only run before redirects; optional-provider errors lose their classification. | `tools/read_url.py:894`, `:1044`. `httpx.ConnectError` skipping recovery reproduced. |
| WA-12 | P2 | **Local sitemap discovery is incomplete.** Index `<loc>` entries are collected as URLs, then `.xml` infrastructure URLs are filtered out instead of recursively fetched. Nested sitemap-only sites fall through to slower discovery or seed-only results. | `stores/knowledge_ingest.py:547`, `:696`, `:808`. Reproduced: child sitemaps were never requested. |
| WA-13 | P2 | **URL identity/scope are inconsistent.** KB canonicalization drops all queries and strips `.md`, potentially merging distinct pages or changing a real source URL. Research checks same-domain before fetching but does not recheck a redirected final URL before content acquisition. Redirect exhaustion in `read_url` lacks a dedicated terminal result. | `stores/knowledge_ingest.py:257`; `tools/web_research.py:305`, `:326`; `tools/read_url.py:919`. Query collapse and external redirect acquisition reproduced. |
| WA-14 | P2 | **Robots and politeness are fragmented.** Research compliance defaults off, retrieves robots through a text-extraction/provider ladder, and fails open for all unavailable conditions. KB reads `Sitemap:` directives but does not enforce `Disallow`. Fixed per-job delays do not coordinate concurrent jobs to the same origin. | `tools/web_research.py:99`, `:287`; `stores/knowledge_ingest.py:547`, `:912`, `:1628`. Static review. |
| WA-15 | P2 | **Readiness/persistence can overstate capability.** Readiness does not qualify Chromium, extraction, local storage, or provider balance. KB persistence helpers log write failure and continue; a durable job can appear successful while status retention failed. Research crawl also fetches HTML separately from extracted content, duplicating network work and possibly observing different page versions. | `tools/research_readiness.py`; `stores/kb_jobs.py:46`, `:61`; `tools/web_research.py:326`. Static review. |
| WA-16 | P1 | **Browser route guards miss HTTP redirect hops.** A public initial request can redirect to a destination that was never checked before contact. Reporting a final scope failure does not undo that contact. | Additional implementation-time actual Chromium witness: an owned origin received both `/start` and out-of-scope `/escape` before the old reader reported `scope_blocked`. Archived failing log: `evidence/browser_redirect_before_broker_2026_10_10.log`. Shared scraper rendering now uses CDP/HTTPX fulfillment; native browser automation still requires migration. |

Additional documentation drift: `docs/docs/guide/web-research.md` advertises `web_acquire.crawl()`, but that package does not export it. The `kb_site` profile declares 200 pages while research `crawl_site` clamps to at most 50; actual KB ingestion has its own cap. Correct the contract without weakening hard limits accidentally.

## Target behavior

### One explicit acquisition policy

Default to `local_only`: direct public-site HTTP + local extraction → bounded local Chromium when required → visible failure/partial result. No Jina, hosted Firecrawl, or residential proxy calls in this mode, even if their keys remain configured. Support an explicit `local_first_with_remote_fallback` policy with a provider allowlist and finite budgets. A deliberate provider-specific override must obey the same egress and budget policy.

Self-hosted acquisition endpoints are explicitly configured trusted services, distinct from arbitrary target URLs. Preserve private-network refusal for crawled targets. Do not weaken SSRF checks to accommodate local SearXNG or a self-hosted extractor. Disable implicit environment-proxy routing unless explicitly admitted by acquisition policy. If a required configured proxy/service is unavailable, report the routing failure; never silently change the promised route. Preserve optional direct behavior only where policy explicitly allows it.

### Structured results and visible truth

Extend the existing `FetchResult` and consumers rather than layering another text-only ladder beside them. Record requested/final URL, HTTP status, backend, proxy identity without credentials, cache/revalidation result, attempts, retryability, elapsed time, received bytes, extraction quality, truncation, policy skips, and a stable actionable error code. `Error:` remains a compatibility presentation at tool boundaries, not the internal decision format.

Separate successful transport from useful content: a CAPTCHA, login screen, empty SPA shell, thin extract, and truncated response must not all count as a healthy page. Preserve raw/structured evidence of status without storing credentials or arbitrary sensitive response bodies in telemetry.

Job outcomes: queued, discovering, fetching, retrying, paused, completed, partial, failed, cancelled, interrupted. Final results retain the failed URL list and reasons. Chat, gateway, Research, and Knowledge Library show the same underlying result; user-visible status does not depend on the LLM choosing to mention a failure.

### Honest third-party usage

Every enabled remote attempt, including map/discovery, retries, anonymous requests, quota failures and ambiguous timeouts, produces an acquisition usage event tied to job/turn and provider. Keep this separate from LLM synthesis and embedding charges.

Show:

- Local HTTP/browser fetches, cache hits, remote attempts and completed pages.
- Remote provider, reason for escalation, units used, and remaining budget.
- Provider-reported credits/balance with account scope and last-refresh time.
- Estimated, reported, unavailable, stale, and unknown values as distinct states. Never display unknown cost as zero.

Firecrawl exposes a documented credit-usage endpoint with `remainingCredits` and billing-period information. Implement a version-aware adapter with cached, single-flight balance refresh; do not spend a provider API call on every UI poll. Its current scrape schema differs from the existing v1 integration, so do not assume every response includes `creditsUsed`. Preserve supported v1/self-hosted installations through capability negotiation and fixtures. [Firecrawl credit usage](https://docs.firecrawl.dev/api-reference/endpoint/credit-usage), [scrape schema](https://docs.firecrawl.dev/api-reference/endpoint/scrape).

Jina documents token-based Reader usage and API-key billing/remaining-token visibility. Capture supported usage/rate-limit metadata, but mark balance unavailable unless a supported programmatic interface is verified; offer the provider dashboard when needed. Anonymous Jina is still third-party egress and rate limited. Do not hard-code current plan pricing or limits as universal. [Jina Reader](https://jina.ai/reader/).

Residential proxy accounting is generally bandwidth-oriented: local byte counts are observations, not automatically the provider invoice. Support provider-reported balances where available and label local bandwidth/cost estimates with their scope and limitations.

Reserve per-attempt units atomically before billable dispatch, with per-job/day ceilings and provider concurrency/rate limits. On an ambiguous timeout, record potential consumption and reconcile; blindly releasing reservations and retrying can overspend. Never add automatic account top-ups. No enabled billing adapter should dispatch when required budget reservation/audit persistence fails.

## Delivery plan

| Wave | Work | Completion gate |
|---|---|---|
| 0 — Protect retained data and boundaries | Fix pruning authority, streamed peer evidence and actual httpcore adapter keys; pin every direct HTTP hop including discovery/HEAD requests; enforce redirect scope; prevent crawl file overwrites. Add adversarial regression tests before each fix. | Capped, partial, failed, interrupted or sparse refresh never deletes good retained pages. A private/rebound target is blocked before target request dispatch on all direct paths; tests exercise actual adapters, not only permissive mocks. Existing user-edited files survive recrawls. |
| 1 — Enforce local-first and result contracts | Introduce the single explicit policy; default local-only; local extraction/browser before any admitted remote fallback; structured attempts/errors/quality/truncation; adapt chat, KB and research without duplicating acquisition. | With provider keys present and external services blackholed, static/JS/tabbed local fixtures succeed with **zero provider/proxy calls**; blocked sites report clear local failure. Every permitted remote fallback is visible and policy checked, including KB discovery. |
| 2 — Bound and own the work | Job-owned browser contexts, bounded browser process pool, sandbox qualification, service-worker policy and network egress isolation; streamed caps for all bodies, decompression limits, bounded frontier; per-origin scheduling, whole-job deadlines, typed retries, cancellation/cleanup. | Overlapping jobs cannot share or close each other's context. Cancellation stops discovery/provider/browser work and releases resources. Huge/chunked/gzip responses, redirect cycles, retry storms and link explosions terminate within explicit budgets. Windows and Linux gates pass. |
| 3 — Durable crawl, discovery and refresh | Persist frontier/page outcomes and immutable policy/budgets; restart/resume only unfinished pages; recursive capped sitemap traversal and llms.txt handling; query-safe identity; raw robots handling and per-origin rules. Stage refreshed versions and prune only on explicit authoritative completion. | Restart resumes safely without repeating confirmed paid work. Nested sitemap/cycle fixtures are complete within caps. Missing discovery, robots 5xx/timeout, page caps and provider failures never authorize pruning. Query-dependent pages remain distinct. |
| 4 — Usage, credits and operator visibility | Durable acquisition ledger + atomic reservations; version-aware provider adapters/balance cache; structured progress and final failures across Web/gateways/TUI/CLI; Settings provider/policy controls and readiness. | Every remote attempt is accounted for; budgets hold under concurrency; balances show freshness/account scope and unknowns; 401/402/429 and fallback are visible. English/Arabic UI and restart/disconnect recovery preserve errors and usage. |
| 5 — Industrial qualification and rollout | Offline adversarial corpus, concurrency/soak and recovery drills, cross-platform execution, documentation/default migrations, staged operator verification. | All gates below pass; release has explicit measured limits and known site limitations. No industrial claim based only on happy-path unit tests. |

Dependencies: Wave 0 first; policy/result identity precedes durable jobs and accounting; shared limits/cancellation precede restart/resume qualification. Deliver small reviewable changes by wave. Do not enable new remote capabilities ahead of their policy, budget and visibility gates.

### Implementation boundaries

- Keep `web_acquire` the public acquisition façade. Move ladder implementation behind it incrementally while retaining `read_url.fetch_full_text` compatibility. Remove broad exception fallbacks around Kazma's own imports after import-integrity coverage is in place.
- Preserve the single proxy factory, LLM/proxy separation, native aliases, source fences, workspace resolution and existing HITL contracts. Automatic artifact creation must be non-clobbering and atomic; intentional replacement uses the existing approved mutation path.
- Use one durable job truth. Evolve/migrate existing KB snapshots into the acquisition job model rather than adding conflicting registries. Register new durable stores, retention, backup/migration and backend behavior through existing repository mechanisms; offload blocking work from the server loop.
- Use job-owned browser contexts and a bounded worker manager. Retain the Windows Proactor bridge, with explicit cross-loop cancellation. Browser routing alone is not a complete DNS-rebinding/network-isolation boundary. Qualify a restricted local worker/container and OS/network egress enforcement; do not copy an unrestricted browser container recipe.
- Official Playwright docs say service-worker-intercepted requests can bypass context routes and recommend blocking service workers when routing. Its crawling guidance recommends a separate user and sandbox/seccomp configuration. Validate equivalents per supported OS. [Context routing](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route), [browser isolation guidance](https://playwright.dev/python/docs/docker#crawling-and-scraping).
- Make robots default-on for site crawls. Treat missing robots separately from unreachable/5xx, cache by origin, honor rules with an identified crawler user agent, and use a declared explicit override where needed. Search API and user-directed single-page reads need separately documented policies.
- Fix capability/preflight checks: local Chromium installation/launch, extractor, storage capacity/writability, policy, providers, and ledger health. Unavailable is not healthy. Live checks are deliberate and bounded, not paid actions during routine readiness polling.
- Update AGENTS.md §§13/24B/32 to match the new explicitly authorized local-first policy. Any changed shipped defaults declare install-follow versus new-install-only through `config_defaults.py` and the shipped-default fixture tooling. Retain local settings precedence. Update public guide/catalog/UI translations and relevant documentation checks in the same changes.

## Qualification gates

1. **Data preservation:** seed-only/sparse/capped discovery; 403/404/410; failed reindex; disk-full; cancellation; crash between staging and commit. No pruning on incomplete evidence. Deletion requires authoritative scope/completion or confirmed explicit removal policy.
2. **Network boundary:** IPv4/IPv6/private/mixed DNS, rebinding, HTTP authority/SNI, HEAD-versus-GET redirects, every redirect hop, proxy modes, environment proxies, browser subresources/service workers/WebSockets/downloads. No arbitrary private access through local-service exceptions.
3. **Extraction fidelity:** static articles, Arabic/English, tables/code, short valid pages, JSON API responses, SPAs, tabbed docs, login/CAPTCHA pages, malformed HTML/charset, redirects and truncation. Reuse fetched bytes/rendered DOM for links and extraction where possible.
4. **Resource safety:** bounded transfer/decompressed/DOM size, frontier, redirects, retries, per-origin concurrency, browser count, job count, elapsed time, cache/disk/ledger retention. Report the exact budget that ended a partial crawl.
5. **Failure visibility:** stable error codes with HTTP/provider details and safe actionable messages; retained page errors at terminal state; no provider outage disguised as healthy/zero usage; same result after reconnect/restart across surfaces.
6. **Accounting:** zero remote calls under local-only; all remote discovery and scrape attempts recorded; per-job/day atomic reservations; 401/402/429; stale/unavailable balances; ambiguous timeouts; concurrency across jobs; provider-account activity outside Kazma not falsely attributed to one crawl.
7. **Recovery/operations:** cancel during DNS/HTTP/decompression/discovery/browser/indexing; browser crash; process restart; database outage; interrupted jobs/reservation reconciliation; graceful shutdown; one bounded load/soak profile on Windows and Linux with measured resource use and no leftover work.
8. **Release:** targeted tests then repository-required full checks/import-integrity and relevant UI/i18n tests. Provider live smoke tests only with deliberately bounded approved usage. Production deployment, if later requested, follows the existing push/pull/guard procedure; no manual server management.

## Recommended first implementation slice

Start with **WA-01, WA-02/03 and WA-09**, then make the local-only policy and typed error/attempt contract concrete. Those changes protect retained data, make the network checks effective, and establish the contract needed for trustworthy jobs and billing. Provider integrations and a richer dashboard come after those controls exist.

The audit is complete at the stated scope. Industrial qualification remains pending implementation and the listed gates.

## Additional transport finding during implementation

The original pin transport rewrote URL hosts to their validated IP before
dispatch. A controlled real HTTPX/httpcore keep-alive pool test reproduced two
different HTTPS hostnames sharing one IP using only the first hostname's TLS
handshake. The current development fix pins `connect_tcp` instead, preserving
logical pool origins and TLS identities. Returning to the first hostname can
still reuse that hostname's connection. Connection-specific pin evidence also
survives map changes rather than claiming a reused socket connected elsewhere.
Gate: `tests/test_ssrf_pin_authority.py`. This extends the baseline network
findings; the archived fifteen baseline witnesses remain unchanged.

The implementation wraps httpcore's public backend interface through one
HTTPX pool/backend compatibility seam, which must remain covered on dependency
upgrades. See [httpcore network backends](https://www.encode.io/httpcore/network-backends/).

## Browser redirect finding and measured remediation

[Playwright's redirect documentation](https://playwright.dev/python/docs/network#redirects)
states the route limitation; the earlier browser-route tests only qualified
initial URLs and JavaScript navigation. The actual HTTP-redirect negative
control above contacted the forbidden destination before returning its error.

Shared scraper rendering now pauses at the CDP Fetch Request stage, obtains
each HTTP response through bounded/public-validated/pinned HTTPX, and fulfills
that response without native HTTP continuation. Redirects retain their actual
URL semantics and receive a new check before the next HTTP dispatch. See the
[official CDP Fetch protocol](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Fetch.pdl).
An owned local deny proxy covers native HTTP/HTTPS/WebSocket fallback; service
workers are blocked. This is defense in depth, not an OS network sandbox.
Per-page bounds and measured cases are recorded in the implementation report.
Global process limits, hostile workers/popups/WebRTC qualification, cancellation
cleanup after crashes, Linux isolation and native browser-tool migration remain
required work. The original route guard must no longer be described as complete
redirect/network containment.
