---
id: configuration
title: Configuration
sidebar_label: Configuration
description: Kazma Configuration — code-audited reference (unified docs, v0.6.1+)
---
> **The exhaustive reference.** Every key in `kazma.yaml`, every environment variable, the ConfigStore override model, the provider/model registry, and the security config files — all traceable to source.

---

## 1. Configuration sources & precedence

Kazma resolves configuration from three layers. For the generic `ConfigStore.get(key)`, the order is:

```mermaid
flowchart LR
    C[In-process cache] -->|miss| DB[(SQLite settings.db)]
    DB -->|miss / child-merge| YAML[(kazma.yaml)]
    YAML -->|fallback| DEFAULT[hardcoded default]
```

| # | Layer | Wins? | Notes |
|---|---|---|---|
| 1 | **Env var** | Only in specific helpers (`get_kazma_secret`, `get_or_create_disclosure_key`) — **not** in the generic `get()`. | e.g. `KAZMA_SECRET` |
| 2 | **ConfigStore DB** (`kazma-data/settings.db`) | **Yes** for runtime reads via `get()`. | DB overrides YAML. |
| 3 | **`kazma.yaml`** | Baseline on first boot. | `reconcile_from_yaml()` seeds DB only for keys not already present. |
| 4 | **Hardcoded default** | Last resort. | e.g. `gpt-4o-mini`, `DEFAULT_DANGER_TOOLS`. |

### Override precedence (detailed) {#override-precedence}

- `ConfigStore.get(key)` (`config_store.py:471-516`): checks the in-process `_cache` first (with a `_MISSING` sentinel for known-absent keys), then an exact DB row, then a DB **child-key re-merge** via `_collect_prefixed` (rows whose key starts with `key.` are de-dotted into a nested dict), then a YAML dotted-key lookup.
- `ConfigStore.set(key, value)` writes one row and **clears the cache** for that key (`config_store.py:518-536`).
- `ConfigStore.batch_set(items)` is the **atomic** multi-key write — single `BEGIN`/`COMMIT`, rollback on any failure (`config_store.py:538-568`). Always prefer it for multi-key updates.
- `ConfigStore.transaction()` is a `@contextmanager` yielding the raw connection for caller-driven multi-op transactions (`config_store.py:572`).
- `reconcile_from_yaml()` seeds DB with `kazma.yaml` leaf values for keys **not already in DB** — it never overwrites existing DB keys (`config_store.py:678-685`). This is the startup step that makes ConfigStore authoritative.
- `export_yaml()` / `import_yaml()` round-trip DB overrides merged into YAML (`config_store.py:632, 650`).
- `reset_all()` deletes all DB rows → reverts to YAML defaults (`config_store.py:732`).

> **Singleton rule:** Always use `get_config_store()` (`config_store.py:760`), never `ConfigStore()` directly. On SQLite init failure it falls back to a thread-safe `_InMemoryStore` with TTL eviction (`config_store.py:777`) — settings then won't survive a restart.

---

## 2. `kazma.yaml` — complete reference

The full default file (`kazma.yaml`) with every key, type, and default. Line numbers reference the shipped file.

### `agent` (lines 1-5)

| Key | Type | Default | Description |
|---|---|---|---|
| `agent.name` | string | `kazma` | Bot display name. |
| `agent.version` | string | `0.2.0` | **Note:** diverges from `pyproject.toml` (`0.3.0`). Not auto-synced. |
| `agent.language` | string | `ar` | UI/agent language. `ar` → RTL + Arabic; `en` → English. |
| `agent.rtl` | bool | `true` | Master RTL switch. |

#### `agent.topic_drift` — embedding topic-shift detection

Read live on every turn check (no restart needed to tune). **Fail-open**: if the
embedder is unavailable or `encode` errors, embedding drift never forces a shift —
regex/explicit and heuristic classifiers still apply.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `agent.topic_drift.enabled` | bool | `true` | Embedding topic-drift detection on/off. Does not affect regex/heuristic shift classifiers. |
| `agent.topic_drift.threshold` | float | `0.55` | Cosine **distance** (1 − similarity) at which a turn is flagged as a topic shift. Clamped `[0.05, 0.95]`. Higher = only more dissimilar turns count as a shift. |

