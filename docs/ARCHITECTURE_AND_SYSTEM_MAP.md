# Kazma — Architecture & System Map

**Single source of truth for system architecture**  
**Version:** 0.6.1+ (post production-readiness remediation)  
**Date:** 2026-07-21  
**Companion docs:** `docs/audits/REPO_CLEANUP_PLAN.md`, `docs/audits/REMEDIATION_PLAN_2026-07-21.md`, `docs/ops/*`, `AGENTS.md`

**Honesty note:** This map prioritizes **production-wired paths** and catalogs **all source modules** under main packages. Generated artifacts (`docs/node_modules`, `docs/build`, `__pycache__`, `.venv`) are excluded. Library-only modules are labeled **[LIBRARY]**.

---

## Section 1: Executive Overview & Data Flow

### Philosophy

Kazma is a **multi-platform autonomous agent framework**: one **LangGraph supervisor brain**, many **mouths** (Telegram/Discord/Slack/Web/TUI), one **IDE/tool execution layer**, and optional **swarm multi-worker orchestration**. Platform IDs never enter LangGraph state. Danger tools require **HITL** (graph interrupt, swarm bus, or pipeline checkpoint). Config is runtime-mutable via **ConfigStore** (SQLite or Postgres).

### Runtime requirements

| Requirement | Notes |
|-------------|--------|
| Python | 3.11–3.14 |
| Default data | `kazma-data/` SQLite WAL |
| Optional RAG | `[rag]` → ChromaDB + sentence-transformers |
| Optional multi-replica | `[postgres]` + `KAZMA_DATABASE_URL` |
| Default bind | Loopback preferred; Docker `0.0.0.0` with secret |

### Operational boundaries

- **Single-operator trusted host** by default.  
- **Production profile:** `KAZMA_PRODUCTION=1` (Docker code_exec, YOLO off, vault key required, workspace root required).  
- **Multi-user SaaS foundation:** platform RBAC + OIDC + opaque sessions + Postgres cutover.  
- **Not:** multi-primary multi-region write DBs without external DB product.

### Data-flow diagram (ASCII)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ CLIENT INPUTS                                                                │
│  Web UI / SSE / WS   CLI (kazma)   TUI (textual)   Gateway adapters          │
│  Telegram/Discord/Slack   GitHub OAuth/webhooks   MCP IDE bridge             │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ EDGE: FastAPI lifespan (app.py)                                              │
│  Auth middleware (secret / opaque session / API token / OIDC session)        │
│  Tenant middleware (prod: ignore spoofed X-Tenant-ID; JWT or default)        │
│  CORS · i18n · static · health/live · health/ready (DB ping)                 │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
   SessionManager        Gateway SessionStore    Swarm Task APIs
   (chat threads)        (platform isolation)    (TaskStore)
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ AGENT BRAIN                                                                  │
│  KazmaAgent / agent_runner  →  build_supervisor_graph (LangGraph)            │
│  Checkpointer: AsyncSqliteSaver | AsyncPostgresSaver                         │
│  Interrupt HITL (danger tools) · turn_input · context compaction             │
│  SubAgentManager → build_child_graph (auto-deny danger)                      │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ TOOLS & SANDBOX                                                              │
│  LocalToolRegistry (SoT)  ·  UnifiedToolExecutor (local + MCP force_danger)  │
│  shell_exec (allowlist + env scrub)  ·  python_exec (Docker jail / blocklist)│
│  IdeService → same tools + HITL  ·  native skills (kazma-skills)             │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ SWARM ENGINE                                                                 │
│  dispatch / broadcast / pipeline / fanout / consult                          │
│  handoff_guards (depth 5, visits 2) · ReliabilityRegistry (breakers/retry)  │
│  Bus adapters (Telegram>Discord>Slack) · NullBus fail-closed                 │
│  TaskStore (SQLite|Postgres) · SSE bridge · checkpoint_manager               │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ DATASTORES                                                                   │
│  ConfigStore (settings / vault refs)  ·  SessionManager  ·  checkpoints      │
│  TaskStore  ·  cron.db  ·  FTS5 memory  ·  VectorMemory/Chroma               │
│  WorkspaceStore  ·  optional Postgres pool (multi-replica shared state)      │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Section 2: Directory & Module Reference

### 2.1 Top-level tree (source-focused)

