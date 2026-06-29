# Kazma Troubleshooting Guide

> Last updated: 2026-06-30. This guide covers the most common failures seen after the ModelRegistry transition and the `hermes` to `kazma` namespace purge.
> Every entry follows **Diagnosis → Root Cause → Solution** and uses real file paths and function names from the repository.

## Before you begin

### Log locations

- SQLite databases (settings, swarm tasks, gateway sessions, checkpoints): `kazma-data/`
- Default ConfigStore: `kazma-data/settings.db`
- Default TaskStore: `kazma-data/swarm_tasks.db`
- Gateway session store: `kazma-data/sessions.db` (default)
- Gateway checkpoint store: `kazma-data/checkpoints.db` (default)
- Standard output/error: the process that runs the server, gateway, TUI, or CLI
- Console tracing backend: `KazmaTracer` with `backend="console"` writes trace events to stdout via `kazma_core/tracing.py`

### Enable debug logging

```python
import logging
logging.getLogger("kazma_core").setLevel(logging.DEBUG)
logging.getLogger("kazma_gateway").setLevel(logging.DEBUG)
logging.getLogger("kazma_ui").setLevel(logging.DEBUG)
```

For a one-shot CLI check:

```bash
python -c "import logging; logging.basicConfig(level=logging.DEBUG); import kazma_core; ..."
```

### Verify ConfigStore

```python
from kazma_core.config_store import ConfigStore

cs = ConfigStore()
print(cs.get("llm.model"))
print(cs.get("registry.active_provider"))
print(cs.get("registry.active_model"))
print(cs.export_yaml())
```

### Inspect TaskStore

```bash
sqlite3 kazma-data/swarm_tasks.db \
  "SELECT id, type, status, workers, created_at FROM swarm_tasks ORDER BY created_at DESC LIMIT 10;"

sqlite3 kazma-data/swarm_tasks.db \
  "SELECT worker, date, tasks_completed, tasks_failed, avg_latency FROM swarm_worker_metrics ORDER BY date DESC LIMIT 10;"
```

### Check GPU / VRAM availability

```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits
```

On a system without an NVIDIA GPU or driver, the command returns an error and `kazma_core/telemetry.py` falls back to zeros.

### Reset singletons (tests or recovery)

```python
from kazma_core.model_registry import reset_model_registry
from kazma_core.swarm.engine import set_swarm_engine
from kazma_core.tracing import get_trace_store, TraceStore
import kazma_core.tracing as _tracing

reset_model_registry()
set_swarm_engine(None)

ts = get_trace_store()
ts._traces.clear()
ts._total_cost = 0.0
ts._total_tokens = 0
ts._total_llm_calls = 0
ts._total_tool_calls = 0

# Alternatively, fully replace the global TraceStore instance.
_tracing._trace_store = TraceStore()
```

---

## 1. Registry Failures

### Symptom: ModelRegistry not initialized (RuntimeError)

**Diagnosis:**
You see `RuntimeError: ModelRegistry not initialized. Call initialize_model_registry() first.` when importing or calling `get_model_registry()`.

**Root Cause:**
`initialize_model_registry(config_store)` must be called once before any consumer uses `get_model_registry()`. The global singleton in `kazma-core/kazma_core/model_registry.py` is created lazily.

**Solution:**

1. In application startup, call:

   ```python
   from kazma_core.config_store import ConfigStore
   from kazma_core.model_registry import initialize_model_registry

   cs = ConfigStore()
   initialize_model_registry(cs)
   ```

2. In tests, ensure `tests/conftest.py` autouse fixture runs:

   ```python
   @pytest.fixture(autouse=True)
   def _init_model_registry(tmp_path):
       from kazma_core.config_store import ConfigStore
       from kazma_core.model_registry import initialize_model_registry, reset_model_registry

       db_path = str(tmp_path / "test_registry.db")
       cs = ConfigStore(db_path=db_path)
       initialize_model_registry(cs)
       yield
       reset_model_registry()
   ```

3. Do not call `reset_model_registry()` in the middle of a test run unless you immediately re-initialize.

### Symptom: Failed model discovery or empty model list

**Diagnosis:**
`ModelRegistry.discover_models(provider_name)` returns `[]`, or `GET /api/swarm/models` returns an empty list, even though a provider is configured.

**Root Cause:**
One of the following is true:
- The provider has no `base_url`.
- The provider has no `api_key` and the provider requires authentication.
- The provider is not enabled.
- The provider does not expose a `/models` endpoint compatible with the discovery logic in `kazma-core/kazma_core/model_registry.py`.
- A previous discovery failure is cached in `self._discovered_models`.

