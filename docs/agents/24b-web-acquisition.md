<!-- AGENTS.md, piece 24b-web-acquisition. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**B. Web acquisition — ONE egress stack.**

2026-10-12 additions (supersede older open-work descriptions below):
- Session process owners reconcile to interrupted; explicit session resume links
  the original durable job and restores verified terminal output without replay.
  History is bounded to 256 rows/128MiB; archive does not release storage.
  Chat `web_search` snapshots (`record_chat_research`, depth `chat`) have their
  own 1024 rows/32MiB: sharing the research allowance, 349 of the live 361
  rows were chat searches and Deep Research refused to start (2026-10-31).
- Origins/robots persist via shared ConfigStore CAS with finite tables and
  fail-closed clock/storage behavior. Fixed sync search and native HTTP share
  origin feedback. Opaque DDGS SDK fanout is skipped, not silently unmetered.
- Durable research snapshots explicit-key/local clients and failover routes;
  ambient AWS/Google ADC are refused. Configuration hashes include all provider
  coordinates and relevant environment; only digests are persisted.
- Native automation uses the contained owner/broker, joined cancellation and
  workspace/tenant identity. Supported form bodies are bounded; uploads refused.
  Strict OS network mode defaults required. Windows refuses strict launch;
  Linux requires a verified isolated network namespace plus cgroup controls.
  Explicit compatibility mode is labelled weaker and never attested as isolated.
- Retained pipeline reports have a separate 3GiB allowance: 512MiB reserved for
  each of the four research runs admission lets in, plus a 1GiB settled share
  (`artifact_storage._LIMIT`, FA-266: 2GiB held exactly four reservations, so
  the fourth admitted run failed after its session row existed). Storage is
  checked in the same admission that answers 503 (`artifact_storage.admit`,
  from `research_runtime._frozen`, before a row or a plan); readiness shows
  settled bytes and running reservations apart. Bounded legacy inventory and
  no automatic deletion; not a whole-install quota.
  Gate: `tests/test_research_storage_sized_with_admission.py`.
- Settings/CLI/TUI explicit credit actions use the same provider adapter; cached
  reads never refresh. Jina token budget is finite; unsupported monetary caps
  refuse dispatch. Never describe reservations/tokens as charges.
- Gates: test_acquisition_durable_controls.py, test_browser_acquisition_broker.py,
  test_research_pipeline_recovery.py and existing provider/origin/robots gates.
  Full suite authorized after implementation on 2026-10-12; run conservatively.