```
kazma/
├── serve.py                 # Alternate WebUI entry (hardened secrets)
├── pyproject.toml           # Workspace package + extras [rag,postgres,…]
├── kazma.yaml               # Product defaults
├── kazma.local.yaml.example # Local overrides template
├── docker-compose.yml       # Single-node container
├── docker-compose.postgres.yml
├── docker-compose.ha.yml    # Multi-replica + optional nginx
├── Dockerfile               # [rag,postgres] image
├── deploy/nginx-ha.conf
├── scripts/                 # backup, restore, migrate, smoke, entrypoint
├── docs/                    # Docusaurus site + audits + ops + this map
├── archive/docs-v2/         # Archived (merged into docs/docs/)
├── tests/                   # Root regression suite (~3.7k tests)
├── loadtests/               # k6/locust
├── examples/                # Sample skills (e.g. almuhalab)
├── archive/                 # Historical packages
├── kazma-core/              # Brain, swarm, tools, safety, db
├── kazma-ui/                # FastAPI web + static + SSE
├── kazma-gateway/           # Platform adapters + slash + routers
├── kazma-tui/               # Textual dashboard/IDE
├── kazma-cli/               # `kazma` CLI
├── kazma-skills/            # Native skill packages + YAML manifests
├── kazma-memory/            # Shared search/tokenizer backend
├── kazma-data/              # Runtime DBs (local; do not commit secrets)
├── kubernetes/              # Sample manifests
└── library/                 # Process notes
```

### 2.2 Package: `kazma-core/kazma_core/` (brain)

