---
id: web-research
title: Web research
sidebar_label: Web research
description: Search, scrape, crawl, and digest public web content with Kazma tools
---

# Web research

Kazma can search the web, fetch pages, page through long documents, crawl a site within bounds, and digest saved extracts — all from **normal chat**, `/swarm`, the **Research panel** (`/research`), or **`/research deep <topic>`**.

**Operator tour of the latest research + proxy + sessions stack:** [Recent features](./recent-features).

## Shared web acquisition stack

Search, scrape, and crawl I/O are centralized under **`kazma_core.web_acquire`**:

| API | Role |
|-----|------|
| `search()` | Multi-backend SERP (same as `web_search` tool) |
| `fetch_text()` | Local HTTP/extraction, local Chromium, then permitted hosted recovery |
| `tools.web_research.crawl_site()` | Bounded multi-page crawl with **profiles** (`research_brief`, `research_deep`, `kb_site`, …); not exported as `web_acquire.crawl()` |
| `rank_urls()` | Heuristic ranking (docs/edu over social/spam) for deep research |

**Knowledge Library ingest** and **research** both use this fetch ladder so hard-page fixes apply once. Product pipelines stay separate (KB → index; research → digest/report). LLM APIs never use this stack (they use `http_pool`).

`fetch_text()` returns a typed outcome with success/error code, requested and
observed final URL, backend, HTTP status, attempts, measured byte counts,
truncation and quality. Page prose beginning `Error:` is still page content.
Chat reads and crawl indexes show backend receipts, including failed third-party
attempts. Cached reads distinguish page reuse from fresh robots metadata requests.
Hosted services that provide no reliable origin status/final URL or accounting
evidence retain unknown values; unavailable credits never mean zero.

Crawl links and text come from one acquired HTTP document or Chromium snapshot.
Clipped content is visible as partial and is neither cached nor indexed into
the Knowledge Library. Automated research source saves refuse it;
`read_url_to_file` can save an explicitly marked partial extract. Incomplete
JSON is refused. Observed password forms with explicit login page titles fail
with `login_required`; this does not detect every SSO screen or paywall.

### Origin limits and cooldowns

HTTPX acquisition, brokered Chromium requests and hosted readers share origin
limits across jobs in the running server: two live origin reservations, 32
waiters per origin, 512 retained origins and a 30-second admission/spacing
limit. Crawl/KB requests and KB metadata use a 300ms start-spacing floor;
a crawl's larger requested delay also applies. Hosted readers reserve both
the source and service origin without double-counting an identical origin.

`Retry-After` accepts decimal seconds and HTTP dates. A cooldown is recorded
when headers arrive, survives a later body failure/cancellation, and cannot
be cleared by an older in-flight success. Redirect delays also apply to the
destination. A pending call during cooldown returns `origin_throttled` with
retry guidance; changing client/proxy or choosing a hosted reader cannot bypass
it. Missing/invalid Retry-After on HTTP 429/503 uses bounded exponential backoff.

Readiness shows aggregate origin reservations, queued calls and cooling
origins. Origin scheduling persists across processes/restarts through the shared settings
store, including fixed HTTP search and native browser HTTP. Live capacity counters
still describe the current process. Opaque DDGS SDK fanout is skipped visibly.

### Robots policy

Automated crawls, Knowledge Library discovery/ingest and research saves enforce
`robots.txt` for `KazmaBot` by default. Checks precede page-cache reuse and every
content redirect or brokered browser asset. HTTP requests identify as KazmaBot.
Missing 404/410 rules allow crawling; access denial, failed requests, malformed
or clipped policies refuse it visibly. Disallowed pages report `robots_disallowed`;
failed checks report `robots_unavailable`.

Control-file requests are bounded to 500KiB, 15 seconds and five same-origin
redirects, with separate bounded admission. Cross-origin robots redirects are
conservatively refused to retain the metadata scope boundary. Successful policies
cache for one hour, denials for ten minutes and failures for thirty seconds;
route and acquisition policy changes partition the cache. Hosted navigation is
refused during enforcement because Kazma cannot check each provider request.

`KAZMA_CRAWL_RESPECT_ROBOTS=0` or explicit `respect_robots=False` opts out; receipts
show `robots disabled by operator`. The setting freezes per job. Metadata receipts
include policy status, check time, cache reuse and unknown proxy usage/credits.
Rules and unavailable observations persist across restarts. Interrupted checks
remain unavailable until their retry deadline; full RFC conformance qualification
remains separate. Manual single-page chat reads keep their existing behavior.

