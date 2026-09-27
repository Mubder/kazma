---
id: roadmap-and-future
title: Roadmap & Future
sidebar_label: Roadmap & Future
description: Kazma Roadmap & Future — code-audited reference (unified docs, v0.11+)
---
> An honest separation of what Kazma does today from what is planned, aspirational, or partially wired. Every row re-checked against the code on 2026-09-27.

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

> **Updated 2026-09-27** — every memory findable, evidence-ranked recall, weekly summaries, About me, forgetting, the Postgres mirror kept whole.  
> Backlog: [`docs/plans/MEMORY_REMAINING.md`](https://github.com/Mubder/kazma/blob/main/docs/plans/MEMORY_REMAINING.md). Full guide: [Memory & RAG](memory-and-rag).

| Capability | Status | Notes |
|---|---|---|
| V2 cognitive engine | ✅ | Bi-temporal belief graph + 4-tier episodes + procedural DAGs. Single memory stack (V1 removed). |
| Per-turn RAG | ✅ | V2 `recall()` (beliefs + episodes + PPR) every user turn. |
| Compaction memory inject | ✅ | V2 recall feeds a compaction. Its summary is not stored in memory (2026-09-27): every turn already is a memory, and the weekly summaries hold the gist. |
| Swarm memory bridge | ✅ | Worker results + SoulEvolution written to V2. |
| Bi-temporal belief graph | ✅ | Functional/set/state predicates; `valid_until`/`invalidated_at`. |
| Local Ego-Graph PPR | ✅ | 2-hop, N≤200, α=0.15 recall boost. |
| Durable consolidation queue | ✅ | `memory_ops.db` task queue + 6h macro_sleep + 6h backup/export + 24h reconsolidation. |
| Procedural action DAGs | ✅ | Laplace-smoothed skill confidence C(d)=(S+1)/(N+2). |
| Backup + export | ✅ | Native `sqlite3.backup()` + JSONL/GraphML every 6 h; restic snapshots, local and offsite. |
| Arabic tokenizer (FTS5) | ✅ | V2 episode FTS5 + symmetric normalization. |
| Multi-install memory | 🟡 | Memories and facts are mirrored to Postgres and kept whole by an anti-entropy sync; recall reads what other installs wrote. Vectors and the graph stay local to each install. |
| Evidence-ranked recall | ✅ | A memory is shown only when its meaning and words clear per-kind thresholds set on a benchmark (`memory/benchmark.py`); nothing is injected when nothing matches. |
| Weekly topic summaries | ✅ | One summary per topic per week, written by the model, recalled after the history. |
| About me | ✅ | Settings → Memory: text the user writes, read at the start of every reply. |
| Forget, don't remember a chat, export | ✅ | Tombstones and a forget ledger every writer asks; `/memory off` on chat platforms; `GET /api/memory/v2/export`. |
| Memory used, per answer | ✅ | Each turn stores which memories, facts and summaries the model was given, shown in its activity. |
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
| Worker autoscaling | ✅ | `dispatch_inner` spawns a worker from `swarm_templates.json` when none matches; idle workers are reaped after 5 minutes. |
| Prometheus metrics | ✅ | Optional `prometheus-client` extra; `/metrics` endpoint in `routes_direct.py`. |

---

## 5. Safety & security

| Capability | Status | Notes |
|---|---|---|
| Graph HITL gate (interrupt) | ✅ | Active on all production build sites. |
| Swarm bus HITL gate (fail-closed) | ✅ | `swarm/safety.py`. |
| Pipeline checkpoint HITL | ✅ | `checkpoint_manager.py`. |
| Skill HMAC signing + verification | ✅ | `hub/cli.py` + `hub/loader.py`. |
| Delegation Ed25519 + AES-GCM | 🔴 | Not in the code. Skills are HMAC-verified at load; there is no signed delegation between agents. |
| MCP SSE bearer auth | ✅ | `mcp/manager.py:461-466`. |
| MCP stdio auth | ✅ | `auth.type: env` / `arg` injection supported on stdio servers. |
| Vault-backed ConfigStore secrets | ✅ | Sensitive keys → AES vault when `KAZMA_VAULT_KEY` set (2026-07 audit remediations). |
| `/undo` / `/edit` checkpoint mutation | ✅ | Live graph path via `aget_state` / `aupdate_state`. |
| Remote secret login page | ✅ | `/login` + `POST /api/auth/login`. |
| Cryptographic "trust tiers" | 🔴 | Only a boolean `certified` flag + unused `trust:` string. |
| `kazma-security.yaml` hardening checks | 🔴 | No code reads that file; it is a declaration. What enforces security lives in the code and CI: the approval gates, the commitment layer, the vault, the static gates in `tests/`, bandit in CI and Dependabot. |

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
| EN/AR i18n + RTL | ✅ | Catalog-merged dict, IBM Plex Sans / IBM Plex Sans Arabic, shared 14px root. |
| X Studio (`/x`) | ✅ | Composer + X-only planner (Post now, Schedule, reschedule, threads, delete). Chat `x_post` stays always-HITL. Official API only. |
| Majlis protocol | 🟡 | A greeting and farewell fast path on the chat platforms; ordinary conversation does not enter its phase machine (`majlis.py`). |
| Voice on Discord/Slack/Web | ✅ | STT + TTS wired into all platforms via `voice_helpers.py` (was Telegram-only). |
| Media / attachments (photo/doc/video) | ✅ | `Attachment` contract on `IncomingMessage`/`OutboundMessage`; inbound+outbound on all platforms + Web `/api/chat/upload`. |
| `/undo`, `/edit` slash commands | ✅ | Handled by the graph (`_handle_undo`/`_handle_edit` mutate checkpoint state). |
| Time-travel replay & fork | ✅ | `SnapshotRecorder` wired into all graph-build sites; `/replay` restore + `/fork` branch + Web UI `/replay` timeline panel + live SSE snapshot events + `/api/replay/*`. |

---

## 7. Integrations

| Capability | Status | Notes |
|---|---|---|
| Provider presets (20: OpenAI-compatible, native, local, custom) | ✅ | `providers.py` — incl. Mistral/Together/Cohere/Fireworks/Perplexity/AI21/Groq/xAI/OpenRouter/NVIDIA. |
| Native non-OpenAI providers | ✅ | `AnthropicProvider` (`/messages`), `AzureProvider` (`api-key`+`api-version`), `BedrockProvider` (SigV4 + Converse), `GeminiProvider` (ADC). See [LLM Providers](../reference/llm-providers). |
| Google Vertex AI (ADC) | ✅ | `google_llm.py`. |
| Local servers (Ollama/LM Studio) | ✅ | Dummy-key handling. |
| MCP (stdio + SSE + Streamable HTTP) | ✅ | `mcp/manager.py` — Streamable HTTP (MCP 2025-03-26 spec) with `Mcp-Session-Id` resumption. Resources/prompts/sampling/roots client surfaces (2026-08-25); sampling is HITL fail-closed. Kazma is also an MCP server: `kazma mcp` (stdio) offers its tools to other agents, danger tools behind the same approval gate. |
| Skill Hub (registry, signing, certification) | ✅ | `hub/`. |
| Langfuse tracing | ✅ | `KazmaTracer`; `logging.langfuse.enabled: auto` when keys exist. `KAZMA_LANGFUSE=0`. |
| OpenTelemetry | ✅ opt-in | GenAI spans for every LLM call and tool execution once `opentelemetry-sdk` is installed and an OTLP endpoint is set ([ops guide](../ops/opentelemetry)). |
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
| OpenTelemetry export | ✅ opt-in | GenAI semantic-convention spans over OTLP ([ops guide](../ops/opentelemetry)). Langfuse and the console remain. |

---

## 9. Suggested next steps

Memory (2026-07): strengthen + L2 graph + consolidator + graph UI are **done**.  
Remaining memory polish/scale only in [`MEMORY_REMAINING.md`](https://github.com/Mubder/kazma/blob/main/docs/plans/MEMORY_REMAINING.md).

Other open items:

1. **429 backoff** — done 2026-08-25 (generic + Anthropic; see leftover GOAL).
2. **OpenTelemetry** — resolved: GenAI spans ship, opt-in ([ops guide](../ops/opentelemetry)).
3. **Vectors** — memory's meaning search is exact and local (sqlite-vec, or NumPy), measured at about 1 ms per 1,000 memories. pgvector is used when the Postgres server has the extension (picked automatically) and Qdrant is a Settings choice; neither is needed. The Knowledge Library's vectors live in Chroma and are rebuilt from its chunks.
4. **IDE chrome** — **CodeMirror 5 `fromTextArea` + `file_apply_patch` / `file_apply_patch_set`** (Hands 0.11). Monaco was tried and reverted (hang / empty tabs). **Codebase index** (ripgrep + symbols) shipped 2026-08-25. `/api/ide/lsp` still exists; the Web editor is syntax-only. **`kazma ask` + ACP stdio** (live tokens, TTY HITL, `session/request_permission`, structured diffs, `session/cancel`).
5. **E2B + Temporal** — **opt-in adapters** (industry stack part 8). Untrusted `python_exec` via Firecracker; durable swarm steps via Temporal. Planner and HITL stay Kazma.

---

## Documentation Audit Notes

- This file intentionally resists over-promising. Where README/marketing copy describes a feature that is only partially wired, the status column says 🟡 with the specific reason.
- The "Suggested next steps" are the audit's opinionated recommendations, prioritized by impact-to-effort ratio. They are not commitments.
- This file reflects code reality as of v0.11+, not marketing futures.
