# Kazma Code Quality Audit — Commit 1160674

**Scope:** 10 priority file groups (new/changed since prior audit)  
**Date:** 2026-06-30  
**Files scanned:** ~25 files, ~8,500 lines of new/changed Python code

---

## 🔴 CRITICAL (Bugs / Runtime Risks)

### C-1. `sse_chat.py:371-374` — `_active_profile` referenced before definition in closure
**File:** `kazma-ui/kazma_ui/sse_chat.py`  
**Lines:** 371, 374 (inside `chat_stream` closure) vs 524 (definition)

`_active_profile` is a closure variable defined at line 524 but referenced at lines 371–374 inside `chat_stream()` which is defined at line 315. Python resolves closures at call time so this works at runtime, but the code reads as a forward-reference bug. Any refactoring that changes execution order will silently break.

### C-2. `checkpoint.py:124-127` — `approve()` calls `wait()` after `set()` (no-op wait)
**File:** `kazma-core/kazma_core/swarm/checkpoint.py`  
**Lines:** 124–130

```python
entry.approval_event.set()        # line 124 — signals event
await entry.approval_event.wait()  # line 127 — returns immediately (already set)
return entry.final_result           # line 130 — may be None if pipeline hasn't finished
```

`approve()` claims to "Wait for the pipeline to complete" but the `wait()` is a no-op since the event was just set. `entry.final_result` can be `None` if `complete_pipeline()` hasn't been called yet. The engine must set `final_result` and then call `set()`, but this isn't enforced.

### C-3. `sse_chat.py:391` — Arabic text in English-only UI
**File:** `kazma-ui/kazma_ui/sse_chat.py`  
**Line:** 391

```python
"⚠️ ميزانية الجلسة انتهت. أعد التشغيل. (Budget exceeded)"
```

Violates `AGENTS.md`: "All UI text must be in English. No Arabic, RTL markers, or bilingual labels."

### C-4. `telegram.py:345` — `assert` used in production code
**File:** `kazma-gateway/kazma_gateway/adapters/telegram.py`  
**Line:** 345

```python
assert self._http is not None
```

`assert` statements are stripped when Python runs with `-O`. Use `if self._http is None: raise RuntimeError(...)` instead.

---

## 🟠 HIGH (Code Smells / Maintainability)

### H-1. `dashboard.py:217-323` — `_do_refresh()` is 106 lines
**File:** `kazma-tui/kazma_tui/dashboard.py`  
**Lines:** 217–323

Single method handles CPU/RAM/VRAM extraction, RPM calculation, latency/error calculation, agent name extraction, and 6 widget updates. Should be split into `_refresh_hardware()`, `_refresh_throughput()`, `_refresh_latency()`, etc.

### H-2. `dashboard.py:410-420` — `_format_cpu()` and `_format_ram()` are dead code
**File:** `kazma-tui/kazma_tui/dashboard.py`  
**Lines:** 410–420

`_format_cpu()` and `_format_ram()` are defined but never called. They were superseded by `_format_health()` (line 422) which combines both.

### H-3. `header.py:64` — `model` reactive declared but never updated
**File:** `kazma-tui/kazma_tui/header.py`  
**Line:** 64

```python
model: reactive[str] = reactive(_FALLBACK_TEXT)
```

Only `self.provider` is set in `refresh_profile()` (line 108). The `model` reactive is never written to — dead reactive attribute.

### H-4. `footer.py:9,15` — Unused `logging` import and `logger`
**File:** `kazma-tui/kazma_tui/footer.py`  
**Lines:** 9, 15

`import logging` and `logger = logging.getLogger(__name__)` are defined but `logger` is never referenced.

### H-5. `sse_chat.py:87,216,221` — `error_yielded` set but never read
**File:** `kazma-ui/kazma_ui/sse_chat.py`  
**Lines:** 87, 216, 221

```python
error_yielded = False    # line 87
error_yielded = True     # line 216
error_yielded = True     # line 221
```

Variable is assigned but never read. Dead code.

### H-6. `sse_chat.py:256` — `checkpointer` parameter unused
**File:** `kazma-ui/kazma_ui/sse_chat.py`  
**Line:** 256

`create_sse_chat_router()` accepts `checkpointer` but it's always passed as `None` and never used inside the function.

### H-7. `settings_manager.py:886,893` — `format` shadows built-in
**File:** `kazma-core/kazma_core/settings_manager.py`  
**Lines:** 886, 893

```python
def export_config(self, format: str = "yaml") -> str:
def import_config(self, data: str, format: str = "yaml", ...) -> int:
```

`format` shadows the Python built-in `format()`. Rename to `fmt` or `output_format`.

### H-8. `settings_manager.py:918,936` — `_flatten` defined twice (different semantics)
**File:** `kazma-core/kazma_core/settings_manager.py`  
**Lines:** 918, 936

Two different `_flatten` functions defined as closures in `import_config()` and `get_config_diff()`. Same name, different return types (`None` vs `dict`). Confusing for readers.

### H-9. `swarm_panel.py:73` — Module-level mutable global `_SHARED_TASK_STORE`
**File:** `kazma-ui/kazma_ui/swarm_panel.py`  
**Line:** 73

```python
_SHARED_TASK_STORE: Any | None = None
```

Mutable module-level state modified via `global` keyword in `_resolve_engine()` and `_reset_swarm_state()`. Makes testing and concurrency harder.

### H-10. `swarm_sse.py:282-427` — Monkey-patching engine methods at runtime
**File:** `kazma-ui/kazma_ui/swarm_sse.py`  
**Lines:** 282–427

