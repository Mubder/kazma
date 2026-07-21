# Troubleshooting & Workarounds

> Practical fixes for the issues you will actually hit: provider limits, Windows/Docker specifics, SQLite concurrency, Arabic tokenization edge cases, and the known codebase gaps the audit uncovered. Every item is source-referenced.

---

## 1. Provider & LLM issues

### 1.1 "Function not found" / 404 on tool calls (NVIDIA NIM)

**Cause:** Some providers (notably NVIDIA NIM) reject tool definitions.

**What Kazma does:** `llm_provider.py:285-300` detects `status_code == 404 and "function" in detail` (and 400/422 with tool/function language) and **retries once without tools**. The caller still gets a text response.

**Workaround if it persists:** Disable tools for that provider, or route through an OpenAI-compatible proxy that translates tool definitions. **Do not remove the fallback branch** — it's a documented invariant.

### 1.2 Provider/model mismatch (wrong endpoint)

**Symptom:** Requests go to the wrong API endpoint after switching models.

**Cause:** Changing the model without switching the provider.

**Fix:** Always use `set_active_model()` (it auto-switches the provider via `find_provider_for_model()`) or `set_active_provider()` — never set one without the other. `get_client()` auto-corrects (`model_registry.py:275-303`) but only persists the provider change when `model is None`.

**Verify:**
```bash
kazma status          # shows active provider/model
```

### 1.3 Anthropic auth header mismatch

**Cause:** The Anthropic preset declares `auth_header: x-api-key`, but `LLMProvider.chat()` always sends `Authorization: Bearer` (`llm_provider.py:171-172`). The preset header only takes effect during `discover_models()`.

**Fix:** Route Anthropic through an OpenAI-compatible proxy, or extend `LLMProvider` to honor the preset `auth_header` for chat.

### 1.4 No rate-limit (429) handling

Kazma has **no 429 backoff**. The retry layer (`retry.py:107-109`) explicitly does not retry 4xx. If you hit rate limits, either:
- Lower concurrency (`BoundedConcurrency`, default 5), or
- Put a rate-limiting proxy in front of the provider.

### 1.5 Cost breaker not halting runaway spend

**Cause:** `CostCircuitBreaker` (default $0.50, 5-min silence) is a standalone dataclass — **not auto-wired** into `chat()`. The agent layer must call `record_cost` / `record_user_interaction` / `should_halt`.

**Fix:** Wire the breaker in your agent loop, or set `KAZMA_MAX_COST` / `KAZMA_SILENCE_WINDOW` and drive it explicitly.

### 1.6 ModelRegistry "not initialized" (`RuntimeError`)

**Symptom:** `RuntimeError: ModelRegistry not initialized. Call initialize_model_registry() first.` when importing or calling `get_model_registry()`.

**Cause:** `initialize_model_registry(config_store)` must run once before any consumer uses the registry. The singleton (`model_registry.py`) is created lazily.

**Fix:**
```python
from kazma_core.config_store import get_config_store
from kazma_core.model_registry import initialize_model_registry

cs = get_config_store()
initialize_model_registry(cs)
```

In tests, use an autouse fixture that re-initializes per test:
```python
@pytest.fixture(autouse=True)
def _init_model_registry(tmp_path):
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import initialize_model_registry, reset_model_registry
    db_path = str(tmp_path / "test_registry.db")
    cs = ConfigStore(db_path=db_path)   # isolated per-test store
    initialize_model_registry(cs)
    yield
    reset_model_registry()
```
Do not call `reset_model_registry()` mid-run unless you immediately re-initialize.

### 1.7 Failed model discovery / empty model list

**Symptom:** `ModelRegistry.discover_models(provider_name)` returns `[]`, or `GET /api/swarm/models` is empty despite a configured provider.

**Cause:** the provider has no `base_url`, no `api_key` (when auth is required), is not enabled, doesn't expose a `/models`-compatible endpoint, or a prior discovery failure is cached in `_discovered_models`.

**Fix:**
```python
from kazma_core.model_registry import get_model_registry
registry = get_model_registry()
print(registry.get_provider("openai"))   # check base_url / api_key / enabled
print(registry.list_providers())
import asyncio
print(asyncio.run(registry.discover_models("openai")))  # explicit rediscovery
```
Discovery logs the failure reason — check startup logs.

