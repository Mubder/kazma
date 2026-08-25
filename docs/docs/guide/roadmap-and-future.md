---
id: roadmap-and-future
title: Roadmap & Future
sidebar_label: Roadmap & Future
description: Kazma Roadmap & Future — code-audited reference (unified docs, v0.9+)
---
> An honest separation of what Kazma does today from what is planned, aspirational, or partially wired. Anchored to the v0.9+ codebase (post production-readiness).

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
| Strict tool JSON Schema | ✅ | `additionalProperties: false` always; `KAZMA_STRICT_TOOLS=1` for OpenAI `function.strict`. |
| Structured outputs (`response_format`) | ✅ | Opt-in on `LLMProvider.chat` / `chat_stream`; not forced on supervisor turns. |
| Pre/Post tool hooks | ✅ | `agent/tool_hooks.py`. Deny/rewrite/observe. Cannot skip HITL. `KAZMA_TOOL_HOOKS=0`. |
| First-class plan mode | ✅ | `/plan on` · `/plan go`. Structural read-only, then execute. `KAZMA_PLAN_MODE=0`. |
| Streaming (SSE) | ✅ | `chat_stream()` + `invoke_llm_chat()`; SSE/WS consume synthetic `on_chat_model_stream`. |
| Context compaction (LLM summarise) | ✅ | `compaction.py`. |
| Compaction with memory retrieval + checkpoint | 🟡 | Memory adapter wired on main paths; checkpoint_manager still optional. |
| Rate-limit (429) handling | ✅ | Exponential backoff + Retry-After in `llm_provider.py` and native Anthropic `/messages`. Exhausted 429 is `transient=True` + `kind=rate_limit_exhausted` (no same-provider re-retry). |
| Cost breaker auto-wired | ✅ | `CostCircuitBreaker` instantiated per-agent (`agent_runner.py`) and driven on the live loop — `record_user_interaction()` on each inbound message, `should_halt()` gate, and `record_cost()` after each LLM call (`graph_builder.py`). Exposed on the dashboard via `.status()`. |

---

## 3. Memory & RAG