**Solution:**

1. Check the provider entry:

   ```python
   from kazma_core.model_registry import get_model_registry

   registry = get_model_registry()
   print(registry.get_provider("openai"))
   print(registry.list_providers())
   ```

2. Ensure `base_url` and `api_key` are set. In the Web UI, go to Settings > Models/Providers. In code, use `registry.set_active_provider()` or `registry.upsert_provider()`.

3. Trigger discovery explicitly:

   ```python
   import asyncio
   models = asyncio.run(registry.discover_models("openai"))
   print(models)
   ```

4. Check the logs. `discover_models()` logs the failure reason in `kazma-core/kazma_core/model_registry.py`.

### Symptom: Active profile returns empty or default values

**Diagnosis:**
`ModelRegistry.get_active_profile()` returns `{"provider": "custom", "base_url": "", "model": "", "api_key": ""}` even though settings were saved.

**Root Cause:**
No active provider has been set, or the registry is falling back to legacy `llm.*` keys that are also empty. `get_active_profile()` in `kazma-core/kazma_core/model_registry.py` falls back to legacy `llm.base_url`, `llm.api_key`, and `llm.model` only when an active provider is not set.

**Solution:**

1. Set the active provider and model explicitly:

   ```python
   from kazma_core.model_registry import get_model_registry

   registry = get_model_registry()
   registry.set_active_provider(
       provider="deepseek",
       base_url="https://api.deepseek.com/v1",
       api_key="sk-...",
       model="deepseek-chat",
   )
   print(registry.get_active_profile())
   ```

2. If you only want to change the model within the active provider, use `set_active_model()`:

   ```python
   registry.set_active_model("deepseek-reasoner")
   ```

3. Verify persistence in ConfigStore:

   ```python
   from kazma_core.config_store import ConfigStore

   cs = ConfigStore()
   print(cs.get("registry.active_provider"))
   print(cs.get("registry.active_model"))
   ```

### Symptom: Settings not persisting after model/provider switch

**Diagnosis:**
You change the model or provider in the Web UI or CLI, but the next request still uses the old values. The exported `kazma.yaml` also does not reflect the change.

**Root Cause:**
- The application is using a different `ConfigStore` instance or database path than the one that saved the setting.
- `ModelRegistry.set_active_provider()` or `set_active_model()` was not called.
- A cached LLM client is still being used. `ModelRegistry` invalidates the cached client for the provider when the active profile changes, but only if the mutation went through the registry.

**Solution:**

1. Confirm that all code paths use the same default ConfigStore database (`kazma-data/settings.db`). Do not create a second `ConfigStore` with a different `db_path` unless you intend to isolate settings.

2. Check the stored values directly:

   ```python
   from kazma_core.config_store import ConfigStore

   cs = ConfigStore()
   print(cs.get("registry.active_provider"))
   print(cs.get("registry.active_model"))
   print(cs.get_all())
   ```

3. Ensure the registry is initialized with the same `ConfigStore` instance that was used to save the setting:

   ```python
   from kazma_core.config_store import ConfigStore
   from kazma_core.model_registry import initialize_model_registry

   cs = ConfigStore()
   initialize_model_registry(cs)
   ```

4. Verify that `set_active_provider()` or `set_active_model()` was called, not just a direct `cs.set()` on unrelated keys. The registry is the only owner of the active profile.

---

## 2. Connection Issues

### Symptom: Telegram bot not responding / "connected" but no replies

**Diagnosis:**
`GET /api/gateway/status` shows the Telegram adapter as `connected`, but the bot does not reply to messages. The queue depth may be rising and no outbound messages are recorded.

**Root Cause:**
- The gateway message handler is not registered (`GatewayManager.on_message()` was never called).
- The adapter's listen task crashed but the `BaseAdapter.start()` done callback did not reset `_running` correctly.
- The bot token is invalid or missing.
- A webhook is still registered on the bot, causing `getUpdates` to return HTTP 409.
- The `allowed_users` whitelist is filtering out the sender.
- The handler is throwing an exception for every message.

**Solution:**

1. Confirm the handler is registered in `kazma-ui/kazma_ui/app.py` or your gateway setup:

   ```python
   gateway_manager.on_message(create_graph_handler(agent))
   ```

2. Check the adapter logs for `getMe` validation. `kazma-gateway/kazma_gateway/adapters/telegram.py` calls `getMe` on startup and sets `_running = False` if the token is bad.

