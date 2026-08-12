---
id: glossary
title: Glossary
sidebar_label: Glossary
description: Kazma Glossary — code-audited reference (unified docs, v0.9+)
---
> Terms used throughout Kazma, with precise meanings grounded in the codebase.

---

## A

**Adapter (platform)**
A class that translates a platform's native events (Telegram updates, Discord gateway events, Slack socket events) into Kazma's `IncomingMessage` and renders `OutboundMessage` back. All extend `BaseAdapter` (`kazma-gateway/.../gateway.py:239`). Distinct from **bus adapter**.

**Adapter (bus)**
A separate adapter for swarm HITL approvals (`TelegramBusAdapter`, `DiscordBusAdapter`, `SlackBusAdapter`), all subclassing `BusAdapter` (`kazma_core/swarm/bus.py:66`). Only one is active at a time; priority Telegram > Discord > Slack.

**Agent handler**
The bridge between platform adapters and the supervisor graph. Now an `agent_handler/` **package** (decomposed from the old `agent_handler.py` file). Owns platform isolation.

**Aggregation**
How fan-out results are combined: `collect`, `first_valid`, `merge_all`, `vote`, `synthesize` (`swarm/aggregator.py`).

**Authority (Context)**
`ContextAuthority` (`authority.py`) — decides when to compact the conversation (at 80% of the context window) and invokes the `CompactionEngine`.

**AutoScaler**
Spawns `\{name\}-pool-\{n\}` workers from templates when `auto` routing finds no capable worker; reaps idle instances.

---

## B

**Blackboard (pipeline)**
The shared context passed between pipeline stages; each stage sees prior stages' outputs.

**BlackboardStore**
The shared scratchpad object passed between pipeline/fan-out/consult workers; each stage reads prior outputs and writes its own.

**Bounded concurrency**
`BoundedConcurrency` (`reliability.py:872`) — an `asyncio.Semaphore` wrapper limiting parallel dispatches (default 5).

**Breaker (circuit)**
`CircuitBreaker` (`reliability.py:238`) — three-state (CLOSED/OPEN/HALF_OPEN) failure isolator. The half-open state allows exactly one probe (`_probe_in_flight`).

**Bus (message)**
The singleton (`get_message_bus()`, `bus.py:282`) that routes swarm HITL approval requests to the active platform bus adapter.

---

## C

**Checkpointer**
The LangGraph persistence layer (`AsyncSqliteSaver` on `kazma-data/checkpoints.db`) that stores conversation state and HITL pauses.

**CheckpointManager**
`swarm/checkpoint_manager.py` — owns paused-pipeline state, arms auto-reject timeouts, persists to SQLite, restores on restart.

**Classify (MCP tool)**
`classify_mcp_tool()` (`mcp/manager.py:71`) — classifies a runtime-discovered MCP tool as `danger`/`safe`/`unknown` by name pattern. Unknown defaults to danger.

**Code-switch token**
An English/Latin run embedded inside Arabic text, tagged `CODE_SWITCH` by the dual tokenizers so it is preserved rather than transliterated or stemmed.

**CommandConsole**
The TUI's vim-style `:`-activated command bar (enter ex-style commands such as `:theme`, `:lang`).

**Compaction**
`CompactionEngine` (`compaction.py`) — summarises the conversation when it nears the context window. In the default wiring, only the LLM summarise step runs (memory retrieval + checkpointing are no-ops).

**ConfigStore**
The SQLite-backed runtime settings store (`config_store.py`) that overrides `kazma.yaml`. Singleton via `get_config_store()`. WAL + `busy_timeout=5000`.

**CONSULT**
A swarm pattern: gather parallel opinions from multiple workers, then LLM-synthesize.

**ContextAuthority**
See *Authority*.

**CostCircuitBreaker**
`cost_breaker.py` — trips when spend exceeds a ceiling (default $0.50) after a silence window (default 5 min). Not auto-wired into `chat()`.

---

## D