`wire_engine_events()` replaces `engine.dispatch`, `engine._finalize_task`, and `engine._dispatch_worker` with patched versions. Fragile pattern — any engine refactor breaks the patches silently.

### H-11. `_sse_frame()` duplicated across two files
**Files:** `kazma-ui/kazma_ui/sse_chat.py:39-51` and `kazma-ui/kazma_ui/swarm_sse.py:37-49`

Identical `_sse_frame()` helper duplicated in both files. Should be extracted to a shared utility.

### H-12. `settings_manager.py:417-449` — Duplicate HTTP test patterns
**File:** `kazma-core/kazma_core/settings_manager.py`  
**Lines:** 417–449

`test_connector()` has near-identical `httpx.AsyncClient` + GET + status check blocks for Telegram and Discord. Should be extracted to `_test_http_connector(url, headers)`.

---

## 🟡 MEDIUM (Style / Consistency)

### M-1. `model_registry.py:84` — `config_store: Any` overly generic
**File:** `kazma-core/kazma_core/model_registry.py`  
**Line:** 84

`config_store: Any` could be typed as `ConfigStore` or a `Protocol` for better IDE support and type checking.

### M-2. `model_registry.py:85` — Abbreviated `self._cs`
**File:** `kazma-core/kazma_core/model_registry.py`  
**Line:** 85

`self._cs` is cryptic. `self._config_store` is clearer.

### M-3. `model_registry.py:100-127` vs `196-224` — Duplicate provider resolution
**File:** `kazma-core/kazma_core/model_registry.py`  
**Lines:** 100–127 (`get_active_profile`) and 196–224 (`get_client`)

Both methods have identical fallback logic: try provider entry → fall back to `llm.*` keys. Should share a `_resolve_provider_config()` helper.

### M-4. `settings_manager.py:429-430` — Duplicate comment
**File:** `kazma-core/kazma_core/settings_manager.py`  
**Lines:** 428–429

```python
# Slack adapter (optional, via env vars)
# Slack adapter (from config store → env)
```

Two consecutive comments for the same block — leftover from editing.

### M-5. `app.py (kazma-ui):251-254` — Bare `except Exception: pass`
**File:** `kazma-ui/kazma_ui/app.py`  
**Lines:** 251–254

```python
except Exception:
    pass
```

Silently swallows errors in the WebSocket dashboard handler. At minimum, log at DEBUG level.

### M-6. `telegram.py` — `listen()` is 190 lines, `send()` is 161 lines
**File:** `kazma-gateway/kazma_gateway/adapters/telegram.py`  
**Lines:** 144–334 (`listen`), 840–1001 (`send`)

Both methods are very long. `listen()` handles webhook deletion, token validation, polling, update processing, voice handling, and callback queries all in one method.

### M-7. `test_model_registry.py:22` — `sys.path.insert` for test imports
**File:** `tests/test_model_registry.py`  
**Line:** 22

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kazma-core"))
```

Fragile path manipulation. Use proper package installation or `conftest.py` path setup.

### M-8. `swarm_panel.py:41-42` — Unused module-level lists
**File:** `kazma-ui/kazma_ui/swarm_panel.py`  
**Lines:** 41–42

```python
_SUPPORTED_MODELS: list[str] = []
_SUPPORTED_PROVIDERS: list[str] = []
```

Declared but never populated or referenced in the scanned code.

---

## 📊 SUMMARY TABLE

| Category | Count | Severity |
|----------|-------|----------|
| 🔴 Critical (bugs / runtime) | 4 | Fix immediately |
| 🟠 High (code smells) | 12 | Fix before merge |
| 🟡 Medium (style) | 8 | Fix in follow-up |
| **Total** | **24** | |

### By Checklist Item

| # | Check | Findings |
|---|-------|----------|
| 1 | Unused imports | 2 (footer.py:logging, sse_chat.py:checkpointer param) |
| 2 | Dead code | 4 (dashboard.py formatters, header.py model reactive, sse_chat.py error_yielded) |
| 3 | Duplicate logic | 3 (model_registry provider resolution, settings_manager HTTP tests, _sse_frame) |
| 4 | Complex functions (>50 lines) | 5 (_do_refresh 106L, listen 190L, send 161L, create_sse_chat_router ~384L, swarm_dispatch ~150L) |
| 5 | TODO/FIXME/HACK | 0 |
| 6 | Bare except clauses | 0 |
| 7 | Mutable default args | 0 |
| 8 | Global state | 3 (_registry singleton, _SHARED_TASK_STORE, monkey-patched engine) |
| 9 | Type hint gaps | 2 (config_store: Any, repeated dict[str, Any] patterns) |
| 10 | Naming issues | 3 (self._cs, format shadowing, _flatten name collision) |

### Top 5 Actionable Fixes

1. **Fix `approve()` no-op wait** (C-2) — Restructure so `complete_pipeline()` sets `final_result` THEN signals the event, and `approve()` waits on a separate completion event.
2. **Remove Arabic text** (C-3) — Replace line 391 with English-only message.
3. **Split `_do_refresh()`** (H-1) — Extract hardware/RPM/latency/agents updates into separate methods.
4. **Extract shared `_sse_frame()`** (H-11) — Move to `kazma_ui/sse_utils.py`.
5. **Remove dead code** (H-2, H-3, H-5) — Delete `_format_cpu`, `_format_ram`, unused `model` reactive, `error_yielded`.
