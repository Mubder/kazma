# Roadmap & Future

> An honest separation of what Kazma does today from what is planned, aspirational, or partially wired. Anchored to the July 2026 codebase state.

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
| Compaction with memory retrieval + checkpoint | 🟡 | `create_authority()` called without `memory_store`/`checkpoint_manager` (`agent_runner.py:162-166`). Summarise-only at runtime. |
| Rate-limit (429) handling | 🔴 | No backoff; `retry.py` skips 4xx. |
| Cost breaker auto-wired | 🟡 | Standalone dataclass; agent must drive it. |

---

## 3. Memory & RAG

| Capability | Status | Notes |
|---|---|---|
| ChromaDB vector memory (RAG tools) | ✅ | Opt-in via `memory_search`/`memory_store`. |
| 4-layer UnifiedMemoryAdapter (RRF) | 🟡 | Works, but only used by `self_improvement.py` + `phonebook.py`. |
| Automatic RAG injection into prompts | 🔴 | Retrieval is tool-based, not automatic. |
| Short-term→permanent consolidation | 🔴 | No promotion logic; only explicit `memory_store`. |
| Document chunking | 🔴 | No chunker; one `add()` = one doc. |
| Arabic tokenizer (FTS5) | ✅ | `arabic_tokenizer.py`. |
| `SQLiteMemoryBackend` used in chat retrieval | 🟡 | Wired as `self.memory` but no chat-path caller of `search()`. |
| `distance()` bug in `search_backend._vector_search` | 🟡 | Known broken path; unreached in practice. |

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
| Prometheus metrics | 🔴 | No `prometheus_client`; no `/metrics`. |

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
| MCP stdio auth | 🔴 | No auth on stdio transport. |
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
| In-house tracing spans | ✅ | `TracingEmitter`. |
| SSE telemetry events | ✅ | `/api/chat/stream` + telemetry router. |
| Prometheus scrape endpoint | 🔴 | Absent. |
| OTel export pipeline | 🟡 | Libraries available; wiring is roadmap. |

---

## 9. Suggested next steps (from the audit)

High-leverage improvements that would close the gaps surfaced by this rewrite:

1. **Wire `memory_store` + `checkpoint_manager` into `create_authority()`** (`agent_runner.py:162-166`). This would activate the memory-enriched, checkpointed compaction the docs already describe. *(High impact, small change.)*
2. **Fix `search_backend.py`** — replace `SELECT sqlite_version()` with a real `load_extension("vec0")` probe, and replace `distance(...)` with proper vec0 `MATCH`/`k` syntax. *(Correctness.)*
3. **Wire the 4-layer adapter into the chat path** (or document explicitly that it's self-improvement-only) so the "4-layer memory" claim matches reality.
4. **Add a document chunker** for `memory_store` so long texts retrieve well.
5. **Add 429 backoff** to the retry layer (or document the proxy requirement more loudly).
6. **Auto-wire `CostCircuitBreaker`** into the agent loop so runaway spend is actually halted.
7. **Sync the version strings** (`pyproject.toml`, `kazma.yaml`, CLI `--help`).
8. **Add Prometheus `/metrics`** or commit to the OTel path and wire it.
9. **Reconcile the K8s manifests** with the main agent (or move them under a `hub/` subdir clearly labelled as the Hub API).
10. **Remove the dead `/ws/chat` endpoint** or the stub `/undo`/`/edit` commands to reduce user confusion.

---

## Documentation Audit Notes

- This file intentionally resists over-promising. Where README/marketing copy describes a feature that is only partially wired, the status column says 🟡 with the specific reason.
- The "Suggested next steps" are the audit's opinionated recommendations, prioritized by impact-to-effort ratio. They are not commitments.
- For the canonical project roadmap, cross-reference `ROADMAP.md` (root) — this file reflects code reality as of July 2026, not future plans.