**Danger tool**
A tool that requires HITL approval before execution. There are **three** lists (graph/swarm/MCP) — see [Security & Safety](security-and-safety#danger-tool-lists-three-of-them).

**Delegation**
Historical inter-agent task handoff with cryptographic integrity (Ed25519 / AES-GCM). **Library/archive only** as of 2026-07 — live multi-worker work uses SwarmEngine handoffs, not the delegation package. Distinct from MCP and skills.

**DISPATCH**
A swarm pattern: one worker handles a task.

**Document Intelligence**
Durable document platform under `kazma_core/documents/`: quarantine → sniff →
isolated parse/OCR → optional index/generate/redact. Public durable boundary is
`DocumentIngestionService`; execution is `DocumentService`. Surfaces: Web
`/documents`, `/api/documents/*`, `document-platform` tools, gateway
`/documents`, TUI Documents tab. See [Document Intelligence](document-intelligence).

**DocumentIR**
Canonical immutable intermediate representation of a parsed document (pages,
blocks, hashes) stored as a per-version manifest — paged reads do not re-parse
hostile bytes.

**DocumentJobState**
Durable job lifecycle enum: `received` → `quarantined` → `validating` →
`ready_to_parse` / `ocr_required` → `parsing` / `ocr_running` → … → `ready`
(plus `retry_wait`, `rejected`, `cancelled`, `dead_letter`).

**Dormant gate**
A HITL gate that is inactive because `hitl_config` was not passed at graph build time (all tools go to `safe_tools`).

---

## F

**FAN_OUT**
A swarm pattern: parallel execution across workers with bounded concurrency, then aggregation.

**Fallback chain**
Sequential worker fallbacks after primary failure (`reliability.py:734`).

---

## H

**Handoff**
A worker transferring a task to another worker mid-execution. Capped at depth 5 and 2 visits per worker (`handoff_guards.py`).

**HITL**
Human-in-the-Loop. Kazma has **three** independent HITL gates: graph interrupt (chat), swarm bus (dispatch), pipeline checkpoints.

**Hub**
The skill registry/marketplace (`kazma_core/hub/`) with signing, certification, and search (`kazma hub …`).

---

## I

**i18n**
The inline EN/AR translation system in `kazma-ui/kazma_ui/i18n.py` (no separate files). Drives `dir`/`lang` per request.

**interrupt()**
LangGraph's suspension primitive, called in `tool_worker_node` for danger tools. Resumed via `Command(resume=...)`.

**InProcessWorker**
The worker implementation for both `in_process` and `telegram_bot` worker types (`worker_factory.py:17`).

---

## M

**Majlis**
The Gulf cultural conversational protocol (`majlis.py`) — 4-phase: GREETING → SOCIAL → TRANSACTION → FAREWELL. A core module, not a UI toggle.

**MCP**
Model Context Protocol. External tool servers (stdio or SSE) proxied into the agent. SSE supports bearer auth; stdio does not.

**ModelRegistry**
The process-wide singleton (`model_registry.py:81`) managing providers, models, discovery, and the active selection. Alias: `UnifiedModelRegistry`.

---

## O

**OutputValidator**
`reliability.py:488` — validates worker output against a Pydantic model, JSON Schema, or simple type dict.

---

## P

**Phonebook**
`swarm/phonebook.py` — direct summon-and-dispatch bypassing the reliability layer; used by topology/DAG executors. Injects episodic memory from the V2 recall path.

**PermissionLevel**
The swarm tool-access tier: `READ_ONLY`, `SYSTEM_EXEC`, `FULL_ACCESS`. Assigned per worker role.

**Pipeline**
A swarm pattern: ordered stages sharing a blackboard, with optional HITL checkpoints.

**Platform isolation**
The invariant that `chat_id`/`user_id`/`message_id` etc. never enter the graph state — they live in the `SessionStore` and are re-attached on reply via `_build_target_id()`.

---

## R

**RRF**
Reciprocal Rank Fusion — a rank-blending algorithm. The V1 `UnifiedMemoryAdapter` used it to fuse its 4 layers (`_RRF_K = 60`); that stack was removed in the V1→V2 cutover. V2 recall still fuses belief + episode + PPR hits via an internal RRF step in `memory/recall.py`.

**ReliabilityRegistry**
`swarm/reliability_registry.py` — a config holder for per-worker breakers, retries, timeouts, validators, concurrency. The state machines live in `reliability.py`.

**RetryPolicy**
`reliability.py:66` — exponential backoff (default 3 retries, 1–60 s). Does not retry 4xx.

---

## S

**SafetyMiddleware**
`swarm/safety.py:47` — the swarm bus HITL gate. `check_sync()` is fail-closed. (There is no class named `SafetyGate`/`SafetyChecker`.)

**SessionStore**
The platform ID ↔ thread_id mapping (`gateway.py:185`). SQLite or in-memory. Never holds graph state.

**Skill**
A packaged, optionally-signed Python entry point + manifest registering one or more tools. Loaded from `kazma-skills/manifests/` or the Hub.

**sqlite-vec**
A SQLite extension for vector search. In V2 it backs `memory/vector_engine.py` (native sqlite-vec with a guarded NumPy fallback) for episode dense-vector recall. Declared in the `[rag]` optional extra (`sqlite-vec>=0.1.6`).

**SwarmEngine**
`swarm/engine.py:103` — the central async orchestrator for swarm workers.

---

## T

**TaskType**
The enum of swarm patterns: `DISPATCH`, `BROADCAST`, `PIPELINE`, `FAN_OUT`, `CONSULT`, `CONDITIONAL`.

**TimeoutGuard**
`reliability.py:397` — per-task timeout (default 300 s) with `fail`/`retry`/`skip` behavior.

**ToolRegistry**
`agent/tool_registry.py` — the registry the supervisor consults each turn. `execute()` is the single execution path and the swarm bus HITL gate.

**TUI**
The Textual terminal dashboard (`kazma-tui`), a read-mostly consumer of the core singletons.

---

## U

**UnifiedMemoryAdapter**
*Removed in the V1→V2 cutover.* The former 4-layer memory blender (`swarm/memory/adapter.py`): ChromaDB + SQLite property graph + FTS5 + sqlite-vec, RRF-fused. Superseded by the **V2 Cognitive Engine** — see *V2 Cognitive Engine*.

**UnifiedRouter**
The auto-routing engine for `workers=["auto"]` swarm dispatch.

---

## V

**V2 Cognitive Engine**
Kazma's single memory stack (`kazma-core/kazma_core/memory/`). Bi-temporal belief graph (functional/set/state predicates with `valid_from`/`valid_until` + `ingested_at`/`invalidated_at` axes), 4-tier episodes, Local Ego-Graph Personalized PageRank recall, procedural skill DAGs, and a durable SQLite-backed consolidation queue. Unified read path: `recall()` in `memory/recall.py`; write path: `mutate_belief()` in `memory/belief_mutation.py`. Active by default (`memory.v2.use_new_stack: true`). Replaced the V1 4-layer RRF stack.

**VectorMemory**
*Removed in the V1→V2 cutover.* The former ChromaDB-backed memory singleton (`memory/vector_store.py`) used for boot / health / tool fallback. V2 uses `memory/vector_engine.py` (sqlite-vec native + guarded NumPy fallback) instead.

**Consolidator**
Post-turn librarian (`memory/consolidator.py`): extracts durable facts + SPO triples (LLM + heuristic), prompt-fenced, deduped vs auto_store, writes adapter + L2 graph.

---

## K

**Kazma (كاظمه)**
The product name in Latin script is **Kazma**. Correct Arabic brand forms are **كاظمه** (preferred) and **كاظمة**. The spelling **كازما** (with ز) is wrong and must not be used. Product self-knowledge is injected via `kazma_core.product_knowledge` into the supervisor system prompt.

**Web research tools**
Built-in search/scrape/research stack: `web_search`, `read_url` (paging), `read_url_to_file`, `crawl_site` (bounded multi-page), chunk/digest helpers. Used from chat (no `/research` slash). Guide: [Web research](web-research).

---

## W

**Worker**
A swarm execution unit (`SwarmWorker`). Registered in `_workers`; resolved via the registry or phonebook.

**WorkerCapabilities**
A `\{role, expertise, tools, model_specialty\}` object describing what a worker can do; used by `UnifiedRouter` and the AutoScaler to match tasks to workers.

**WorkerRegistry**
JSON-backed worker registry (`swarm/registry.py`), loaded from `swarm_registry.json`.