**Tuning direction:**
- **False shift** (agent abandons a legit multi-step task mid-flow) → **raise** the threshold.
- **Missed shift** (agent resumes the old task after a real pivot) → **lower** the threshold.

Both keys are read via `topic_drift_config()` from ConfigStore and overlay `kazma.yaml`.

#### `agent.nonstop` — Non-Stop & Self-Healing Execution Engine

Configurable via Settings UI (**Settings → Agent → Non-Stop & Self-Healing**), ConfigStore (`agent.nonstop.*`), or `kazma.yaml`. Read live on every turn execution (`get_nonstop_config()`).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `agent.nonstop.enabled` | bool | `false` | Master switch for non-stop execution & watchdog. |
| `agent.nonstop.watchdog.stall_threshold_seconds` | int | `60` | Watchdog stall detection threshold in seconds. |
| `agent.nonstop.tool_timeout_seconds` | int | `120` | Per-tool execution timeout (`asyncio.wait_for`). |
| `agent.nonstop.healing.max_recovery_attempts` | int | `3` | Max recovery & resume attempts before escalating. |
| `agent.nonstop.healing.backoff_base_seconds` | float | `2.0` | Exponential backoff base for watchdog recovery. |
| `agent.nonstop.healing.backoff_max_seconds` | float | `30.0` | Max backoff wait between recovery attempts. |
| `agent.nonstop.failover.enabled` | bool | `false` | Enable model failover chain on primary LLM failure. |
| `agent.nonstop.failover.chain` | list/str | `[]` | Ordered list or comma-separated string of failover model IDs. |
| `agent.nonstop.failover.cooldown_seconds` | int | `300` | Cooldown period in seconds before retrying a failed model in chain. |
| `agent.nonstop.ledger.enabled` | bool | `true` | Enable durable per-call LLM execution logging (`kazma-data/llm_calls.db`). |

### `models` (lines 6-9)

| Key | Type | Default | Description |
|---|---|---|---|
| `models.default` | string | `gpt-4o-mini` | Default model id. |
| `models.router` | string | `litellm` | **String only.** Gates the fallback-model branch in `llm_provider.py:336`. Kazma does **not** `import litellm` — it treats LiteLLM as a compatible proxy endpoint on port 4000. |
| `models.fallback` | string | `gpt-4o-mini` | Model used on retry if `router == "litellm"` and a request fails (`llm_provider.py:335-347`). |

### `llm` (lines 10-18)

| Key | Type | Default | Description |
|---|---|---|---|
| `llm.base_url` | string | `https://api.openai.com/v1` | OpenAI-compatible endpoint. `/v1` is auto-appended if missing (except Ollama :11434 and LiteLLM :4000). |
| `llm.api_key` | string | `''` | Leave empty to load from env (`OPENAI_API_KEY` → `KAZMA_API_KEY` → `"not-needed"` for local). |
| `llm.model` | string | `gpt-4o-mini` | Model id sent in the payload. |
| `llm.max_tokens` | int | `4096` | Completion token cap. |
| `llm.temperature` | float | `0.7` | Sampling temperature. |
| `llm.timeout` | float | `60.0` | Per-request timeout (seconds). |
| `llm.input_cost_per_1m` | float | `0.15` | USD per 1M input tokens — used for cost accounting. |
| `llm.output_cost_per_1m` | float | `0.6` | USD per 1M output tokens. |

### `mcp` (lines 19-32)

| Key | Type | Default | Description |
|---|---|---|---|
| `mcp.servers` | list | see below | MCP server definitions. |
| `mcp.servers[].name` | string | — | Server identifier. |
| `mcp.servers[].transport` | string | `stdio` | `stdio`, `sse`, or `streamable_http` (alias `http`). SSE and streamable_http support an `auth` field (bearer/custom header); streamable_http also tracks `Mcp-Session-Id` for resumable sessions (MCP 2025-03-26 spec). |
| `mcp.servers[].trust` | string | `trusted` | **Plain config string — not consumed by any trust-tier code.** |
| `mcp.servers[].command` | list | — | argv for stdio spawn. |
| `mcp.ide_server.enabled` | bool | `true` | Enable the in-process IDE/file MCP server. |
| `mcp.ide_server.root` | string | `.` | Workspace root. |
| `mcp.ide_server.max_file_size` | int | `1048576` | 1 MB file size cap. |

