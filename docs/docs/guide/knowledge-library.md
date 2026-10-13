---
id: knowledge-library
title: Knowledge Library
sidebar_label: Knowledge Library
description: Ingest whole documentation sites once, then have the agent reason over the corpus with cited sources.
---

# Knowledge Library

A **Knowledge Library** is a named, managed corpus of ingested documentation — for example, the Meta WhatsApp Cloud API. You point Kazma at a doc root once; it crawls the whole tree, chunks it hierarchy-aware, embeds it, and indexes it. The agent then **reasons over** the corpus and **cites sources** when you ask questions.

**Operator tour (smart re-index, inject, Settings toggles):** [Recent features](./recent-features#3-knowledge-library-hardening).

This is RAG (Retrieval-Augmented Generation) over a curated, updatable corpus — not live scraping, and not fine-tuning. Doc content lives in its own per-library namespace, completely isolated from chat memory.

> **Arabic brand:** product name is **Kazma** / **كاظمه** (or **كاظمة**). Never **كازما**.

## When to use it

| You want | What to do |
|----------|------------|
| The agent to know a specific API/docset deeply | Ingest once, then ask questions — it cites the source URL + section |
| Authoritative answers with sources | Each `knowledge_search` hit carries `source_url` + `section_header` |
| Update when docs change | Re-ingest (refresh); only changed pages are re-indexed (content-hash dedup) |
| Multi-platform access | Same library works from Web, Telegram, Discord, Slack, TUI |

If you instead want the agent to *search the live web* per question, see [Web research](./web-research.md) — that's a different feature (ephemeral results, no indexed corpus).

### Document Intelligence → library bridge {#document-intelligence--library-bridge}

You can also **publish a processed local document** (upload / `document_import`)
into a Knowledge library after it reaches `ready`:

| Path | How |
|------|-----|
| Web | Documents page → pick a library → **Add to library**; the page lists the libraries a document is in, each with a remove button (`POST /api/documents/{document_id}/unindex`) |
| API | `POST /api/documents/{document_id}/index` with library id |
| Agent | tool `document_index(document_id, library_id)` |
| Search | `document_search` / `POST /api/documents/search` — hits are **fenced** as untrusted data |

Auto-index after parse is **off by default** (explicit index only). Crawl-based
KB ingestion (this page) and document-platform indexing share library ids but
are different intake pipelines. Deleting a document takes it out of every
library that holds it first; a library it cannot leave stops the delete, so a
retry can finish it. The document store records what it indexed where: after a
restore of the store, each worker start re-publishes every version the store
records as indexed but its library no longer holds, and reports a missing
library or manifest instead of inventing one
(`scripts/document_pg_backup.py index` runs the same check by hand). See
[Document Intelligence](./document-intelligence.md).

## How ingestion works

When you give Kazma a seed URL (e.g. `https://developers.facebook.com/docs/whatsapp/cloud-api`):

1. **Discovery (sitemap-first).** Kazma reads `robots.txt` for `Sitemap:` directives, then tries `/sitemap.xml`, `/sitemap_index.xml`, `/docs/sitemap.xml`. The resulting URLs are filtered to the **seed's path prefix** (`/docs/whatsapp/overview` → `/docs/whatsapp/`), so the crawl stays inside the doc subtree and doesn't wander into unrelated docs. Fallback: BFS link-walk using Playwright-rendered HTML (so SPA nav links are captured).
2. **Fetch (tiered + tab-aware).** Each page is fetched via the shared tiered extractor (Jina → Firecrawl → httpx+trafilatura → Playwright). Tabbed/JS pages get a Playwright full-DOM pass that pulls text from **all** elements including hidden panels, so per-tab content isn't lost.
3. **Chunk (hierarchy-aware).** Markdown is split on `#`/`##`/`###`/`####` headers with a section breadcrumb (`"Messages > Send Text Message"`). **Fenced code blocks are atomic** — never split, even when oversized.
4. **Embed + index.** Each chunk is embedded (local `BAAI/bge-m3` by default, or any OpenAI-compatible `/embeddings`) and stored in a per-library ChromaDB collection + a dedicated FTS5 table + SQLite (source of truth).

Discovery + fetch caps: `KAZMA_KB_MAX_PAGES` (default 200, hard cap 1000), `KAZMA_KB_MAX_DEPTH` (default 10), `KAZMA_KB_DELAY_MS` (default 300), `KAZMA_KB_SCOPE_MODE` (`tree` | `prefix` | `domain` | `exact`, default **`tree`**).

### Smart re-index (refresh hygiene)

| Case | Behavior |
|------|----------|
| Page **unchanged** (same ordered `content_hash` list) | **Skip** purge + embed (cheap refresh) |
| Page **changed** or shrank | Purge that URL (SQLite + FTS + Chroma), then write new chunks — no orphan sections |
| URL **gone** from discovery (in seed scope) | Pruned on full-site crawl/refresh |
| **Refresh** jobs | Durable (ConfigStore `kb_jobs`, same as crawl) |

Agent tool `knowledge_ingest_site` is capped lower (~15 pages) than UI crawls — use `/knowledge` or `/kb crawl` for large trees.

### Recall + inject (one hybrid stack)

Chat does **not** merge KB into the memory schema. Product path:

1. **Hybrid RRF** — per-library Chroma semantic + FTS5 BM25, fused once (`KnowledgeIndex.search_all_sync`)  
2. **Federated search** — labels hits `store=knowledge` next to V2 memory hits (no one-table merge)  
3. **Inject mode** — `auto_inject` libraries (tenant + non-archived); optional smart expansion  
4. **Fence** — all inject goes through `format_untrusted_block` (docs are untrusted)  
5. **Kill switches** — `KAZMA_KB_AUTO_INJECT=0`, `merge_knowledge_into_chat` in memory config  

**Auto-inject** only includes **non-archived** libraries for the **current tenant** (when multi-user/prod tenant filter is on).

**Smart search (optional):** set `KAZMA_KB_SMART_SEARCH=1` (or ConfigStore
`knowledge.smart_search=true`) to also retrieve from **active libraries with
chunks** when the user message looks technical (API/docs/how-to), even if
per-library auto-inject is off.

## Use it

### From the Web UI

Open **`/knowledge`** → "Add a library" → enter an ID (e.g. `shipx_whatsapp_api`), a name, and the seed URL → choose:

- **Ingest single page** — instant, one URL.
- **🕷️ Crawl whole doc tree** — background job; watch live progress (discovered / fetched / ingested / failed).

Per library you can: **🔍 Test search** (try a query in-page), **↻ Refresh** (re-crawl from seed), **📋 Browse** chunks, **🗑 Delete**.

### From chat (Telegram / Discord / Slack)

| Command | Does |
|---------|------|
| `/kb` | List libraries + help |
| `/kb add <id> <url>` | Create-or-use library, ingest ONE page (sync) |
| `/kb crawl <id> <url> [N]` | Ingest the WHOLE doc tree (background job) |
| `/kb refresh <id>` | Re-crawl a library from its seed URL |
| `/kb search <id> <query>` | Direct search (also useful without the LLM) |
| `/kb status <id>` | Live progress of a running crawl/refresh |
| `/kb delete <id>` | Delete a library + all its chunks |

Example:

```
/kb crawl shipx_whatsapp_api https://developers.facebook.com/docs/whatsapp/cloud-api
/kb status shipx_whatsapp_api
```

Then just ask your question normally. The agent decides when to consult the library via the `knowledge_search` tool.

## Auto-inject (the "just knows" behaviour)

By default the agent calls `knowledge_search` when *it* decides the question needs library context. If you'd rather have relevant chunks **folded into every prompt automatically**, flip the **auto-inject** toggle on a library (Web UI checkbox, or `PATCH /api/kb/libraries/{id}`).

When auto-inject is on, the top-k chunks for the user's latest message are retrieved and added to the system prompt — **fenced as untrusted data** (`<kazma:data source="knowledge" untrusted="true">`), so a malicious doc page can't smuggle instructions. Three injection points (mirroring the self-improvement Soul, see [Security & safety](./security-and-safety.md)):

- `agent_runner.py` — main agent init (no-op; auto-inject is per-turn)
- `sse_chat/` — Web SSE chat, per turn
- `gateway graph.py` — Telegram/Discord/Slack, per turn

**Kill switch:** `KAZMA_KB_AUTO_INJECT=0` disables the whole subsystem at runtime (checked live, per turn). Per-library opt-in is still required even with the kill switch on, so behaviour is strictly opt-in.

Tunable: `KAZMA_KB_AUTO_INJECT_TOP_K` (default 3, max 10) controls how many chunks per turn.

## Citation footer

Every answer derived from Knowledge Library data carries a visible footer so you can tell where the information came from:

> 📚 This data is from Knowledge "shipx_whatsapp_api".

This applies to both the `knowledge_search` tool path (explicit) and the auto-inject path (implicit). The footer names the specific library (or libraries, if the search spanned multiple). This is a soft, prompt-level directive — the model is instructed to append it verbatim.

## Archive

Libraries can be **archived** — hidden from the Active list without deleting their chunks. Useful for failed or abandoned crawls that you don't want cluttering the main view, but whose data you might still want to search.

- **📦 Archive button** on each library card (Active view).
- **♻️ Restore button** on each library card (Archived view).
- **Active / Archived tabs** at the top of the library list.
- Archived libraries stay **searchable** — their chunks remain in the index. Only the list view hides them.
- **Delete** is separate and permanent; archive is reversible.

This mirrors the Research panel's archive pattern (a soft `archived` flag, not a separate table).

## Crawl progress and completion alerts

The notification bell retains a completion alert linking to the saved Knowledge
crawl. Partial, failed, cancelled and interrupted jobs have distinct alerts.
No observed progress produces a warning; meaningful progress clears it.
**Settings → System → Web acquisition** controls these alerts and thresholds.
Monitoring reads saved local evidence without provider calls or automatic
retries. Reopening the page restores retained job status; explicitly resume an
interrupted job after reviewing uncertain effects.
See [Progress and result alerts](web-research#progress-and-result-alerts).

## Architecture notes (for contributors)

The shared fetch result distinguishes coded failures, observed login screens,
partial content and successful extracts. Content beginning `Error:` is not
treated as a failed request. Partial shared-ladder content is refused before
indexing. The shared knowledge profile preserves hidden panels, code and tables;
bounded Chromium interaction captures each supported lazy tab while it exists.
Link discovery uses that same scoped typed acquisition. Redirect destinations
are checked before dispatch, including browser HTTP redirect hops. Hosted
discovery is refused when the provider cannot enforce crawl scope. Query values
retain page identity. Native deny-proxy controls are not OS network isolation.
KB site/refresh jobs persist ownership before acceptance and retain page/BFS and
parsed sitemap/llms metadata outcomes. Reconnect restores status; explicitly
resume interrupted jobs to continue pending work. Uncertain acquisition/indexing
effects are skipped, visibly reducing coverage. Completed requests are not
replayed. This journal retains at most four active jobs, 24 total jobs and 8MiB;
the limit fails before more acquisition. It is not an unlimited crawl archive.

The page shows actual acquisition attempts, bounded remote request reservations
and cached provider balances. Firecrawl balance refresh is explicit and metered,
with five-minute freshness and account scope. Zero means an observed zero;
unavailable/stale balances and per-request charges are not converted to zero.
Request allowances are not financial spending caps. Origin scheduling persists
through the shared settings store. Research pipelines have a separate stage
journal with explicit recovery and refusal to replay uncertain required effects.
See the [implementation report](https://github.com/Mubder/kazma/blob/main/docs/audits/WEB_ACQUISITION_IMPLEMENTATION_2026-10-10.md)
for measured qualification, retained failed attempts and platform limits.

| Layer | File | Purpose |
|-------|------|---------|
| Store | `kazma-core/kazma_core/stores/knowledge.py` | SQLite `knowledge_libraries` + `knowledge_chunks` + `knowledge_chunks_fts` (FTS5). Source of truth. |
| Chunker | `kazma-core/kazma_core/stores/knowledge_chunker.py` | Pure-stdlib header+code-aware splitter. **No LangChain dep.** |
| Index | `kazma-core/kazma_core/stores/knowledge_index.py` | Per-library ChromaDB + FTS5 + RRF (k=60). Hard isolation from `agent_memory`. |
| Ingest | `kazma-core/kazma_core/stores/knowledge_ingest.py` | Sitemap-first discovery, tiered fetch, Playwright full-DOM for tabs. |
| Tool | `kazma-core/kazma_core/agent/tool_registry.py` `knowledge_search` | Agent-callable; empty `library` → cross-library RRF. |
| Gateway | `kazma-gateway/kazma_gateway/agent_handler/commands.py` `_try_kb_command` | `/kb` slash commands on all chat platforms. |
| Web API | `kazma-ui/kazma_ui/kb_api.py` | `/api/kb/*` router. |
| Web page | `kazma-ui/kazma_ui/templates/knowledge_base.html` + `static/js/kb.js` | `/knowledge` page. |

**Why a separate namespace (not chat memory)?** The KB uses its own ChromaDB collections + FTS5 (`kazma_kb_<library_id>`), fully isolated from V2 chat memory (`memory_state.db` beliefs/episodes). Without isolation, KB content would leak into chat recall, and the KB's per-library metadata filtering + UID scheme (`sha256(text)[:16]`) doesn't match V2's bi-temporal belief model. The KB reuses the shared `VectorStore` *class* (`memory/vector_store_global.py`) and `get_embedder()` singleton, but in dedicated per-library collections.

## Optional dependencies

Indexed retrieval needs the `rag` extra (`pip install -e ".[rag]"` → `chromadb`, `sentence-transformers`, `sqlite-vec`); JS/tabbed-page fetch needs the `web` extra (`pip install -e ".[web]"` → `playwright`, then `playwright install chromium`). Without them the system degrades gracefully: no semantic search (FTS5-only), no JS-page rendering.

**Every chunk gets its meaning vector.** A chunk is embedded when it is ingested, if the vector store is up at that moment. A 15-minute maintenance pass (`KnowledgeIndex.backfill_vectors`, up to 5 minutes each, last in the maintenance cycle) makes each library's vectors match its chunks: it embeds any chunk the store lacks -- the shortest first, across all libraries -- and removes the vectors of chunks that were retired while it was down, so a library indexed during an outage catches up by itself. Library health shows `vector_chunks` beside `sqlite_chunks`. From 2026-07-31 to 2026-09-26 the store never came up -- its client module had been deleted and the failure was logged as "chromadb not installed" -- so every library was keyword-only; the pass restores them.

Vectors are compared by angle (cosine), and each is made unit length before it is stored or searched, so a collection created before 2026-09-26 (Chroma's default, squared L2) ranks the same. A vector is made from the first 1,500 characters of its chunk; keyword search reads the whole chunk. (On CPU, bge-m3 embeds a chunk of median length in a few hundredths of a second and a 4,000-character one in about three and a half seconds.) They live in `KAZMA_VECTOR_PATH` if set, else in `vector_memory/` beside the Knowledge database (`kazma-data/vector_memory`). Deleting a library drops its collection. Each vector records the embedding model that made it: after a model switch the maintenance pass embeds the library again, rebuilding a collection whose vectors have another size first. The memory "Rebuild embeddings" action no longer deletes this folder (it did, from when the folder held the retired V1 chat index). Backups leave the folder out -- a copy of a live Chroma database can be torn -- and the maintenance pass rebuilds it after a restore. Chroma's telemetry is off.

Each library on the Knowledge page shows how much of it meaning search reaches: "Meaning search: N of M", or "Keyword search only" when the vector store is unavailable.

## Limits & honest caveats

- **Acquisition defaults to local-only.** Install the `web` extra and Chromium for JavaScript pages. Some bot walls defeat both HTTP and local Chromium; those pages must remain visible failures. Hosted Firecrawl/Jina require `KAZMA_ACQUISITION_MODE=local_first_with_remote_fallback` and their names in `KAZMA_ACQUISITION_REMOTE_PROVIDERS`, plus credentials where required. Stored keys and legacy Jina flags alone cannot authorize hosted fetching. See [web research](./web-research) for the policy and current accounting limitations.
- **Refreshes preserve retained pages.** Missing a URL in discovery, including sparse/capped/failed discovery, does not delete its indexed content. An ingest where every page fails ends in error and keeps the reasons.
- **Robots checks default on.** KB discovery and ingest use bounded raw KazmaBot policies before content, redirects and browser assets. Missing 404/410 differs from failed or malformed checks, which refuse crawling. Explicit `KAZMA_CRAWL_RESPECT_ROBOTS=0` is recorded in acquisition evidence. Hosted readers cannot bypass enforcement; cross-origin control-file redirects are conservatively refused. See [web research](./web-research#robots-policy) for limits and remaining qualification.
- **Chromium binary is separate from the Python package.** `pip install -e ".[web]"` installs the playwright Python bindings; you still need `playwright install chromium` to download the browser binary itself. The job error names this explicitly: *"Chromium binary not installed (run: playwright install chromium)"*.
- **Linux Chromium needs delegated cgroup v2 controls.** The driver and browser share an aggregate memory/CPU/task budget. Missing memory, pids, CPU or group-kill controls cause a visible unavailable error; configure service delegation or `KAZMA_BROWSER_CGROUP_ROOT`. See [web research](./web-research) for limits, watchdog cleanup and remaining qualification.
- A small fraction of heavily-obfuscated SPA doc sites can resist all of the above. Pages that fail are reported in the job log so you can add them manually.
- Local embeddings (`BAAI/bge-m3`) are free but slower on CPU; remote (e.g. NVIDIA NIM `nv-embed-v1`) is faster/better but costs money. See `memory.embedding:` in `kazma.yaml` or the **Settings → Embedder** page.
- Crawl is bounded by `KAZMA_KB_MAX_PAGES` (hard cap 1000) to prevent runaway.
- **Library IDs are slugged automatically** to keep them ChromaDB-safe (`"Meta WhatsApp Docs"` → `meta_whatsapp_docs`). Spaces/uppercase in the input are normalized on insert; you'll see the slug form in the UI.
- **Job evidence is durable and shared by the installation.** `/api/kb/jobs/{id}` and `/kb status` restore retained crawl observations. Live worker/capacity counters remain per-process; restored status alone does not resume interrupted execution.
