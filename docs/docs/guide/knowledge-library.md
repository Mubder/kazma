---
id: knowledge-library
title: Knowledge Library
sidebar_label: Knowledge Library
description: Ingest whole documentation sites once, then have the agent reason over the corpus with cited sources.
---

# Knowledge Library

A **Knowledge Library** is a named, managed corpus of ingested documentation — for example, the Meta WhatsApp Cloud API. You point Kazma at a doc root once; it crawls the whole tree, chunks it hierarchy-aware, embeds it, and indexes it. The agent then **reasons over** the corpus and **cites sources** when you ask questions.

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

## How ingestion works

When you give Kazma a seed URL (e.g. `https://developers.facebook.com/docs/whatsapp/cloud-api`):

1. **Discovery (sitemap-first).** Kazma reads `robots.txt` for `Sitemap:` directives, then tries `/sitemap.xml`, `/sitemap_index.xml`, `/docs/sitemap.xml`. The resulting URLs are filtered to the **seed's path prefix** (`/docs/whatsapp/overview` → `/docs/whatsapp/`), so the crawl stays inside the doc subtree and doesn't wander into unrelated docs. Fallback: BFS link-walk using Playwright-rendered HTML (so SPA nav links are captured).
2. **Fetch (tiered + tab-aware).** Each page is fetched via the shared tiered extractor (Jina → Firecrawl → httpx+trafilatura → Playwright). Tabbed/JS pages get a Playwright full-DOM pass that pulls text from **all** elements including hidden panels, so per-tab content isn't lost.
3. **Chunk (hierarchy-aware).** Markdown is split on `#`/`##`/`###`/`####` headers with a section breadcrumb (`"Messages > Send Text Message"`). **Fenced code blocks are atomic** — never split, even when oversized.
4. **Embed + index.** Each chunk is embedded (local `all-MiniLM-L6-v2` by default, or any OpenAI-compatible `/embeddings`) and stored in a per-library ChromaDB collection + a dedicated FTS5 table + SQLite (source of truth). Re-ingest dedups via `content_hash`.

Discovery + fetch caps: `KAZMA_KB_MAX_PAGES` (default 200, hard cap 1000), `KAZMA_KB_MAX_DEPTH` (default 10), `KAZMA_KB_DELAY_MS` (default 300), `KAZMA_KB_SCOPE_MODE` (`tree` | `prefix` | `domain` | `exact`, default **`tree`**).

**Re-ingest hygiene:** indexing a URL **purges** prior chunks for that URL first (SQLite + FTS + Chroma) so page shrinks never leave orphan sections. **Refresh** jobs are durable (same ConfigStore job store as crawl). Agent tool `knowledge_ingest_site` is capped lower (~15 pages) than UI crawls — use `/knowledge` or `/kb crawl` for large trees.

**Auto-inject** only includes **non-archived** libraries for the **current tenant** (when multi-user/prod tenant filter is on).

**Smart search (optional):** set `KAZMA_KB_SMART_SEARCH=1` (or ConfigStore
`knowledge.smart_search=true`) to also retrieve from **active libraries with
chunks** when the user message looks technical (API/docs/how-to), even if
per-library auto-inject is off. Kill switch `KAZMA_KB_AUTO_INJECT=0` still
disables all injection.

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
- `sse_chat.py` — Web SSE chat, per turn
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

## Architecture notes (for contributors)

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

**Why a separate namespace (not the shared `UnifiedMemoryAdapter`)?** The shared adapter's L1 is the `agent_memory` collection (KB would leak into chat recall), its L3 FTS5 layer doesn't reliably filter by metadata, and every layer keys UID on a bare `sha256(text)[:16]` which collides on identical sections across pages. The KB reuses the `VectorStore` *class* and `get_embedder()` singleton, but in dedicated per-library collections.

## Optional dependencies

Indexed retrieval needs the `rag` extra (`pip install kazma[rag]` → `chromadb`, `sentence-transformers`, `sqlite-vec`); JS/tabbed-page fetch needs the `web` extra (`pip install kazma[web]` → `playwright`, then `playwright install chromium`). Without them the system degrades gracefully: no semantic search (FTS5-only), no JS-page rendering.

## Limits & honest caveats

- **Bot-walled sites need a fetch backend.** Sites that block non-browser clients via TLS/header fingerprinting — Meta (`developers.facebook.com`), Cloudflare-protected properties, Instagram, etc. — return a 200-OK `<title>Error</title>` stub to httpx and defeat vanilla Playwright too. For those, set **one** of:
  - `KAZMA_FIRECRAWL_API_KEY=<key>` — paid, best quality, handles JS + bot walls
  - Jina Reader (`r.jina.ai`) — used automatically on hard-page recovery unless `KAZMA_JINA_READER=0`; set `=1` to always try first; optional `JINA_API_KEY`
  - `pip install kazma[web]` then `playwright install chromium` — local headless browser, works for SPAs that aren't server-side fingerprinting
  The job's `errors` list (visible in the progress panel + `/kb status`) names the missing backend when all tiers fail, so you know exactly which to enable.
- **Chromium binary is separate from the Python package.** `pip install kazma[web]` installs the playwright Python bindings; you still need `playwright install chromium` to download the browser binary itself. The job error names this explicitly: *"Chromium binary not installed (run: playwright install chromium)"*.
- A small fraction of heavily-obfuscated SPA doc sites can resist all of the above. Pages that fail are reported in the job log so you can add them manually.
- Local embeddings (`all-MiniLM-L6-v2`) are free but slower on CPU; remote (e.g. NVIDIA NIM `nv-embed-v1`) is faster/better but costs money. See `memory.embedding:` in `kazma.yaml`.
- Crawl is bounded by `KAZMA_KB_MAX_PAGES` (hard cap 1000) to prevent runaway.
- **Library IDs are slugged automatically** to keep them ChromaDB-safe (`"Meta WhatsApp Docs"` → `meta_whatsapp_docs`). Spaces/uppercase in the input are normalized on insert; you'll see the slug form in the UI.
- **Job registries are per-process**: a crawl started from the web UI is visible via `/api/kb/jobs/{id}` (web) but not via `/kb status` (chat), and vice versa. Cross-process job visibility is a planned future improvement; for now, start and check from the same surface.