3. Verify `deleteWebhook` is called at startup. The adapter does this automatically in `TelegramAdapter.listen()`.

4. Check the `allowed_users` list. If it is non-empty, only those Telegram user IDs are processed. Use `TelegramAdapter.set_allowed_users([...])` to update it.

5. Look at `gateway.stats` for `handler_registered` and `queue_depth`:

   ```python
   from kazma_gateway.gateway import GatewayManager

   manager = GatewayManager()
   print(manager.stats)
   ```

### Symptom: Telegram 401 / 403 errors and webhook conflicts

**Diagnosis:**
You see `401 Unauthorized` or `403 Forbidden` from the Telegram Bot API, or the chat shows "The model request was rejected due to an invalid or missing API key." after a reply fails.

**Root Cause:**
- `401` means the bot token is wrong, expired, or revoked.
- `403` often means the bot is not a member of the group or channel, or the user has blocked it.
- `409 Conflict` on `getUpdates` means a webhook is still registered on the bot.
- `400` on `sendMessage` can be a Markdown parse error from unescaped characters in the agent response.

**Solution:**

1. Validate the token by calling `getMe` directly:

   ```bash
   curl https://api.telegram.org/bot<TOKEN>/getMe
   ```

2. Ensure `TelegramAdapter.listen()` calls `deleteWebhook(drop_pending_updates=False)` before polling. This is automatic in `kazma-gateway/kazma_gateway/adapters/telegram.py`.

3. If you set a webhook manually with another tool, remove it:

   ```bash
   curl https://api.telegram.org/bot<TOKEN>/deleteWebhook
   ```