- SoT ladder: `tools/read_url.fetch_result` → `_bounded_fetch_result` →
  `_fetch_full_text_impl` (SSRF-validate → httpx →
  local Chromium → explicitly permitted hosted recovery), built on
  `proxy.client.get_scraping_client`. `web_acquire.policy` defaults to
  `local_only`; hosted Firecrawl/Jina and scraping proxies require both
  `local_first_with_remote_fallback` and their names in the remote allowlist.
  Keys and legacy flags never grant permission; they can narrow it. KB discovery
  uses local sitemaps/llms.txt/BFS; hosted mapping is refused because the
  provider cannot enforce navigation scope. Cache entries are partitioned by
  policy AND extraction profile (generic/knowledge/discovery). The complete text ladder has a 90s deadline (operator
  range 1–300s); HTTPX network errors reach local recovery. The raw GET layer
  `web_acquire.http.fetch_public` bounds the full redirect chain separately.
  `read_capped` checks the live peer BEFORE body reads, bounds raw gzip/deflate
  expansion with zlib max_length, and refuses unsupported content encodings.
  Hosted scrape responses use the same bounded decoder through
  `read_response_capped`, after their policy gate; this decoder alone is NOT
  a public-target egress gate. Bodies default to 5MB, hard-ceiling 20MB.
  The typed result records actual final URL/status/backend/attempts/bytes,
  clipping and quality. Unknown hosted origin provenance/usage/credits remain
  null. A cache hit makes no new attempts. Partial bodies are not cached or
  indexed into KB; automated research saves refuse them. Explicit chat saves
  may publish a visibly marked partial extract. A real password input plus an
  explicit login title fails `login_required`; authentication prose/widgets
  alone do not. Gate: `tests/test_web_acquisition_results.py`.
  `web_acquire.accounting` reserves finite remote job/day requests through the
  existing ConfigStore conditional batch BEFORE HTTP/proxy effects. Failures,
  cancellation and uncertain launch retain reservations; never refund or label
  them provider charges. Defaults: 256/job, 1024/UTC day; zero refuses, active
  limits may narrow only. Fixed HTTP search reserves six requests; DDGS proxy
  search is refused because SDK request counts are opaque. HTTP hooks precede
  transport dispatch, including redirects, hosted scrape, mapping and probes.
  `web_acquire.credits` explicitly refreshes Firecrawl's team balance through
  the same allowance, account scoped/fresh five minutes, 60-second cadence.
  Status reads are local only; unsupported balances/per-request charges stay null.
  Jina's single valid `X-Usage-Tokens` header is a provider-reported response
  observation, captured durably before body consumption. Keep zero distinct
  from missing/malformed metadata. Retain observations on body failure/cancellation;
  cache reads make no new observations. Tokens never imply money or account balance.
  Gate: `tests/test_acquisition_provider_usage.py`.
  Allowance/crawl journals and their CAS revisions are RUNTIME in
  `settings_restore.KEY_RULES`; backups must never rewind spent requests or
  completed effects. Gate: `tests/test_settings_restore.py`.
  Gates: `tests/test_acquisition_accounting.py`, `tests/test_acquisition_credits.py`,
  `tests/test_acquisition_postgres.py` (opt-in disposable real database).
  Unsupported monetary bounds refuse remote dispatch. Strict browser networking
  requires the qualified Linux namespace; Windows refuses strict launch. See
  `docs/audits/WEB_ACQUISITION_IMPLEMENTATION_2026-10-10.md` for remaining gates.
  `web_acquire.crawl_jobs` persists KB ownership before Web acceptance, bounded
  page/BFS frontiers and metadata effects before fetch/indexing. One settings
  row per job (`acquisition.crawl.<digest>`, its own CAS revision; Knowledge
  audit KB-E04, 2026-10-14): a page update writes its own job only. Four
  active, 24 retained, 16 MiB, 4 MiB a job (frontier admission checks it);
  finished jobs are evicted by count AND bytes, never a running one;
  `kazma acquisition purge-finished` is the operator's purge. Owners carry
  install, host, pid, start time and a lease renewed on every write (300 s,
  KB-E07): a dead owner on this host is recovered, another host's only after
  its lease lapsed, and the taken-over owner's next write is refused; rows of
  another install are never read. The owner's start and its stamp (Linux:
  boot id and start ticks) are compared only through
  `kazma_core.process_start` (§35): a fresh reading of the start compared
  exactly moved with the Linux boot time, and a running crawl read its own
  owner as gone (2026-11-27). **A job's writers in one process take turns**
  (`_job_lock`, `_REGISTRY_LOCK`, 2026-10-31): the compare-and-set is for other
  processes, with a random pause between its 8 attempts (`_retry_pause`).
  Racing it, a research run's four parallel fetches starved each other
  ("journal is busy" on 16% of writes with 4 writers on Postgres) and every
  live deep research failed after its searches
  (`tests/test_crawl_journal_concurrency.py`). One crawl per KB library (`CrawlLibraryBusy`,
  KB-E14); the journal is the only crawl state (`kb.ingest_jobs` and
  `stores/kb_jobs.py` retired). An explicit KB resume fetches uncertain pages
  again and skips completed ones; resume requires identical policy. Gate:
  `tests/test_knowledge_crawl_journal.py`. Metadata keeps parsed links, never raw bodies.
  Faithful identity preserves `.md`, slash, matrix parameters and query bytes;
  do not infer aliases without an observed redirect. Web recovery uses the
  journal as truth; errors/partial coverage cannot become a success toast.
  Gates: `tests/test_durable_crawls.py`, `tests/test_kb_api_routes.py`,
  `tests/test_kb_recovery_browser.py` (real EN/AR Alpine plus old-state controls),
  `tests/js/test_kb_recovery.js`. `research_pipeline_job` now extends this SAME
  CAS journal (kind `research_pipeline`), owning each stage BEFORE dispatch.
  Never turn its stages into fake URL pages or store raw bodies in settings.
  Completed values/files require SHA256/size verification; running stages settle
  uncertain on interruption. Explicit `run_research_pipeline(resume_job_id=...)`
  requires identical scope: skip uncertain sources with a partial report, refuse
  failed/uncertain required stages, never blindly repeat paid/search/publication
  effects. Evidence commits before source publication. Join all sibling tasks and
  file workers before journal settlement. Stages reserve retained bytes BEFORE
  action (512MiB/job, 128 effects, 512KiB values, 24MiB/file); no disk GC is implied.
  Configuration digests are checked between stages; explicit-key/local LLM and
  failover clients are snapshotted for each pipeline. Ambient AWS/Google ADC
  routes are refused. Sessions reconcile dead owners and require explicit resume;
  history and retained reports have independent finite allowances. Gate:
  `tests/test_research_pipeline_recovery.py`
  (real SQLite fresh-process crashes, original unowned planner negative control,
  concurrent resume, file tampering, scope changes and held-worker cancellation).
  Research crawl uses the same acquired HTTP document/Chromium snapshot for
  text and links (no separate raw GET). Scope reaches the content fetch and top-level Chromium
  navigation through a per-acquisition ContextVar. Scoped reads bypass the
  cache (policy/scope enforcement must precede reuse) and refuse hosted readers
  that cannot enforce navigation scope. Do not drop the `scope_check`
  propagation in acquisition. Gate: `tests/test_web_acquisition_scope.py`.
  Chromium HTTP uses `web_acquire.browser_network` CDP Request-stage pauses,
  NOT Playwright route continuation (routes skip HTTP redirect hops). Each
  request uses bounded HTTPX through the proxy factory, public DNS validation,
  direct pinning and a live-peer check. Fulfill the actual response, preserving
  redirects/relative URLs/repeated cookies. Never continue HTTP natively.
  Public acquisition target validation uses `web_acquire.dns_worker.validate_public_url`,
  including redirects and CDP assets. Do not replace it with
  `to_thread(validate_url)`: cancellation cannot terminate a native resolver
  in a Python thread. Four owned validators / 16 queued / 10s admission share
  a separate FIFO pool; each resolver is an isolated credential-free child
  capped at 5s, 256MiB and 16KiB output (128 unique addresses / 2048 raw records).
  Check every returned address with the existing SSRF validator; excess is a
  refusal, never a truncated permission. Direct HTTP connects validated pins.
  Windows Job kill-on-close and Linux verified parent-death/kernel wall timers
  protect the child; cancellation joins its process/pipes/per-call executor.
  Uncertain cleanup retains handles and quarantines capacity in readiness;
  the next validation starts one retained cleanup thread, with `_drain_cleanup`
  for joining it. `web_acquire.endpoint_transport` uses that same owned DNS
  for async/sync proxies and hosted async connections, preserving logical HTTP,
  CONNECT and verified TLS SNI. Only an operator-selected proxy endpoint may
  resolve private; content targets still require independent public validation.
  Reject alternate transports/mounts that would bypass this boundary. Sync
  proxy HTTP refuses execution on an event loop. Legacy synchronous validation
  outside acquisition remains separate work; native automation now uses the
  same owned DNS and broker. Gates: `tests/test_endpoint_transport.py`,
  `tests/test_acquisition_dns.py`; Linux evidence in
  `docs/audits/evidence/dns_linux_boundary_2026_10_11.py`.
  HTML and sitemap XML extraction use `web_acquire.parser` owned children (15s, 768MiB,
  24MiB encoded input, 8MiB output; XML refuses DTDs/entities and bounds
  leaf/index frontiers at 4000/64 -- a larger sitemap keeps its first URLs,
  answers `limited`, and discovery is marked partial; a sitemap past the
  parser's budget is skipped, never a failed crawl: rfc-editor.org's
  sitemap-1.xml holds 6,000 URLs, 2026-10-31); do not put hostile parsing back on shared
  Python threads. `search_runtime` owns the complete backend chain/deadline;
  `search_http` bounds HTTP and parsing in a fixed child. Both use the common
  owned-worker cleanup/quarantine contract. Research crawl seed/page
  prevalidation also uses `validate_public_url`, not native resolver threads.
  Gates: `tests/test_acquisition_parser.py`, `tests/test_search_http_worker.py`,
  `tests/test_search_sdk_worker.py`, `tests/test_acquisition_dns.py`.
  `BrowserJob` owns the context and a native deny proxy; sandbox enabled, service
  workers blocked, DNS/UDP suppression are defense in depth, NOT OS network
  isolation. Fixed per-page limits: 128 intercepted requests, 4 concurrent,
  5MB per response, 32MB aggregate retained decoded bytes, 5 redirects; DOM 5M
  characters, links 2000, supported lazy tabs 12. A page's text is
  `document.PAGE_TEXT_MAX` (1M characters) for every reader -- chat, research,
  crawls, Knowledge -- on the HTTP and browser paths alike (2026-10-31: at
  100K, RFC 9110's 464K was partial, so never indexed or saved). What a model
  gets stays bounded by its reader: `read_url` windows (16K default, 100K at
  most, `_HARD_MAX`), research synthesis 16K a source. On a page whose whole
  reading is 50K+, an article extract under a tenth of it is a fragment and
  the whole reading is used (`document.article_or_whole`: trafilatura kept
  7,067 of RFC 9110's characters and called it complete). Gate: the long-page
  tests in `tests/test_web_acquisition_results.py`. Refusals/failed
  resources are typed or partial; KB never indexes partial content. KB text
  and BFS use the same typed ladder, with explicit seed scope and query-safe
  identity; metadata redirects stay on the seed host. Gate:
  `tests/test_browser_acquisition_broker.py`,
  `tests/test_knowledge_acquisition_profile.py`.
  `web_acquire.resources` provides process-wide FIFO admission across caller
  threads/event loops: 2 browser owners (8 queued), 6 ordinary acquisitions
  (24 queued), and 2 reserved acquisitions for existing browser owners (8
  queued); admission waits expire at 30s within the acquisition deadline.
  Reserve owner slots to avoid deadlock when ordinary calls wait for browsers
  held by a crawler that needs its next page. Cache hits need no admission;
  saturation is a typed `resource_limit` before dispatch. The Windows bridge
  tracks the actual task and joins asynchronous finalizers before acknowledging
  cancellation/timeout, including repeated caller cancellation. A failed
  browser teardown retains/quarantines its capacity and native deny endpoint;
  readiness exposes that unhealthy capacity. The native deny endpoint admits at
  most 16 handler tasks per owner and joins them before releasing its slot.
  Close the owned page BEFORE CDP detach: a paused navigation can otherwise
  leave detach waiting indefinitely, even after its HTTP stream has closed.
  `web_acquire.browser_driver` owns Playwright's Node driver and Chromium in
  one OS boundary before untrusted browser code starts: Windows Job Objects
  (2GiB aggregate, 32 processes, approximately two logical CPUs) or delegated
  Linux cgroup v2 (2GiB memory, no swap, 256 tasks, two CPUs). Linux needs
  writable memory/pids/CPU/group-kill controls, optionally selected through
  `KAZMA_BROWSER_CGROUP_ROOT`; missing controls refuse rendering. Never launch
  an unrestricted browser or substitute per-process RLIMIT_AS. The driver gets
  a minimal environment; credentials, proxy variables and NODE_OPTIONS stay out.
  Protocol frames/queued commands are capped at 32MiB, total traffic at 512MiB.
  An independent watchdog ends the process tree on cancellation, a 30-minute
  owner lifetime or five-second cleanup grace; parent-process death also ends
  descendants (Windows kill-on-close, Linux pidfd guardian). Capacity releases
  only after OS verification of no live descendants; uncertainty quarantines it.
  The private Playwright Connection/stdio compatibility seam is isolated here,
  qualified against 1.61.0 and, 2026-10-17, 1.64.0 (35 boundary tests, real Chromium). Upgrades must pass the real-browser/boundary gates.
  **1.65 rewrote Playwright's client** (2026-11-13): `_impl._connection` is gone, and 1.65.1 failed
  every page that needed the browser (37 browser tests). `BrowserDriver.start` now wires
  `_impl.connection.Connection(loop)` to `_Transport` as Playwright's own client does
  (`onmessage` -> `send`, `on_message` -> `dispatch`, `initialize_playwright()`, `close()`);
  qualified on 1.65.1 (141 browser tests, real Chromium). pyproject holds Playwright to one
  qualified minor (`>=1.65.1,<1.66`): `kazma update` installs the newest allowed and does not
  read uv.lock, so a new minor reaches installs only after these tests pass on it.
  **A job's browser is relaunched between pages, before a limit ends it under
  one** (2026-11-13, `BrowserJob._relaunch_reason` / `_retire_impl`): when its
  tree has ended (a limit, a crash: `BrowserDriver.failure`), Chromium
  disconnected, less than `_RELAUNCH_MARGIN_S` (180 s, at most a third) of
  the lifetime or `_RELAUNCH_TRAFFIC_HEADROOM` (128 MiB) of the protocol
  budget is left, or after `_RELAUNCH_AFTER_RENDERS` (150) renders. A job
  kept its dead context before: every render after the 30-minute lifetime
  failed `resource_limit` and a long Knowledge crawl lost every page that
  needed the browser. The retire ends the old tree first and keeps the job's
  capacity lease; a cleanup it cannot verify keeps every handle quarantined
  and launches nothing (the next page tries the cleanup again). A page under
  which the browser died (`RenderedDocument.lost`: the driver's failure, a
  disconnected Chromium, Playwright's "closed"/"crashed") is rendered once
  more in the relaunched browser (`_render_in_job` -> `_render_once`), never
  a third time. Gate: `tests/test_browser_relaunch.py` (real Chromium with the
  lifetime shrunk to seconds, and a Chromium killed mid-crawl: every page
  stored; the old rule as the negative control).
  Gates: `tests/test_browser_driver_budget.py`,
  `tests/test_acquisition_resources.py` and the broker suite. This boundary does
  not constrain arbitrary Python HTTP/XML threads or all install storage,
  and is distinct from the required Linux network namespace. Native automation
  shares this contained owner, with bounded mutating HTTP. Report/standalone
  publication and Knowledge SQLite have finite allowances; vector/WAL/temp and
  arbitrary user files are outside those disk bounds. Strict Windows launch is
  refused; compatibility mode is visibly weaker.
  `web_acquire.origins` shares HTTP origin limits across jobs and event loops:
  2 live origin reservations / 32 queued per origin, 512 retained origins and
  a 30s admission/spacing deadline. Crawl/KB extraction and KB metadata request
  starts have a 300ms floor; a crawler's larger requested delay also propagates
  through rendering. HTTPX streams retain their lease through body closure.
  Record Retry-After at headers (integer or HTTP-date), before body reads;
  propagate redirect delays to the destination authority. Never clamp server
  cooldowns downward, clear them on an older successful in-flight response or
  evict active/cooling origin state to reset a limit. Return `origin_throttled`
  before another request/recovery backend; do not turn it into bot recovery.
  Hosted readers reserve source and service origins in stable deduplicated order.
  Production origin leases, spacing and cooldowns use durable ConfigStore CAS
  across processes, including fixed sync search and native browser HTTP. Live
  capacity counters describe this Python process, not every concurrent process.
  **A lease is never held by a release that failed** (2026-11-05): this
  process's writers of a durable table take turns (`durable_state._table_lock`;
  the CAS is for other processes, with `_retry_pause`), a poll that only waits
  writes nothing (`durable_state.Unchanged`, `origin_state._waited`),
  `origin_state.release` never raises (a WARNING), and a lease naming this
  process that it no longer holds (`origin_state._HELD`) is taken back at the
  origin's next admission. Live, a deep research's parallel searches polled
  with a write every 50 ms until the table was "busy"; two releases gave up,
  the running server held SearXNG's two slots, and every later search waited
  its 30 s and failed until a restart. Gate:
  `tests/test_origin_leases_under_contention.py` (the old lock, poll and
  release as negative controls).
  Gates: `tests/test_origin_scheduler.py`, real browser/KB compatibility suites.
  Tests isolate the scheduler per case; production cooldowns persist across jobs.
  `web_acquire.robots` defaults automated crawl/KB/research-save jobs to KazmaBot
  robots enforcement. Check before cache reuse and every HTTP/browser hop,
  including assets. Raw public metadata is bounded to 500KiB/15s/five
  redirects, with dedicated bounded capacity to avoid nested content-slot
  deadlock. **robots.txt is read as RFC 9309 says** (2026-10-31, from the
  recorded corpus below): any 4xx but 429 -- 401 and 403 too -- means no rules
  apply (the page request decides); 429, 5xx, network, malformed or clipped
  checks refuse crawling; a redirect is followed across hosts, up to five,
  each hop checked public (http://python.org/robots.txt moves to
  https://www.python.org/), and the rules apply to the origin asked about.
  Refusing 401/403 and cross-host redirects left such sites uncrawlable.
  KazmaBot's name carries a contact link (`robots.USER_AGENT`): Wikimedia
  refuses a bot that names none (403, "please respect our robot policy").
  Explicit opt-out is visible; hosted
  recovery is refused while robots enforcement requires per-request control.
  Cache policy by origin, acquisition policy and route; separate manual/crawler
  page caches. Freeze policy per job; never retain a ContextVar across async
  generator progress yields. Robots metadata receipts are call-local; proxy
  request reservations use the durable ledger, with per-request charges unknown.
  Parsed robots policy and interrupted lookup refusals persist in a bounded CAS
  table. Full RFC conformance and broader compatibility qualification remain open.
  Gates: `tests/test_web_robots.py`, actual Chromium broker fixtures.
  **No acquisition state field is named like a credential** (2026-10-31): the
  settings store moves such a field into the vault and `get_category` reads
  the `vault://` pointer back, so the robots claim (then `token`, now
  `claim`) never matched and every robots-checked crawl failed "Robots lookup
  is owned or was interrupted". Every `batch_set` in `web_acquire` passes
  `durable_state.refuse_vaulted` (`config_store.vaulted_paths`, the store's
  own walk). Gate: `tests/test_acquisition_state_never_vaulted.py`.
  **A crawl's outcome says what happened to each page** (2026-10-31,
  `web_acquire/page_outcome.py`): ingested, unchanged, excluded (a rule:
  robots, scope, a sign-in, a page gone, a page past Kazma's size limit -- a
  part is never stored) or failed, each with a reason code the Knowledge
  page words with what to do (`knowledge.reason.*`, `knowledge.hint.*`). A
  crawl is partial only for a failed or uncertain page; limits and exclusions
  are notes (`crawl_verdict`); every page kept out with none failed is
  completed with `nothing_stored` (the monitor's yellow "empty", never red).
  A hiccup -- network, 408/425/429/5xx, a private DNS answer during an
  outage -- is tried again inside the crawl (`_fetch_with_retries`, 5 s then
  30 s, at most 20 a crawl). **A site that refuses page after page stops its
  crawl** (2026-11-13, `page_outcome.RefusalStreak`): `REFUSAL_STREAK` (10)
  failed pages of one class (`REFUSAL_CLASSES`: bot wall, HTTP refusal,
  server error, network) with no page stored between end the crawl
  `interrupted` (`interrupted_by: site_refusing`, `row['refusal']`,
  `crawl_jobs.stop_refused` / `refusal_text`); the pages not reached stay
  pending with the reason `site_refusing` for Resume, never resumed on their
  own. Other failures (no text, an unreadable format) neither count nor clear
  it. Every remaining page used to run the whole ladder (up to 90 s each, a
  render behind a bot wall) to the page cap. A crawl a restart cut off in the
  last day resumes on its own (`interrupted_by: process_ended`,
  `kb_api.resume_interrupted_crawls`) up to `AUTO_RESUMES_PER_DAY` (3) times a
  day, each only if it settled more pages since its last automatic resume
  (`row['auto_resumes']`, `crawl_jobs._settled_pages`; it was once ever, and a
  Windows update restarts twice); one the owner stopped waits. **A refresh
  takes out what the site removed, and only that** (2026-11-13): it also asks
  the site for the pages the library holds that discovery did not list
  (`_pages_to_revisit`, at most `_REVISIT_MAX` 200, missing ones first:
  `KnowledgeStore.web_page_urls`); the site's own answer decides -- 410
  takes the page out at once, a 404 is recorded (`knowledge_pages_gone`,
  `gone_unconfirmed`) and takes it out when a later crawl finds it missing
  again at least `_GONE_CONFIRM_S` (an hour) after the first; a page that
  answers again clears its record (`publish_page`). Absence from discovery
  alone never takes a page out (KB-S14 stands). The page leaves the chunks,
  the keyword index (like a replaced page's words), its vectors and its
  fetched copy in one transaction (`KnowledgeStore.page_gone`,
  `KnowledgeIndex.page_gone`); never a Document's chunks. Outcome `removed`,
  counted apart (`row['removed']`, the note `removed`). Before, a 404/410 was
  "excluded: gone" and the text stayed searchable for good. **One text is
  stored once, and one page's query-string copies do not fill a crawl**
  (2026-11-13): a page whose text another page of the crawl stored (digest
  of the whitespace-folded text, `_text_digest`, kept on the journal's page
  so a resume knows it) is excluded `duplicate`; at most
  `_QUERY_VARIANTS_PER_PATH` (5) query-string addresses per page in the
  frontier (`_cap_query_variants`, note `query_variants_capped`) and in the
  link walk (`_QueryVariants`). The corpus held three duplicate pairs
  (`/` and `/index.html`, http and https, a `/stable/` alias). The
  Knowledge page previews a crawl (`knowledge_ingest.preview_site`: the
  crawl's own discovery, nothing stored) and takes a scope in the owner's
  words (this section / whole site / just this page), kept per library
  (`knowledge_libraries.crawl_scope`) for refreshes; a resume keeps its own.
  "Just this page" follows its seed's redirects within the site, and a
  version segment ("3" in /3/tutorial/) is not a section's topic. After
  discovery a Knowledge crawl's row keeps no parsed sitemap list and only the
  latest receipt. Every retained crawl's pages by outcome and reason, against
  the target of 99% stored or kept out by a rule (`crawl_jobs.PAGE_TARGET`),
  are the Dashboard's Knowledge crawls card (`/api/kb/outcomes`), `/metrics`
  (`kazma_kb_crawl_*`) and the weekly report ("Knowledge crawl", "Knowledge
  page not stored", "Knowledge page removed"); a removed page counts as
  handled. Gates: `tests/test_crawl_outcomes.py`,
  `tests/test_crawl_auto_resume.py`, `tests/test_crawl_preview.py`,
  `tests/test_crawl_report.py`, `tests/js/test_kb_crawl_report.js`,
  `tests/test_crawl_refusing_site.py` (a replayed site that turns to 403 after
  five pages: 15 fetched of 40; the streak out of reach as the control),
  `tests/test_crawl_removed_pages.py` (410 at once, 404 an hour later, the old
  refresh as the control), `tests/test_crawl_duplicates.py` (a replayed site
  of sixty months of one page; no cap and no text check as the controls).
  **Twenty real sites are recorded and replayed through the whole crawl**
  (`tests/_crawl_replay.py`, `tests/fixtures/crawl_corpus/`): every HTTP
  response a real crawl received, through the one HTTP seam
  (`proxy.client.get_scraping_client`), replayed with production's robots
  manager and the vault on; each site's outcome page by page is locked in
  `expected.json` (`scripts/crawl_corpus.py record|lock|show`; review the diff
  before committing a lock). The first recording found five bugs no synthetic
  test had. Also with a real Chroma store and on real Postgres
  (`tests/test_crawl_corpus.py`). **A daily canary crawls one known page
  for real** (`web_acquire/canary.py`, RFC 2606 by default, Settings ->
  System -> Web acquisition): the whole crawl into a private store
  (`<data dir>/canary`, `knowledge_ingest._PRIVATE_KNOWLEDGE`), journal row
  kind `canary` removed after; a failure is the ops alert
  `acquisition.canary_failed` (error) the same day, a pass after one
  `acquisition.canary_recovered`; the weekly report expects seven runs
  (`tests/test_crawl_canary.py`). It also renders the page in Kazma's own
  browser (2026-11-13, `canary._render_leg`, the outcome's `render`): the
  crawl reaches Chromium only when plain HTTP fails, so a broken driver,
  contained process tree or request broker went unseen until a page needed
  JavaScript. A failed render fails the canary; no browser part, or a host
  that refuses to start it (`browser_isolation.browser_unavailable`, which
  holds the one "is Chromium installed" rule the install asks too), is
  `skipped` with why. A real Chromium renders the replayed page in the test
  where one is installed; the canary without the leg is the control.
  The factory provides
  (proxy provider + rotating UA pool); `web_acquire` is the façade
  (`fetch_text`/`search`/`rank_urls`/profiles) used by research pipeline, KB
  ingest, and readiness. Direct (no-proxy) hops pin the validated IP
  (`PinHostAsyncTransport`) and abort if the peer is private (§32). Do not
  pin through `proxy=` — CONNECT would break scraping.
- `crawl_site(profile=...)` accepts named cap presets (`research_brief` |
  `research_deep` | `kb_site` | `single_page`); explicit args win; hard env
  ceilings (`KAZMA_CRAWL_MAX_PAGES` etc.) still clamp.
  Its research frontier deduplicates pending URLs and retains at most four
  times the page cap (8192 characters per URL); exhausted coverage is visible.
  Partial extracts are not published automatically. Saves pin the starting
  workspace through the existing path scope. Validation errors preserve their
  typed class. `crawl_site(resume_job_id=...)` now uses the shared bounded
  crawl journal with `kind=research_crawl`; freeze workspace, output options,
  acquisition/robots policy and limits. Own each page before fetch/publication,
  persist its receipt before saving, and settle summary plus pending frontier
  atomically. Resume only interrupted jobs with identical scope, skipping
  completed/failed/ambiguous pages; uncertainty or capped coverage stays partial.
  Publication and terminal commits join cancellation; a committed terminal
  outcome must not be overwritten by interrupted cleanup. KB API filters
  research jobs; acquisition readiness can inspect them by job ID. Research
  pipeline/session durability remains separate required work.
  Gates: `tests/test_research_crawl_bounds.py`,
  `tests/test_research_crawl_recovery.py` (real SQLite child crashes before/after
  publication, cancellation/terminal race, concurrent resume and invalid state),
  `tests/test_acquisition_postgres.py` (real independent PostgreSQL connections).
