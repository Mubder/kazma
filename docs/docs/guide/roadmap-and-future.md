---
id: roadmap-and-future
title: Roadmap & Future
sidebar_label: Roadmap & Future
description: Kazma Roadmap & Future — code-audited reference (unified docs, v0.6.1+)
---
> An honest separation of what Kazma does today from what is planned, aspirational, or partially wired. Anchored to the v0.6.1+ codebase (post production-readiness).

---

## 1. How to read this file

Items are marked:

- ✅ **Implemented & wired** — works in the default runtime, verified in code.
- 🟡 **Implemented but not fully wired** — code exists but isn't connected in the default path.
- 🔴 **Planned / Roadmap** — not in the codebase, or declared as a goal in `ROADMAP.md`.

---

## 2. Core agent

| Capability | Status | Notes |
|---|---|---|
| LangGraph supervisor ReAct loop | ✅ | `graph_builder.py`. |
| Tool calling with OpenAI-compatible providers | ✅ | `httpx`, no SDK. |
| NVIDIA NIM tool-fallback | ✅ | `llm_provider.py:285-300`. |
| Streaming (SSE) | ✅ | `streaming.py`. |
| Context compaction (LLM summarise) | ✅ | `compaction.py`. |
| Compaction with memory retrieval + checkpoint | 🟡 | Memory adapter wired on main paths; checkpoint_manager still optional. |
| Rate-limit (429) handling | ✅ | Exponential backoff + Retry-After in `llm_provider.py` (2026-07). |
| Cost breaker auto-wired | ✅ | `CostCircuitBreaker` instantiated per-agent (`agent_runner.py`) and driven on the live loop — `record_user_interaction()` on each inbound message, `should_halt()` gate, and `record_cost()` after each LLM call (`graph_builder.py`). Exposed on the dashboard via `.status()`. |

---

## 3. Memory & RAG

> **Updated July 2026** — the memory overhaul (Phases 1–4) closed all previously-documented gaps. Items below reflect the current state.

| Capability | Status | Notes |
|---|---|---|
| ChromaDB vector memory (RAG tools) | ✅ | Via `memory_search`/`memory_store` + compaction injection. |
| **Memory injection during compaction** | ✅ | `CompactionEngine.retrieve_memories()` now retrieves top-5 and injects `## Relevant Memories`. Lazy resolution handles init ordering. |
| 4-layer UnifiedMemoryAdapter (RRF) | ✅ | **Fixed** — L1 import typo + caller tuple-unpacking bug resolved. Used by swarm (self-improvement + phonebook). |
| Automatic RAG injection into prompts | ✅ | Compaction injects at 80% context; LLM tools remain opt-in. |
| Short-term→permanent consolidation | 🟡 | Explicit-only (`memory_store` tool). No background auto-promotion — deliberate design choice. |
| Document chunking | ✅ | `VectorMemory.add()` chunks at 2000 chars with 200-char overlap. |
| Arabic tokenizer (FTS5) | ✅ | **Improved** — symmetric normalization, conservative clitic splitting, deduplicated stop words, dead rules removed. |
| VectorMemory CRUD | ✅ | `delete()`, `update()`, `clear()` added; chunk-aware. |
| `SQLiteMemoryBackend` vector search | ✅ | **Fixed** — `distance()` replaced with cosine distance; `_vec_available` detection corrected. |
| BM25 ranking | ✅ | **Fixed** — ascending sort (was inverted). |
| `checkpoint_manager` in compaction | 🟡 | Still not passed to `create_authority()` — low-risk (LangGraph checkpointer covers this). |

---

## 4. Swarm orchestration

| Capability | Status | Notes |
|---|---|---|
| Six dispatch patterns | ✅ | dispatch/broadcast/pipeline/fan-out/consult/conditional. |
| Aggregation (collect/first_valid/merge_all/vote/synthesize) | ✅ | `aggregator.py`. |
| Circuit breakers (half-open single-probe) | ✅ | `reliability.py`. |
| Retry / timeout / output validation / bounded concurrency | ✅ | `reliability.py`. |
| Pipeline HITL checkpoints with auto-reject timeout | ✅ | `checkpoint_manager.py`. |
| Handoff cycle detection (depth 5, visits 2) | ✅ | `handoff_guards.py`. |
| Worker autoscaling | 🟡 | `get_autoscaler()` referenced; verify depth. |
| Prometheus metrics | ✅ | Optional `prometheus-client` extra; `/metrics` endpoint in `routes_direct.py`. |

---

## 5. Safety & security

| Capability | Status | Notes |
|---|---|---|
| Graph HITL gate (interrupt) | ✅ | Active on all production build sites. |
| Swarm bus HITL gate (fail-closed) | ✅ | `swarm/safety.py`. |
| Pipeline checkpoint HITL | ✅ | `checkpoint_manager.py`. |
| Skill HMAC signing + verification | ✅ | `hub/cli.py` + `hub/loader.py`. |
| Delegation Ed25519 + AES-GCM | ✅ | `delegation/security.py`. |
| MCP SSE bearer auth | ✅ | `mcp/manager.py:461-466`. |
| MCP stdio auth | ✅ | `auth.type: env` / `arg` injection supported on stdio servers. |
| Vault-backed ConfigStore secrets | ✅ | Sensitive keys → AES vault when `KAZMA_VAULT_KEY` set (2026-07 audit remediations). |
| `/undo` / `/edit` checkpoint mutation | ✅ | Live graph path via `aget_state` / `aupdate_state`. |
| Remote secret login page | ✅ | `/login` + `POST /api/auth/login`. |
| Cryptographic "trust tiers" | 🔴 | Only a boolean `certified` flag + unused `trust:` string. |
| Hardening runner enforcement | 🟡 | `kazma-security.yaml` declares policy; verify runtime enforcement. |

