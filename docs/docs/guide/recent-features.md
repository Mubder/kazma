---
id: recent-features
title: Recent features guide
sidebar_label: Recent features
description: Operator guide for deep research, KB hardening, proxy coverage, memory explain, and Settings toggles added in the research/KB/memory tranche
---

# Recent features guide

This page is the **operator-facing tour** of the features landed in the
research → KB → memory polish tranche. Use it to turn features on, try them
once, and find the deep docs when you need detail.

**Smoke checklist (when you test later):** [Smoke matrix](../ops/smoke-matrix).  
**Architecture context:** [Web research](./web-research) · [Knowledge Library](./knowledge-library) · [Memory best path](./memory-best-path).

---

## At a glance

| Area | What you get | Where |
|------|----------------|--------|
| Deep research | Multi-source pipeline, live sessions, routing, rubric | `/research`, chat, `/research deep` |
| Proxy Provider | Residential proxy for scrape/crawl/Playwright/SERP | Settings → System |
| Knowledge Library | Smart re-index, gone-URL prune, hybrid inject | `/knowledge`, Settings → Memory |
| Memory explain | Channel chips on chat turns + Dashboard probe | Settings → Memory → Explain recall |
| Golden eval | Offline recall regression | Dashboard → Run golden eval |

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
| Local SearXNG | **No** (loopback) |
| Jina / Firecrawl **API** calls | **No** (third-party fetch) |
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

1. Enable **Explain recall** in Settings → Memory.  
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

---

## 6. API cheat sheet

| Method | Path | Role |
|--------|------|------|
| POST | `/api/research/sessions` | Start deep research |
| GET | `/api/research/sessions` | List sessions |
| GET | `/api/research/sessions/{id}/stream` | SSE progress |
| POST | `/api/research/sessions/{id}/cancel` | Cancel run |
| GET | `/api/research/eval` | Rubric score |
| GET/PUT | `/api/settings/memory/merge-kb` | Inject, promote, smart search, explain |
| GET/PUT | `/api/settings/proxy` | Proxy provider |
| POST | `/api/memory/v2/probe` | Recall dry-run (explain on) |
| POST | `/api/memory/v2/federated-search` | Memory + KB labeled |
| POST | `/api/memory/v2/eval/golden` | Golden recall suite |

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