> **Updated 2026-07-27** — strengthen + SQLite L2 graph + consolidator + graph UI on `main`.  
> Backlog: [`docs/plans/MEMORY_REMAINING.md`](https://github.com/Mubder/kazma/blob/main/docs/plans/MEMORY_REMAINING.md). Full guide: [Memory & RAG](memory-and-rag).

| Capability | Status | Notes |
|---|---|---|
| V2 cognitive engine | ✅ | Bi-temporal belief graph + 4-tier episodes + procedural DAGs. Single memory stack (V1 removed). |
| Per-turn RAG | ✅ | V2 `recall()` (beliefs + episodes + PPR) every user turn. |
| Compaction memory inject | ✅ | V2 `recall.search()` + summary store via `swarm_bridge`. |
| Swarm memory bridge | ✅ | Worker results + SoulEvolution + compaction summaries written to V2. |
| Bi-temporal belief graph | ✅ | Functional/set/state predicates; `valid_until`/`invalidated_at`. |
| Local Ego-Graph PPR | ✅ | 2-hop, N≤200, α=0.15 recall boost. |
| Durable consolidation queue | ✅ | `memory_ops.db` task queue + 6h macro_sleep + 24h backup/export. |
| Procedural action DAGs | ✅ | Laplace-smoothed skill confidence C(d)=(S+1)/(N+2). |
| Nightly backup + export | ✅ | Native `sqlite3.backup()` + JSONL/GraphML on a 24h scheduler. |
| Arabic tokenizer (FTS5) | ✅ | V2 episode FTS5 + symmetric normalization. |
| Multi-replica shared vectors/graph | 🔴 | Local files only — MEMORY_REMAINING S1–S2. |
| `checkpoint_manager` in compaction | 🟡 | Still optional — LangGraph checkpointer covers turns. |

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
| Delegation Ed25519 + AES-GCM | 🟡 | Library/archive only — not wired into default runtime; SwarmEngine is SoT. |
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
| WebSocket chat | ✅ | `/ws/chat/{session_id}` is telemetry / cursor resume; SSE `/api/chat/stream` is the graph transport (`KAZMA_WS_GRAPH=1` restores WS graph). |
| TUI | ✅ | Textual, read-mostly. |
| EN/AR i18n + RTL | ✅ | Inline dict, Calibri + 16px base. |
| Majlis protocol | ✅ | `majlis.py` (core), not a UI toggle. |
| Voice on Discord/Slack/Web | ✅ | STT + TTS wired into all platforms via `voice_helpers.py` (was Telegram-only). |
| Media / attachments (photo/doc/video) | ✅ | `Attachment` contract on `IncomingMessage`/`OutboundMessage`; inbound+outbound on all platforms + Web `/api/chat/upload`. |
| `/undo`, `/edit` slash commands | ✅ | Handled by the graph (`_handle_undo`/`_handle_edit` mutate checkpoint state). |
| Time-travel replay & fork | ✅ | `SnapshotRecorder` wired into all graph-build sites; `/replay` restore + `/fork` branch + Web UI `/replay` timeline panel + live SSE snapshot events + `/api/replay/*`. |

---

## 7. Integrations

| Capability | Status | Notes |
|---|---|---|
| OpenAI-compatible providers (18 presets) | ✅ | `providers.py` — incl. Mistral/Together/Cohere/Fireworks/Perplexity/AI21/Groq/xAI/OpenRouter/NVIDIA. |
| Native non-OpenAI providers | ✅ | `AnthropicProvider` (`/messages`), `AzureProvider` (`api-key`+`api-version`), `BedrockProvider` (SigV4 + Converse), `GeminiProvider` (ADC). See [LLM Providers](../reference/llm-providers). |
| Google Vertex AI (ADC) | ✅ | `google_llm.py`. |
| Local servers (Ollama/LM Studio) | ✅ | Dummy-key handling. |
| MCP (stdio + SSE + Streamable HTTP) | ✅ | `mcp/manager.py` — Streamable HTTP (MCP 2025-03-26 spec) with `Mcp-Session-Id` resumption. Resources/prompts/sampling/roots client surfaces (2026-08-25); sampling is HITL fail-closed. Not an MCP *server*. |
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
| Langfuse tracing | ✅ | Wired via `KazmaTracer`; **auto-on when keys exist** (`logging.langfuse.enabled: auto`). |
| Prometheus scrape endpoint | ✅ | `/metrics` + `/api/metrics` in `kazma_ui/metrics.py`, mounted in `app.py` (gateway-active block). Emits `text/plain; version=0.0.4` with inbound/outbound/error counters, active threads, adapter, queue-depth, and swarm gauges. |
| OpenTelemetry export | 🔴 | **Removed** — dead code + 8 packages purged. Langfuse + Console remain as the two backends. Re-add only if OTLP export to Jaeger/Tempo becomes a real requirement. |

---

## 9. Suggested next steps

Memory (2026-07): strengthen + L2 graph + consolidator + graph UI are **done**.  
Remaining memory polish/scale only in [`MEMORY_REMAINING.md`](https://github.com/Mubder/kazma/blob/main/docs/plans/MEMORY_REMAINING.md).

Other open items:

1. **429 backoff** — done 2026-08-25 (generic + Anthropic; see leftover GOAL).
2. **Resolve the OpenTelemetry question** — dead OTel code + `[tracing]` extra removed; Langfuse + Console remain. Re-add only if OTLP export is required. **Wontfix here** (D5).
3. **Hosted vector DB** — **pgvector is now the default dense engine when Postgres is on** (industry stack part 6). Pick **Qdrant** in Settings if recall latency becomes the bottleneck. Do not grow Chroma as production memory.
4. **IDE chrome** — **Monaco + `file_apply_patch`** (industry stack part 7). **Codebase index** (ripgrep + symbols) shipped 2026-08-25. **LSP** (hover/complete/definition/diagnostics) shipped 2026-08-25. **`kazma ask` + ACP stdio** shipped 2026-08-25 (live tokens, TTY HITL, `session/request_permission`).
5. **E2B + Temporal** — **opt-in adapters** (industry stack part 8). Untrusted `python_exec` via Firecracker; durable swarm steps via Temporal. Planner and HITL stay Kazma.

---

## Documentation Audit Notes

- This file intentionally resists over-promising. Where README/marketing copy describes a feature that is only partially wired, the status column says 🟡 with the specific reason.
- The "Suggested next steps" are the audit's opinionated recommendations, prioritized by impact-to-effort ratio. They are not commitments.
- This file reflects code reality as of v0.9+, not marketing futures.