### Proxy routing failures

A selected proxy with missing credentials, unavailable settings/secrets, an
unknown provider or an invalid URI fails with `proxy_routing_error`. Correct
Settings → Proxy Provider, or deliberately choose `none` for direct routing.
A failure does not silently change the route. Each resolution uses one live
account snapshot; changes apply to the next resolution.

Settings Test honors the acquisition policy, probes the resolved route once,
and bounds execution to 15 seconds and the decoded response to 128 bytes.
Local-only policy refuses remote proxy probes. Opaque DuckDuckGo SDK fanout is
skipped visibly. Fixed SearXNG, Bing and Wikipedia HTTP backends run in bounded
owned children, with a shared 90-second backend deadline and at most 30 seconds
per child. Cancellation joins child cleanup before returning. These checks do
not measure proxy credits; unavailable usage and balances remain unknown.

### Deep pipeline quality (R0 / R1 / R2)

`run_research_pipeline` / `/research deep`:

1. **Adaptive plan** (`research_planner`) — LLM sub-questions + queries (`plan.json`); template fallback  
2. Multi-query **search** via `web_acquire`  
3. **Rank** URLs (`rank_urls`) before acquire  
4. Full-page acquire → **`sources.json`** metadata (backend, attempts, coded failures, provenance/limits and unknown usage/credits)
5. **Fail-closed** in deep mode if fewer than min sources (override: `KAZMA_RESEARCH_ALLOW_THIN=1`)  
6. Digests + heuristic **evidence claims** (`claims.json` / `claims.md`)  
7. LLM synthesis (cite materials only)  
8. **Gap critic** — follow-up search/acquire + re-synthesize (`gap_loop_0.json`; off: `KAZMA_RESEARCH_GAP_LOOP=0`)  
9. Structural **rubric** (`rubric.json`)  

| Env | Default | Role |
|-----|---------|------|
| `KAZMA_RESEARCH_LLM_PLANNER` | on | Adaptive search plan |
| `KAZMA_RESEARCH_GAP_LOOP` | on for deep | Verification pass |
| `KAZMA_RESEARCH_LLM_CRITIC` | on | LLM gap critique |
| `KAZMA_RESEARCH_ALLOW_THIN` | off | Allow deep under min sources |

Golden topics / scoring: `kazma_core.tools.research_eval`.

### Preflight (industry)

`GET /api/research/ready` reports SearXNG config, DDG package, proxy, and
hard-page backends, the acquisition policy, and whether usage/credit accounting
is available. Unknown usage and balances are returned as unavailable, not zero.
Deep pipeline runs the same check at start and **fails
fast** if no search path exists. Optional live micro-search:
`?live=1` or an explicit `research_readiness(probe_search=True)` call.
Cached reads always honor `probe_search=False`; the former
`KAZMA_RESEARCH_PREFLIGHT_LIVE` override is ignored. SearXNG credentials are
not included in readiness output.

Use `kazma acquisition status [--job ID] [--json] [--language en|ar]` or the
manual cached-status panel in TUI Settings for local inspection. These surfaces
show stored failures, uncertain effects, reported tokens (including zero),
request reservations and cached balance freshness. They make no provider calls
or balance refreshes. Stored job/accounting evidence is shared by the
installation; live capacity is specific to the inspecting Python process.

### Research sessions + panel UX (R3)

Deep runs can be started from the **Research** page (`/research`) without chat:

1. **Start form** — topic, depth (`deep` / `brief`), max sources  
2. **`POST /api/research/sessions`** creates a durable row in `kazma-data/research_sessions.db` and runs `run_research_pipeline` in a background task  
3. **SSE** `GET /api/research/sessions/{id}/stream` streams stage/message progress while this process is live  
4. Finished reports still register as pipeline **papers** under `research/reports/` and appear in the results list  

Sessions survive restarts for list/detail; live SSE is in-process only (reconnect after restart falls back to polling session status).

### Routing + eval (R4)