Shipped default server:

```yaml
mcp:
  servers:
    - name: filesystem
      transport: stdio
      trust: trusted
      command: [npx, -y, '@modelcontextprotocol/server-filesystem', 'kazma-data/workspace']
  ide_server:
    enabled: true
    root: .
    max_file_size: 1048576
```

### `system_prompt` (lines 33-45)

Multi-line string. The default is Arabic-aware: "You are Kazma (كاظمه), an autonomous AI agent framework…" and instructs the model to respond in the user's language/dialect.

### `storage` (lines 46-49)

| Key | Type | Default | Description |
|---|---|---|---|
| `storage.engine` | string | `sqlite` | Checkpointer engine. |
| `storage.path` | string | `kazma-data/checkpoints.db` | LangGraph checkpointer DB. |
| `storage.vector_dim` | int | `1024` | Declared vector dimension (informational — should match `memory.embedding.dim`; default BGE-M3 is **1024**). |

### `memory` (lines 50-54)

| Key | Type | Default | Description |
|---|---|---|---|
| `memory.enabled` | bool | `true` | Master switch (per-turn RAG, auto-store, consolidator). ConfigStore overlays yaml. |
| `memory.per_turn_retrieval` | bool | `true` | Inject top-k memories on every user turn. |
| `memory.auto_store` | bool | `true` | Heuristic durable / turn writes after each reply. |
| `memory.auto_store_mode` | str | `both` | `durable` \| `turns` \| `both`. |
| `memory.max_context_tokens` | int | `128000` | Context window for compaction (fires at 80%). |
| `memory.retrieval_top_k` | int | `5` | Top-K for per-turn RAG and compaction. |
| `memory.provenance` | bool | `true` | Tag memories with source metadata. |
| `memory.consolidation.enabled` | bool | `true` | Post-turn librarian (facts + graph triples). |
| `memory.consolidation.use_llm` | bool | `true` | LLM extract; heuristic fallback if fail/off. |
| `memory.consolidation.every_n_turns` | int | `1` | Cost control: run consolidator every N turns. |
| `memory.consolidation.skip_llm_in_demo` | bool | `true` | No LLM under `KAZMA_DEMO_MODE`. |
| `memory.embedding.provider` | str | `local` | `local` or remote OpenAI-compatible embed API. |
| `memory.embedding.model` | str | `BAAI/bge-m3` | Embedding model id (multilingual, 1024-dim). |
| `memory.embedding.dim` | int | `1024` | Must match the embedder. |
| `memory.embedding.base_url` | str | unset | `/embeddings` endpoint for remote providers. |
| `memory.embedding.api_key_env` | str | `KAZMA_EMBED_API_KEY` | Env var holding the remote API key. |

The embedder is also configurable from the Web UI: **Settings → Embedder**
(save there takes effect after a server restart, and includes a one-click
background "Rebuild embeddings" action + the vector-space composition of
your memory DB). The ConfigStore override (`embedding.*`) takes precedence
over `kazma.yaml`; env vars (`KAZMA_EMBED_*`) win over both. After a model
switch, run the rebuild so every row lives in the same vector space.

### `skills` (lines 55-57)

| Key | Type | Default | Description |
|---|---|---|---|
| `skills.path` | string | `kazma-skills/manifests/` | Skill manifest directory. |
| `skills.auto_discover` | bool | `true` | Auto-load manifests on startup. |

### `connectors` (lines 58-68)

| Key | Type | Default | Token env var |
|---|---|---|---|
| `connectors.telegram.enabled` | bool | `true` | `TELEGRAM_BOT_TOKEN` |
| `connectors.discord.enabled` | bool | `false` | `DISCORD_BOT_TOKEN` |
| `connectors.slack.enabled` | bool | `false` | `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` |

### `gateway` (lines 70-79)

| Key | Type | Default | Description |
|---|---|---|---|
| `gateway.rate_limits.telegram` | int | `30` | Requests per window. |
| `gateway.rate_limits.discord` | int | `5` | Requests per window. |
| `gateway.rate_limits.slack` | int | `1` | Requests per window. |
| `gateway.suggestions.enabled` | bool | `true` | Suggested-followup UI. |
| `gateway.voice.enabled` | bool | `false` | Voice (STT inbound + TTS outbound) across **all platforms** (Telegram, Discord, Slack) + Web. Also settable at runtime via the Settings UI. |
| `gateway.voice.stt_provider` | string | `openai` | Speech-to-text provider: `openai`, `groq`, `cohere`, `nvidia`, or `faster-whisper` (local). |
| `gateway.voice.tts_provider` | string | `edgetts` | Text-to-speech provider: `edgetts` (free, default), `openai`, `nvidia`, `kokoro` (local), `coqui` (local). |

