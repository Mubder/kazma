---
id: recent-features
title: Recent features guide
sidebar_label: Recent features
description: Operator guide for deep research, KB hardening, proxy coverage, memory explain, /memory admin (rename, list↔graph, hub), and Settings toggles
---

# Recent features guide

This page is the **operator-facing tour** of the features landed in the
research → KB → memory polish tranche (including the **/memory** admin
graph/rename/hub work). Use it to turn features on, try them once, and find
the deep docs when you need detail.

**Smoke checklist (when you test later):** [Smoke matrix](../ops/smoke-matrix).  
**Architecture context:** [Web research](./web-research) · [Knowledge Library](./knowledge-library) · [Document Intelligence](./document-intelligence) · [Document phases](./document-phases) · [Memory best path](./memory-best-path).

---

## At a glance

| Area | What you get | Where |
|------|----------------|--------|
| Deep research | Multi-source pipeline, live sessions, routing, rubric | `/research`, chat, `/research deep` |
| **Document Intelligence** | Secure ingest, OCR, index, generate/convert/redact, ops (capacity/GC/audit), cert | `/documents`, `/api/documents/*`, `/documents` slash, `document_*` tools, TUI Documents |
| Proxy Provider | Residential proxy for scrape/crawl/Playwright/SERP | Settings → System |
| Knowledge Library | Smart re-index, gone-URL prune, hybrid inject; **document_index** bridge | `/knowledge`, Settings → Memory, Documents → Index |
| Memory admin | Graph dedupe, rename, list↔graph, belief edit, hub brand | `/memory` |
| Memory explain | Channel chips on chat turns + Dashboard probe | Settings → Memory → Explain recall |
| Golden eval | Offline recall regression | Dashboard → Run golden eval |
| Topic-shift focus | Agent soft-resets focus when user changes subject; tunable drift threshold | Settings → `agent.topic_drift.*` |
| Non-Stop & Self-Healing | Supervisor watchdog, model failover chain, call ledger, orphan recovery, HITL timeout | Settings → Agent → Non-Stop Execution |
| Scraper Hardening | Size caps (5MB default), 5xx retry backoff, robots.txt compliance | `read_url`, `crawl_site`, `KAZMA_FETCH_MAX_BYTES` |
| Truncation Auto-Retry | Double max_tokens on length truncation + `file_append` chunk tool | `llm_provider`, `LocalToolRegistry` |

---

## 1. Deep research (product path)

### What it does

`run_research_pipeline` plans queries, searches via `web_acquire`, ranks URLs,
acquires full pages, digests, synthesizes, optional gap-fill, and writes a
report under `research/reports/` with a structural **rubric**.

Industry stages (R0–R4): ranking + claims + fail-closed deep → adaptive plan +
gap loop → **durable sessions + SSE** → soft route to pipeline + eval API.

### How to run

| Entry | How |
|-------|-----|
| **Web** | Open **`/research`** → topic, depth (Deep/Brief), max sources → **Start** |
| **Chat** | “Deep research on …” (supervisor prefers `run_research_pipeline`) |
| **Gateway** | `/research deep <topic>` (progress pings while running) |

### Live sessions (R3)

- Session rows: `kazma-data/research_sessions.db`
- Start: `POST /api/research/sessions`
- Live progress: `GET /api/research/sessions/{id}/stream` (SSE)
- **Cancel** button while status is `running` / `pending`
- List shows `[Deep]` sessions + pipeline papers + swarm research tasks

### Routing (R4)

Deep-worded asks get a system hint to call the pipeline once instead of a long
manual `web_search` chain. Manual multi-hop on a deep request gets one “prefer
pipeline” nudge. Disable: `KAZMA_RESEARCH_ROUTE=0`.

### Quality

- Report + `rubric.json` under the paper folder  
- Session stores `rubric_score` / `rubric_ok`  
- Score any report: `GET /api/research/eval?path=…` or `?session_id=…`

**Deep dive:** [Web research](./web-research).

---

## 2. Shared web acquisition + Proxy Provider

### Shared stack

Search / scrape / crawl I/O live in **`kazma_core.web_acquire`** (used by
research *and* KB page extract). LLM APIs never go through this stack.

### Proxy coverage (Settings → System → Proxy Provider)

When a provider is configured (e.g. anyIP), it applies to:

| Path | Proxied? |
|------|----------|
| Page fetch (httpx ladder) research + KB | Yes |
| Playwright recovery (`read_url` + KB) | Yes |
| `crawl_site` link spider | Yes |
| KB sitemap / robots discover | Yes |
| Bing / Wikipedia SERP | Yes |
| DuckDuckGo (`ddgs`) | Yes (when Proxy Provider configured) |
| Remote SearXNG | Yes |
| Local / Docker SearXNG | **No** (loopback — no hairpin) |
| Jina / Firecrawl **API** calls | **No** (they fetch the target server-side) |
| LLM provider APIs | **Never** |

Config is live (no restart). Password vault-encrypts.

**Deep dive:** [Web research → Bulletproof scraping](./web-research#bulletproof-scraping-proxy-provider-addon-ipua-rotation).

---

## 3. Knowledge Library hardening

### Smart re-index

| Situation | Behavior |
|-----------|----------|
| Page **unchanged** (same ordered content hashes) | Skip purge + embed |
| Page **changed** or **shrank** | Purge URL (SQLite + FTS + Chroma), rewrite |
| URL **gone** from discovery (in seed scope) | Pruned on site crawl/refresh |

Job UI / toasts show **skipped · unchanged · pruned · failed**.

### Recall + inject (one hybrid stack)

**Stores stay separate** — no one-table merge with V2 beliefs.

```text
Chat turn
  ├─ V2 memory recall (beliefs + episodes)
  ├─ KB inject (RRF: Chroma semantic + FTS5)  ← same as knowledge_search
  └─ All inject fenced as untrusted docs
```

| Mode | Libraries |
|------|-----------|
| **Inject** | `auto_inject=1` libs (+ smart search expansion) |
| **Federated / tools** | All active non-archived libs with chunks |

### Settings → Memory

| Toggle | Key | Effect |
|--------|-----|--------|
| Inject Knowledge into chat | `memory.v2.merge_knowledge_into_chat` | Per-turn KB inject |
| Promote top KB hits | `memory.v2.promote_kb_to_episodes` | Soft mirror to episodes |
| **Smart Knowledge search** | `knowledge.smart_search` | On technical Qs, inject from all active libs with chunks |
| **Explain recall** | `memory.v2.explain_recall` | Tag hits + chat panel (below) |

Kill switch: `KAZMA_KB_AUTO_INJECT=0`.

**Deep dive:** [Knowledge Library](./knowledge-library) · [Memory best path](./memory-best-path).

---

## 4. Memory explain + golden eval

### Chat-turn Memory context panel

**Industry default:** `explain_recall` is **on** in config defaults (and Settings
UI default). When inject happens with explain off, the panel still shows a
**summary** (counts + short previews) plus a hint to enable full chips.

1. Keep **Explain recall** on (Settings → Memory) for full channel chips.  
2. Chat as usual (seed a fact, then ask).  
3. Open the turn **workbench** (progress card).  
4. **Memory context** lists beliefs / episodes / KB rows with **channel chips**:

| Chip | Meaning |
|------|---------|
| `fts5` / `belief_fts` | Lexical |
| `dense` | Embedding similarity |
| `belief_ppr` | Multi-hop on the belief graph (default **3-hop**) |
| `session_boost` | Same-thread episodes |
| `kb_rrf` | Knowledge hybrid RRF |

Empty turn → “No memory/KB hits this turn”.

Works on **SSE and WebSocket** chat.

### Dashboard

- **Memory probe** — same channel chips  
- **Federated** — memory + KB labeled  
- **Run golden eval** — offline fixture pass rate (`POST /api/memory/v2/eval/golden`)

### Belief multi-hop (PPR)

Belief-graph Personalized PageRank uses confidence-weighted edges and a
configurable hop radius (`memory.v2.ppr_hop_radius`, default **3**) so chains
like `user → works_at → Acme → located_in → Paris` can surface.

**Deep dive:** [Memory & RAG](./memory-and-rag).

---

## 4b. Memory admin UI — graph, rename, list bridge (2026-08)

Operator page **`/memory`**: graph on top, entities/beliefs/merge/hygiene below.

| Capability | Behavior |
|------------|----------|
| **No duplicate graph ids** | Object text that equals an entity id (e.g. `shipx`) is one real node, not entity + virtual fact |
| **Display rename** | Id stable; name + aliases change (`ShipX`, hub **Mubder**) |
| **List ↔ graph** | Click row ⇄ click node; rename/merge/invalidate refresh canvas |
| **Edit belief** | Beliefs → **Edit** → PATCH triple (object/predicate/subject) |
| **Hub identity** | `ent_*` person User shells map to hub `user`; rename syncs hub label |

**Deep dive:** [Memory & RAG — admin UI](./memory-and-rag.md#memory-admin-ui-memory) · [Memory best path](./memory-best-path).

---

## 4c. Memory page overhaul + cron reminders (2026-08)

A full pass on the operator `/memory` page — usefulness, performance,
correctness, accessibility — plus a long-standing cron-reminder crash fix.

**Memory page (`/memory`):**

| Capability | What changed |
|------------|--------------|
| **Pagination + counts** | Lists show "Showing 1–150 of 3,412" + **Load more**; graph shows a truncation banner. No more silent cap masquerading as empty. |
| **Real search** | Diacritic-insensitive (`francais`→`Français`), alias-aware FTS5 search across beliefs + entities. |
| **"Why recalled"** | Click a belief → recall history (count, last time, origin episode) + **Probe from this belief**. |
| **Undo** | Invalidate / link / edit / delete show an **[Undo]** toast for 60s. Merge shows a rewired-count receipt. |
| **~10× faster page-open** | Materialized entity counts replace per-row correlated subqueries; self-heals if a write site is missed. |
| **Multi-tenant** | `KAZMA_MEMORY_ENFORCE_TENANT=1` isolates memory per tenant (off by default). |
| **Accessibility** | `aria-live` status, table captions, dynamic canvas descriptions, single belief-edit modal. |

**Cron reminders now actually deliver:**

- The scheduler was constructed without a `graph_builder=`, so every scheduled
  reminder crashed with "No graph builder configured" on fire. Now wired.
- Reminders also never reached Telegram because the delivery target wasn't
  captured; now `delivery_target` (the originating `telegram:<chat_id>`) is
  captured at schedule time and used at fire time. (SessionStore lookup is
  not a viable fallback — sessions TTL-evict after 5 min.)

**Deep dive:** [Memory & RAG — operator capabilities](./memory-and-rag.md#operator-capabilities-2026-08-overhaul) · [env vars](../reference/environment-variables.md).

---

## 4d. Non-Stop Execution & Self-Healing Engine (2026-08)

An enterprise-grade self-healing execution layer designed for long-horizon autonomous tasks.

| Capability | Behavior |
|------------|----------|
| **Supervisor Watchdog** | `supervised_invoke()` wraps graph execution with node heartbeats, stall detection (default 60s), and incident classification (`stalled`, `transient_llm`, `context_overflow`, `panic`). Auto-rolls back to last durable checkpoint, injects reflection, and resumes up to N attempts. |
| **Model Failover Chain** | Exhausted primary models fail over down `agent.nonstop.failover.chain` with per-model cooldowns (default 300s) without mutating active settings profiles. |
| **LLM Execution Ledger** | Durable SQLite WAL (`kazma-data/llm_calls.db`) recording thread, iteration, model, token usage, cost, latency, status, and failover origin for every LLM call. |
| **Startup Orphan Recovery** | Swarm tasks stranded in `status='running'` by process crashes or restarts are requeued to `pending` on startup up to 3 attempts. |
| **HITL Approval Timeout** | Background watchdog scans pending HITL approval interrupts every 15s and auto-denies stale turns after `safety.hitl.approval_timeout_seconds` (default 60s). |
| **Resilient Chat** | Non-graph LLM calls (swarm workers, research planner, research synthesizer) use `resilient_chat` with transient retries, failover chain, and tool-execution timeouts (`agent.tool_timeout_seconds`, 120s). |
| **Settings UI Card** | Agent Settings tab includes a Non-Stop & Self-Healing section with live-re-read toggles, thresholds, and failover chain controls (EN/AR i18n supported). |

---

## 4e. Scraper Hardening & Truncation Recovery (2026-08)

Industry-grade web scraping resilience and model output truncation recovery.

| Feature | Details |
|---------|---------|
| **Response Size Caps** | `read_url` streams body reads and enforces `KAZMA_FETCH_MAX_BYTES` (default 5 MB) to prevent memory exhaustion and gzip-bomb exploits. |
| **Content-Type Gate** | Non-textual binary payloads (PDFs, images, archives) fail fast with actionable guidance instead of polluting the text extractor. |
| **5xx Retry Loop** | HTTP scraper retry loop covers 500, 502, 503, and 504 status codes with backoff and jitter (3 attempts). |
| **robots.txt Compliance** | Opt-in `crawl_site(respect_robots=True)` or `KAZMA_CRAWL_RESPECT_ROBOTS=1` parses host `robots.txt` and flags disallowed URLs as `blocked_robots`. |
| **Auto-Retry Truncation** | When LLM completion stops with `finish_reason='length'`, `llm_provider.chat` transparently retries ONCE with doubled `max_tokens` (capped at 4x configured / 32k) instead of returning broken tool JSON. |
| **`file_append` Tool** | Built-in tool in `LocalToolRegistry` allowing agents to write large files in chunks (`file_write` to initialize, `file_append` for subsequent sections). |

---

## 5. Quick operator recipes

### A. First deep research paper

1. Ensure web search works (SearXNG recommended).  
2. `/research` → topic → Start.  
3. Watch stages; open MD when done; note rubric score on the card.

### B. Cheap KB refresh

1. Crawl a library once.  
2. Refresh immediately.  
3. Confirm **unchanged** / skipped in the job bar (little re-embed work).

### C. “Agent just knows my docs”

1. Ingest library; set **auto_inject** on that library.  
2. Keep **Inject Knowledge into chat** on.  
3. Optional: **Smart Knowledge search** for technical questions across all active libs.  
4. Ask a docs question; expect citations / knowledge footer.

### D. Debug what memory used

1. **Explain recall** on.  
2. Ask a personal-fact question after “Remember …”.  
3. Inspect **Memory context** chips and/or Dashboard probe.

### E. Harden scraping

1. Settings → System → Proxy Provider → anyIP (or none).  
2. **Test Connection**.  
3. Retry a blocked `read_url` / KB crawl.

### F. Brand the memory hub (You → Mubder)

1. Open **`/memory`**.  
2. Entities tab → find person **User** or `ent_…` (or hub).  
3. **Rename** → `Mubder` (or `Kazma`).  
4. Hard-refresh; canvas center label should match. Click the row → hub zooms.

### G. Fix a bad belief without SQL

1. `/memory` → Beliefs → search.  
2. **Edit** → correct object (then predicate/subject if needed).  
3. Graph refresh should show the new edge text.

---

## 6. API cheat sheet

| Method | Path | Role |
|--------|------|------|
| GET | `/api/research/ready` | Preflight (optional `?live=1`) |
| POST | `/api/research/sessions` | Start deep research |
| GET | `/api/research/sessions` | List sessions |
| GET | `/api/research/sessions/{id}/stream` | SSE progress |
| POST | `/api/research/sessions/{id}/cancel` | Cancel run |
| GET | `/api/research/eval` | Rubric score |
| GET/PUT | `/api/settings/memory/merge-kb` | Inject, promote, smart search, explain |
| GET/PUT | `/api/settings/proxy` | Proxy provider |
| GET/PUT | `/api/settings/agent/nonstop` | Non-Stop & Self-Healing settings |
| POST | `/api/memory/v2/probe` | Recall dry-run (explain on) |
| POST | `/api/memory/v2/federated-search` | Memory + KB labeled |
| POST | `/api/memory/v2/eval/golden` | Golden recall suite |
| GET | `/api/memory/v2/graph` | Belief canvas payload (unique ids, hub label) |
| GET | `/api/memory/v2/entities` | Entity list (`is_self`, `graph_id`) |
| POST | `/api/memory/v2/entities/{id}/rename` | Display rename (+ hub sync for self) |
| PATCH | `/api/memory/v2/beliefs/{id}` | Operator edit triple |
| GET | `/memory` | Memory admin HTML page |

Full tables: [API routes](../reference/api-routes).

---

## 7. What we deliberately did *not* do

| Idea | Status |
|------|--------|
| Merge KB + beliefs into one SQLite table | **Won’t fix** — federated / inject only |
| Require Neo4j for memory | **No** — SQLite SoT; Neo4j dual-write optional |
| Proxy LLM traffic | **No** — scraping only |
| Postgres-primary / multi-region as default | **Later** (scale issues #76–#78) |

---

## 8. Verify after upgrade

Use the checkbox matrix: **[Smoke matrix](../ops/smoke-matrix)**  
(Production go-live still uses [Production checklist](../ops/production-checklist).)

---

## Related docs

| Topic | Link |
|-------|------|
| Full research pipeline & proxy | [Web research](./web-research) |
| KB ingest & smart re-index | [Knowledge Library](./knowledge-library) |
| V2 + inject operator path | [Memory best path](./memory-best-path) |
| Web surfaces | [Web UI](../products/web-ui) |
| Multi-path debugging | [Diagnosis map](../ops/diagnosis-map) |
