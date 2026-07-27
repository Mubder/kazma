# Plan: Knowledge Library hardening + Research depth product

**Status:** Implemented on `main` (2026-07-27) — Phases 0–3 landed (core + polish)  
**Scope:** Knowledge Base (managed docs RAG) + live web research tools + “comprehensive research paper” orchestration  
**Related:**  
- [Knowledge Library guide](../docs/guide/knowledge-library.md)  
- [Web research guide](../docs/guide/web-research.md)  
- [Diagnosis map](../docs/ops/diagnosis-map.md)  
- Audit findings (session 2026-07-27): multi-path diagnosis, SearXNG/Jina recovery already shipped  

**Problem statement**

1. **KB** is architecturally strong for curated documentation RAG but has refresh/tenant/orphan gaps that hurt production reliability.  
2. **Research tools** are a capable *toolbox* (search → fetch → save → crawl → extractive digest) but **not** a *research product*. Users ask for deep research and often get **one `web_search` + a shallow summary** — because nothing forces multi-hop, multi-source analysis, or paper-shaped synthesis.

**Success statement**

| Mode | User expectation | Target outcome |
|------|------------------|----------------|
| **KB Q&A** | “Answer from my ingested docs with sources” | Reliable ingest/refresh/search; citations; optional auto-inject |
| **Quick research** | “Look this up” | 1–2 searches + 1–2 reads, short cited answer (current default OK) |
| **Deep research** | “Research thoroughly / write a comprehensive report” | Multi-query, multi-source, analyzed, full structured paper with citations |

---

## 1. Current state (audit scores)

### 1.1 Knowledge Library

| Dimension | Score (1–10) | Notes |
|-----------|:------------:|-------|
| Design / isolation from chat memory | 8.5 | Per-lib Chroma + SQLite + FTS5 |
| Ingest (with Jina/Firecrawl/Playwright) | 7.5 | Sitemap/BFS/Firecrawl map; tab-aware Playwright |
| Search (RRF hybrid) | 7.5 | Vector + BM25; degrades without Chroma |
| Refresh / ops | 5.5 | Orphans; refresh job durability weaker than crawl |
| Multi-tenant readiness | 4 | Auto-inject not tenant-scoped |
| **Overall (single-tenant docs RAG)** | **7.5–8** | Ship with backends; harden before SaaS |

**Intended:** Named corpus, ingest once, retrieve with URL+section citations.  
**Actual:** Matches intent when ingest succeeds and model calls `knowledge_search` (or auto-inject is on).

### 1.2 Research tools

| Capability | Score (1–10) | Notes |
|------------|:------------:|-------|
| Search (with SearXNG) | 7 | Multi-backend failover; snippets only |
| Page fetch / hard sites | 7.5–8 | Recovery cascade shipped |
| Multi-page crawl | 5.5 | Default 8 pages / depth 2 |
| Synthesis / “paper” quality | 3.5 | Extractive digests; free-form ReAct only |
| **Deep research UX** | **~4.5** | Toolbox ~7; orchestration ~3 |

**Root cause of shallow research (ordered):**

1. No research protocol in system / product knowledge  
2. Search snippets satisfy the model for short answers  
3. Digests are extractive (no nested LLM synthesis)  
4. No min-sources / min-queries gate  
5. Swarm auto-researcher missing save/digest tools  
6. Defaults favor short loops (5 results, 15 iterations, optional hops)

### 1.3 What we will *not* rebuild

- Do not replace free-form tools with a single monolithic crawler.  
- Do not merge KB corpus into chat `agent_memory`.  
- Do not require paid SERP APIs as a hard dependency (SearXNG remains primary).  
- Do not make *every* casual question run a full paper pipeline.

---