### `safety.hitl` (lines 81-96)

| Key | Type | Default | Description |
|---|---|---|---|
| `safety.hitl.enabled` | bool | `true` | Master HITL switch (graph path). |
| `safety.hitl.require_approval_for` | list | see below | Danger tools for the **graph** path. |
| `safety.hitl.approval_timeout_seconds` | int | `60` | Pipeline-checkpoint auto-reject timeout. |
| `safety.hitl.auto_deny_on_timeout` | bool | `true` | Auto-reject paused tasks on timeout. |

Default `require_approval_for`:

```yaml
safety:
  hitl:
    enabled: true
    require_approval_for:
      - file_write
      - file_delete
      - shell_exec
      - code_exec
      - python_exec
      - spawn_agent
      - spawn_agents
      - schedule_task
      - cancel_scheduled
    approval_timeout_seconds: 60
    auto_deny_on_timeout: true
```

> The **swarm bus** uses a separate, broader list (`_EXTENDED_DANGER` adds `python_exec`, `code_exec`, `spawn_agent`, `spawn_agents`, `schedule_task`, `cancel_scheduled`, `run_tests`). The **MCP** path classifies dynamically by name pattern. See [Security & Safety → danger-tool lists](security-and-safety#danger-tool-lists-three-of-them).

### `notifications.lifecycle` (lines 143-157)

Server lifecycle status notifications — pushes a status update to every configured platform when the server starts, restarts, shuts down, or fails to boot. See [Deployment → Lifecycle notifications](deployment#10-lifecycle-status-notifications).

| Key | Type | Default | Description |
|---|---|---|---|
| `notifications.lifecycle.enabled` | bool | `true` | Master switch. |
| `notifications.lifecycle.events` | list | `[starting, started, shutting_down, startup_failed]` | Which events trigger a notification. Remove entries to silence specific events. |
| `notifications.lifecycle.restart_window_seconds` | int | `60` | If a shutdown→start happens within this window, reports "🔄 Restarted" instead of "🟢 Started". `0` disables restart detection. |

Default:

```yaml
notifications:
  lifecycle:
    enabled: true
    events:
      - starting
      - started
      - shutting_down
      - startup_failed
    restart_window_seconds: 60
```

Notifications route through the SwarmMessageBus (no parallel path). Set `connectors.<platform>.swarm_chat_id` to the chat ID where messages should land. Without it, the bus is `NullBusAdapter` and messages are dropped silently.

### `ui` (lines 98-102)

| Key | Type | Default | Description |
|---|---|---|---|
| `ui.host` | string | `127.0.0.1` | Bind host. Switches to `0.0.0.0` under `kazma serve` only if `KAZMA_SECRET` is set. |
| `ui.port` | int | `8000` | Bind port. |
| `ui.rtl` | bool | `true` | UI RTL. |
| `ui.title` | string | `Kazma Dashboard` | Page title. |

### `logging` (lines 103-109)

| Key | Type | Default | Description |
|---|---|---|---|
| `logging.level` | string | `INFO` | Log level. |
| `logging.format` | string | `json` | `json` or plain. |
| `logging.langfuse.enabled` | bool | `false` | Langfuse tracing. **Roadmap** — dependency present, integration not active. |
| `logging.langfuse.public_key` | string | `''` | |
| `logging.langfuse.secret_key` | string | `''` | |

### `time_travel` (lines 111-114)

| Key | Type | Default | Description |
|---|---|---|---|
| `time_travel.enabled` | bool | `true` | Enable `/replay`. |
| `time_travel.max_snapshots` | int | `50` | Snapshot cap (per thread). ConfigStore override `time_travel.max_snapshots` (Settings → Embedder → Time travel) takes precedence over this value; effective resolution is store > yaml > default. Applies at server startup. |
| `time_travel.retention_days` | int | `30` | Prune snapshots older than this many days (1–3650). ConfigStore override `time_travel.retention_days` (Settings → Embedder → Time travel) is read LIVE by the daily maintenance loop — no restart needed. |
| `time_travel.auto_maintain` | bool | `true` | Enable the daily snapshot prune + VACUUM loop. ConfigStore override `time_travel.auto_maintain` is read live, same as `retention_days`. |
| `time_travel.db_path` | string | `kazma-data/snapshots.db` | Snapshot DB. |

### `swarm` (lines 116-127)

| Key | Type | Default | Description |
|---|---|---|---|
| `swarm.enabled` | bool | `true` | Master swarm switch. |
| `swarm.group_chat_id` | int | `0` | Real value read from `SWARM_CHAT_ID` env. |
| `swarm.default_pattern` | str | `dispatch` | Fallback pattern: `dispatch` \| `pipeline` \| `consult` \| `fan_out` \| `broadcast`. |
| `swarm.auto_route` | bool | `true` | Enable semantic auto-routing (`UnifiedRouter`) for `workers=["auto"]`. |
| `swarm.max_concurrent_tasks` | int | `10` (1–100) | Max concurrent swarm tasks. |
| `swarm.max_concurrent` | int | `5` | Fan-out / broadcast / consult worker concurrency. |
| `swarm.orchestrator.name` | string | `Kazma Orchestrator` | Orchestrator display name. |
| `swarm.orchestrator.profile` | string | `default` | Orchestrator profile id. |
| `swarm.workers` | list | `[]` | Populated at runtime via Web UI / `POST /api/swarm/workers`. |
| `swarm.output_target` | obj | none | `\{bot_token, chat_id, platform, enabled\}` — when set, the token must match the active Telegram bot token. |

### `pipelines` (lines 129-162)

Two predefined pipelines (lists of stages, each with `worker`, `depends_on`, `system_prompt`):

- **`standard`** — 4 stages: `researcher` (worker `core`) → `refiner` (worker `bridge`) → `builder` (worker `core`) → `validator` (worker `bridge`).
- **`quick`** — 2 stages: `researcher` (worker `core`) → `builder` (worker `core`).

---

## 3. Environment variables

### 3.1 Documented in `.env.example`

| Variable | Purpose | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram adapter token. | placeholder |
| `DISCORD_BOT_TOKEN` | Discord adapter token. | empty |
| `SLACK_BOT_TOKEN` | Slack bot token. | empty |
| `SLACK_APP_TOKEN` | Slack app token (Socket Mode). | empty |
| `OPENAI_API_KEY` | OpenAI key (also generic LLM fallback #2). | empty |
| `DEEPSEEK_API_KEY` | **Declared in `.env.example` but not read by code** — set the key via the provider list instead. | empty |
| `ANTHROPIC_API_KEY` | **Declared in `.env.example` but not read by code.** | empty |
| `GOOGLE_CLOUD_PROJECT` | GCP project for Vertex AI (if ADC lacks a default). | commented out |
| `SWARM_BOT_TOKEN` | Swarm output bot token. | empty |
| `SWARM_CHAT_ID` | Swarm group chat id (feeds `swarm.group_chat_id`). | empty |
| `KAZMA_SECRET` | HITL shared secret; binds `serve` to `0.0.0.0`; hub write-auth; `kazma hub sign`. | commented out |
| `KAZMA_VECTOR_PATH` | Vector memory dir. | `~/.kazma/vector_memory` |
| `KAZMA_VECTOR_COLLECTION` | ChromaDB collection name. | `agent_memory` |
| `KAZMA_VECTOR_MODEL` | Embedding model (legacy alias — prefer `KAZMA_EMBED_MODEL`). | `BAAI/bge-m3` |

### 3.2 Read in code, not in `.env.example`

| Variable | Purpose | Location |
|---|---|---|
| `KAZMA_AUTH_DISABLED` | If `true`/`1`/`yes`, `get_kazma_secret()` returns `""` (auth disabled). | `config_store.py:52` |
| `KAZMA_DISCLOSURE_KEY` | Disclosure HMAC key; auto-generated if unset. | `config_store.py:95` |
| `KAZMA_API_KEY` | LLM key fallback #3. | `llm_provider.py:142` |
| `KAZMA_MAX_COST` | Cost breaker ceiling (default `$0.50`). | `cost_breaker.py:42` |
| `KAZMA_HARD_MAX_COST` | Hard max cost ceiling for immediate trip (default 3x soft max, `$15.0`). | `cost_breaker.py:46` |
| `KAZMA_SILENCE_WINDOW` | Cost breaker silence window (default `300`s). | `cost_breaker.py:44` |
| `KAZMA_SEMANTIC_CACHE` | Enable response cache (`"true"`, default off). | `llm_provider.py:212` |
| `KAZMA_FETCH_MAX_BYTES` | Streamed response byte limit for `read_url` (default `5242880` / 5 MB). | `tools/read_url.py` |
| `KAZMA_CRAWL_RESPECT_ROBOTS` | Opt-in `robots.txt` compliance switch for `crawl_site` (`1` or `true`). | `tools/web_research.py` |
| `KAZMA_OTLP_ENDPOINT` | OTLP HTTP JSON trace collector endpoint. | `swarm/tracing.py` |
| `KAZMA_TOOL_TIMEOUT_SECONDS` | Per-tool execution timeout in seconds (default `120`). | `agent/graph_builder.py` |
| `KAZMA_HUB_DB` | Hub SQLite registry path. | `hub/cli.py:109` |
| `KAZMA_HUB_URL` | Hub API base (default `https://hub.kazma.ai`). | `hub/cli.py:115` |
| `KAZMA_PORT` | Server port override (default `8000`). | `gateway.py:36` |
| `HF_HUB_DISABLE_SYMLINKS_WARNING` / `HF_HUB_DISABLE_TELEMETRY` | Silence HuggingFace telemetry (set by CLI). | `main.py:9-10` |

> **No dedicated per-provider env vars** for DeepSeek/Anthropic/xAI/Groq/Gemini are read by `kazma_core`. Key those providers through the ConfigStore provider list or `kazma.yaml`.

---

## 4. API keys {#api-keys}

`LLMProvider._resolve_api_key()` (`llm_provider.py:136-146`) resolves in this order:

1. `self.config.api_key` (from `LLMConfig`)
2. `os.getenv("OPENAI_API_KEY")`
3. `os.getenv("KAZMA_API_KEY")`
4. `"not-needed"` (for local LM Studio/Ollama)

Provider-specific **dummy keys** for local servers (`url_utils.py:138-172`):

| Server | Dummy key |
|---|---|
| LM Studio (:1234) | `sk-lm-studio-dummy-key` |
| Ollama (:11434) | `ollama` |
| LiteLLM proxy (:4000) | `sk-litellm-dummy-key` |
| other localhost | `not-needed` |

**Google Vertex AI** uses Application Default Credentials only — no API key. `GeminiProvider._resolve_api_key()` returns `"adc-placeholder"` and the real bearer token is fetched per-call via `google.auth.default()` + `credentials.refresh()` (`google_llm.py:232-252`). Project resolution: explicit `project_id=` > `GOOGLE_CLOUD_PROJECT` > `google.auth.default()` > ADC `quota_project_id` > gcloud `config_default`.

Keys are stored per-provider in ConfigStore `providers.list` (each entry has `api_key`, `base_url`, `models`, …). Masked placeholders (`***`) are rejected on upsert unless a real key already exists (`model_registry.py:646-652`); keys are masked in all read-backs (`_mask_profile`).

---

## 5. The Model Registry

`ModelRegistry` (`model_registry.py:81`) is a process-wide singleton (module global `_registry`, thread-safe via `threading.RLock()`). Backward-compat alias: `UnifiedModelRegistry` (line 950).

### 5.1 Lifecycle

| Function | Purpose |
|---|---|
| `initialize_model_registry(config_store)` | Construct + deserialize + seed presets. |
| `get_model_registry()` | Retrieve singleton (raises `RuntimeError` if uninitialized). |
| `reset_model_registry()` | Teardown. |

### 5.2 Built-in provider presets

From `kazma-core/kazma_core/providers.py`. Most presets speak the OpenAI Chat Completions wire format and work through the generic `LLMProvider` (Bearer auth). **Four providers have dedicated native classes** (see [LLM Providers](../reference/llm-providers)) because their auth/schema differs: `google` → `GeminiProvider`, `anthropic` → `AnthropicProvider` (native `/messages` API), `azure` → `AzureProvider` (`api-key` header + `api-version`), `bedrock` → `BedrockProvider` (AWS SigV4 + Converse API).

| Key | Display name | `base_url` | `auth_header` | Native class? |
|---|---|---|---|---|
| `openai` | OpenAI | `https://api.openai.com/v1` | `Bearer` | no |
| `anthropic` | Anthropic | `https://api.anthropic.com/v1` | `x-api-key` | **yes** — `AnthropicProvider` |
| `deepseek` | DeepSeek | `https://api.deepseek.com/v1` | `Bearer` | no |
| `google` | Google Gemini | *(computed per project/location)* | `Bearer` | **yes** — `GeminiProvider` |
| `xai` | xAI / Grok | `https://api.x.ai/v1` | `Bearer` | no |
| `openrouter` | OpenRouter | `https://openrouter.ai/api/v1` | `Bearer` | no |
| `groq` | Groq | `https://api.groq.com/openai/v1` | `Bearer` | no |
| `mistral` | Mistral AI | `https://api.mistral.ai/v1` | `Bearer` | no |
| `together` | Together AI | `https://api.together.xyz/v1` | `Bearer` | no |
| `cohere` | Cohere | `https://api.cohere.ai/v1` | `Bearer` | no |
| `fireworks` | Fireworks AI | `https://api.fireworks.ai/inference/v1` | `Bearer` | no |
| `perplexity` | Perplexity | `https://api.perplexity.ai` | `Bearer` | no |
| `ai21` | AI21 Labs | `https://api.ai21.com/studio/v1` | `Bearer` | no |
| `nvidia` | NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | `Bearer` | no |
| `azure` | Azure OpenAI | *(computed from resource + deployment)* | `api-key` | **yes** — `AzureProvider` |
| `bedrock` | AWS Bedrock | *(computed from region)* | `Bearer` (SigV4) | **yes** — `BedrockProvider` |
| `ollama` | Ollama (Local) | `http://127.0.0.1:11434/v1` | *(none)* | no |
| `lm-studio` | LM Studio (Local) | `http://localhost:1234/v1` | *(none)* | no |
| `custom` | Custom Endpoint | *(blank)* | `Bearer` | no |

Hardcoded `GEMINI_MODELS` (Vertex AI has no static `/models` endpoint): `gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-2.0-flash`, `gemini-2.0-flash-lite`.

> **Default-enabled provider:** Only `google` is `enabled=True` out of the box (`model_registry_store.py:117`). All others must be configured before use. `custom` is excluded from preset seeding.

### 5.3 Model discovery

`discover_models(provider_name)` (`model_registry.py:427`) hits `\{base_url\}\{models_endpoint\}` (default `/models`), parses the OpenAI `\{"data":[\{"id":...\}]\}` shape, with an **SSRF guard** (`kazma_core.security.ssrf.validate_url`). Results cached in `_discovered_models`.

### 5.4 ConfigStore keys (registry)

| Key | Purpose |
|---|---|
| `providers.list` | Stored provider array. |
| `providers.health.*` | Per-provider health. |
| `models.saved.*` | Saved model profiles. |
| `models.defaults.*` | Per-task defaults (`chat`, `code`, `summarize`, `translate`). |
| `llm.model`, `llm.base_url`, `llm.api_key` | Legacy fallbacks. |
| `registry.active_provider`, `registry.active_model`, `registry.discovered_models` | Active selection + cache. |

---

## 6. `retry` keys

The `tenacity`-based retry decorators read overrides from ConfigStore (`retry.py:69-86`):

| Key | Default | Description |
|---|---|---|
| `retry.max_attempts` | `3` | Max retry attempts. |
| `retry.min_wait` | `2` (s) | Min backoff. |
| `retry.max_wait` | `10` (s) | Max backoff. |

> Retries fire **only** on network/timeout exceptions (`ConnectionError`, `TimeoutError`, `asyncio.TimeoutError`, httpx `TimeoutException`/`ConnectError`/`RemoteProtocolError`). **4xx errors are never retried** (`retry.py:107-109`). There is no 429 backoff.

---

## 7. Security config files

### 7.1 `kazma-permissions.yaml` — library only (not runtime-enforced)

> **Status:** `PermissionManager` + this YAML describe an **enterprise division
> RBAC design** that is **not enforced** on the live tool execute path. Runtime
> authorization is HITL + shell allowlist + MCP classification + optional
> platform RBAC (`KAZMA_MULTI_USER`). See `docs/audits/UNWIRED_INVENTORY.md`.

Example shape (for future wiring / offline policy docs only):

```yaml
divisions:
  gas_oil:
    allowed_mcp_servers: [oil-pricing-api, contract-manager, supplier-directory]
    denied_mcp_servers:  [tourism-booking-api, general-inventory-api]
  tourism:
    allowed_mcp_servers: [booking-engine, hotel-api, flight-search]
    denied_mcp_servers:  [oil-pricing-api, contract-manager]
  general_trading:
    allowed_mcp_servers: [inventory-api, supplier-directory, procurement-api]
    denied_mcp_servers:  [oil-pricing-api, booking-engine]

cross_division_rules:
  require_explicit_approval: true
  max_approval_duration_hours: 24
  notify_admins: true
  audit_all_access: true
```

### 7.2 `kazma-security.yaml`

| Section | Key options |
|---|---|
| `scanning` | `enabled`, `interval: "24h"`, `sources: [osv, github_advisories, nvd]`, `auto_create_issues`, `severity_threshold: medium`, `ignore`. |
| `disclosure` | `enabled`, `response_window: "48h"`, `assessment_window: "7d"`, `pgp_key_url`, `encrypted_channels`. |
| `bug_bounty` | `enabled`, `min_payout: 50`, `max_payout: 2000`, `currency: USD`, `tiers` (`critical` `[500,2000]`, `high` `[200,500]`, `medium` `[50,200]`, `low` Hall of Fame). |
| `hardening` | `run_on_startup`, `fail_on_critical`, `auto_fix: false`, `checks` (8: `secrets_in_logs`, `input_validation`, `rbac_enforcement`, `tls_required`, `dependency_audit`, `least_privilege`, `audit_trail`, `config_integrity`). |

> These files declare a security **policy posture**. Whether every check is actively enforced at runtime should be verified against the hardening runner before relying on it in production — see [Security & Safety](security-and-safety).

### 7.3 `services.yaml`

```yaml
commands:
  install: "pip install -e kazma-tui/ -e kazma-core/"
  test: "python -m pytest kazma-tui/tests/ -v"
  lint: "python -m ruff check kazma-tui/kazma_tui/"
  typecheck: "python -m mypy kazma-tui/kazma_tui/"
services: {}
```

### 7.4 `proxy.*` keys (scraping proxy provider addon)

Opt-in. Configured via **Settings → System → Proxy Provider** (the values below
live in ConfigStore under `proxy.*`; `proxy.password` auto-vault-encrypts). The
active provider is re-read live on every fetch — no restart needed.

| Key | Default | Purpose |
|-----|---------|---------|
| `proxy.provider` | `none` | `none` (direct) \| `anyip` |
| `proxy.host` | `portal.anyip.io` | Proxy endpoint host |
| `proxy.port` | `1080` | Proxy endpoint port |
| `proxy.username` | _(empty)_ | anyip username (e.g. `user_YOURID`) |
| `proxy.password` | _(empty, vault) | anyip password |
| `proxy.network` | `mixed` | `residential` \| `mobile` \| `mixed` |
| `proxy.country` | _(empty)_ | Optional ISO country code (e.g. `US`) |
| `proxy.session_sticky` | `false` | `true` = same IP across requests (logins); `false` = rotate per request |

See [Web research → Bulletproof scraping](web-research#bulletproof-scraping-proxy-provider-addon-ipua-rotation).

---

## Documentation Audit Notes

- **Version drift:** `pyproject.toml` is `0.3.0`; `kazma.yaml` `agent.version` is `0.2.0`; the CLI `--help` text prints `v0.2.0`. These are independent and unsynchronized — a known wart.
- **Memory flags** are read via `kazma_core.memory.config` (ConfigStore ← yaml). See [Memory & RAG](memory-and-rag) and `docs/plans/MEMORY_REMAINING.md`.
- **`.env.example` lists `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY`** but no code reads them — flagged to prevent user confusion.
- **`mcp.servers[].trust`** is a plain YAML string with no enforcing consumer — not a cryptographic trust tier.