### 1.8 Active profile returns empty / default values

**Symptom:** `get_active_profile()` returns `{"provider": "custom", "base_url": "", "model": "", "api_key": ""}` despite saved settings.

**Cause:** no active provider is set; the registry is falling back to legacy `llm.*` keys, which are also empty.

**Fix:**
```python
registry.set_active_provider(
    provider="deepseek",
    base_url="https://api.deepseek.com/v1",
    api_key="sk-...",
    model="deepseek-chat",
)
# or, within the active provider only:
registry.set_active_model("deepseek-reasoner")
```
Verify persistence via `get_config_store().get("registry.active_provider")` / `...active_model`.

### 1.9 Settings not persisting after a model/provider switch

**Symptom:** you switch model/provider in the UI or CLI, but the next request uses the old value.

**Cause:** a different ConfigStore instance/db_path was used to save; the mutation bypassed the registry (so the cached client wasn't invalidated); or a stale client is still in use.

**Fix:**
1. All code paths must share the singleton — `get_config_store()` (`kazma-data/settings.db`). Never construct a second `ConfigStore()` for the same DB (SQLite lock contention).
2. Confirm stored values: `get_config_store().get_all()` vs `cs.export_yaml()`.
3. Mutate **through the registry** (`set_active_provider` / `set_active_model`), not via a bare `cs.set()` on unrelated keys — the registry owns the active profile and client-cache invalidation.
4. To revert a key to its YAML default: `get_config_store().delete("registry.active_model")`. To force a full reset: delete `kazma-data/settings.db` (loses **all** runtime settings) and restart.

---

## 2. Memory & RAG issues

> **Updated July 2026** — most previously-documented memory bugs have been fixed in the overhaul. Remaining items are design decisions, not bugs.

### 2.1 "My agent doesn't seem to remember anything"

**After the overhaul, memory works in two ways:**

1. **LLM tools** — the model can call `memory_search` / `memory_store` at any time.
2. **Compaction injection** — when the conversation hits 80% of the context window, the `CompactionEngine` now **automatically retrieves the top-5 relevant memories** from ChromaDB and injects them into the fresh system message as `## Relevant Memories`. This was previously dead code; it's now active.

**If memories still aren't surfacing:**
- Verify `pip install -e ".[rag]"` was run (ChromaDB + sentence-transformers).
- Check logs for `[VectorMemory]` warnings (now logged at WARNING level, not debug).
- Instruct the model (via `system_prompt`) to proactively call `memory_store` for important facts — there is no background auto-promotion (by design).

### 2.2 VectorMemory not initialized

**Cause:** the `[rag]` extra is not installed, or ChromaDB failed to initialize (corrupt DB, disk permission, version incompatibility).

**What happens:** `get_vector_memory()` returns `None`. The tools return `"Error: VectorMemory not initialized."` Compaction's memory retrieval gracefully returns `[]`.

**Fix:** `pip install -e ".[rag]"`. If it was installed but failed, check the WARNING-level log line (previously invisible at debug level — now elevated). The constructor catches all exceptions and degrades to FTS5; if even that fails, the singleton stays `None`.

### 2.3 Embedding dimension mismatch (cosmetic)

`kazma.yaml` declares `storage.vector_dim: 1536`, but the actual model (`all-MiniLM-L6-v2`) is **384-d**. This value is informational and not enforced by `VectorMemory`. Don't rely on it for dimension logic.

### 2.4 Long documents

**Fixed:** `VectorMemory.add()` now chunks at 2000 characters with 200-char overlap. No manual pre-splitting needed.

### 2.5 Previously-documented bugs (now fixed)

These were fixed in the July 2026 memory overhaul:
- ~~`distance()` SQL function error~~ → replaced with cosine distance in Python
- ~~`_vec_available` false-positive detection~~ → now uses `load_extension("vec0")` + `vec_version()`
- ~~Arabic FTS5 search asymmetric~~ → tokenized query now used in MATCH
- ~~BM25 sort inverted~~ → ascending sort (most negative = best = first)
- ~~4-layer adapter L1 dead~~ → `GlobalVectorStore` typo fixed to `VectorStore`
- ~~4-layer callers crash on tuple unpack~~ → uses `MemoryHit` attribute access

---

## 3. HITL / safety issues

### 3.1 Danger tools execute without approval

**Check 1 — is the graph gate active?** All production build sites pass `hitl_config` (see [Security & Safety §2.2](security-and-safety.md)). If you wrote a custom build via `create_supervisor_graph()` without `hitl_config`, the gate is **dormant**.

**Check 2 — is the tool on the right list?** There are **three** lists:
- Graph path: `kazma.yaml safety.hitl.require_approval_for`.
- Swarm bus: `_EXTENDED_DANGER` (`swarm/safety.py:23`).
- MCP: pattern-based `classify_mcp_tool`. Adding to one does **not** add to the others.

**Check 3 — is `allow_headless_danger=True` set?** That disables fail-closed in headless/test mode. Ensure it's `False` in production.

### 3.2 HITL pauses never resume

**Cause:** the resume endpoint is `POST /api/approve/{thread_id}` in `routes_direct.py:454` (not `app.py`). It requires `KAZMA_SECRET` if set, and enforces ownership (403 on cross-user).

**Fix:** ensure the approving caller has the matching identity fields and the correct secret.

### 3.3 Pipeline tasks stay "paused" after restart

**Cause:** `restore_paused_tasks()` re-arms auto-reject timeouts on startup (`checkpoint_manager.py:222-230`). If the approval never arrives within `checkpoint_timeout`, the task is auto-rejected.

**Fix:** approve before the timeout, or raise `checkpoint_timeout` in task metadata.

---

## 4. SQLite concurrency

### 4.1 "database is locked"

All Kazma stores use `PRAGMA busy_timeout=5000` + WAL. If you still see lock errors:

- **One writer at a time.** WAL allows concurrent readers but a single writer. Long write transactions block others for up to 5 s.
- **Use `batch_set()` for multi-key writes** (atomic, single transaction) rather than looping `set()`.
- **Always use `get_config_store()`** — constructing `ConfigStore()` directly bypasses the singleton and can open a second connection.
- **Don't share a connection across processes.** Kazma is single-process; if you fork workers, each gets its own connection and they'll contend on the same DB file.

### 4.2 In-memory fallback silently active

If SQLite init fails, `get_config_store()` returns an `_InMemoryStore` (TTL eviction, 1-hour, 10k entries). **Settings then don't survive a restart.** Check startup logs for SQLite init errors.

---

## 5. Windows specifics

### 5.1 PowerShell command chaining

Never use `&&` or `||` in PowerShell. Use `;` and check `$LASTEXITCODE` (per AGENTS.md):

```powershell
& '.venv\Scripts\python.exe' -m pytest kazma-core/tests/ -v
if ($LASTEXITCODE -ne 0) { Write-Error "tests failed" }
```

### 5.2 Path length / non-ASCII paths

The repo path `G:\GitHubRepos\kazma` and Arabic content in `kazma.yaml`/templates are fine, but some Windows tools choke on long paths or non-ASCII. If you hit `FileNotFoundError` on deeply nested test temp dirs, enable long paths (`HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled = 1`).

### 5.3 The `.pytest_tmp_*` directories

The repo root contains many `.pytest_tmp_*` directories (artifacts from test runs). They are git-ignored clutter; safe to delete: `Remove-Item -Recurse -Force .pytest_tmp_*`.

---

## 6. Docker specifics

### 6.1 Vector volume path mismatch

`docker-compose.yml` mounts `kazma_vectors:/root/.kazma/vector_memory`, but the container runs as user `kazma` (home `/home/kazma`). Set `KAZMA_VECTOR_PATH=/app/kazma-data/vector_memory` (aligned with the data volume) to avoid writes to an unmounted path.

### 6.2 OOM with RAG extras

ChromaDB + sentence-transformers can exceed 512 Mi. The K8s manifest's `512Mi` limit is too small for the main agent with RAG. Use ≥1 Gi for the main agent container.

### 6.3 Health check path

Use `/api/gateway/status` (as `docker-compose.yml` does) or `/health/live` — **not** `/api/v1/health` (which belongs to the separate Hub API).

---

## 7. Arabic tokenization edge cases

### 7.1 Conflicting hamza rules

`ؤ` and `ئ` are normalized by **two** overlapping rules in `arabic_tokenizer.py` (Yeh normalization at lines 220-232 and the Waw/Ya-Hamza rules at lines 154-157). This is a known minor conflict; in practice the later rule wins. If you see inconsistent search results for hamza-bearing words, this is why.

### 7.2 Stemmer is basic

The stemmer (`_init_stemmer`, lines 104-130) does regex suffix/prefix stripping only — it's **not** a lemmatizer. Plural/gender variants may not collapse to a common stem. For higher recall, consider pre-normalizing queries or adding domain synonyms.

### 7.3 Tatweel (ـ) in stored text

Tatweel is stripped on tokenize, so stored `content_arabic` won't contain it — but if you query with raw text containing `ـ`, the same normalization applies, so matches still work.

---

## 8. Framework / build quirks

### 8.1 Version drift

Three independent version strings:
- `pyproject.toml`: `0.3.0`
- `kazma.yaml agent.version`: `0.2.0`
- CLI `--help` text: `v0.2.0`

They are **not** synced. Don't rely on any single one for "the Kazma version."

### 8.2 `models.router: litellm` does not import LiteLLM

The string `"litellm"` only gates the fallback-model branch (`llm_provider.py:336`). Kazma never `import litellm`. If you point at a LiteLLM proxy, use `http://host:4000/v1` as `base_url`.

### 8.3 `.env.example` lists unused env vars

`DEEPSEEK_API_KEY` and `ANTHROPIC_API_KEY` appear in `.env.example` but **no code reads them**. Key those providers via the ConfigStore provider list instead. Don't waste time wondering why setting them has no effect.

### 8.4 `mcp.servers[].trust` is a no-op

The `trust: trusted` string in `kazma.yaml` MCP config is **not read by any code**. It's documentation-only. "Trust tiers" are not a code feature.

### 8.5 `/undo` and `/edit` are stubs

Both slash commands exist in the help system but are explicitly "not yet implemented" (`slash_commands.py:257, 267`). Don't advertise them to users.

### 8.6 `UnifiedModelRegistry` is just `ModelRegistry`

The alias (`model_registry.py:950`) exists for backward compatibility. They are the same class.

---

## 9. Diagnostics checklist

When something is wrong, run through these:

```bash
# 1. Is the server up? Versions sane?
kazma status

# 2. Health
curl -s http://127.0.0.1:8000/health/details | jq

# 3. Active provider/model
curl -s http://127.0.0.1:8000/api/provider/active | jq

# 4. Gateway adapters
curl -s http://127.0.0.1:8000/api/gateway/status | jq

# 5. Swarm status + breaker states
curl -s http://127.0.0.1:8000/api/swarm/status | jq
curl -s http://127.0.0.1:8000/api/swarm/circuit-breakers | jq

# 6. Pending HITL approvals
curl -s http://127.0.0.1:8000/api/pending-approvals | jq
```

Enable JSON logging for structured diagnostics:

```yaml
logging:
  level: DEBUG
  format: json
```

Or enable debug loggers in code:
```python
import logging
for name in ("kazma_core", "kazma_gateway", "kazma_ui"):
    logging.getLogger(name).setLevel(logging.DEBUG)
```

### 9.1 Where the data lives

- Settings ConfigStore → `kazma-data/settings.db`
- Swarm TaskStore → `kazma-data/swarm_tasks.db`
- Gateway sessions → `kazma-data/sessions.db`
- LangGraph checkpoints → `kazma-data/checkpoints.db`
- Console tracing → `KazmaTracer` (`backend="console"`) writes to stdout via `kazma_core/tracing.py`

### 9.2 Inspect the TaskStore directly

```bash
sqlite3 kazma-data/swarm_tasks.db \
  "SELECT id, type, status, workers, created_at FROM swarm_tasks ORDER BY created_at DESC LIMIT 10;"

sqlite3 kazma-data/swarm_tasks.db \
  "SELECT worker, date, tasks_completed, tasks_failed, avg_latency FROM swarm_worker_metrics ORDER BY date DESC LIMIT 10;"
```

### 9.3 Check GPU / VRAM

```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits
```
On a system without an NVIDIA GPU or driver, this errors and `kazma_core/telemetry.py` falls back to zeros.

### 9.4 Reset singletons (test or recovery)

```python
from kazma_core.model_registry import reset_model_registry
from kazma_core.swarm.engine import set_swarm_engine
from kazma_core.tracing import get_trace_store
import kazma_core.tracing as _tracing

reset_model_registry()
set_swarm_engine(None)
ts = get_trace_store()
ts._traces.clear(); ts._total_cost = 0.0; ts._total_tokens = 0
# or fully replace the global TraceStore:
_tracing._trace_store = TraceStore()
```

---

## 10. Gateway & Telegram

### 10.1 "Connected" but no replies

**Symptom:** `GET /api/gateway/status` shows the Telegram adapter as `connected`, but the bot doesn't reply and the queue depth is rising.

**Cause:** the message handler wasn't registered (`GatewayManager.on_message()` never called); the adapter's listen task crashed and the `BaseAdapter.start()` done callback left `_running` stale; invalid/missing bot token; a webhook is still registered (`getUpdates` → 409); `allowed_users` is filtering out the sender; or the handler is throwing on every message.

**Fix:**
1. Confirm the handler is registered: `gateway_manager.on_message(create_graph_handler(agent))`.
2. Check adapter logs for `getMe` validation — `adapters/telegram.py` calls `getMe` on startup and sets `_running = False` on a bad token.
3. `deleteWebhook` is called automatically in `TelegramAdapter.listen()` — verify it ran.
4. If `allowed_users` is non-empty, only those Telegram user IDs are processed (`TelegramAdapter.set_allowed_users([...])`).
5. Inspect `gateway.stats` for `handler_registered` and `queue_depth`.

### 10.2 Telegram 401 / 403 / 409 / 400 errors

**Cause:**
- `401` → bot token wrong/expired/revoked.
- `403` → bot isn't a member of the group/channel, or the user blocked it.
- `409` on `getUpdates` → a webhook is still registered.
- `400` on `sendMessage` → Markdown parse error from unescaped characters.

**Fix:**
```bash
# validate the token directly
curl https://api.telegram.org/bot<TOKEN>/getMe
# clear a stale webhook
curl https://api.telegram.org/bot<TOKEN>/deleteWebhook
```
For outbound `400`, the adapter auto-retries once without `parse_mode`. If you call `sendMessage` manually, escape Markdown specials (`_`, `*`, `` ` ``, `[`) or set `parse_mode=""`.

For **LLM** 401/403, the provider key is the problem — update it in **Settings → Models/Providers**. `retry.py` maps these to the friendly message *"The model request was rejected due to an invalid or missing API key."*

### 10.3 Gateway bus queue full / adapter offline

**Symptom:** `GET /api/gateway/status` reports `queue_depth` at `100` (the default `maxsize`), or an adapter is `offline`; messages dropped with `asyncio.QueueFull`.

**Cause:** the handler is too slow/blocked; the adapter's `listen()` task raised an unhandled exception and exited; or no handler is registered.

**Fix:**
1. Check `gateway.stats` (`handler_registered`, `queue_depth`, per-adapter `running`).
2. Move heavy work off the consumer path. The queue default is `maxsize=100` (`GatewayManager.__init__`) — don't raise it unless you've also improved consumer throughput.
3. If an adapter is `offline`, find the original exception in the logs (the `BaseAdapter` done callback in `gateway.py` logs it). Fix the root cause, then restart the gateway.
4. Always call `GatewayManager.on_message(handler)` **before** `GatewayManager.start()`.

### 10.4 Tracing a message by `correlation_id`

Every `IncomingMessage` gets a `correlation_id` at ingress — `field(default_factory=lambda: f"cid-{uuid.uuid4().hex[:12]}")` (`gateway.py`). It is carried through the bus but **not** auto-injected into downstream loggers.

```python
async def handler(msg: IncomingMessage):
    logger.info("[%s] Processing message from %s", msg.correlation_id, msg.sender_id)
```
```bash
grep "cid-abc123def456" /var/log/kazma.log
```
To trace into the graph/swarm, add it to the graph state or `SwarmTask.metadata` in your handler.

---

## 11. Providers & Connectors hub (UI)

### 11.1 Provider shows "down"/"degraded" / Test Connection fails

**Symptom:** a provider card in **Settings → Providers & Connectors** shows `down`/`degraded`, or **Test Connection** returns `Cannot connect to <base_url>` / `HTTP 401`.

**Cause:** Base URL wrong/unreachable; API key missing/expired/invalid; provider disabled; or the provider's `/models` endpoint isn't OpenAI-compatible.

**Fix:** Edit the provider, verify the **Base URL**, paste the current key, click **Test Connection** (backend hits `/models` and reports latency or the exact HTTP error). The **Save** button is disabled until the test passes. On `401`, regenerate the key from the provider dashboard. For local servers (Ollama, LM Studio), ensure the server is running and the URL has the `/v1` suffix (e.g., `http://127.0.0.1:11434/v1`).

### 11.2 Platform connector test fails (Telegram / Discord / Slack)

**Cause:** token not saved; token revoked; Slack needs a **Bot Token** (`xoxb-...`) for the polling Web API adapter (no Socket Mode); Discord expects `Authorization: Bot <TOKEN>`.

**Fix:** the backend runs a non-destructive health check:
- Telegram → `GET https://api.telegram.org/bot<TOKEN>/getMe`
- Discord → `GET https://discord.com/api/v10/users/@me` with `Authorization: Bot <TOKEN>`
- Slack → `POST https://slack.com/api/auth.test` with the bot token

Save stays disabled until the test passes. After saving, click **Refresh Gateway** (or restart the server) so the new token is picked up.

### 11.3 Masked-secret placeholder overwrites the real key

**Symptom:** you edit a provider, leave the API key field as `****XXXX`, save, and the provider stops authenticating.

**Cause:** the backend preserves the existing secret **only** when it recognizes the value as a masked placeholder (`***` or containing `****`). An unexpected format can be saved as the real key.

**Fix:** leave the masked value unchanged to keep the existing secret, **or** clear the field and paste the full new secret. If you suspect the placeholder was saved, clear + repaste + Test + Save. Verify:
```python
from kazma_core.model_registry import get_model_registry
provider = get_model_registry().get_provider("openai")
print(provider.get("api_key", "")[:4])   # should NOT be "****"
```

### 11.4 Test-before-save behavior

Save is disabled for any provider/connector until **Test Connection** succeeds (new entries and edits). For existing entries, opening the modal pre-fills the masked secret and marks the entry tested, so you can save without re-testing if the secret is unchanged.

---

## 12. TUI (`kazma-tui`)

### 12.1 Dashboard freezes / metrics stop updating

**Cause:** a synchronous call in the 2 s refresh path is blocking the Textual event loop. `MetricsDashboard._do_refresh()` (`kazma-tui/kazma_tui/dashboard.py`) calls `TraceStore.recent()`, `MetricsCollector.get_all_metrics()`, and `SwarmEngine._workers` synchronously — any block freezes the UI. An exception every refresh leaves metrics stuck at their last good value.

**Fix:** check logs for `Dashboard refresh failed`; ensure `psutil` is installed and `kazma-core` importable. To isolate a blocking custom source, inject it from the constructor (`MetricsDashboard(hardware_monitor=..., trace_store=..., ...)`).

### 12.2 Dashboard shows "N/A" everywhere

**Cause:** the dashboard is read-only and falls back to `N/A` when any source is missing: `psutil` absent (`HardwareMonitor` import fails); `TraceStore` empty (RPM `None`); `MetricsCollector`/`SwarmEngine` unavailable; or running outside an event loop.

**Fix:**
```bash
uv pip install -e ".[tui]"     # or: pip install textual psutil
```
Verify the sources import, then generate some trace activity (RPM is `N/A` when idle).

### 12.3 VRAM card shows N/A / 0.0

**Cause:** `kazma_core/telemetry.py` shells out to `nvidia-smi`. If it's not in `PATH`, the driver is missing, the subprocess times out, or the GPU is non-NVIDIA, it falls back to zeros (debug-level log).

**Fix:**
```bash
nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits
```
If that fails, the TUI can't show VRAM — install the NVIDIA driver / add `nvidia-smi` to `PATH`. On non-NVIDIA systems, `N/A` is expected (graceful degradation).

### 12.4 TUI crashes on startup (import error / Python < 3.11)

**Cause:** `textual` not installed; Python older than 3.11; `ModelRegistry` not initialized before the TUI header composes (`header.py` calls `get_model_registry()`); or a `kazma-core` import failing.

**Fix:**
```bash
uv pip install -e ".[tui]"
python --version        # must be ≥ 3.11
```
```python
from kazma_core.config_store import get_config_store
from kazma_core.model_registry import initialize_model_registry
from kazma_tui.app import main
initialize_model_registry(get_config_store())   # before the TUI reads the profile
main()
```

---

## 13. CLI

### 13.1 `kazma.yaml` disagrees with the running config

**Cause:** ConfigStore (`config_store.py`) overrides `kazma.yaml` at runtime. The SQLite DB (`kazma-data/settings.db`) is the source of truth for any key written via the UI/CLI/code; env vars override both.

**Fix:**
```python
from kazma_core.config_store import get_config_store
cs = get_config_store()
print(cs.get("llm.model"), cs.get("registry.active_model"))   # active values
print(cs.export_yaml())                                         # YAML base
cs.delete("registry.active_model")                              # revert one key to YAML default
```
For a full reset: `rm kazma-data/settings.db` (loses **all** runtime settings) and restart.

### 13.2 `kazma` command not found / entry point missing

**Cause:** not installed, or installed in a venv that isn't activated; the `pyproject.toml` entry point (`kazma = "kazma_cli.main:main"`) wasn't wired (incompatible editable mode); on Windows the script isn't on `PATH`.

**Fix:**
```bash
uv sync --all-extras
uv pip install -e .
which kazma && kazma --help          # PowerShell: Get-Command kazma
# fallback if the entry point won't work:
python -m kazma_cli --help
```

### 13.3 Shell tab-completion

**Cause:** completion script not installed for the active shell; `--list-models`/`--list-providers` need a `ModelRegistry` (they fall back if uninitialized); zsh `fpath` / bash-PowerShell source missing.

**Fix:**
```bash
kazma completion install {bash|zsh|powershell}
kazma completion --list-models      # verify dynamic lists; init registry first if this errors
kazma completion --list-providers
```
Restart the shell (or source the printed path) after install.

> **Model profiles:** `save_model_profile(name, profile)`, `get_model_profile(name)`, and `list_model_profiles()` are valid registry methods (`model_registry.py`) for named `{provider, base_url, model, api_key}` snapshots. Note: profiles are stored under `models.saved.{name}` in ConfigStore — they are **not** dispatched by any worker.

---

## 14. Swarm panel & workers

### 14.1 `OutputValidator` rejects the worker's plan

**Cause:** the dispatch payload carried a `validation_schema` (JSON Schema or Pydantic model) in `metadata`, and the worker output didn't match. `OutputValidator` (`swarm/reliability.py`) rejects before the result is accepted.

**Fix:**
1. Inspect the `validation_schema` in your payload and align the worker prompt to it.
2. Ensure the worker returns valid JSON **without** Markdown fences (stringified JSON is parsed).
3. Remove `validation_schema` from the payload if validation isn't needed.

### 14.2 Worker error / timeout / circuit breaker open

**Cause:** the worker's LLM call failed; the task exceeded `timeout` (default 300 s, `swarm/worker.py`); or the circuit breaker (`swarm/reliability.py`) has opened after repeated failures.

**Fix:**
```bash
kazma swarm circuit-breaker worker-1              # inspect
kazma swarm circuit-breaker worker-1 --reset      # reset once the root cause is fixed
curl -X POST http://localhost:8000/api/swarm/workers/worker-1/circuit-breaker/reset
```
Raise the per-task timeout for genuinely long work: `{"workers": ["worker-1"], "task": "...", "timeout": 600}`.

### 14.3 Task not appearing in Active Tasks

**Cause:** the SSE bus isn't wired to the engine — `kazma-ui/kazma_ui/swarm_panel.py` calls `wire_engine_events(engine, _sse_bus)` in `_current_engine()`; if the engine predates the bus, events are lost. Or the dispatch didn't return a `task_id`; or a frontend JS error in `swarm.js` `dispatchTask()`.

**Fix:**
1. Confirm `wire_engine_events()` ran and the engine was obtained **after** the SSE bus was available.
2. Check the dispatch response for `task_id`:
   ```bash
   curl -X POST http://localhost:8000/api/swarm/dispatch -H "Content-Type: application/json" \
     -d '{"workers":["worker-1"],"task":"test"}'
   ```
3. Open the browser console for `swarm.js` errors; confirm the SSE stream responds:
   ```bash
   curl -N http://localhost:8000/api/swarm/tasks/<task_id>/stream
   ```

### 14.4 Results Dashboard empty / task-detail modal blank

**Cause:** `SwarmTask.to_dict()` nests result fields under `result`, but the UI expects them top-level — `_flatten_swarm_task()` in `swarm_panel.py` is the bridge. If it misses a field, the UI renders nothing. Or the modal element is missing from the template / `viewTaskDetail()` reads the wrong key.

**Fix:**
```bash
curl http://localhost:8000/api/swarm/tasks/<task_id>
```
The response should carry `task_id`, `status`, `worker_results`, `aggregated_output`, `synthesized_output`, `duration_seconds`, `total_cost`, `total_tokens` at the top level. If a new field is missing, promote it from the nested `result` dict in `_flatten_swarm_task()`. Confirm the `task-detail-modal` element exists in `templates/swarm.html` and that `swarm.js` `renderTaskDetailHTML()` reads `task.worker_results` / `task.individual_opinions` / `task.synthesized_output`.

### 14.5 Task history lost on restart

**Cause:** `SwarmEngine` was created without a shared `TaskStore`, so tasks live only in-memory and are lost on restart. The default `TaskStore` writes to `kazma-data/swarm_tasks.db`.

**Fix:**
```python
from kazma_core.swarm.engine import get_swarm_engine
engine = get_swarm_engine()
print(engine.task_store)   # must not be None
```
```bash
ls -la kazma-data/swarm_tasks.db
sqlite3 kazma-data/swarm_tasks.db ".tables"
```
If you construct a `SwarmEngine` manually, always pass the same `TaskStore`:
```python
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.config import SwarmConfig
from kazma_core.swarm.task_store import TaskStore
engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=TaskStore())
```

### 14.6 Pipeline HITL checkpoint missing (swarm/pipeline)

> **Two HITL mechanisms, don't confuse them.** This section is about **swarm/pipeline checkpoints** (`POST /api/swarm/tasks/{id}/approve|reject`). The **agent tool-call** approval gate is a different endpoint — `POST /api/approve/{thread_id}` (`routes_direct.py:454`) — covered in [§3.2](#32-hitl-pauses-never-resume).

**Symptom:** a pipeline task should pause at a checkpoint, but no HITL card appears, or approve/reject does nothing.

**Cause:** the task wasn't dispatched with `metadata.hitl_checkpoints` (1-based step indices where the pause occurs); the SSE bus wasn't wired so the `checkpoint` event didn't reach the frontend; `HITLCheckpointHandler` wasn't restored from `TaskStore` after restart (`SwarmEngine.restore_paused_tasks()` must run on startup); or approve/reject targets the wrong `task_id`.

**Fix:**
```python
payload = {
    "workers": ["worker-1", "worker-2"],
    "task": "...",
    "type": "pipeline",
    "metadata": {"hitl_checkpoints": [1]},   # pause before step 1
}
```
```bash
curl -X POST http://localhost:8000/api/swarm/tasks/<task_id>/approve
curl -X POST http://localhost:8000/api/swarm/tasks/<task_id>/reject
```
Ensure `restore_paused_tasks()` runs on startup (else paused tasks auto-reject on their `checkpoint_timeout` — see [§3.3](#33-pipeline-tasks-stay-paused-after-restart)).

---

## Documentation Audit Notes

This file consolidates the **actionable** findings from the whole audit plus operational debugging from prior operator docs. The most important gotchas for new operators:

1. **Memory is opt-in (§2.1)** — don't assume recall.
2. **Three HITL lists (§3.2)** — adding a danger tool in one place isn't enough.
3. **`KAZMA_SECRET` gates approval auth (§3.2)** — set it always.
4. **K8s manifests deploy the wrong thing (Deployment §4)** — don't apply them for the main agent.
5. **No 429 handling (§1.4)** — proxy or throttle externally.
6. **Two HITL mechanisms (§3.2 vs §14.6)** — agent tool-call approval (`/api/approve/{thread_id}`) ≠ swarm/pipeline checkpoints (`/api/swarm/tasks/{id}/approve`).
7. **Always `get_config_store()`** (§4.1, §1.9) — never construct `ConfigStore()` directly for the shared DB.

> **Provenance:** §1.6–§1.9, §9.1–§9.4, and §10–§14 were ported from the original operator troubleshooting guide and re-verified; stale `TelegramWorker`/`hermes` references and direct `ConfigStore()` construction were corrected.
