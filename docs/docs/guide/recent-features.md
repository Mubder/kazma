---
id: recent-features
title: Recent features guide
sidebar_label: Recent features
description: Operator guide for recent Kazma features — Turn Delivery, HITL registry, research, KB, memory, documents, X Studio
---

# Recent features guide

This page is the **operator-facing tour** of the features landed in the
research → KB → memory polish tranche (including the **/memory** admin
graph/rename/hub work). Use it to turn features on, try them once, and find
the deep docs when you need detail.

**New in 2026-09-01/02:** Turn Delivery V2 (journal + `close_turn`; the chat
bubble **projects**). HITL Gate Registry (`hitl_gates.db` — one card, one
row, 409 on a second Approve). Industrial audit waves 0–8 shipped
([exec](https://github.com/Mubder/kazma/blob/main/docs/audits/AUDIT_DEEP_2026-09-01_EXEC.md)).
SSRF pin-IP on direct scraping. Context integrity (scratchpad merge, stored
proposals, trim summary net, `shift_explicit` vs inferred). FanOut HITL is
**tri-state**. Reload: `kazma_guard.py --reload`. Guard 503 cause-quality is
[deferred](https://github.com/Mubder/kazma/blob/main/docs/plans/GUARD_OPS_ALERTING_CAUSE_QUALITY.md).

**New in 2026-08-31:** [X Studio](./x-publisher) (`/x`) is the compose and
plan surface (Post now, Schedule, reschedule, threads, delete, saved
drafts). IBM Plex Sans / IBM Plex Sans Arabic is the shared face across
the web UI, Docusaurus, and generated documents; the letterhead K is the
logo and favicon. `kazma_guard.py --reload` is the deploy path — it skips
the crash backoff ladder so a fifth reload in one day is not a 300s wait
(restart **KazmaAgent** once if an old guard is still climbing). Do not
hand-kill uvicorn. Filesystem tools no longer stall `/health/ready`. Plan
hops stay in the Plan widget. Windows Postgres checkpoints need the
Selector event loop (`kazma serve` / the guard, not `python -m uvicorn`).

**New in 2026-08-27:** [Task Ledger](./task-ledger) (durable intent
resolution + git-write blast radius), transcript recall fallback
([Memory & RAG](./memory-and-rag)), the X audit log
([X publisher](./x-publisher)), and the SearXNG truth-notes fix
([Web research](./web-research)).

**Smoke checklist (when you test later):** [Smoke matrix](../ops/smoke-matrix).  
**Architecture context:** [Web research](./web-research) · [Knowledge Library](./knowledge-library) · [Document Intelligence](./document-intelligence) · [Document phases](./document-phases) · [Memory best path](./memory-best-path).

---

## What's new — reliability & ecosystem (August–September 2026)

| Area | What you get | Where |
|------|--------------|-------|
| **Turn Delivery + HITL registry (2026-09)** | Chat journal is SoT; `close_turn` is the only closer; client projects. One HITL row in `hitl_gates.db` (`pending` = live buttons; second claim **409**). FanOut is tri-state. | [Security](./security-and-safety); [Diagnosis map](../ops/diagnosis-map); AGENTS.md §30–§31 |
| **SSRF pin-IP (Wave 8)** | Direct scrape connects to the validated public IP; private peer abort. Skip pin when a proxy is set. | [Security](./security-and-safety); `KAZMA_JINA_READER=1` still opt-in |
| **Context integrity (2026-08-30)** | Scratchpad merges; drafts live in `agent_artifacts.db`; trim always summarizes dropped turns; only an **explicit** topic pivot disarms recall. | [Memory](./memory-and-rag); AGENTS.md §29 |
| **X Studio (2026-08-31)** | First-class `/x` composer + X-only planner. Post now / Schedule / reschedule / thread hops / delete. Saved drafts stamp `proposal_id` (stored text wins). Chat `x_post` stays always-HITL. **All clocks** → `/scheduled` (mixed cron + X). | [X publisher](./x-publisher); sidebar → X Studio |
| **Brand type + mark (2026-08-31)** | IBM Plex Sans / IBM Plex Sans Arabic across UI, docs, and generated documents (Amiri naskh fallback). Letterhead K is logo, favicon, avatar. PPTX/XLSX no longer hard-code Calibri. | [Arabic & cultural](./arabic-cultural-features); [Document rendering](./document-rendering) |
| **Guard reload + stall fixes (2026-08-31)** | `--reload` stops the recorded child and port holder, skips crash backoff, waits the boot budget, kicks `KazmaAgent` only if the watcher is dead. `file_search` off the event loop. File-tool results capped at 32k. Plan-only hops stay out of the chat transcript. | `python scripts/service/kazma_guard.py --reload`; [Deployment](./deployment) |
| **Chat as the product (2026-08-26)** | `/` is immersive (no Chat/Home/Chat header). Composer: attach + input + send; Long/YOLO/cost live under **⋯**. Sidebar stays the grouped Work / Activity / Settings list (More was reverted — it never collapsed). | [GOAL](https://github.com/Mubder/kazma/blob/main/docs/plans/CHAT_AS_PRODUCT_UI_GOAL.md); restart after pull |
| **Sampling HITL + native CUA + CI smoke (2026-08-25)** | MCP sampling is a real Once card (`KAZMA_MCP_SAMPLING=1`). `computer_use` calls Anthropic CUA / Gemini function when that model is active. CI Playwright job: `/health/live` + `#chat-input`. | [MCP](./skills-mcp-and-tools#57-resources-prompts-sampling-roots); `KAZMA_CUA_PLANNER=0` |
| **Post-industry leftovers (2026-08-25)** | MCP resources/prompts (fenced; sampling HITL). CUA planner adapters on `computer_use`. LiveKit TTS published into the room. 429 backoff on Anthropic. Router word-boundaries + `models.defaults`. Eval tool-trace. Bright Data/Oxylabs stubs. | [GOAL](https://github.com/Mubder/kazma/blob/main/docs/plans/POST_INDUSTRY_NON_SAAS_GOAL.md); [MCP](./skills-mcp-and-tools#57-resources-prompts-sampling-roots) |
| **LiveKit duplex (web) (2026-08-25)** | Live button: WebRTC AEC + barge-in (interrupt TTS). Brain is still LangGraph. Needs `LIVEKIT_URL` + API key/secret. Telegram stays voice notes. | [Voice](./voice-and-media); `KAZMA_VOICE_DUPLEX=0` |
| **LiteLLM optional gateway (2026-08-25)** | `KAZMA_LITELLM_URL` (or `llm.gateway.url`) sends OpenAI-compatible calls through a LiteLLM proxy. Native Anthropic/Azure/Bedrock/Gemini stay direct. Locals stay direct. Not exclusive. | [FAQ](./faq#do-i-need-litellm); `KAZMA_LITELLM=0` |
| **Computer use + leftover polish (2026-08-25)** | `computer_use` screenshot→action loop (HITL). Langfuse **auto-on** with keys. Hosted embed fleet (`KAZMA_EMBED_FLEET=1`). Docling/LlamaParse salvage for hard PDFs. Voice is **turn-based**, not Realtime. | [Tools](../reference/tools-catalog); `KAZMA_COMPUTER_USE=0`; [Voice](./voice-and-media) |
| **kazma ask + ACP (2026-08-25)** | `kazma ask "…"` runs the graph without the web server. Tokens stream; TTY HITL (`y/N`). `kazma acp` is ACP stdio with `session/update` + `session/request_permission`. `--yolo` is headless. | [Quickstart](./quickstart); `kazma ask --help` |
| **IDE LSP (2026-08-25)** | Monaco hover, complete, Ctrl+click definition, Python/JSON diagnostics. Workspace-scoped; uses the code index. | `/ide`; `KAZMA_IDE_LSP=0`; [IDE](../products/ide) |
| **Plan mode (2026-08-25)** | `/plan on` inspects (write/exec blocked). `/plan go` or **Proceed** executes. HITL still on. | Plan pill; [Slash commands](../reference/slash-commands); `KAZMA_PLAN_MODE=0` |
| **Pre/Post tool hooks (2026-08-25)** | Claude Code–style PreToolUse / PostToolUse (deny, rewrite, observe). Not a permission system — HITL still gates danger tools. | `agent.hooks.*`; `KAZMA_TOOL_HOOKS=0`; [Architecture](./architecture#56-tool-hooks) · [Security](./security-and-safety) |
| **Strict tool schemas (2026-08-25)** | Tool JSON Schema is closed (`additionalProperties: false`). OpenAI `strict` tools + `response_format` are opt-in. | `KAZMA_STRICT_TOOLS=1`; [Tools catalog](../reference/tools-catalog); [Architecture](./architecture#55-strict-schemas--structured-outputs) |
| **Codebase index (2026-08-25)** | `codebase_search` finds functions/classes (tree-sitter or regex) plus live ripgrep. Index refreshes on write/patch. | extra `kazma[index]`; `KAZMA_CODE_INDEX=0`; [IDE](../products/ide) |
| **E2B + Temporal (2026-08-25)** | Opt-in Firecracker `python_exec` (`E2B_API_KEY`) and Temporal-wrapped swarm dispatch (`KAZMA_TEMPORAL_HOST`). Defaults unchanged. | extras `kazma[sandbox]` / `kazma[durable]`; [env vars](../reference/environment-variables) |
| **Monaco + apply-patch (2026-08-25)** | `/ide` uses the VS Code Monaco engine (textarea fallback). Agent edits use `file_apply_patch` (HITL) instead of rewriting whole files. | `/ide`; [IDE](../products/ide); [Tools catalog](../reference/tools-catalog) |
| **pgvector memory search (2026-08-25)** | When Postgres is on, dense recall uses pgvector (auto). Postgres-primary is ILIKE + vector RRF, not ILIKE-only. `KAZMA_PGVECTOR=0` keeps sqlite-vec. | Settings → Memory; [Memory & RAG](./memory-and-rag); [Postgres & SaaS](../ops/postgres-and-saas) |
| **Memory system audit (2026-08-24)** | Ego-graph hub anchors (no more floating concept nodes), PG-mirror tombstones + `scripts/reconcile_memory_mirror.py`, tenant-scoped graph-clear (no all-tenants wipe), FTS drift rebuild on the 6h sweep, merge-ledger archive, Ungroup, honest truncation banner | `/memory`; restart after `git pull` |
| **Universal backup** | One unified backup of ALL data: every SQLite DB (WAL-safe), all assets (document-store, workspace, attachments, vectors). Auto **6h** + manual; **checks** PG dump freshness (does not dump twice). Progress bar, delete/archive/download | Settings → **Backup tab**; `POST /api/backup/now` |
| **Postgres backup** | Automatic `pg_dump` of `KAZMA_PG_TABLES` (atomic, validated; local staging retention **3**, restic keeps history) + boot-time schema verification + one-command restore | `kazma-data/backups/pg/`; `python scripts/pg_backup.py backup\|restore --latest\|list` |
| **Agent Skills marketplace** | Browse/install the open `agentskills.io` ecosystem (GitHub `topic:agent-skills`) from the UI; the agent can `search_agent_skills` + `install_agent_skill` | `/skills` → Marketplace tab |
| **Bundled starter skills** | 3 Kazma-native skills ship in-tree (release-notes, conventional-commits, ui-conventions), checksum-verified | `/skills` (scope: bundled) |
| **Windows tool fixes** | Browser tools, `shell_exec`, telemetry, Ollama pull, runtime installs now actually work on Windows (selector-loop subprocess trap fixed) | transparent — tools that silently did nothing now run |
| **Memory recall fix** | Search no longer returns `[]` for queries containing punctuation (`==`, `//`) | transparent |
| **429 failover restored** | A rate-limited primary model now fails over to the backup chain instead of hard-failing the turn | transparent |
| **Boot fixes** | Paused-task restore no longer hangs (deferred timeout arming); PG schema guard actually verifies; research export fixed | transparent |

**Operator actions after `git pull`:** `kazma ask` / `kazma acp` work
without a restart (CLI; tokens stream, TTY HITL). Restart the server to activate
**X Studio** (`/x`), the IBM Plex / letterhead-K brand, the `--reload` backoff
skip (restart **KazmaAgent** once so the *guard* is on the new code),
**computer_use**, **Langfuse auto-on**, Docling/LlamaParse salvage,
**IDE LSP** (hover/complete on `/ide`), **plan mode** (`/plan on` · Plan
pill), **tool hooks** (`agent.hooks.*`;
`KAZMA_TOOL_HOOKS=0` disables), the
**closed tool schemas** (`additionalProperties: false`; optional
`KAZMA_STRICT_TOOLS=1` for OpenAI `strict`), the memory audit fixes (hub
anchors, FTS sweep, graph-clear bind), **pgvector dense recall** (when
`KAZMA_DATABASE_URL` is set — run `CREATE EXTENSION vector` first), the
template fixes (MCP/skills buttons), the backup system (first universal
backup ~2 min after boot), and the Windows tool fixes. Full engineering
detail in `CHANGELOG.md` and `AGENTS.md` §21–§23. Audit:
[`AUDIT_MEMORY_SYSTEM_2026-08-24.md`](https://github.com/Mubder/kazma/blob/main/docs/audits/AUDIT_MEMORY_SYSTEM_2026-08-24.md).

---

## At a glance

| Area | What you get | Where |
|------|----------------|--------|
| **X Studio** | Compose / schedule / reschedule / thread / delete on X (official API) | `/x`; [X publisher](./x-publisher) |
| Deep research | Multi-source pipeline, live sessions, routing, rubric | `/research`, chat, `/research deep` |
| **Document Intelligence** | Secure ingest, OCR, index, generate/convert/redact, ops (capacity/GC/audit), cert | `/documents`, `/api/documents/*`, `/documents` slash, `document_*` tools, TUI Documents |
| Proxy Provider | Residential proxy for scrape/crawl/Playwright/SERP | Settings → System |
| Knowledge Library | Smart re-index, gone-URL prune, hybrid inject; **document_index** bridge | `/knowledge`, Settings → Memory, Documents → Index |
| Memory admin | Graph dedupe, rename, list↔graph, belief edit, hub brand, Group/Ungroup, truncation honesty | `/memory` |
| Memory explain | Channel chips on chat turns + Dashboard probe | Settings → Memory → Explain recall |
| Golden eval | Offline recall regression | Dashboard → Run golden eval |
| Topic-shift focus | **Explicit** pivot disarms recall; inferred drift only re-ranks. Interrogative check-ins never count as drift | Settings → `agent.topic_drift.*`; AGENTS.md §29 |
| Non-Stop & Self-Healing | Supervisor watchdog, model failover chain, call ledger, orphan recovery, HITL timeout | Settings → Agent → Non-Stop Execution |
| Scraper Hardening | Size caps (5MB default), 5xx retry backoff, robots.txt compliance | `read_url`, `crawl_site`, `KAZMA_FETCH_MAX_BYTES` |
| Truncation Auto-Retry | Double max_tokens on length truncation + `file_append` chunk tool | `llm_provider`, `LocalToolRegistry` |
| **Turn Delivery / HITL registry** | Journal + `close_turn`; one gate row; 409 on second Approve | [Security](./security-and-safety) · [Diagnosis](../ops/diagnosis-map) |
| **Commitment Layer** | Resolve-before-act gate; semantic clarify/confirm cards; exec denylist; modes + kill-switches | [Commitment Layer](./commitment-layer) |
| **Steer / Abort** | `/steer` (soft), `/steer!` (pause+inject), `/abort` for a running task | [Slash commands](../reference/slash-commands#-running-task-commands) |
| **Path grants** | Outside-workspace access by permission (session grant or durable `extra_roots`) | [IDE → Path grants](../products/ide#path-grants-outside-workspace-access) |
| **UI theme overhaul** | ABYSS design tokens, server-authoritative theme, dark-slate palette, mobile chrome | [Web UI → Theming](../products/web-ui#theming--design-tokens) |

---

## 0. Latest shipping (2026-09, then 2026-08)

**September 2026 (industrial audit waves 0–8):**

- **Turn Delivery V2** — event-sourced chat: journal + `close_turn`. Token
  deltas append; only `turn_complete` replaces. Do not restore a second
  painter in `chat.js`.
- **HITL Gate Registry** — `hitl_gates.db` CAS (`pending → claimed →
  settled`). Ghost / pre-stamped Approved cards were the incident class.
  Kill-switch `KAZMA_GATE_REGISTRY=0` is a thin execution fallback, not a
  second author. [Security](./security-and-safety) · AGENTS.md §30.
- **FanOut tri-state** — with 2+ chat platforms, a Deny is a vote until
  `expected_voters` or the deadline. Web first-claim stays 200/409.
- **SSRF pin-IP** — direct scraping pins the public IP; abort if the peer is
  private. Do not pin through `proxy=`.
- **Context integrity** — scratchpad merge reducer; durable proposals;
  summary net on every trim; `shift_explicit` vs `shift_inferred`.
- **Docs pass** — `AGENTS.md`, architecture, swarm, system map, security,
  memory, commitment, diagnosis, this page, production checklist.
- Guard 503 still says `unreachable: Service Unavailable` (Docker/Postgres).
  Deferred: [Guard/ops alerting](https://github.com/Mubder/kazma/blob/main/docs/plans/GUARD_OPS_ALERTING_CAUSE_QUALITY.md).

The August tranche — beyond the items in the table above:

- **X Studio (`/x`)** — compose, schedule, reschedule, thread hops, delete a
  live tweet, load a saved draft. Chat `x_post` stays always-HITL; the Web
  click is the approval. **All clocks** opens `/scheduled` (cron + X).
  [X publisher](./x-publisher).
- **Brand type + mark** — IBM Plex Sans / IBM Plex Sans Arabic (UI, docs,
  generated documents). Letterhead K is logo/favicon. Amiri remains the naskh
  fallback. [Arabic & cultural](./arabic-cultural-features).
- **Guard `--reload` skips crash backoff** — a deploy kill is not a crash, so
  the fifth reload of the day is not a 300s wait. Restart **KazmaAgent** once
  if an old guard is still climbing. [Deployment](./deployment).
- **Steer & Abort (`/steer`, `/steer!`, `/abort`)** — out-of-band signals to a
  *running* task. Soft steer folds text into the next step; hard steer pauses,
  injects a requirement, and resumes (demoting to soft if the task is
  finalizing); `/abort` cancels and abandons. See
  [Running task commands](../reference/slash-commands#-running-task-commands).
- **Commitment Layer (resolve-before-act)** — a policy gate between the LLM and
  durable mutations. All phases (0–8) shipped: `authorize_effect`,
  semantic clarify/confirm interrupt cards with per-option buttons, exec
  denylist, config protected keys, outbound allowlist, swarm scope tokens,
  soul-confirm gate, modes (strict/balanced/autonomous/yolo), and kill-switches.
  Dedicated guide: [Commitment Layer](./commitment-layer).
- **HITL semantic-resume parity (PR1–PR5)** — a single enforced resume
  chokepoint, WS/SSE parity for semantic interrupts, the terminal-clarify
  invariant that kills the clarify loop, args-first memory-checked remind gate,
  and the semantic clarify option-cards + `/metrics` loop counter.
- **Path grants** — outside-workspace access is deny-by-default but openable by
  permission: smooth session grants via the `request_path_access` HITL card, or
  durable `workspace.extra_roots`. [IDE → Path grants](../products/ide#path-grants-outside-workspace-access).
- **Primary `kazma update`** — a fail-closed operator upgrade path: preflight,
  named stash, hard reset to `origin/main`, extras-preserving reinstall, and
  postflight verification. [Kazma Update](../ops/kazma-update).
- **Arabic PDF ingestion** — industry-grade extraction: PyMuPDF primary with a
  multi-engine bake-off (pypdfium2/pdfplumber/pypdf), layout-aware reading
  order, fuzzy RTL round-trip verifier, and `ara+eng` OCR routing.
  [Document Intelligence](./document-intelligence).
- **Document rendering richness** — code/syntax highlighting (PDF+HTML), XLSX
  charts (multi-series + axis titles), approved-asset image embedding, and a
  real PDF TOC + clickable HTML TOC. [Document rendering](./document-rendering#7-rich-content-features).
- **UI theme overhaul (P1–P4)** — unified ABYSS design tokens, server-
  authoritative theme (persists across devices), dark-slate palette with real
  layer depth, mobile chrome fixes, and forced HTML revalidation.
  [Web UI → Theming](../products/web-ui#theming--design-tokens).

---

## 1a. Document Intelligence (product path)

### What it does

End-to-end **secure document platform**: streamed intake → quarantine CAS →
policy sniff → isolated parse/OCR → durable jobs → optional Knowledge index,
generate/convert/redact, ops (capacity, GC, audit, cert).

### How to run

| Entry | How |
|-------|-----|
| **Web** | `/documents` upload + ops; Settings → **Documents** |
| **Agent** | `document_import` / `document_read` / `document_index` / … |
| **Gateway** | `/documents list\|read\|status\|…` (alias `/docs`) |
| **TUI** | Documents tab |
| **Cert** | `python scripts/certify_documents.py` |

### Install engines (optional)

```bash
pip install -e ".[document-platform]"
# System: Tesseract (OCR), ClamAV (malware), LibreOffice (some convert)
```

Deep docs: [Document Intelligence](./document-intelligence) ·
[Ops](../ops/document-processing) · [Security](../security/document-security) ·
[Phases 0–10](./document-phases).

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
| **Ego-graph anchors** | Payload-object facts (`subject → pred → literal`) also get `user → related_to → subject` so they are not a disconnected component |
| **Group / Ungroup** | View-only clustering from inspect; 30s poll uses `groups` on the graph payload (no extra GET) |
| **Truncation honesty** | Banner reports nodes **and** connections hidden by the top-N cap |

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
| GET | `/api/memory/v2/graph` | Belief canvas payload (unique ids, hub label, embedded `groups`) |
| DELETE | `/api/memory/v2/graph/groups/{id}` | Ungroup (view-only) |
| POST | `/api/memory/graph/clear` | Tenant-scoped bi-temporal invalidate + Neo4j cleanup |
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

Operator reload (do **not** kill python/uvicorn):

```powershell
& '.venv\Scripts\python.exe' scripts\service\kazma_guard.py --reload
& '.venv\Scripts\python.exe' scripts\service\kazma_guard.py --status
```

Then: `GET /health/deep` 200; HITL one-card Approve + 409 on the second click;
[Smoke matrix](../ops/smoke-matrix). Production go-live:
[Production checklist](../ops/production-checklist).

---

## Related docs

| Topic | Link |
|-------|------|
| Full research pipeline & proxy | [Web research](./web-research) |
| KB ingest & smart re-index | [Knowledge Library](./knowledge-library) |
| V2 + inject operator path | [Memory best path](./memory-best-path) |
| Web surfaces | [Web UI](../products/web-ui) |
| Multi-path debugging | [Diagnosis map](../ops/diagnosis-map) |
| Build contract | [`AGENTS.md`](https://github.com/Mubder/kazma/blob/main/AGENTS.md) (§30–§33) |
| Industrial audit | [`AUDIT_DEEP_2026-09-01_EXEC.md`](https://github.com/Mubder/kazma/blob/main/docs/audits/AUDIT_DEEP_2026-09-01_EXEC.md) |