---

## 6. Platforms & UX

| Capability | Status | Notes |
|---|---|---|
| Telegram adapter (full-featured) | ✅ | Long-poll + optional webhook, voice, reactions, keyboards. |
| Discord adapter | ✅ | Gateway WebSocket. |
| Slack adapter | ✅ | Socket Mode / polling. |
| Web UI (SSE) | ✅ | `/api/chat/stream`. |
| WebSocket chat | 🔴 | `/ws/chat` returns 410 Gone. |
| TUI | ✅ | Textual, read-mostly. |
| EN/AR i18n + RTL | ✅ | Inline dict, Calibri + 16px base. |
| Majlis protocol | ✅ | `majlis.py` (core), not a UI toggle. |
| Voice on Discord/Slack/Web | 🔴 | Telegram only. |
| `/undo`, `/edit` slash commands | 🔴 | Stubs. |

---

## 7. Integrations

| Capability | Status | Notes |
|---|---|---|
| OpenAI-compatible providers (10 presets) | ✅ | `providers.py`. |
| Google Vertex AI (ADC) | ✅ | `google_llm.py`. |
| Local servers (Ollama/LM Studio) | ✅ | Dummy-key handling. |
| MCP (stdio + SSE) | ✅ | `mcp/manager.py`. |
| Skill Hub (registry, signing, certification) | ✅ | `hub/`. |
| Langfuse tracing | 🟡 | Dependency present; `logging.langfuse.enabled` flag; integration not active. |
| OpenTelemetry | 🟡 | `[tracing]` extra has exporters; Kazma's own tracing is in-house spans, not OTel. |
| Cloudflare Pages / edge | 🔴 | Not applicable — stateful Python service. |
| PostgreSQL / Redis | 🔴 | Only referenced in `kubernetes/hub-*.yaml` (Hub API), not the main agent. |

---

## 8. Observability

| Capability | Status | Notes |
|---|---|---|
| Structured JSON logs | ✅ | `logging.format: json`. |
| Swarm metrics (in-memory + SQLite) | ✅ | `MetricsCollector`. |
| In-house tracing spans | ✅ | `TraceStore` (dashboard) + `TracingEmitter` (swarm). |
| SSE telemetry events | ✅ | `/api/chat/stream` + telemetry router. |
| Langfuse tracing | ✅ | Wired via `KazmaTracer`; dormant by default (`logging.langfuse.enabled: false`). |
| Prometheus scrape endpoint | ✅ | `/metrics` + `/api/metrics` in `kazma_ui/metrics.py`, mounted in `app.py` (gateway-active block). Emits `text/plain; version=0.0.4` with inbound/outbound/error counters, active threads, adapter, queue-depth, and swarm gauges. |
| OpenTelemetry export | 🔴 | **Removed** — dead code + 8 packages purged. Langfuse + Console remain as the two backends. Re-add only if OTLP export to Jaeger/Tempo becomes a real requirement. |

---

## 9. Suggested next steps (updated post-overhaul)

The memory overhaul closed items #1–4 below. Remaining items:

1. ~~**Wire `memory_store` into `create_authority()`**~~ ✅ **Done** (Phase 1) — compaction now retrieves + injects memories.
2. ~~**Fix `search_backend.py`**~~ ✅ **Done** (Phase 2) — vec detection + cosine distance in Python.
3. ~~**Fix the 4-layer adapter**~~ ✅ **Done** (Phase 2) — L1 import typo + caller bug fixed.
4. ~~**Add a document chunker**~~ ✅ **Done** (Phase 3) — 2000-char chunks with 200-char overlap.
5. **Add 429 backoff** to the retry layer (or document the proxy requirement more loudly).
6. **Auto-wire `CostCircuitBreaker`** into the agent loop so runaway spend is actually halted.
7. **Sync the version strings** (`pyproject.toml`, `kazma.yaml`, CLI `--help`).
8. **Resolve the OpenTelemetry question** — either remove the dead OTel code + `[tracing]` extra (6 unused packages), or wire it properly with a `logging.opentelemetry.enabled` config flag.
9. **Add Prometheus `/metrics`** or commit to the Langfuse/OTel path.
10. ~~**Remove the dead WebSocket endpoint**~~ / **Remove stub `/undo`/`/edit`** commands to reduce user confusion.
6. **Auto-wire `CostCircuitBreaker`** into the agent loop so runaway spend is actually halted.
7. **Sync the version strings** (`pyproject.toml`, `kazma.yaml`, CLI `--help`).
8. **Add Prometheus `/metrics`** or commit to the OTel path and wire it.
9. **Reconcile the K8s manifests** with the main agent (or move them under a `hub/` subdir clearly labelled as the Hub API).
10. **Remove the dead `/ws/chat` endpoint** or the stub `/undo`/`/edit` commands to reduce user confusion.

---

## Documentation Audit Notes

- This file intentionally resists over-promising. Where README/marketing copy describes a feature that is only partially wired, the status column says 🟡 with the specific reason.
- The "Suggested next steps" are the audit's opinionated recommendations, prioritized by impact-to-effort ratio. They are not commitments.
- For the canonical project roadmap, cross-reference `ROADMAP.md` (root) — this file reflects code reality as of v0.6.1+, not marketing futures.