| Module | Purpose |
|--------|---------|
| `__init__.py` | Package exports |
| `agent_runner.py` | KazmaAgent lifecycle, graph ensure, turn timeout, Postgres/SQLite checkpointer |
| `audit_logger.py` | Structured security/ops audit events |
| `authority.py` | Context authority / compaction threshold helpers |
| `authorization_flow.py` | **[LIBRARY]** Cross-division approval flows |
| `compaction.py` | Message history compaction |
| `config_loader.py` | Merged YAML (`kazma.yaml` + `kazma.local.yaml`) |
| `config_schema.py` | Pydantic config models |
| `config_store.py` | Runtime settings SoT (SQLite or Postgres) |
| `constants.py` | Shared constants (danger tool lists — prefer CANONICAL) |
| `cost_breaker.py` | Session budget circuit breaker |
| `cultural_context.py` / `cultural_context_enrichment.py` | Cultural prompt enrichment |
| `dialect_detector.py` | Arabic dialect detection |
| `division_sandbox.py` | **[LIBRARY]** Division-scoped sandbox |
| `exceptions.py` | Shared exception types |
| `git_identity.py` | Bot git author identity for commits |
| `google_genai_provider.py` / `google_llm.py` | Google/Vertex LLM paths |
| `http_pool.py` | Shared httpx client pool |
| `kuwaiti_tokenizer.py` / `msa_tokenizer.py` | Arabic tokenization helpers |
| `language_lock.py` | Response language lock |
| `llm_provider.py` | OpenAI-compatible LLM client, retries, reconfigure aclose |
| `logging_config.py` | Logging setup |
| `majlis.py` | **[LIBRARY]** Cultural orchestrator shell |
| `mcp_client.py` | Legacy/alternate MCP client helpers |
| `metrics.py` | Metrics helpers |
| `migrations.py` | Config/admin migration runner |
| `model_registry.py` / `model_registry_store.py` | Provider/model resolution + persistence |
| `pacing.py` | Reply pacing for gateways |
| `paths.py` | Data path resolution |
| `permissions.py` | **[LIBRARY]** YAML permission manager |
| `personalities.py` | Personality prompts |
| `providers.py` | Provider catalog helpers |
| `rbac.py` | Division RBAC engine (enterprise) |
| `retry.py` | Generic retry utilities |
| `router.py` / `routing_engine.py` | Routing helpers / unified worker routing |
| `service_container.py` | DI container |
| `settings_manager.py` / `settings_mcp.py` / `settings_providers.py` | Settings facades |
| `shutdown.py` | Global graceful-shutdown flag |
| `state.py` | Agent state types |
| `streaming.py` | Streaming helpers |
| `summarizer.py` | Summarization utility |
| `telemetry.py` | Telemetry collection |
| `tenant_context.py` | ContextVar tenant_id |
| `time_travel.py` | Checkpoint time-travel helpers |
| `token_counter.py` / `tokenizer.py` | Token counting |
| `tone_adapter.py` | Tone adaptation for platforms |
| `tool_sandbox.py` | **[LIBRARY]** Alternate tool policy sandbox |
| `tracing.py` | Langfuse/tracer integration |
| `url_utils.py` | URL helpers |
| **agent/** | |
| `agent/graph_builder.py` | LangGraph supervisor + tool_worker + interrupt HITL |
| `agent/hitl_supersede.py` | Cancel pending HITL on new turn |
| `agent/pipeline_schema.py` | Pipeline-related schemas |
| `agent/state.py` | Supervisor state / NodeName |
| `agent/sub_agent.py` | SubAgentManager spawn + auto_deny HITL |
| `agent/tool_registry.py` | LocalToolRegistry SoT + built-in tools |
| `agent/turn_input.py` | Build messages from checkpointer + user turn |
| **agent_skills/** | Agent Skills install/discover/parse |
| **cron/** | `scheduler.py` SQLite cron + concurrency + shutdown |
| **db/** | Postgres backend selection + pool + helpers |
| **delegation/** | **[LIBRARY]** Parallel multi-agent design |
| **docs/** | **[LIBRARY]** Doc generator |
| **hub/** | Skill hub API/CLI/registry/validator |
| **ide/** | IdeService, env_context, workspace_scope |
| **mcp/** | AsyncMCPManager + UnifiedToolExecutor + classify_mcp_tool |
| **memory/** | VectorMemory, FTS5, auto_store, health |
| **models/** | Provider discovery (SSRF-guarded), model router |
| **observability/** | Alerts |
| **safety/** | hitl, hitl_grants, yolo |
| **security/** | ssrf, vault, web_sessions, platform_rbac, oidc + offline scanners **[LIBRARY]** |
| **stores/** | workspaces, bookmarks |
| **swarm/** | Full orchestration (see §3.3) |
| **system/** | installer, maintenance, runtime_manager |
| **tools/** | Standalone tool implementations + swarm ShellTool registry |
| **voice/** | STT/TTS/VAD |
| **chaos/** | Chaos testing hooks |
| **cli/** | Wizard helpers |

### 2.3 Package: `kazma-ui/kazma_ui/`

| Module | Purpose |
|--------|---------|
| `app.py` | FastAPI factory, lifespan, gateway/cron/swarm boot, router mount |
| `auth.py` | Secret/session/API-token auth, tenant middleware, RBAC path gates |
| `saas_api.py` | Multi-user + tenants admin API |
| `session_manager.py` | Chat sessions (SQLite\|Postgres) |
| `sse_chat.py` | Primary SSE chat stream + YOLO intercept + HITL frames |
| `sse_utils.py` | SSE framing helpers |
| `chat.py` | Chat page + WebSocket chat path |
| `ide_api.py` | `/api/ide/*` file/run/git/swarm |
| `workspace_api.py` | Workspace web routes |
| `settings.py` | Settings HTML/API (masked secrets) |
| `dashboard.py` | Dashboard + session list APIs |
| `swarm_panel/*` | Swarm UI APIs (tasks/workers/metrics) |
| `swarm_sse.py` | Swarm task event streams |
| `agents.py` / `mcp_ui.py` / `skills_ui.py` | Feature pages + APIs |
| `providers.py` / `models_route.py` / `models.py` | Provider/model management UI |
| `health.py` | live/ready/details probes |
| `metrics.py` | Prometheus metrics |
| `routes_direct.py` | Login, approve, system, gateway wiring, OIDC, many APIs |
| `routes_voice.py` / `routes_voice_ws.py` | STT/TTS REST + WS |
| `routes_chaos.py` / `routes_migrate.py` | Chaos + migrations UI APIs |
| `telemetry_route.py` | Telemetry SSE/snapshot |
| `gateway_monitor.py` | Gateway status start/stop |
| `hitl_approval.py` | HITL API helpers |
| `i18n.py` | en/ar translations |
| `services.py` | Service status helpers |
| `static/js/*` | Alpine/UI modules (chat, ide, swarm, settings, streaming) |
| `templates/*` | Jinja pages (chat, ide, swarm, settings, login, …) |

### 2.4 Package: `kazma-gateway/kazma_gateway/`

| Module | Purpose |
|--------|---------|
| `gateway.py` | Adapter orchestration queue |
| `adapters/telegram*.py` | Telegram bot + bus + callbacks + STT |
| `adapters/discord*.py` / `slack*.py` | Discord/Slack adapters + HITL buses |
| `agent_handler/graph.py` | Inbound message → agent graph |
| `agent_handler/hitl.py` | Gateway HITL ownership fail-closed |
| `agent_handler/commands.py` | Slash + `/ide` commands |
| `agent_handler/store.py` | Platform session isolation |
| `agent_handler/swarm_*.py` | Swarm dispatch/output from gateway |
| `routers/github*.py` | GitHub OAuth, clone, API |
| `routers/workspaces.py` / `workspace.py` | Workspace CRUD + prod root confine |
| `routers/git.py` / `bookmarks.py` / `pipeline.py` | Git ops, bookmarks, pipelines |
| `stores/checkpoint.py` | CheckpointManager + Postgres/SQLite saver |
| `stores/sqlite.py` | Gateway session SQLite store |
| `mcp_server.py` | IDE MCP server bridge |
| `slash_commands.py` / `suggestions.py` | Command catalog / UX |
| `swarm_notify.py` | **[LIBRARY]** Optional Telegram notifier |
| `telegram_format.py` / `typing_keepalive.py` / `rate_feedback.py` | Platform UX helpers |

### 2.5 Packages: CLI, TUI, skills, memory

**`kazma-cli`:** `main` (serve/wizard/status), `gateway`, `swarm`, `update`, `project`, `completions`, `banner`.

**`kazma-tui`:** Textual app, chat/dashboard/editor/files/swarm screens, widgets (HITL modal, palette, toasts).

**`kazma-skills/native/*`:** Packaged skills (git, cron, crawler, vault, health, code-review, …) + YAML manifests.

**`kazma-memory`:** Shared Arabic tokenizer + search backend used by core memory layers.

### 2.6 Scripts & deploy

| Path | Purpose |
|------|---------|
| `scripts/backup_kazma.py` / `restore_kazma.py` | DR zip backup/restore |
| `scripts/migrate_sqlite_to_postgres.py` | Full store migration |
| `scripts/smoke_production.py` | Production smoke suite |
| `scripts/docker-entrypoint.sh` | Optional auto-migrate then uvicorn |
| `deploy/nginx-ha.conf` | Multi-replica reverse proxy sample |

---

## Section 3: Subsystem Deep-Dives

### 3.1 Agent Supervisor & Graph

| Concern | Location | Behavior |
|---------|----------|----------|
| Graph build | `agent/graph_builder.py` | Supervisor ↔ tool_worker ↔ respond; max tool iterations |
| HITL interrupt | `tool_worker_node` + `hitl_config` | Danger tools `interrupt()`; batch combined card |
| Resume | `POST /api/approve/{thread_id}`, gateway `/hitl` | `Command(resume=…)` |
| Double-gate prevent | `_hitl_approved_ctx` ContextVar only | LLM cannot inject approval |
| Turn assembly | `turn_input.py` | Checkpointer history + user message |
| Sub-agents | `sub_agent.py` + `build_child_graph` | Auto-deny danger, timeout, tool filter |
| Persistence | `agent_runner` / `stores/checkpoint.py` | SQLite or AsyncPostgresSaver |
| HITL supersede | `hitl_supersede.py` | New user message cancels stale interrupt |

### 3.2 Tools & Sandboxing

| Path | Module | Notes |
|------|--------|-------|
| Agent tools | `LocalToolRegistry` | file_*, shell_exec, python_exec, memory_*, config_*, spawn_agent(s), context_info |
| MCP | `mcp/manager.py` | `force_danger=True`; prod HITL for non-allowlist |
| Swarm shell | `tools/registry.py` ShellTool | Stricter binary allowlist |
| code_exec | `tools/code_exec.py` | Docker network=none preferred; import blocklist local |
| shell_exec | tool_registry | shlex + create_subprocess_exec; env scrub; path policy; no interpreters |
| IDE | `ide/service.py` | All mutations via registry execute |

**Danger SoT:** `safety/hitl.CANONICAL_DANGER_TOOLS` → swarm `_EXTENDED_DANGER`.

### 3.3 Swarm Engine & Async Tasks

| Component | Module |
|-----------|--------|
| Orchestrator | `swarm/engine.py` |
| Patterns | `patterns.py`, `broadcast.py`, `consultation.py` |
| Handoff limits | `handoff_guards.py` (depth 5, visits 2) |
| Reliability | `reliability.py`, `reliability_registry.py` |
| Dispatch | `worker_dispatch.py`, `dispatch_inner.py` |
| Persistence | `task_store.py` (SQLite\|Postgres) |
| Lifecycle | `task_lifecycle.py`, `task_control.py` |
| HITL pipeline | `checkpoint.py`, `checkpoint_manager.py` |
| Bus | `bus.py` + platform adapters |
| Autoscaler | `autoscaler.py` |
| Memory layers | `swarm/memory/*` |

### 3.4 Event Bus & Background Services

| Service | Module | Cycle |
|---------|--------|-------|
| Swarm message bus | `swarm/bus.py` | Pub approval/report/alerts |
| Cron scheduler | `cron/scheduler.py` | Poll interval, max concurrent, stale RUNNING recovery, shutdown-aware |
| SSE telemetry | `telemetry_route.py` | Stream until `is_shutting_down` |
| SSE chat | `sse_chat.py` | Per-turn stream |
| Swarm SSE | `swarm_sse.py` / panel | Task events |
| Gateway queue | `gateway.py` | Adapter inbound → handler |
| Shutdown signal | `shutdown.py` | Global flag for loops |

### 3.5 Memory & Persistence

| Store | Backend | Notes |
|-------|---------|-------|
| ConfigStore | SQLite WAL / Postgres `kazma_settings` | Vault refs for secrets |
| SessionManager | SQLite / `kazma_chat_sessions` | LRU warm cache + lock |
| TaskStore | SQLite / Postgres tables | WAL + json_each workers filter |
| Checkpoints | aiosqlite / AsyncPostgresSaver | Thread isolation |
| FTS5 | SQLite + lock | Keyword memory |
| VectorMemory | Chroma PersistentClient | Optional `[rag]` |
| WorkspaceStore | SQLite | Repo identity columns |

### 3.6 Web Gateway, IDE & UI

| Surface | Tech |
|---------|------|
| App factory | FastAPI + lifespan shutdown drain |
| Auth | Secret / opaque session / API token / OIDC |
| Chat | SSE primary (`/api/chat/stream`), WS legacy |
| IDE | `/ide` page + `/api/ide/*` + CodeMirror-style `ide.js` |
| Swarm panel | `/swarm` + `/api/swarm/*` |
| Settings | Alpine + masked secrets + SaaS user admin |
| Health | `/health`, `/health/live`, `/health/ready` |

---

## Section 4: API, Route & Tool Matrices

### 4.1 API & Route Matrix (primary surfaces)

Auth scope: **Open** = always open; **Secret** = KAZMA_SECRET / session / token when secret set; **Admin** = platform role admin (multi-user).

| Method | Endpoint Path | Auth Scope | HITL / Danger | Description & Module |
| :--- | :--- | :--- | :--- | :--- |
| GET | `/health` | Open | — | Basic health (`routes_direct`) |
| GET | `/health/live` | Open | — | LB liveness (`health.py`) |
| GET | `/health/ready` | Open | — | Readiness + DB ping (`health.py`) |
| GET | `/health/details` | Open* | — | Debug details (`health.py`) |
| GET | `/api/status` | Open | — | App status |
| GET | `/api/telemetry` | Open | — | Light telemetry |
| GET | `/login` | Open | — | Multi-mode login page |
| GET/POST | `/api/auth/*` | Open (login/status/oidc) | — | Auth bootstrap (`routes_direct`) |
| GET | `/api/auth/me` | Secret | — | Principal |
| POST | `/api/chat/stream` | Secret | Graph HITL | SSE agent chat (`sse_chat`) |
| WS | `/ws/chat` (if mounted) | Secret | Graph HITL | WS chat (`chat.py`) |
| POST | `/api/approve/{thread_id}` | Secret | Resume interrupt | HITL approve/deny/yolo scope |
| GET/POST | `/api/ide/*` | Secret | Bus HITL on mutate | IDE backend (`ide_api`) |
| GET/POST | `/api/swarm/*` | Secret | Pipeline HITL | Swarm control panel |
| GET | `/api/swarm/tasks/{id}/stream` | Secret | — | Task SSE |
| CRUD | `/api/settings/*` | Secret/Admin | — | Settings (`settings.py`) |
| CRUD | `/api/saas/*` | Admin | — | Users/tenants (`saas_api`) |
| GET/POST | `/api/mcp/*` | Secret/Admin | MCP force_danger | MCP server mgmt |
| GET/POST | `/api/skills/*` | Secret | — | Skills UI |
| GET/POST | `/api/agents/*` | Secret | — | Agents status/traces |
| GET/POST | `/api/models/*`, `/api/providers/*` | Secret | SSRF on discovery | Models/providers |
| GET/POST | `/api/workspace*`, `/api/workspaces*` | Secret | Path confine | Workspaces |
| GET/POST | `/api/github/*` | Mixed (OAuth open callback) | — | GitHub OAuth/API |
| GET/POST | `/api/git/*` | Secret | shell HITL | Git ops |
| POST | `/api/voice/*` | Secret | — | STT/TTS |
| GET | `/api/gateway/*` | Secret | — | Gateway monitor |
| GET | `/metrics` | Secret | — | Prometheus |
| POST | `/api/webhooks/telegram` | Webhook secret | Agent tools | Telegram webhook |
| GET | `/`, `/chat`, `/ide`, `/swarm`, … | Pages: shells open; data via API | — | SPA-like pages |
| GET | `/settings`, `/dashboard` | Secret (HTML gated) | — | Admin pages |

\*Prefer not exposing details publicly in production reverse proxies.

### 4.2 Tools & CLI Matrix

| Tool / Command Name | Type | Default Danger | Sandbox | Module |
| :--- | :--- | :--- | :--- | :--- |
| `file_read` / `file_list` / `file_search` | Local | safe | Workspace scope | `tool_registry` |
| `file_write` / `file_delete` | Local | **danger** | Workspace + HITL | `tool_registry` |
| `shell_exec` | Local | **danger** | Allowlist + env scrub + HITL | `tool_registry` |
| `python_exec` / `code_exec` | Local | **danger** | Docker jail / blocklist + HITL | `code_exec` |
| `memory_search` / `memory_store` | Local | safe / write | Vector/FTS | `tool_registry` |
| `config_read` / `config_save` | Local | secrets masked / blocked sensitive | ConfigStore | `tool_registry` |
| `spawn_agent` / `spawn_agents` | Local | **danger** (swarm extended) | SubAgentManager | `tool_registry` / `sub_agent` |
| `current_datetime` / `context_info` | Local | safe | — | `tool_registry` |
| `read_url` / `web_search` | Local tools pkg | SSRF guarded | `validate_url` | `tools/read_url` etc. |
| MCP tools (dynamic) | MCP | danger/unknown force HITL; prod non-allowlist HITL | MCP server process | `mcp/manager` |
| `kazma serve` | CLI | — | Auth required non-loopback | `kazma_cli/main` |
| `kazma gateway *` | CLI | — | HTTP to UI | `gateway.py` |
| `kazma swarm *` | CLI | dispatch may HITL | HTTP API | `swarm.py` |
| `kazma update` / `project` / `docs` | CLI | — | local | CLI modules |
| `/ide *` slash | Gateway cmd | danger via tools | IdeService | `commands.py` |
| `/yolo` | Chat/SSE | bypass HITL if allowed | yolo.py | `sse_chat` / gateway |
| `/hitl approve\|deny` | Gateway | resume | hitl.py | agent_handler |

---

## Section 5: Configuration & Environment Master Reference

| Variable Name | Default | Required in Prod | Purpose & Security Scope |
| :--- | :--- | :--- | :--- |
| `KAZMA_SECRET` | generated (loopback) | **Yes** (non-loopback) | Shared admin secret / API auth |
| `KAZMA_HOST` | `127.0.0.1` (CLI/serve) | Set explicitly | Bind address |
| `KAZMA_PORT` | 9090 CLI / 8000 Docker | No | Listen port |
| `KAZMA_TRUST_LAN` | `0` | Keep `0` | LAN auto-cookie |
| `KAZMA_PRODUCTION` | unset | **Yes** (`1`) | Force Docker code_exec, YOLO off, vault required, workspace root |
| `KAZMA_VAULT_KEY` | auto-dev only | **Yes** | Encrypt secrets at rest |
| `KAZMA_ALLOW_YOLO` | unset | No (off) | Opt-in YOLO under production |
| `KAZMA_YOLO_TTL_SECONDS` | 4h | No | YOLO expiry |
| `KAZMA_CODE_EXEC_DOCKER` | auto | `force` | code_exec jail policy |
| `KAZMA_CODE_EXEC_IMAGE` | python:3.12-slim | No | Jail image |
| `KAZMA_WORKSPACE` | data/workspace | No | Default workspace pin |
| `KAZMA_WORKSPACE_ROOT` | unset | **Yes** if prod | Confine workspace paths |
| `KAZMA_CLONE_DIR` | ~/kazma-repos | No | Clone root |
| `KAZMA_DATABASE_URL` | unset | Multi-replica | Postgres shared state |
| `KAZMA_DB_BACKEND` | auto | Optional force | `postgres` / `sqlite` |
| `KAZMA_PG_POOL_MIN/MAX` | 1 / 10 | No | Pool sizing |
| `KAZMA_PUBLIC_URL` | unset | OAuth/OIDC | Fixed public base URL |
| `KAZMA_JWT_SECRET` | unset | Multi-tenant JWT | Verified tenant claims |
| `KAZMA_CORS_ORIGINS` | localhost list | Prod: your origin | CORS allowlist |
| `KAZMA_OPAQUE_SESSIONS` | `1` | Keep on | Opaque browser sessions |
| `KAZMA_SESSION_TTL_SECONDS` | 14d | No | Session cookie TTL |
| `KAZMA_TURN_TIMEOUT_SECONDS` | 600 | No | Graph wall timeout |
| `KAZMA_MCP_SAFE_ALLOWLIST` | empty | Optional | MCP tools skip HITL (prod) |
| `KAZMA_ALLOW_PRIVATE_LLM` | unset | No | Private URL discovery opt-in |
| `KAZMA_MULTI_USER` | unset | SaaS | Force multi-user mode |
| `KAZMA_OIDC_*` | unset | SaaS SSO | OIDC issuer/client/secret/redirect/role claim |
| `KAZMA_PROVIDER` / `KAZMA_MODEL` | unset | No | Boot provider/model |
| `KAZMA_API_KEY` / `OPENAI_API_KEY` | unset | Provider | LLM keys (prefer ConfigStore/vault) |
| `TELEGRAM_BOT_TOKEN` | unset | Telegram | Adapter |
| `TELEGRAM_WEBHOOK_SECRET` | generated if empty | Webhook | Inbound authenticity |
| `DISCORD_BOT_TOKEN` / `SLACK_*` | unset | Platform | Adapters |
| `GITHUB_TOKEN` / `GITHUB_OAUTH_*` | unset | GitHub features | PAT / OAuth app |
| `KAZMA_VECTOR_*` | path/collection/model | RAG | Vector memory |
| `KAZMA_EMBED_API_KEY` | unset | Remote embed | Embeddings |
| `KAZMA_BOT_NAME` / `EMAIL` | defaults | No | Git identity |
| `KAZMA_DEMO_MODE` | unset | No | Demo shortcuts |
| `KAZMA_CHAOS_ENABLED` | unset | No | Chaos routes |
| `KAZMA_ENV` | unset | `production` for error redaction | Error detail policy |
| `KAZMA_AUTO_MIGRATE` | `0` | No | Docker entrypoint migrate |
| `KAZMA_SMOKE_BASE` | localhost:9090 | No | Smoke script base URL |
| `SWARM_BOT_TOKEN` / `SWARM_CHAT_ID` | unset | Optional | Swarm notify bot |

---

## Section 6: Production Readiness Verification (Remediation Alignment)

Cross-reference: `docs/audits/REMEDIATION_PLAN_2026-07-21.md` (all WP 0.x–4.x marked complete in code).

### Phase 0 → live targets

| WP | Target files | Status |
|----|--------------|--------|
| 0.1 serve secret | `serve.py` | **Remediated** — no assign of known secret; refuse bad; generate/loopback |
| 0.2 CLI bind | `kazma-cli/kazma_cli/main.py` | **Remediated** — default `127.0.0.1`; non-loopback needs secret |
| 0.3 compose | `docker-compose.yml`, `Dockerfile` | **Remediated** — `/health`, vector path, prod env |

### Phase 1 → live targets

| WP | Target files | Status |
|----|--------------|--------|
| 1.1 shutdown | `kazma_ui/app.py` `_on_shutdown` | **Remediated** |
| 1.2 reject active | `swarm/engine.py` `reject_checkpoint` | **Remediated** |
| 1.3 cancel finalize | `task_control.py`, `engine._finalize_task` | **Remediated** |
| 1.4 breaker probe | `reliability.py`, `worker_dispatch.py` | **Remediated** |
| 1.5 LLM aclose | `llm_provider.py` `reconfigure` | **Remediated** |
| 1.6 NullBus | `swarm/bus.py` | **Remediated** (`False`) |
| 1.7 YOLO prod | `safety/yolo.py`, SSE/routes | **Remediated** (+ `KAZMA_ALLOW_YOLO`) |

### Phase 2 → live targets

| WP | Target files | Status |
|----|--------------|--------|
| 2.1 discovery SSRF | `models/discovery.py` | **Remediated** |
| 2.2 code_exec | `tools/code_exec.py` | **Hardened** |
| 2.3 shell policy | `agent/tool_registry.py` | **Hardened** |
| 2.4 auth default-deny | `auth.py` | **Remediated** |
| 2.5 cron | `cron/scheduler.py` | **Remediated** |
| 2.6 HITL ownership | `agent_handler/hitl.py`, `routes_direct` | **Remediated** |
| 2.7 workspace root | `routers/workspaces.py` | **Remediated** |

### Phase 3–4 + ops polish → live targets

| Area | Targets | Status |
|------|---------|--------|
| Opaque sessions / RBAC / OIDC | `web_sessions.py`, `platform_rbac.py`, `oidc.py`, `saas_api.py` | **Shipped** |
| Postgres cutover | `config_store.py`, `session_manager.py`, `task_store.py`, `checkpoint.py`, `agent_runner.py` | **Shipped** |
| DR / HA / smoke | `scripts/*`, `docker-compose.ha.yml`, `docs/ops/*` | **Shipped** |

### Open residual risks (not unfixed blockers — residual by design)

| Risk | Severity | Notes |
|------|----------|-------|
| Post-HITL shell/code still powerful | High residual | Intended after human approve; YOLO amplifies |
| Untrusted MCP `trust: trusted` | Medium | Operator footgun |
| Empty secret open mode on loopback | Low–Medium | Documented DX |
| Dual docs trees / unwired libraries | Low maintainability | Cleanup plan |
| Multi-primary multi-region DB | N/A | Infra product, not app |

### Verification commands

```powershell
# Security-critical automated sample
& .venv\Scripts\python.exe -m pytest tests/test_auth_middleware.py tests/test_hitl_wiring.py tests/test_mcp_hitl.py tests/test_pg_store_dual_backend.py -q

# Live smoke (server running)
& .venv\Scripts\python.exe scripts\smoke_production.py --base http://127.0.0.1:9090 --secret $env:KAZMA_SECRET
```

---

## Section 7: Feature Inventory Highlights (including recent)

| Feature area | Modules / surfaces |
|--------------|-------------------|
| Command Center / swarm live | `swarm.html`, `swarm.js`, `swarm_panel/*`, `swarm_sse.py` |
| IDE CodeMirror-style editor | `ide.html`, `ide.js`, `ide_api.py`, `ide/service.py` |
| SSE streaming chat | `sse_chat.py`, `streaming.js` |
| WebSocket voice | `routes_voice_ws.py`, `voice.js` |
| Guardian health | `health.py`, cron, circuit breakers, cost_breaker |
| Cultural/Arabic | dialect, tokenizers, i18n ar, tone/pacing, majlis library |
| Multi-agent | SwarmEngine live; `delegation/*` library-only |
| SaaS multi-user | login multi-mode, `/api/saas`, header principal |
| Postgres multi-replica | `db/*`, dual stores, HA compose |

---

## Related documents

| Doc | Role |
|-----|------|
| `docs/audits/REPO_CLEANUP_PLAN.md` | Hygiene matrix |
| `docs/audits/REMEDIATION_PLAN_2026-07-21.md` | WP checklist (complete) |
| `docs/audits/AUDIT_PRODUCTION_READINESS_2026-07-21.md` | Security audit + remediation footer |
| `docs/audits/UNWIRED_INVENTORY.md` | Library-only packages |
| `docs/ops/SAAS_AND_POSTGRES.md` | Cutover guide |
| `docs/ops/DISASTER_RECOVERY.md` | DR runbook |
| `docs/ops/MULTI_REGION.md` | Multi-replica ops |
| `docs/ops/OIDC_IDP_SETUP.md` | IdP setup |
| `AGENTS.md` | Agent coding critical rules |

---

*Generated 2026-07-21 as monorepo SoT architecture map. Update when dual systems are archived or new packages are added.*