4. For outbound `400` errors, the adapter retries once without `parse_mode` automatically. If you are calling `sendMessage` manually, escape Markdown special characters (`_`, `*`, `` ` ``, `[`) or use `parse_mode=""`.

5. For LLM 401 / 403 errors, update the provider API key in Settings > Models/Providers. `kazma-core/kazma_core/retry.py` maps these to the friendly message: "The model request was rejected due to an invalid or missing API key. Go to Settings > Models/Providers and update your credentials."

### Symptom: Gateway message bus queue full / adapter not listening

**Diagnosis:**
`GET /api/gateway/status` reports `queue_depth` at `100` (the default `maxsize`), or the adapter status is `offline`. Messages are dropped with `asyncio.QueueFull` warnings.

**Root Cause:**
- The registered message handler is too slow or blocked, so the consumer cannot keep up.
- The adapter's `listen()` task raised an unhandled exception and exited. The `BaseAdapter.start()` done callback is supposed to reset `_running`, but the adapter may still be marked as stopped.
- No handler is registered, so messages are dropped.

**Solution:**

1. Check the handler and queue status:

   ```python
   print(gateway.stats)
   ```

   Look for `handler_registered`, `queue_depth`, and the `running` flag for each adapter.

2. If the handler is slow, move heavy work off the consumer path or increase the worker pool. The queue default is `maxsize=100` in `GatewayManager.__init__()`. Do not increase the queue size unless you have also improved the consumer throughput.

3. If an adapter is `offline`, check the logs for the original exception. The `BaseAdapter` done callback in `kazma-gateway/kazma_gateway/gateway.py` logs it, but only if the exception escaped the adapter's own loop. Fix the root cause, then restart the gateway.

4. Ensure `GatewayManager.on_message(handler)` is called before `GatewayManager.start()`.

### Symptom: Correlation ID tracing and how to find logs by `correlation_id`

**Diagnosis:**
You need to trace a single message through the gateway, the agent graph, and any swarm dispatches.

**Root Cause:**
Every `IncomingMessage` in `kazma-gateway/kazma_gateway/gateway.py` gets a `correlation_id` at ingress via `field(default_factory=lambda: f"cid-{uuid.uuid4().hex[:12]}")`. The field is carried through the message bus but is not automatically injected into downstream loggers unless the handler passes it along.

**Solution:**

1. Read the correlation ID from the incoming message:

   ```python
   async def handler(msg: IncomingMessage):
       logger.info("[%s] Processing message from %s", msg.correlation_id, msg.sender_id)
   ```

2. Search logs for the ID:

   ```bash
   grep "cid-abc123def456" /var/log/kazma.log
   ```

3. If you need to propagate the correlation ID to the agent graph or swarm, add it to the graph state or `SwarmTask.metadata` in your handler code.

---

## 2a. Provider & Connector Connectivity

### Symptom: LLM provider shows "down" or "degraded" in the Providers & Connectors hub

**Diagnosis:**
A provider card in Settings > Providers & Connectors shows `down` or `degraded`, or the **Test Connection** button returns an error such as `Cannot connect to <base_url>` or `HTTP 401`.

**Root Cause:**
- The **Base URL** is incorrect or unreachable.
- The **API Key** is missing, expired, or invalid.
- The provider is disabled.
- The provider's `/models` endpoint does not match the expected OpenAI-compatible format.

**Solution:**

1. Open **Settings > Providers & Connectors** and select the **LLM Providers & Models** sub-tab.
2. Click **Edit** on the provider, verify the **Base URL**, and paste the current API key.
3. Click **Test Connection**. The backend calls the provider's `/models` endpoint and reports latency or the exact HTTP error.
4. If the test succeeds, the **Save** button becomes enabled. Save only after a successful test.
5. If the test fails with `401`, regenerate the API key from the provider's dashboard (OpenAI, Anthropic, DeepSeek, etc.) and retry.
6. For local servers (Ollama, LM Studio), ensure the server is running and the base URL includes the `/v1` suffix (e.g., `http://127.0.0.1:11434/v1`).

### Symptom: Platform connector test fails (Telegram, Discord, Slack)

**Diagnosis:**
Clicking **Test Connection** on a connector card returns `No token configured`, `HTTP 401`, or `invalid_auth`.

**Root Cause:**
- The connector token/key has not been saved.
- The token is revoked or invalid.
- Slack requires both a **Bot Token** (`xoxb-...`) and an **App Token** (`xapp-...`) for Socket Mode.
- Discord expects the token to be sent as `Bot <TOKEN>`.

**Solution:**

1. Open **Settings > Providers & Connectors** and select the **Platform Connectors** sub-tab.
2. Click **Edit** on the connector, enter the token, and fill in any extra fields (Slack app token, Discord guild ID, etc.).
3. Click **Test Connection**. The backend performs a non-destructive health check:
   - Telegram: `GET https://api.telegram.org/bot<TOKEN>/getMe`
   - Discord: `GET https://discord.com/api/v10/users/@me` with `Authorization: Bot <TOKEN>`
   - Slack: `POST https://slack.com/api/auth.test` with the bot token
4. The **Save** button stays disabled until the test succeeds. Save only after a successful test.
5. After saving, click **Refresh Gateway** on the legacy Connectors tab or restart the server so the new token is picked up by the gateway adapters.

### Symptom: Masked secret looks wrong or saving a provider does not update the key

**Diagnosis:**
You edit an existing provider, leave the API key field as `****XXXX`, save, and the provider no longer authenticates.

**Root Cause:**
The UI sends the masked placeholder back to the backend. The backend preserves the existing secret **only** when it recognizes the value as a masked placeholder (`***` or containing `****`). If the placeholder format is unexpected, the placeholder itself may be saved as the new key.

**Solution:**

1. When editing, leave the masked value unchanged to keep the existing secret, or clear the field and paste the full new secret.
2. If you suspect the placeholder was saved as the real key, open the provider edit modal, clear the API key field, paste the real key, run **Test Connection**, and then save.
3. Verify the stored value directly:

   ```python
   from kazma_core.config_store import ConfigStore
   from kazma_core.model_registry import get_model_registry

   cs = ConfigStore()
   registry = get_model_registry()
   provider = registry.get_provider("openai")
   print(provider.get("api_key", "")[:4])  # Should not be "****"
   ```

### Test-before-save behavior

- **Save is disabled** for any provider or connector until **Test Connection** succeeds.
- This applies to new entries and edits. For existing entries, opening the modal pre-fills the masked secret and marks the entry as tested, so you can save without re-testing if you did not change the secret.
- A failed test shows the exact error under the card or in the modal, so you can fix the URL, token, or network issue before persisting the configuration.

---

## 3. TUI / Metrics

### Symptom: kazma-tui dashboard freezes or metrics stop updating

**Diagnosis:**
The `kazma-tui` dashboard stops refreshing. Cards are stuck at old values and the UI may become unresponsive.

**Root Cause:**
- A synchronous call in the 2-second refresh path is blocking the Textual event loop. `MetricsDashboard._do_refresh()` in `kazma-tui/kazma_tui/dashboard.py` calls `TraceStore.recent()`, `MetricsCollector.get_all_metrics()`, and `SwarmEngine._workers` synchronously. If one of those blocks, the UI freezes.
- The hardware monitor update is started with `asyncio.ensure_future()`, but if the event loop is not running, it falls back to `run_until_complete()` in a way that can block.
- An exception is being raised every refresh and caught by `_refresh_now()`. The interval continues, but all metrics stay at their last successful values.

**Solution:**

1. Check the logs for `Dashboard refresh failed` or hardware update failures. The dashboard catches exceptions and logs them in `kazma-tui/kazma_tui/dashboard.py`.

2. Run the TUI with mocked or lightweight data sources if testing. In production, ensure `psutil` is installed and `kazma-core` is importable.

3. If a custom data source blocks, inject it from the constructor so you can use a non-blocking mock:

   ```python
   from kazma_tui.dashboard import MetricsDashboard

   dashboard = MetricsDashboard(
       hardware_monitor=monitor,
       trace_store=trace_store,
       metrics_collector=collector,
       swarm_engine=engine,
   )
   ```

### Symptom: Dashboard shows "N/A" everywhere

**Diagnosis:**
All metric cards in `kazma-tui` show `N/A`, even though the underlying system is healthy.

**Root Cause:**
`MetricsDashboard` in `kazma-tui/kazma_tui/dashboard.py` is a read-only consumer. It falls back to `N/A` when any data source is missing or raises an exception. Common causes:
- `psutil` is not installed, so `HardwareMonitor` cannot be imported.
- `TraceStore` has no entries, so RPM is `None`.
- `MetricsCollector` is not available or `SwarmEngine` is not initialized.
- The dashboard is running outside an event loop, so the async hardware update path is skipped.

**Solution:**

1. Install the optional TUI dependency:

   ```bash
   uv pip install -e ".[tui]"
   # or explicitly
   pip install textual psutil
   ```

2. Verify the data sources can be imported:

   ```python
   from kazma_core.telemetry import HardwareMonitor
   from kazma_core.tracing import get_trace_store
   from kazma_core.swarm.metrics import MetricsCollector
   from kazma_core.swarm.engine import get_swarm_engine

   print(HardwareMonitor())
   print(get_trace_store())
   print(MetricsCollector())
   print(get_swarm_engine())
   ```

3. Generate some trace activity so RPM is non-zero. `N/A` is expected when there is no recent activity.

### Symptom: VRAM not showing / nvidia-smi not available

**Diagnosis:**
The `VRAM (GB)` card shows `N/A` or `0.0 / 0.0 GB`, even on a system with a GPU.

**Root Cause:**
`kazma-core/kazma_core/telemetry.py` uses `nvidia-smi` for GPU metrics. If `nvidia-smi` is not in `PATH`, the driver is missing, the subprocess times out, or the GPU is not NVIDIA, the code falls back to zeros and logs the failure at debug level.

**Solution:**

1. Run the same command Kazma uses:

   ```bash
   nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits
   ```

   If this fails, the TUI cannot show VRAM. Install the NVIDIA driver or add `nvidia-smi` to `PATH`.

2. Check that the TUI has not disabled the hardware monitor. `MetricsDashboard._get_hardware_monitor()` lazily imports `HardwareMonitor`. If the import fails, the card stays at `N/A`.

3. On non-NVIDIA systems, VRAM is expected to be `N/A` or zero. This is intentional graceful degradation.

### Symptom: TUI crashes on startup or import error

**Diagnosis:**
Running `kazma-tui` or `python -m kazma_tui` raises an import error, `RuntimeError`, or widget mount failure.

**Root Cause:**
- `textual` is not installed.
- Python is older than 3.11.
- `ModelRegistry` is not initialized before the TUI header is composed. `kazma-tui/kazma_tui/header.py` calls `get_model_registry()` to read the active profile.
- A `kazma-core` import is failing because of a missing dependency.

**Solution:**

1. Install the TUI extra:

   ```bash
   uv pip install -e ".[tui]"
   ```

2. Initialize the registry before launching the TUI:

   ```python
   from kazma_core.config_store import ConfigStore
   from kazma_core.model_registry import initialize_model_registry
   from kazma_tui.app import main

   cs = ConfigStore()
   initialize_model_registry(cs)
   main()
   ```

3. Check that the Python version is 3.11 or newer:

   ```bash
   python --version
   ```

4. If the error is inside a Textual widget, run the TUI tests to isolate the widget:

   ```bash
   uv run pytest kazma-tui/tests/ -v
   ```

---

## 4. Common CLI Errors

### Symptom: `kazma profile` errors / profile does not exist

**Diagnosis:**
A component or worker reports that a profile does not exist. `TelegramWorker` in `kazma-core/kazma_core/swarm/worker.py` shells out to `kazma -p <profile>`, and the command fails because the profile is unknown or the `kazma` CLI is not on `PATH`. Alternatively, `ModelRegistry.get_model_profile(name)` returns `None`.

**Root Cause:**
- `kazma -p <profile>` is an external Telegram bot runner invocation, not a built-in subcommand of the main `kazma` CLI. It requires the `kazma` binary to be installed and available on `PATH`.
- The saved model profile requested by name does not exist in `ConfigStore` under `models.saved.{name}`.
- The profile name passed to `TelegramWorker` does not match any configured profile.

**Solution:**

1. Verify the saved model profile exists:

   ```python
   from kazma_core.model_registry import get_model_registry

   registry = get_model_registry()
   print(registry.list_model_profiles())
   print(registry.get_model_profile("core"))
   ```

2. If the profile is missing, create it:

   ```python
   registry.save_model_profile("core", {
       "provider": "deepseek",
       "base_url": "https://api.deepseek.com/v1",
       "model": "deepseek-chat",
       "api_key": "sk-...",
   })
   ```

3. Confirm that `kazma` is on `PATH` when `TelegramWorker` dispatches:

   ```bash
   which kazma
   kazma -p core --help
   ```

   If `kazma` is not installed, use `InProcessWorker` instead of `TelegramWorker`, or install the external bot runner separately.

### Symptom: Configuration mismatches (kazma.yaml vs ConfigStore DB)

**Diagnosis:**
`kazma.yaml` says one model, but the running application uses another. `ConfigStore.export_yaml()` returns different values than `kazma.yaml`.

**Root Cause:**
`ConfigStore` in `kazma-core/kazma_core/config_store.py` overrides `kazma.yaml` at runtime. The SQLite database (`kazma-data/settings.db`) is the source of truth for any key that has been written through the UI, CLI, or code. Environment variables take precedence over both.

**Solution:**

1. Check the active value in the DB:

   ```python
   from kazma_core.config_store import ConfigStore

   cs = ConfigStore()
   print(cs.get("llm.model"))
   print(cs.get("registry.active_model"))
   ```

2. Compare with the YAML base:

   ```python
   print(cs.export_yaml())
   ```

3. To revert a key to the YAML default, delete it from the DB:

   ```python
   cs.delete("registry.active_model")
   cs.delete("llm.model")
   ```

4. To force a full revert, delete the DB file (this loses all runtime settings):

   ```bash
   rm kazma-data/settings.db
   ```

   Then restart the application.

### Symptom: `kazma` command not found / entry point missing

**Diagnosis:**
Running `kazma` returns `command not found`, or `python -m kazma_cli` works but the shell command does not.

**Root Cause:**
- The package is not installed, or it is installed in a virtual environment that is not activated.
- The `pyproject.toml` entry point `kazma = "kazma_cli.main:main"` is not being used because the install was done with an incompatible editable mode.
- On Windows, the script is not on `PATH`.

**Solution:**

1. Activate the virtual environment and install in editable mode:

   ```bash
   uv sync --all-extras
   uv pip install -e .
   ```

2. Verify the console script exists:

   ```bash
   which kazma
   kazma --help
   ```

   On Windows PowerShell:

   ```powershell
   Get-Command kazma
   kazma --help
   ```

3. If you cannot use the entry point, run the module directly:

   ```bash
   python -m kazma_cli --help
   ```

### Symptom: Shell completion issues

**Diagnosis:**
Tab completion for `kazma` does not work, or `--model` and `--provider` completion returns no values.

**Root Cause:**
- The completion script was not installed for the active shell.
- The completion script uses `kazma completion --list-models` and `kazma completion --list-providers`, which require `ModelRegistry` to be initialized. If the registry is not initialized, the fallback list is used.
- The shell's completion directory is not in `fpath` (zsh) or is not sourced (bash / PowerShell).

**Solution:**

1. Generate and install completion for your shell:

   ```bash
   kazma completion install bash
   kazma completion install zsh
   kazma completion install powershell
   ```

2. Source the script or restart the shell. The install command prints the exact path and instructions.

3. Verify the dynamic list commands work:

   ```bash
   kazma completion --list-models
   kazma completion --list-providers
   ```

   If these fail with a `RuntimeError`, initialize the registry first (see the ModelRegistry not initialized section).

---

## 5. Swarm Rejections & Worker Errors

### Symptom: Validator rejects Orchestrator's plan

**Diagnosis:**
A swarm dispatch returns `status=failed` with an error mentioning `OutputValidator` or a JSON schema mismatch. The worker produced output, but the orchestrator rejected it.

**Root Cause:**
The dispatch payload included a `validation_schema` (or a Pydantic model) in `metadata`, and the worker output did not match. The `OutputValidator` in `kazma-core/kazma_core/swarm/reliability.py` rejects invalid output before the result is accepted.

**Solution:**

1. Inspect the validation schema in your dispatch payload:

   ```python
   payload = {
       "workers": ["worker-1"],
       "task": "...",
       "metadata": {
           "validation_schema": {
               "type": "object",
               "properties": {"answer": {"type": "string"}},
               "required": ["answer"],
           }
       }
   }
   ```

2. Update the worker prompt to request the exact output format. If you are using `InProcessWorker`, the worker prompt is the task text plus context.

3. If the output is stringified JSON, the validator tries to parse it. Ensure the worker returns valid JSON without markdown fences.

4. If you do not need validation, remove the `validation_schema` from the payload.

### Symptom: Worker returns error / timeout / circuit breaker open

**Diagnosis:**
A dispatch returns `status=error`, `status=timeout`, or a circuit breaker state of `open`. The worker never completes the task.

**Root Cause:**
- The worker's underlying LLM call failed or returned an invalid response.
- The task exceeded the `timeout` (default 300 seconds in `kazma-core/kazma_core/swarm/worker.py`).
- The worker has failed repeatedly and the circuit breaker in `kazma-core/kazma_core/swarm/reliability.py` has opened.

**Solution:**

1. Check the worker logs and the returned error string:

   ```python
   print(result["error"])
   ```

2. Inspect the circuit breaker status:

   ```bash
   kazma swarm circuit-breaker worker-1
   ```

3. Reset the breaker if the underlying issue is fixed:

   ```bash
   kazma swarm circuit-breaker worker-1 --reset
   ```

   Or via the API:

   ```bash
   curl -X POST http://localhost:8000/api/swarm/workers/worker-1/circuit-breaker/reset
   ```

4. Increase the per-task timeout if the task is genuinely long:

   ```python
   payload = {"workers": ["worker-1"], "task": "...", "timeout": 600}
   ```

### Symptom: Task not showing in Active Tasks

**Diagnosis:**
A task is dispatched from the Swarm Panel or CLI, but no card appears in the Active Tasks tab.

**Root Cause:**
- The SSE event bus is not wired to the `SwarmEngine`. `kazma-ui/kazma_ui/swarm_panel.py` calls `wire_engine_events(engine, _sse_bus)` in `_current_engine()`.
- The dispatch endpoint did not return a `task_id`, so the frontend cannot subscribe to the SSE stream.
- The frontend optimistic card creation failed because of a JavaScript error.

**Solution:**

1. Verify that `wire_engine_events(engine, _sse_bus)` was called. In `kazma-ui/kazma_ui/swarm_panel.py`, `_current_engine()` does this on first use. If the engine was created before the SSE bus was available, events are lost.

2. Check the dispatch response for `task_id`:

   ```bash
   curl -X POST http://localhost:8000/api/swarm/dispatch \
     -H "Content-Type: application/json" \
     -d '{"workers":["worker-1"],"task":"test"}'
   ```

3. Open the browser console and look for JavaScript errors in `kazma-ui/kazma_ui/static/js/swarm.js`. The `dispatchTask()` function creates a pending card and upgrades it to the real `task_id`.

4. Confirm the SSE stream endpoint works:

   ```bash
   curl -N http://localhost:8000/api/swarm/tasks/<task_id>/stream
   ```

### Symptom: Results Dashboard empty or task detail modal not opening

**Diagnosis:**
The Results Dashboard tab shows no task results, or clicking a result card does not open the detail modal. The task detail API returns data but the modal is blank.

**Root Cause:**
- `SwarmTask.to_dict()` nests result fields under a `result` key, but the UI expects top-level fields. `kazma-ui/kazma_ui/swarm_panel.py` `_flatten_swarm_task()` is the bridge. If the flatten helper is missing a field, the UI renders nothing.
- The task detail modal HTML is hidden or missing from the page template.
- The JavaScript `viewTaskDetail()` function in `kazma-ui/kazma_ui/static/js/swarm.js` is looking for a different key.

**Solution:**

1. Verify that `GET /api/swarm/tasks/{id}` returns the flattened shape:

   ```bash
   curl http://localhost:8000/api/swarm/tasks/<task_id>
   ```

   The response should contain `task_id`, `status`, `worker_results`, `aggregated_output`, `synthesized_output`, `duration_seconds`, `total_cost`, and `total_tokens` at the top level.

2. If a new field is missing, update `_flatten_swarm_task()` in `kazma-ui/kazma_ui/swarm_panel.py` to promote it from the nested `result` dict.

3. Check that `kazma-ui/kazma_ui/templates/swarm.html` contains the modal element (`task-detail-modal` or equivalent) and that `swarm.js` `viewTaskDetail()` sets `modal.style.display = 'flex'`.

4. If the modal opens but is empty, check the browser console for key access errors. The `renderTaskDetailHTML()` function in `swarm.js` reads `task.worker_results`, `task.individual_opinions`, and `task.synthesized_output`.

### Symptom: Task history lost on restart

**Diagnosis:**
Completed swarm tasks disappear after the Web UI server restarts. Worker metrics also reset.

**Root Cause:**
`SwarmEngine` was created without a shared `TaskStore`. Without a `TaskStore`, tasks only live in the in-memory `_task_history` and are lost on restart. The default `TaskStore` writes to `kazma-data/swarm_tasks.db`.

**Solution:**

1. Verify that `kazma-ui/kazma_ui/app.py` creates one shared `TaskStore` and passes it to the `SwarmManager` / `SwarmEngine`.

2. Check the engine for the `task_store` attribute:

   ```python
   from kazma_core.swarm.engine import get_swarm_engine

   engine = get_swarm_engine()
   print(engine.task_store)
   ```

3. Ensure the database path is writable:

   ```bash
   ls -la kazma-data/swarm_tasks.db
   sqlite3 kazma-data/swarm_tasks.db ".tables"
   ```

4. If you are creating a `SwarmEngine` manually, always pass the same `TaskStore`:

   ```python
   from kazma_core.swarm.engine import SwarmEngine
   from kazma_core.swarm.config import SwarmConfig
   from kazma_core.swarm.task_store import TaskStore

   store = TaskStore()
   engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
   ```

### Symptom: HITL checkpoint not appearing / cannot approve or reject

**Diagnosis:**
A pipeline task should pause at a checkpoint, but the HITL card does not appear in the Active Tasks tab, or the approve/reject buttons do nothing.

**Root Cause:**
- The pipeline task was not created with `metadata.hitl_checkpoints` containing the 1-based step index where the pause should occur.
- The SSE event bus was not wired, so the `checkpoint` event is not delivered to the frontend.
- The `HITLCheckpointHandler` was not restored from `TaskStore` after a restart. `SwarmEngine.restore_paused_tasks()` must be called on startup.
- The approve/reject API calls target the wrong `task_id` or the engine is unavailable.

**Solution:**

1. When dispatching a pipeline, include the checkpoint metadata:

   ```python
   payload = {
       "workers": ["worker-1", "worker-2"],
       "task": "...",
       "type": "pipeline",
       "metadata": {
           "hitl_checkpoints": [1]
       }
   }
   ```

2. Verify that `wire_engine_events(engine, _sse_bus)` is called. The `checkpoint` event is emitted in `kazma-ui/kazma_ui/swarm_sse.py` from the patched `_finalize_task()`.

3. After a server restart, confirm paused tasks are restored:

   ```python
   from kazma_core.swarm.engine import get_swarm_engine

   engine = get_swarm_engine()
   engine.restore_paused_tasks()
   ```

4. Approve or reject via the CLI:

   ```bash
   kazma swarm approve <task_id>
   kazma swarm reject <task_id>
   ```

   Or via the API:

   ```bash
   curl -X POST http://localhost:8000/api/swarm/tasks/<task_id>/approve
   curl -X POST http://localhost:8000/api/swarm/tasks/<task_id>/reject
   ```

---

## Common Gotchas

1. **ModelRegistry RuntimeError:** `initialize_model_registry()` not called. See the first entry in section 1.
2. **Telegram Worker still calls old `hermes` CLI:** The `TelegramWorker` in `kazma-core/kazma_core/swarm/worker.py` now shells out to `kazma -p <profile>`. If you see errors about `hermes`, update your deployment and verify `kazma` is on `PATH`.
3. **Swarm task history lost on restart:** `SwarmEngine` must be created with a shared `TaskStore`. See the "Task history lost on restart" entry.
4. **TUI dashboard shows "N/A":** Lazy data sources are not available. This is graceful degradation, not a crash. See section 3.
5. **Results Dashboard task detail modal invisible:** The modal was inside a hidden tab and the task shape was nested. Both are fixed in `kazma-ui/kazma_ui/swarm_panel.py` and `kazma-ui/kazma_ui/static/js/swarm.js`. If you still see it, verify the flattened task shape and the modal DOM.
