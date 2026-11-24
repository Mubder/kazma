<!-- AGENTS.md, piece 23-windows-subprocess. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 23. Windows asyncio.subprocess trap (`SelectorEventLoop`)

The Kazma server runs a **`SelectorEventLoop`** (`kazma_core/eventloop.py` —
forced because psycopg async refuses the Proactor loop, and Postgres-backed
checkpoints must persist on Windows). On Windows the selector loop **does not
implement subprocess transports**: `asyncio.create_subprocess_exec` /
`create_subprocess_shell` raise `NotImplementedError`. This is a recurring
footgun — every tool that spawns a subprocess must avoid the bare asyncio API.

**The rule:** on the server loop, spawn subprocesses via
**`asyncio.to_thread(subprocess.run, …)`** (blocking, bounded) or
**`asyncio.to_thread(subprocess.Popen, …)`** (non-blocking start). The MCP
manager (`mcp/manager.py`) already has an explicit `NotImplementedError` →
Popen fallback — mirror that.

**Known-correct sites (keep them):**
- `system/installer.py`, `system/runtime_manager.py`, `telemetry.py`,
  `models/discovery.py`, `agent/tool_builtins/system.py:shell_exec` — all use
  `to_thread` + `subprocess`.
- **Playwright**: the browser tools (`kazma_skills/native/browser_automation`)
  run on the **sync API inside `to_thread`**; the heavier crawl/fetch paths
  (`knowledge_ingest.py`, `read_url.py`) route through
  `kazma_core/playwright_loop.py` (a dedicated ProactorEventLoop daemon thread).
  Do NOT revert these to the async Playwright API on the server loop.
- `security.process_budget.run_bounded_async` shields and joins its worker
  receipt on cancellation, including repeated cancellation. Setting a threading
  event alone cannot acknowledge that the native process tree/pipes have ended.
  Keep the join; `tests/test_process_budgets.py` holds real cleanup open and runs
  the old return-before-cleanup boundary as a negative control.

**Symptom of a regression:** a tool reports `error=False` in 0 ms while doing
nothing, and the log shows `Task exception was never retrieved …
NotImplementedError` from `playwright/_impl/_transport.py` or
`asyncio/base_events.py`. That means someone re-introduced
`create_subprocess_exec` on the server loop.