| Mechanism | Role |
|-----------|------|
| **Soft route hint** | On deep-intent user messages, the supervisor gets a system hint to prefer `run_research_pipeline` once (`KAZMA_RESEARCH_ROUTE=soft`, default; set `0` to disable) |
| **Pipeline prefer nudge** | If the model starts manual `web_search` / `read_url` on a deep request, one nudge steers it to the pipeline tool |
| **Source depth nudge** | Still fires when search-only without enough acquires (skipped if pipeline already called) |
| **Structural rubric** | Pipeline writes `rubric.json`; sessions store `rubric_score` / `rubric_ok`; list cards show score |
| **Eval API** | `GET /api/research/eval?path=research/reports/…/report.md` or `?session_id=rs_…`. Like every research route that reads a report a request names, it reads only a file inside a `research/reports` folder |
| **Gateway `/research deep`** | Uses durable sessions + stage progress messages (not a silent multi-minute hang) |

> **Arabic brand:** product name is **Kazma** / **كاظمه** (or **كاظمة**). Never **كازما**.

## How you use it

| You want | What to do |
|----------|------------|
| Quick research with sources | Chat: *“Research X, use the web, cite URLs”* |
| Deep multi-page research | Chat: *“Crawl `https://docs…` and digest the pages”* |
| Multi-worker parallel research | `/swarm research …` or *“use the swarm to research X”* |
| Deep multi-source paper | **Research panel → Start**, chat *“deep research on X”*, or **`/research deep X`** |
| Force tool chain | Name tools: *“`web_search`, then `read_url_to_file`, then `digest_research_file`”* |

The supervisor chooses tools; you do not need to name them unless you want control.

## Tool map

| Tool | Role |
|------|------|
| `web_search` | Search (SearXNG → DuckDuckGo → Bing HTML). Prefer `KAZMA_SEARXNG_URL`. |
| `read_url` | One URL, **paged window** (`offset`, `max_chars`). Header shows total length + next offset. |
| `read_url_to_file` | Full extract saved **inside the workspace** (default folder `KAZMA_RESEARCH_DIR`, usually `research/`). |
| `crawl_page` | Native alias of `read_url` (advanced-web-crawler skill). |
| `crawl_site` | **Bounded** same-domain multi-page crawl; saves pages + returns an index. |
| `list_research_chunks` | Chunk index + previews for a saved file. |
| `read_research_chunk` | One chunk by index. |
| `summarize_research_file` | Light extractive outline. |
| `digest_research_file` | Walks **all** chunks **in-tool**; returns one **bounded** digest (context-safe). |

Native skill **`advanced-web-crawler`** also registers `web_search_duckduckgo`, `crawl_page`, `parse_document` (auto-loaded; not an Agent Skills marketplace install).

For `crawl_site(same_domain_only=True)`, the seed hostname limits both link
discovery and content acquisition, including top-level Chromium navigation.
Chromium HTTP requests use the bounded HTTP broker, which validates each actual
redirect hop before dispatch and preserves final URLs and relative assets.
Public CDN resources also pass its public-address and connected-peer checks.
Native HTTP/HTTPS/WebSocket fallback goes to a job-owned deny proxy. Native
browser automation shares the contained owner and HTTP broker. Strict rendering
also requires the OS network boundary described below. Scoped content reads
currently bypass the text cache and skip hosted readers because those paths
cannot establish the required redirect provenance. Scope failures appear in
the crawl index with their page-level reason.

See [Tools catalog](../reference/tools-catalog).

## Caps (important for long pages)

| Stage | Default | Env |
|-------|---------|-----|
| `read_url` window | 16 000 chars | `KAZMA_READ_URL_MAX_CHARS` |
| HTTP/hosted response body | 5 000 000 bytes; hard ceiling 20 000 000 | `KAZMA_FETCH_MAX_BYTES` |
| Graph truncate (normal tools) | 4 000 | `KAZMA_TOOL_RESULT_MAX_CHARS` |
| Graph truncate (research tools) | 16 000 | `KAZMA_TOOL_RESULT_RESEARCH_MAX_CHARS` |
| Digest output | 12 000 | `KAZMA_RESEARCH_DIGEST_MAX` |

**Double-cap history:** older builds used hard 8k scrape + 4k graph truncate. Research tools now use the higher research graph cap so paging is useful.

Paging example (agent or explicit):

```text
read_url(url, offset=0)
read_url(url, offset=16000)   # next window; full text cached in-process ~15 min
```

Full-page research:

```text
read_url_to_file(url) → digest_research_file(path) → read_research_chunk for details
```

