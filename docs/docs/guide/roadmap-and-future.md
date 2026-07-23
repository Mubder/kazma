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
| WebSocket chat | ✅ | Legacy `/ws/chat` removed; SSE `/api/chat/stream` is the sole transport. |
| TUI | ✅ | Textual, read-mostly. |
| EN/AR i18n + RTL | ✅ | Inline dict, Calibri + 16px base. |
| Majlis protocol | ✅ | `majlis.py` (core), not a UI toggle. |
| Voice on Discord/Slack/Web | ✅ | STT + TTS wired into all platforms via `voice_helpers.py` (was Telegram-only). |
| Media / attachments (photo/doc/video) | ✅ | `Attachment` contract on `IncomingMessage`/`OutboundMessage`; inbound+outbound on all platforms + Web `/api/chat/upload`. |
| `/undo`, `/edit` slash commands | ✅ | Handled by the graph (`_handle_undo`/`_handle_edit` mutate checkpoint state). |

---

## 7. Integrations

| Capability | Status | Notes |
|---|---|---|
| OpenAI-compatible providers (18 presets) | ✅ | `providers.py` — incl. Mistral/Together/Cohere/Fireworks/Perplexity/AI21/Groq/xAI/OpenRouter/NVIDIA. |
| Native non-OpenAI providers | ✅ | `AnthropicProvider` (`/messages`), `AzureProvider` (`api-key`+`api-version`), `BedrockProvider` (SigV4 + Converse), `GeminiProvider` (ADC). See [LLM Providers](../reference/llm-providers). |
| Google Vertex AI (ADC) | ✅ | `google_llm.py`. |
| Local servers (Ollama/LM Studio) | ✅ | Dummy-key handling. |
| MCP (stdio + SSE + Streamable HTTP) | ✅ | `mcp/manager.py` — Streamable HTTP (MCP 2025-03-26 spec) with `Mcp-Session-Id` resumption. |
| Skill Hub (registry, signing, certification) | ✅ | `hub/`. |
| Langfuse tracing | 🟡 | Dependency present; `logging.langfuse.enabled` flag; integration not active. |
| OpenTelemetry | 🟡 | `[tracing]` extra has exporters; Kazma's own tracing is in-house spans, not OTel. |
| Cloudflare Pages / edge | 🔴 | Not applicable — stateful Python service. |
| PostgreSQL (main agent) | ✅ | First-class backend for ConfigStore/sessions/swarm/checkpoints; HITL pending-approvals enumerate Postgres threads (`hitl_approval.py`). |

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

## 9. Suggested next steps

The memory overhaul closed items #1–4; the capability-expansion sprint closed #6–10. Remaining open items:

1. ~~**Wire `memory_store` into `create_authority()`**~~ ✅ Done — compaction retrieves + injects memories.
2. ~~**Fix `search_backend.py`**~~ ✅ Done — vec detection + cosine distance in Python.
3. ~~**Fix the 4-layer adapter**~~ ✅ Done — L1 import typo + caller bug fixed.
4. ~~**Add a document chunker**~~ ✅ Done — 2000-char chunks with 200-char overlap.
5. **Add 429 backoff** to the retry layer (or document the proxy requirement more loudly).
6. ~~**Auto-wire `CostCircuitBreaker`**~~ ✅ Done — instantiated per-agent and driven on the live loop (`agent_runner.py`, `graph_builder.py`).
7. ~~**Sync the version strings**~~ ✅ Done — `pyproject.toml`, `kazma.yaml`, gateway, TUI all track 0.6.1; parity test added.
8. **Resolve the OpenTelemetry question** — the dead OTel code + `[tracing]` extra were removed; Langfuse + Console remain as backends. Re-add only if OTLP export becomes a real requirement.
9. ~~**Prometheus `/metrics`**~~ ✅ Done — `/metrics` + `/api/metrics` emit Prometheus 0.0.4 text (`kazma_ui/metrics.py`).
10. ~~**Remove dead `/ws/chat` + stub `/undo`/`/edit`**~~ ✅ Done — `/ws/chat` removed; `/undo`/`/edit` now handled by the graph.
11. **Hosted vector DB** (Pinecone/pgvector/Weaviate) — the local 4-layer RAG stack is fine for single-replica; add a managed backend for multi-replica SaaS.

---

## Documentation Audit Notes

- This file intentionally resists over-promising. Where README/marketing copy describes a feature that is only partially wired, the status column says 🟡 with the specific reason.
- The "Suggested next steps" are the audit's opinionated recommendations, prioritized by impact-to-effort ratio. They are not commitments.
- This file reflects code reality as of v0.6.1+, not marketing futures.
