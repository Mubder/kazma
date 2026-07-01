# APPROVAL PROMPT FOR NEXT AGENT

Copy/paste the text below as the first message to a new Droid session:

---

Your audit was verified against the actual codebase. Here is the verdict and what to do next.

## Audit Verification: 12/13 CORRECT

All findings confirmed except C1. Proceed with fixes.

## Fix Order

### Batch 1: Bugs (highest impact)
1. **B1** — `task_store.py` `_row_to_task`: restore `validation_schema` from DB (same pattern as the other fields already restored below it)
2. **B2** — `discovery.py:458-459`: `openai/*` prefix must resolve to `https://api.openai.com/v1`, NOT `_LM_STUDIO_DEFAULT_URL` (localhost:1234). This is a leftover hardcoded fallback that was supposedly eliminated.
3. **B3** — `config_store.py` `import_yaml`: nested dict values get flattened to scalar keys. `swarm.output_target: {platform, chat_id, enabled}` must survive an export→import round-trip as a dict, not 3 separate keys. Fix `_flatten()` to store dict values as JSON blobs, or detect dotted-key dict children and merge them back.
4. **B4** — `swarm.js:1403`: replace `Number(chatId)` with `parseInt(chatId, 10)` or `BigInt(chatId).toString()` to avoid precision loss on large Telegram supergroup IDs.

### Batch 2: Gaps
5. **G1** — `agent_handler.py` `_dispatch_swarm_from_chat` except block: also call `_maybe_send_to_output_target(manager, error_reply, target_override)` so failures mirror to the group too.
6. **G2** — `agent_handler.py:723`: either read `task.metadata["output_target"]` in the routing path or remove the dead write. Since routing already uses the local `target_override` variable correctly, remove the dead metadata write.
7. **G3** — `_parse_output_target_suffix`: add a `logger.info` when a suffix is detected but unrecognized so it's not a silent no-op.

### Batch 3: Dead code
8. **D1** — Remove dead JS functions: `populateModelDatalist`, `populateProfileSelect`, `applyModelProviderDefaults`, and their init bindings (lines 64-67). Clean up spawn-* references.
9. **D2** — Delete `summary_worker.py` (never imported/instantiated anywhere).
10. **D3** — Remove `TelegramWorker` class from `worker.py` and remove it from `__init__.py` exports and `engine.py` `_create_worker` telegram_bot branch. Keep `WorkerConfig.type` validation accepting `"telegram_bot"` for backward compat with persisted configs.
11. **_fallback_html correction** — The handoff was wrong: `_fallback_html` is NOT dead, it's the reachable fallback at `swarm_panel.py:363`. Leave it as-is.

### Batch 4: Minor
12. **InProcessWorker silent except** — Add `logger.debug` to the `except Exception` in provider resolution.

## C1 Clarification: Arabic strings are CORRECT — DO NOT REMOVE

Your audit flagged Arabic UI strings as a "convention violation." This is **wrong** for the current project. The old `AGENTS.md` had an English-only rule, but we rewrote it (commit `09215ae`) to reflect the full framework scope. The bilingual behavior is intentional:

**Language policy (DO NOT change):**
- If the user speaks Arabic, the model responds in Arabic
- If the user speaks English, the model responds in English
- If the user code-switches (mixes Arabic + English), the model matches their mixing pattern
- The user can explicitly declare a language preference to override this at any time
- System prompts contain this rule: "You MUST respond in the EXACT same language the user writes in"

The Arabic strings in `agent_handler.py`, `graph_builder.py`, `chat.py`, and `sse_chat.py` are correct. They show budget-exceeded and error messages in Arabic because the framework's default user base is Arabic-speaking (Kuwaiti/Gulf Arabic dialects). The English translation in parentheses `(Budget exceeded)` is kept as a fallback for non-Arabic users.

**Do NOT touch any Arabic strings. Do NOT "translate" them to English.**

## G4 (test coverage)
After all fixes are done, write tests for the Phase 5 functions: `_get_output_target_config`, `_parse_output_target_suffix`, `_maybe_send_to_output_target`, and the `/api/swarm/output-target` endpoints. Use `pytest` with mocked `ConfigStore`.

## Before you start
1. Read `AGENTS.md` for project scope and critical subsystems
2. Compile-check after every file change: `& '.venv\Scripts\python.exe' -c "import py_compile; py_compile.compile(r'<file>', doraise=True); print('OK')"`
3. JS syntax-check: `node --check "<file>"`
4. Commit each batch separately with clear messages
5. Restart the server after all batches to verify:
   ```powershell
   Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id)).CommandLine -like '*uvicorn*kazma*' } | ForEach-Object { Stop-Process -Id $_.Id -Force }
   cd 'G:\GitHubRepos\kazma'; & '.venv\Scripts\python.exe' -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 8090
   ```

Proceed with Batch 1.