## Multi-page crawl (`crawl_site`)

| Control | Default | Hard ceiling |
|---------|---------|--------------|
| `max_pages` | 8 | 50 (`KAZMA_CRAWL_MAX_PAGES`) |
| `max_depth` | 2 | 5 (`KAZMA_CRAWL_MAX_DEPTH`) |
| `same_domain_only` | `true` | recommended |
| `delay_ms` | 300 | politeness |
| SSRF | every URL | private/metadata blocked |

Saves under the workspace (default research subfolder) and returns a markdown index.
The starting workspace stays bound through the crawl, including a later repository
switch. Partial extracts are reported without automatic publication. Pending URLs
are deduplicated and bounded to four times the page cap, with 8192 characters per
URL. The index reports incomplete coverage when the page/frontier cap is reached.
DNS failures retain their actual error type. Interrupted jobs require explicit
recovery with their original scope; see the durable pipeline recovery section.

## Stronger search (SearXNG)

`web_search` tries **SearXNG first**, then DuckDuckGo → Bing → Wikipedia.

| Setup | Command / env |
|-------|----------------|
| Compose profile | `docker compose --profile search up -d searxng` |
| Host port | `http://127.0.0.1:8088` (maps container `8080`) |
| Env | `KAZMA_SEARXNG_URL=http://127.0.0.1:8088` |
| ConfigStore | key `search.searxng_url` (same purpose) |
| Settings | `deploy/searxng/settings.yml` enables **JSON** format (required) |

Kazma multi-base discovery also probes `localhost:8088`, `host.docker.internal:8088`, and `searxng:8080`. Live bases are cached briefly; dead hosts cool down ~60s.

## Optional harder fetch backends

Page acquisition defaults to **local-only**: HTTP + local extraction, then local
Chromium where needed. Public websites still require network access. Search engines,
LLM synthesis and embeddings have separate provider settings. Some sites remain
blocked; a visible failure is preferable to undisclosed hosted fallback.

Hosted extraction requires both the remote mode and a named provider allowlist:

| Env | Purpose |
|-----|---------|
| `KAZMA_ACQUISITION_MODE` | Default `local_only`; opt-in `local_first_with_remote_fallback` |
| `KAZMA_ACQUISITION_REMOTE_PROVIDERS` | Default empty; comma-separated `jina`, `firecrawl`, `proxy` permissions |
| `KAZMA_FETCH_BACKEND` | Legacy hosted selection: `auto` \| `httpx` \| `jina` \| `firecrawl`; cannot override policy |
| `KAZMA_FETCH_DEADLINE_SECONDS` | Whole text acquisition deadline, default 90s (1–300s) |
| `KAZMA_FIRECRAWL_API_KEY` | Optional Firecrawl API key; hosted policy permission is also required |
| `KAZMA_FIRECRAWL_URL` | Optional public Firecrawl-compatible endpoint; private self-hosted endpoints remain unsupported |
| `KAZMA_JINA_READER` | `0`/`off` vetoes Jina; `1` does not grant policy permission |
| `JINA_API_KEY` / `KAZMA_JINA_API_KEY` | Optional Jina auth (higher rate limits) |

**Fetch order**

1. Local httpx + trafilatura.
2. Local Chromium recovery on bot walls / thin or empty extracts.
3. Permitted Firecrawl → permitted Jina, only after local recovery fails.

Knowledge ingest shares this ladder. Site discovery tries local sitemaps, llms.txt
and link walking before hosted mapping/seed expansion. Retained KB pages are not
deleted merely because a refresh failed to rediscover them.

Local acquisition admission is bounded within each server process: two browser
jobs, six ordinary acquisitions and two reserved acquisitions for browser owners.
Their waiting queues hold at most 8, 24 and 8 calls respectively; admission waits
expire at 30 seconds within the acquisition deadline. A full queue returns a
visible resource-limit error. Node and Chromium now share a 2GiB OS memory
budget, approximately two logical CPUs and a 30-minute owner lifetime. Windows
uses a Job Object capped at 32 processes; Linux uses a delegated cgroup v2 capped
at 256 tasks with swap disabled. Driver frames and queued commands each have a
32MiB cap; total protocol traffic is capped at 512MiB. A watchdog can terminate
the browser even while its Python event loop is stalled. Parent-process death
also ends the browser tree. Graceful cleanup gets five seconds before forced
termination; capacity releases only after OS verification that descendants ended.
Readiness reports quarantined capacity when cleanup cannot be verified.