## 2. Architecture (target)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  USER INTENT ROUTER (prompt + optional /research · run_research_pipeline)│
│  casual Q&A │ quick research │ deep research │ KB Q&A                    │
└───────────┬───────────────────┬───────────────────┬─────────────────────┘
            │                   │                   │
            ▼                   ▼                   ▼
     ReAct tools          RESEARCH PIPELINE      KNOWLEDGE LIBRARY
     (light use)          (new orchestration)    (hardened RAG)
            │                   │                   │
            │         plan → discover → acquire     │
            │         → map → reduce → gap → paper  │
            │                   │                   │
            └───────────────────┴───────────────────┘
                              │
              web_search · read_url* · crawl · digest
              synthesize_from_digests · knowledge_*
```

**Shared fetch stack (already unified):** `read_url._fetch_full_text` used by research + KB ingest.

---

## 3. Workstreams

### Workstream A — Knowledge Library production hardening  
### Workstream B — Research depth (prompt → soft gates → pipeline)  
### Workstream C — Docs, diagnosis map, tests, ops defaults  

Each workstream has phased PRs. Phases can run in parallel where noted.

---

## 4. Workstream A — KB hardening

### A0 — Findings inventory (do not re-audit)

| ID | Finding | Severity | Module |
|----|---------|----------|--------|
| A-F1 | Orphan chunks on re-ingest (page shrink keeps old indices) | High | `knowledge.py` has `delete_chunks_for_source` unused; `knowledge_ingest` never calls it |
| A-F2 | Auto-inject ignores `tenant_id` | High (SaaS) | `list_auto_inject_libraries` |
| A-F3 | Archived libs can still auto-inject | Med | same query |
| A-F4 | Refresh jobs not fully durable (Web + gateway) | Med | `kb_api.py`, `commands.py` |
| A-F5 | Chroma re-upserts unchanged chunks | Med | `knowledge_index.index` |
| A-F6 | Playwright browser per page | Med | `knowledge_ingest` |
| A-F7 | FTS5 query specials fragile | Low–Med | `knowledge.py` search |
| A-F8 | Docs drift (scope default `tree` vs docs `prefix`; agent crawl 15 vs UI 200) | Low | docs + `tool_registry` |
| A-F9 | No always-on RAG without auto-inject / tool call | Design | product decision |

### A1 — Re-ingest hygiene (orphan purge)  · **P0**

**Goal:** Re-crawl/refresh never leaves stale chunk IDs for a URL.

**Design**

1. Before indexing chunks for `(library_id, source_url)`:
   - Call `delete_chunks_for_source(library_id, source_url)` (SQLite + FTS).  
   - Remove matching IDs from Chroma collection for that library.  
2. Then upsert new chunks as today.  
3. Report metrics: `chunks_removed`, `chunks_new`, `chunks_unchanged` (optional).

**Files:** `knowledge_ingest.py`, `knowledge_index.py`, `knowledge.py` (if delete API incomplete for vectors).  
**Tests:** unit with multi-chunk page → fewer chunks on re-ingest → search does not return old section headers.  
**Acceptance:** Re-ingest shrinks page from 5→2 chunks; store count for URL = 2; search never hits deleted content.

### A2 — Auto-inject tenant + archive filters  · **P0 (SaaS) / P1 (single-tenant)**

**Goal:** Auto-inject only current tenant’s **non-archived** libraries with `auto_inject=1`.

**Design**

- Mirror `list_libraries` tenant filter in `list_auto_inject_libraries`.  
- Add `AND archived = 0`.  
- Keep kill switch `KAZMA_KB_AUTO_INJECT=0`.

**Files:** `knowledge.py`, `knowledge_index.py` (auto-inject block builder), tests.  
**Acceptance:** Cross-tenant lib never appears in inject list; archived+auto_inject does not inject.

### A3 — Durable refresh jobs  · **P1**

**Goal:** Refresh progress survives process restart like crawl jobs.

**Design**

- Web: every refresh progress tick uses same `_remember_job` / ConfigStore path as crawl (`kb_jobs`).  
- Gateway `/kb refresh`: `upsert_job` parity with crawl.  
- Status commands/UI read durable store first.

**Files:** `kb_api.py`, `commands.py`, `kb_jobs.py`, docs.  
**Acceptance:** Kill process mid-refresh; restart; `/kb status` or UI shows last progress / interrupted state.

### A4 — Index cost: skip unchanged Chroma upserts  · **P1**

**Goal:** If SQLite `upsert_chunk` reports unchanged (same content_hash), skip embed/vector write.

**Files:** `knowledge_index.py`.  
**Acceptance:** Re-ingest identical page → zero (or near-zero) Chroma writes; metrics show skipped.

### A5 — Playwright reuse per crawl job  · **P2**

**Goal:** One browser/context per `ingest_site` job, not launch/close per page.

**Files:** `knowledge_ingest.py`.  
**Acceptance:** Instrumented crawl shows single browser lifecycle; wall time down on multi-page bot-wall sites.

### A6 — FTS5 query sanitization  · **P2**

**Goal:** Technical queries with punctuation don’t zero the lexical layer.

**Design:** Strip/escape FTS5 operators; quote multi-word tokens as needed.  
**Tests:** known-bad query strings still return hits when content exists.

### A7 — Docs + agent crawl honesty  · **P1**

- Document default scope `tree` (or change code to `prefix` if product prefers docs — **decision: align docs to code `tree`** unless product wants prefix).  
- Document agent `knowledge_ingest_site` max_pages=15 vs UI 200–1000.  
- Document durable jobs.

### A8 — Optional “smart always-search” (product decision)  · **P3**

**Not required for hardening.** Optional later:

- Config: `knowledge.auto_search_active=1` → if any non-archived lib has chunks and query looks technical, inject top-k without per-lib toggle.  
- Still fenced as untrusted; still cite footer.

**Decision gate:** only implement if operators complain “agent never uses KB.” Prefer better tool descriptions first (Workstream B overlap).

---

## 5. Workstream B — Research depth product

### B0 — Product modes (locked for this plan)

| Mode | Trigger (examples) | Behavior |
|------|--------------------|----------|
| **Quick** | “look up”, “what is”, short question | Free-form tools; 1–2 hops OK |
| **Deep** | “research thoroughly”, “comprehensive”, “write a report/paper”, “deep dive”, `/research deep` | Pipeline or hard protocol |
| **Docs crawl** | User points at a doc root | Prefer `crawl_site` or KB ingest |

**Detection v1:** keyword / slash / tool flag.  
**Detection v2 (later):** small classifier LLM.  
**Fail-open:** if unsure → quick mode (do not always burn deep budget).

### B1 — Research protocol in prompts  · **P0 (highest ROI)**

**Goal:** When user asks for research, model stops answering from SERP snippets alone.

**Design — inject short protocol via `build_product_knowledge()` and strengthen tool descriptions:**

```
When the user asks to research, investigate, or report thoroughly:
1. Plan 3–7 sub-questions (use ```plan fence when useful).
2. Run ≥2 distinct web_search queries (different angles).
3. Acquire ≥2 full sources (read_url_to_file preferred; or multi-window read_url).
4. For long sources: digest_research_file before final answer.
5. For documentation roots: crawl_site or knowledge_ingest_site.
6. Final answer: structured sections + URL citations. Never claim “full research”
   from titles/snippets alone.
Casual factual Q&A may use a single search.
```

**Also update:**

- `web_search` tool description: “snippets only; for thorough research, fetch full pages next.”  
- `read_url` / `read_url_to_file` / `digest_research_file` descriptions: chain them for deep work.  
- Gateway suggestions: include crawl/digest once (not only web_search).

**Files:** `product_knowledge.py`, `tool_registry.py`, optional `suggestions.py`.  
**Tests:** unit assert protocol strings present; optional golden “research intent” system block.  
**Acceptance:** Manual: “Research X thoroughly” produces ≥2 tool classes (search + read/save), not search-only.

### B2 — Swarm researcher tool allowlist + playbook  · **P0**

**Today (incomplete):**

```text
web_search, read_url, crawl_site, file_write
```

**Target:**

```text
web_search, read_url, read_url_to_file, crawl_site,
list_research_chunks, read_research_chunk, summarize_research_file,
digest_research_file, file_write
```

**System prompt:** same multi-step playbook as B1 (not just “be comprehensive”).

**Files:** `tool_registry.py` `dispatch_swarm` auto-worker block.  
**Acceptance:** Auto-created researcher can call `digest_research_file` without manual worker config.

### B3 — Soft graph guardrail (research intent)  · **P1**

**Goal:** One corrective nudge without hard-blocking casual chat.

**Design**

- In tool worker or supervisor post-tool path: if research intent detected **and** only `web_search` completed **and** model attempts final answer → inject system nudge once:

  > Fetch and digest at least two primary sources (read_url_to_file or read_url) before concluding.

- Max **one** nudge per turn.  
- Config: `research.soft_min_sources=2` (default on for deep keywords only).

**Files:** `graph_builder.py` (or small `agent/research_policy.py`).  
**Tests:** unit with mock tool history.  
**Acceptance:** Search-only premature end triggers one more tool-capable hop; casual “what is 2+2” unaffected.

### B4 — Caps / defaults for depth  · **P1**

| Change | Default today | Target | Notes |
|--------|---------------|--------|-------|
| `web_search` max_results | 5 | **8** when deep intent (or always 8) | Still hard-cap 15 |
| Document `agent.max_iterations` | 15 | Docs: **20–25 for deep** | ConfigStore already tunable |
| Crawl playbook defaults | 8 / 2 | Deep mode suggest **12–20** / 3 | Respect hard caps 50 / 5 |
| Digest output | 12k | Keep; raise only if synthesis (B5) needs more | |

Prefer **intent-scoped** raises over global always-expensive defaults.

### B5 — `synthesize_from_digests` tool  · **P1–P2**

**Goal:** Real analytical reduce step (nested LLM), not extractive bullets only.

**API (sketch)**

```text
synthesize_from_digests(
  paths: list[str] | str,   # research files and/or prior digest texts
  question: str,
  outline: str = "",        # optional H1/H2
  max_chars: int = 20000,
) -> str
```

**Behavior**

1. Load digests (or run extractive digest inline if raw saves given).  
2. Cap concatenated context (env `KAZMA_RESEARCH_SYNTH_MAX_IN`).  
3. Single (or map-reduce if over cap) LLM call with strict instructions: sections, claims, inline `[n]` citations mapped to URLs from file headers.  
4. Return markdown report body (not yet final “paper assembly”).

**Files:** new `tools/research_synthesize.py` or section in `read_url.py`; register in `tool_registry.py`.  
**HITL:** read-safe (no danger).  
**Acceptance:** Two saved sources → tool returns multi-section analysis with URLs; not a copy of one digest.

### B6 — Deep research pipeline (comprehensive paper mode)  · **P2**

**Goal:** Deterministic stages when user opts into deep mode.

**Entry points (implement at least one in P2; both preferred):**

1. Tool: `run_research_pipeline(topic, depth="deep"|"standard", language=…)`  
2. Slash / chat: `/research deep <topic>` (resolver → tool or graph handler)

**Stages**

| # | Stage | Owner | Outputs | Stop rules |
|---|-------|--------|---------|------------|
| 0 | Intent / language | Router | mode=deep, topic | Clarify only if topic empty |
| 1 | Plan | LLM (no tools) | 5–8 sub-questions, outline, 8–12 queries | JSON schema |
| 2 | Discover | Tools | URL pool, dedupe by domain | ≥12 unique URLs or queries done |
| 3 | Acquire | Tools | Saved files under `research/<slug>/` | Target **8–12** sources; log failures |
| 4 | Map | Tools | Per-source digest path | All acquired files digested |
| 5 | Reduce | `synthesize_from_digests` | Section drafts + citations | Token budget / map-reduce |
| 6 | Gap check | LLM + 1–2 searches | Optional extra sources | Max 1 extra hop |
| 7 | Assemble | LLM + optional `generate_markdown_doc` | `research/reports/<slug>-report.md` | Abstract, body, risks, sources |
| 8 | Emit | Chat | Link/path + short executive summary | User can open full file |

**Progress:** emit workbench plan steps + status (Web); Telegram-friendly stage lines.

**Config**

| Key / env | Purpose | Default |
|-----------|---------|---------|
| `research.deep_min_sources` | Min successful acquires | 4 (stretch 8) |
| `research.deep_max_queries` | Search budget | 12 |
| `research.deep_max_pages` | Max acquires | 12 |
| `agent.max_iterations` during pipeline | May use internal loop outside ReAct | N/A if tool-orchestrated |

**Implementation placement**

- `kazma-core/kazma_core/agent/research_pipeline.py` (orchestrator)  
- `kazma-core/kazma_core/prompts/research_comprehensive.md` (or string module)  
- Wire tool registration + optional slash in gateway `commands.py`  
- UI: optional Research panel tag `kind=research_paper`

**Acceptance (golden path)**

1. User: `/research deep <topic>` or tool with depth=deep.  
2. Workbench/stage logs show plan → search → ≥4 saved sources → digests → synthesis.  
3. Final artifact is a multi-section markdown report with a Sources section (URLs).  
4. Duration/cost higher than quick mode (expected); does not hang past timeout (configurable, e.g. 10–15 min).

### B7 — Reliability ops for research  · **P1 (docs) / continuous**

- Prefer SearXNG: document compose profile `search` + `KAZMA_SEARXNG_URL`.  
- Keep `scripts/smoke_research_stack.py` in CI-ish manual smoke.  
- After B6: add `scripts/smoke_research_deep.py` (mocked tools OK for CI).

---

## 6. Workstream C — Docs, diagnosis, quality gates

| Item | Action |
|------|--------|
| Web research guide | Document modes: quick vs deep; pipeline stages; min-sources policy |
| Knowledge library guide | Orphan fix, scope default, agent vs UI crawl caps, refresh durability |
| Diagnosis map | Add rows: shallow research → protocol/pipeline; KB stale hits → orphan purge |
| AGENTS.md | Pointer to this plan; note research_pipeline when landed |
| CHANGELOG | Per PR |
| Tests | See §8 |

---

## 7. Phased roadmap (PR plan)

### Phase 0 — Policy only (fast)  
**Est:** 0.5–1 day · **Risk:** Low  

| PR | Contents |
|----|----------|
| **PR-0** | B1 protocol + tool descriptions + B2 swarm allowlist + C docs snippets |

**Exit:** Deep-worded user asks yield multi-hop more often without new tools.

### Phase 1 — Correctness + soft depth  
**Est:** 2–4 days · **Risk:** Medium  

| PR | Contents |
|----|----------|
| **PR-1a** | A1 orphan purge on re-ingest |
| **PR-1b** | A2 tenant + archive auto-inject filters |
| **PR-1c** | B3 soft graph nudge + B4 intent-aware caps |
| **PR-1d** | A3 durable refresh + A7 docs sync |

**Exit:** KB refresh trustworthy; research intent cannot one-shot from snippets only.

### Phase 2 — Synthesis + paper mode  
**Est:** 4–7 days · **Risk:** Medium–High (LLM cost/latency)  

| PR | Contents |
|----|----------|
| **PR-2a** | B5 `synthesize_from_digests` |
| **PR-2b** | B6 `run_research_pipeline` + `/research deep` |
| **PR-2c** | A4 Chroma skip + A5 Playwright pool (can parallelize with 2a/2b) |
| **PR-2d** | Smokes, guide updates, diagnosis map |

**Exit:** User can produce a multi-source research paper artifact on demand.

### Phase 3 — Polish / optional  
**Est:** 2–4 days  

| PR | Contents |
|----|----------|
| **PR-3a** | A6 FTS5 sanitization |
| **PR-3b** | A8 smart always-search (if product says yes) |
| **PR-3c** | Parallel swarm fan-out for Stage 2–3 only |
| **PR-3d** | DOCX export of paper via document generator |
| **PR-3e** | Research panel: open report file, compare runs |

---

## 8. Testing strategy

| Layer | Coverage |
|-------|----------|
| Unit | Orphan purge; tenant auto-inject; FTS sanitize; protocol strings; soft nudge; synthesize mocks; pipeline stage transitions with fake tools |
| Integration | Ingest → search → re-ingest shrink; research pipeline with httpx mocks |
| Live smoke (manual/WSL) | `smoke_research_stack.py`; new deep smoke with SearXNG up; one real doc-site KB crawl |
| Regression | Existing `test_knowledge_*`, `test_research_read_url`, `test_web_search_fallback`, HITL gates unchanged |

**Do not** require live Meta crawls in CI (network flaky); fixture-based discovery/chunk/search is enough.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Deep mode too slow/expensive | Caps on sources/queries; timeout; clear “deep = slower” UX |
| Soft nudge loops | One nudge max; recursion_limit already 100 |
| Nested LLM in synthesize | Cap input; map-reduce; optional disable env |
| KB purge deletes good data | Only purge by exact `(library_id, source_url)` before re-index of that URL |
| Tenant filter breaks single-tenant | Default tenant / empty tenant = all libs as today |
| Model ignores protocol after B1 | B3 + B6 enforce structure |

---

## 10. Success metrics

### KB

- [ ] Re-ingest shrink: zero orphan chunks for URL  
- [ ] Auto-inject never crosses tenant; never injects archived  
- [ ] Refresh progress survives restart  
- [ ] Identical re-ingest: Chroma skip rate > 80% on unchanged pages  

### Research

- [ ] “Research thoroughly X” (manual n=5 topics): ≥80% runs use search **and** ≥2 page acquires  
- [ ] Deep pipeline: report file exists with ≥4 sources in Sources section  
- [ ] Quick mode latency not regressed for simple Q&A (no forced deep)  

### Product honesty

- [ ] Docs state digests are extractive; synthesis/pipeline is where analysis lives  
- [ ] Diagnosis map lists shallow-research → B protocol / pipeline  

---

## 11. Dependency order (critical path)

```
PR-0 (protocol + swarm tools)
    │
    ├──────────────────► PR-1c (soft nudge + caps)
    │                           │
    │                           ▼
    │                    PR-2a synthesize ──► PR-2b pipeline
    │
    └─ parallel ──► PR-1a orphans ──► PR-1d refresh durability
                    PR-1b tenant inject
                    PR-2c index/Playwright perf
```

**Minimum lovable “deep research”:** PR-0 + PR-1c + PR-2a + PR-2b.  
**Minimum lovable “KB production”:** PR-1a + PR-1b + PR-1d.

---

## 12. Open decisions (defaults if no reply)

| Topic | Default in this plan |
|-------|----------------------|
| KB scope default | Keep code **`tree`**; fix docs |
| Deep entry | Both tool **and** `/research deep` |
| Soft nudge scope | Deep keywords only (not all questions) |
| Min sources deep | Soft 2 (B3); pipeline target 4–8 (B6) |
| Smart always-search KB | **Defer** (Phase 3) |
| Paid SERP | Not required |

---

## 13. Implementation checklist (agent/dev)

When executing a phase:

1. Read this plan + [diagnosis map](../docs/ops/diagnosis-map.md).  
2. Touch only listed files; compile-check Python; `node --check` if JS.  
3. Add/adjust tests per phase.  
4. Update guide pages + CHANGELOG.  
5. Push `main` only if that is the team workflow; otherwise PR.  
6. Mark checklist items in §10 when verified.

---

## 14. Summary

| Area | Condition today | After plan |
|------|-----------------|------------|
| **KB** | Strong RAG design; refresh/tenant/orphan gaps | Production-hardened single-tenant docs RAG; SaaS-safe inject |
| **Research tools** | Strong primitives; shallow default behavior | Prompt + soft gates + synthesis + optional full paper pipeline |
| **User pain (“not real research”)** | Valid; orchestration missing | Deep mode produces multi-source analyzed reports |

**Bottom line:** Improve **KB reliability** and **research orchestration**. Do not throw away the existing tool stack; **force depth when the user asks for it**, and keep casual chat light.

---

*Plan version: 2026-07-27 · Source: KB + research deep audit (same date).*