Linux requires writable delegated memory, pids, CPU and group-kill controls.
`KAZMA_BROWSER_CGROUP_ROOT` may select that delegation; otherwise Kazma tries
its current cgroup. Configure delegation in the service manager; Kazma does not
change host controllers or elevate privileges. Unsupported/missing boundaries
produce a visible unavailable error. HTTP bodies, DNS and HTML/XML parsing have
separate bounded ownership. Retained reports and journals have finite allowances;
these do not cover the whole installation, vector files or SQLite WAL/temporary
files. Native tools use the same contained owner/broker. Strict OS network mode
is required by default: Linux needs a verified isolated network namespace plus
cgroup controls; Windows currently refuses strict browser launch. Explicit
`KAZMA_BROWSER_NETWORK_MODE=compatibility` reports the weaker boundary. Windows
compatibility rendering failed the longer lifecycle profile: Node exit exceeded
its five-second wait and capacity was quarantined. It remains unqualified; see
the implementation report for preserved failed attempts and supported routes.

Public-target DNS validation has its own local worker boundary, including
redirects and browser HTTP assets. Four active validations and 16 queued calls
share a ten-second admission deadline. Each resolver child has five seconds,
256MiB memory and 16KiB output; incomplete or excessive answers refuse the
request. Every returned address still passes the existing public-address check.
Cancellation joins the child and its validator thread. Unverifiable cleanup
keeps its capacity quarantined, appears in readiness and is retried on the next
validation. Async and sync proxy transport and hosted async service connections
use the same bounded resolver; only an operator-selected proxy may have a private
endpoint. Logical Host/CONNECT names and verified TLS SNI are retained.
Acquisition synchronous target validation uses the same resolver budget; native
browser content is brokered through validated HTTP rather than direct DNS.

HTML/sitemap XML extraction and fixed HTTP search execute in bounded owned children, with
credential-free environments, capped input/output, memory and deadlines. Search
backends share one owned deadline/cancellation chain. An opaque DDGS proxy search
is refused because its request count cannot be bounded; fixed HTTP fallbacks
reserve their six-request upper bound before dispatch and keep the selected route.

Stored keys alone do not permit hosted requests. Scraping proxies require `proxy`
in the same explicit allowlist and configured proxy settings; when permitted,
they route HTTP/browser requests. Ambient `HTTP_PROXY`/`HTTPS_PROXY` are ignored.
Remote effects reserve requests durably before dispatch: `KAZMA_REMOTE_JOB_REQUESTS`
defaults to 256 (0–1024), `KAZMA_REMOTE_DAY_REQUESTS` to 1024 (0–4096, UTC day).
Zero denies dispatch. Redirects/retries/failures and uncertain effects consume
allowance; reservations are never refunds or observed provider charges. Active
limits can narrow but cannot widen mid-allowance. Storage failure refuses dispatch.
The ledger uses the existing SQLite/PostgreSQL settings backend with conditional
writes and bounded retention. Fixed search reserves an upper bound even if fewer
requests run.

`research_readiness(job_id=...)` and KB status expose retained evidence without
contacting providers. The Web page and `research_readiness(refresh_credits=True)`
can explicitly refresh Firecrawl's team balance; this query consumes allowance.
The cached observation is account scoped and fresh for five minutes, with a
60-second refresh cadence. Jina Reader's valid `X-Usage-Tokens` response header is
recorded before body reads and shown as provider-reported token usage, including
zero. Missing or malformed metadata remains unknown. Body failures and cancellation
retain the observation; cached reads create no new usage. These observations are
neither a money charge nor an account balance. Jina/proxy balances without a verified adapter and
per-request charges remain unavailable. A configured monetary ceiling refuses
remote requests without a provable upper bound. Cached English/Arabic Web, chat,
CLI and TUI inspection shares saved errors, reservations, usage and credit
freshness. Measured release outcomes and platform limits are recorded in the
[implementation report](https://github.com/Mubder/kazma/blob/main/docs/audits/WEB_ACQUISITION_IMPLEMENTATION_2026-10-10.md).

Playwright (optional install): `pip install -e ".[web]"` and `playwright install chromium`.

## Optional scraping proxy (IP/UA rotation) {#bulletproof-scraping-proxy-provider-addon-ipua-rotation}

The fetcher has an **opt-in, pluggable proxy provider** (`kazma_core/proxy/`)
so scraping can route through a residential rotating proxy — resilient to IP
blocks and rate limits. **Disabled by default** (`NullProvider` = direct); non-
users keep direct acquisition. The acquisition mode and `proxy` allowlist permission
are required in addition to the settings below.

- **Configure** in Settings → System → **Proxy Provider** (ConfigStore keys
  `proxy.*`; `proxy.password` auto-vault-encrypts). Live config — the provider is
  re-resolved on every fetch (`proxy/registry.py::get_proxy_provider`), so a
  Settings change takes effect without a restart.
- **anyIP.io** (`proxy/anyip.py`) is the first provider: residential/mobile
  rotation via `portal.anyip.io:1080`, flag-decorated username
  (`type_residential`, `country_US`, per-request `sessid_` for rotation or a
  sticky session). Each request exits from a different residential IP unless
  `session_sticky` is set.
- **Resilience applied transparently** across research + KB scrape paths:
  | Path | How proxy applies |
  |------|-------------------|
  | `read_url` / `web_acquire.fetch_text` (httpx tier) | `get_scraping_client` + 429/403 retry |
  | **Playwright** recovery (`read_url` + KB ingest) | CDP HTTP broker → `get_scraping_client`; native Chromium traffic goes to a local deny proxy |
  | `crawl_site` link spider | `get_scraping_client` |
  | Deep research pipeline acquires | Same fetch ladder as above |
  | KB page extract | Same ladder |
  | KB sitemap/robots discovery | `get_scraping_client` |
  | Remote SERP (Bing HTML, Wikipedia) | Fixed bounded HTTP child; permitted proxy route and reserved request allowance |
  | Local / Docker SearXNG | **Direct** (loopback — no residential hairpin) |
  | Remote SearXNG | Fixed bounded HTTP child; permitted proxy route when configured |
  | DuckDuckGo (`ddgs`) | Skipped visibly because its opaque origin fanout cannot enforce the shared origin policy |
  | Jina / Firecrawl **API** calls | **Direct** (third-party fetches the target; keep API keys off residential) |
  | LLM APIs | **Never** (`http_pool`) |
  - **IP rotation** — a blocked IP retries from a new residential IP.
  - **UA rotation** — curated pool in `proxy/client.py::USER_AGENT_POOL`.
  - **429/403 retry with backoff** (1.5 s, 3 s) on the `read_url` httpx path.
- **Scraping-scoped only.** The proxy never touches LLM API calls.
- **Graceful degradation** — unconfigured provider = direct; fetch does not hard-fail only because proxy is off.
- **Adding future providers** (BrightData, Oxylabs) = one class under `proxy/` +
  one registry line + one Settings dropdown option.

> **Note:** This does **not** solve CAPTCHAs (only IP/UA rotation + Playwright).
> A CAPTCHA-solving service could be a future provider type.

## Safety & honesty

- **SSRF-safe** on all fetches and redirects.  
- Saves stay **inside the active workspace** (any subpath; default auto-dir `KAZMA_RESEARCH_DIR`).  
- **Not** unlimited internet spidering.  
- **Not** anti-bot invincible.  
- Digests are **extractive** (no nested LLM inside the tool); the chat model synthesizes the final report.  
- HITL still applies to danger tools; research web tools are generally **read/safe** (writes go to workspace files via pathlib).

## Modes: quick vs deep

| Mode | How to trigger | Behavior |
|------|----------------|----------|
| **Quick** | “look up”, short questions | Free-form tools; 1–2 hops OK |
| **Deep / paper** | “research thoroughly”, “comprehensive report”, **`/research deep <topic>`**, or tool `run_research_pipeline` | Multi-query search → full-page acquire → digests → **LLM synthesis** → report under `research/reports/` |

The supervisor also has a **soft depth gate**: deep-worded requests that only ran `web_search` get one system nudge to fetch ≥2 full sources before concluding.

### Synthesis tools

| Tool | Role |
|------|------|
| `digest_research_file` | Extractive map of one file (no nested LLM) |
| `synthesize_from_digests` | Cross-source LLM analysis |
| `run_research_pipeline` | Full paper pipeline (search→acquire→digest→synthesize→save) |

## Recommended playbooks

### Single topic

1. `web_search` (≥2 queries for thorough work)  
2. `read_url_to_file` on top results (≥2 sources)  
3. `digest_research_file` then `synthesize_from_digests`  
4. Answer with citations  

### Docs site

1. `crawl_site(start_url, max_pages=12, max_depth=2)`  
2. `digest_research_file` per saved path (or selective chunks)  
3. Report  

The crawl index includes a durable job ID. Inspect its retained status through
`research_readiness(job_id="…")`. To continue an interrupted crawl, call
`crawl_site` with `resume_job_id="…"` and the same seed, workspace and options.
Changed scope, policy or limits refuse resume. Completed and uncertain pages are
not fetched or published again; uncertainty and capped coverage remain partial.
Existing files are preserved. Research crawls share the installation's four
active-job, 24 retained-job and 8MiB journal limits with KB crawls. Deep research
pipelines journal stage ownership separately in that shared store. Explicit
`resume_job_id` restores verified completed artifacts and continues only safe
pending stages; uncertain required planning, search, synthesis or publication
effects refuse replay. Background sessions expose interrupted state and an
explicit Resume action using the same pipeline contract.

### Comprehensive paper

```text
/research deep <topic>
# or
run_research_pipeline(topic="...", depth="deep", max_sources=8)
```

Works on **Web SSE**, **WebSocket**, and **gateway** chat. Pipeline runs
**parallel** search + acquire (semaphore 4), writes
`research/reports/pipeline-<id>/report.md`, optional DOCX
(`export_docx=True` or `KAZMA_RESEARCH_EXPORT_DOCX=1`), and registers the run
for the Research panel (`GET /api/research/papers`).

Stage progress is streamed as tool/status events (WS) or italic stage lines (SSE).

Each Python process admits at most four active pipelines and four background
sessions, with no waiting queue. A saturated start returns a visible capacity
error; the Web API returns 503 without accepting a new session. Execution keeps
its starting workspace and acquisition/robots policy. Pipelines have a cooperative
30-minute deadline, and concurrent runs use distinct report directories. Worker
and file-publication cleanup is joined before ownership ends; native I/O still requires its owned worker boundary. Interrupted background
sessions require explicit operator recovery.

`max_sources` bounds actual published source files, including gap fills. Failed
candidates may be replaced within the remaining budget. The pipeline records
every batch in `sources.json`; failed preflight or receipt storage stops the run
with a visible error. The pipeline returns a durable job ID. Explicitly resume
an interrupted job with `run_research_pipeline(topic="…", resume_job_id="…")`,
using its original topic, depth, source limit, language, parallel/export options
and workspace. Changed acquisition/configuration scope refuses resume. Completed
stages require unchanged saved file hashes. Uncertain sources are skipped and
reported as partial; a failed or uncertain required stage refuses replay. Review
that job before starting a new run, which may repeat searches or paid requests.
Dead background owners become interrupted and expose explicit Resume in the
Research page or `start_deep_research(resume_session_id="…")`. History is capped
at 256 rows/128MiB. Legacy chat-search records count toward this storage bound
even though they are excluded from completion monitoring. An install already
over the bound refuses new background sessions with a visible capacity error.
Review/export saved results before deliberately deleting settled history;
archiving alone does not release the row allowance. No automatic deletion is
performed to admit new work. Pipeline report retention has a separate 2GiB allowance
(512MiB reserved per active run); deleting a journal row does not reset it.
Existing files are never automatically deleted. Explicit-key/local model and
fallback clients are frozen; ambient AWS/Google ADC routes are refused for
durable research until their credentials can be captured.
`research_readiness(job_id="…")` can display cached reported usage from an earlier
remote job after switching to local-only mode, without making a provider call.
Reported tokens do not establish charges or an account balance.

### Swarm

`/swarm research …` / `dispatch_swarm` auto-researcher includes save/digest/pipeline tools.

## Diagnostics & fixes operators should know (2026-08-27)

- **SearXNG status notes are now truthful.** When a search reports
  `searxng:empty@…`, the log note carries SearXNG's OWN
  `unresponsive_engines` list (e.g. `brave: suspended`,
  `duckduckgo: CAPTCHA`) — the real story behind "all backends empty" for
  obscure queries. Suspensions self-heal; the note tells you which engines
  to wait on. A reachable instance returning 0 results no longer triggers
  the 60s dead-cooldown that used to make the next query skip SearXNG
  entirely.
- **Registry API JSON unblocked.** `read_url` accepts RFC 6839
  `+json`/`+xml` content types (`application/rdap+json`, `ld+json`, …) —
  taken-domain RDAP checks (HTTP 200 + rdap+json) previously died at the
  content-type gate while free-domain checks (404) worked. Domain
  availability sweeps now work end-to-end through `read_url`.

## Progress and result alerts

While the server runs, a local monitor checks durable deep/brief research
sessions, standalone research pipelines and crawls, and Knowledge crawls every
15 seconds. Completion produces a persistent notification with a link to the
saved run and results. Partial, failed, cancelled and interrupted outcomes have
distinct alerts. Linked session/pipeline records describe one run and do not
produce duplicate completion alerts. Ordinary chat-search rows keep their own
chat reply instead. A crawl or pipeline the agent runs inside its own turn
(`crawl_site`, or `run_research_pipeline` outside a Research session) is not
alerted either: its result goes back to the agent, which answers in that
turn, and an alert an older build stored for such a run is settled at the
next check. The run stays on the Research page. Research sessions and
Knowledge crawls you start still alert. Existing finished history is seeded quietly on first boot;
new outcomes and previously observed runs survive server restarts.

After 180 seconds without a stage, source-count or durable-effect change, the
monitor warns that no progress was observed. Heartbeats do not reset that clock.
The warning does not fail, cancel or retry the work. Meaningful progress clears
the warning and produces a recovery notice. Intentional long stages can therefore
produce a warning; inspect the run before intervening. Failed result-list loads
and disconnected live streams also show persistent page warnings.

Under **Settings → System → Web acquisition**, **Read alert settings** exposes
monitoring, completion notifications, the stall threshold (30–3600 seconds) and
the low-credit threshold (default 100). These settings are stored locally.
Fresh supported provider balances at or below the threshold produce an alert;
stale or unknown balances do not establish recovery. Actual HTTP 402, 429,
authentication and service failures have separate alerts. Exhausting a local
remote-request allowance is distinct from spending billed credits. Monitoring
does not contact providers, refresh balances, change acquisition policy or
resume interrupted work. An explicit permitted credit refresh is still needed
to obtain a newer account balance.

The notification bell and global banner read the persistent local ledger.
External messages reuse the existing ops routes in **Settings → Adapters &
Routes**. A platform acceptance records **delivered**; **requested** and
**uncertain** are not delivery proof. Refused delivery remains visible in the
notification. An uncertain delivery is not automatically replayed after a
crash. History retains the latest 128 observations for 30 days plus active stall
warnings; the UI shows the latest 50 plus active warnings. Monitor failures or
missed checks are visible rather than interpreted as healthy progress.

Acquisition defaults to `local_only`: API keys alone never authorize hosted
scraping. Remote fallback requires an explicit mode and provider allowlist.
This policy covers acquisition, not all AI computation: planning, summarizing
and report synthesis use the configured model. Choose a local model if those
steps must also run locally.

## Related

- [Tools catalog](../reference/tools-catalog)  
- [Environment variables](../reference/environment-variables)  
- [Skills, MCP & Tools](skills-mcp-and-tools)  
- [Portability](../ops/portability) (workspace + `kazma-data/`)  

### Cancellation acknowledgement

A live deep-research session enters **cancelling** when cancellation is requested.
It becomes **cancelled** after its owned pipeline work finishes cleanup. The Web
panel keeps its progress connection open during that wait and reconnects when a
cancelling session is reopened. Deleting an active session requests cancellation
and returns HTTP 409 (`research_session_busy`); retry deletion after cleanup.
Late progress cannot restore a cancelled session to running. This contract does
not authorize automatic replay after a restart. Explicit session/job resume uses
the durable recovery contract above.

Standalone extract/screenshot publication has a conservative shared lifetime
allowance of 256 files/512MiB and 24MiB per file. Reservations precede writes
and are retained on failure; no automatic refund or destructive cleanup is
performed. Existing standalone output is preserved and is not retroactively
inventoried by this ledger. Knowledge SQLite text/FTS/history has a 1GiB file
ceiling; a full database refuses writes. These controls do not bound all Chroma
vector files, SQLite temporary/WAL space or unrelated user files.
