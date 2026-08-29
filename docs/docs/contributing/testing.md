---
sidebar_position: 3
---

# Testing

## Test framework

- pytest with pytest-asyncio
- Coverage with pytest-cov

## Writing tests

```python
import pytest
from kazma_core.checkpoint import CheckpointManager

@pytest.fixture
async def manager(tmp_path):
    m = CheckpointManager(db_path=str(tmp_path / "test.db"))
    yield m
    await m.close()

@pytest.mark.asyncio
async def test_save_and_load(manager):
    state = {"agent_id": "test", "step": 1}
    await manager.save(state)
    loaded = await manager.load(agent_id="test")
    assert loaded["step"] == 1
```

## Running tests

```bash
pytest
pytest --cov=kazma_core --cov-report=html
pytest -v
```

## Static gates

Two suites run on every commit via `pre-commit` (see
[Development Setup](development-setup.md)). Both are AST-only — they never
boot the app, and together they take about eight seconds.

```bash
pytest tests/test_imports.py tests/test_static_gates.py -q
```

They exist because each closes a class of defect that was found by *grepping
the tree*, not by a failing test:

| Gate | What fails the build |
|------|----------------------|
| `test_every_product_module_imports` | A module that no longer imports. Born from the `crawl.py` incident: a deletion left a dangling import, `py_compile` passed, and research broke in production at first use. |
| `test_no_dangling_kazma_import_references` | A `kazma_*` import reference that resolves to nothing — catches function-level imports the smoke test misses. |
| `test_no_blocking_db_driver_in_async` | A synchronous `sqlite3.connect` inside `async def`. It pins the event loop that also serves every SSE and WebSocket stream. Fix by dropping `async` (FastAPI threadpools sync handlers) or wrapping in `asyncio.to_thread`. |
| `test_no_bare_create_task` | A discarded `asyncio.create_task(...)` result. The loop holds only a weak reference, so the task can be garbage-collected mid-run — silently, with no traceback. Use `kazma_core.background.spawn_background`. |
| `test_every_registered_tool_has_a_tier` | A registered tool with no entry in `TOOL_TIERS`. HITL default-denies what it cannot classify, so an untiered tool would start prompting on a read. |
| `test_no_unfenced_web_tool_output` | A web tool returning remote-authored text without an untrusted fence. |

The allowlist in `test_no_blocking_db_driver_in_async` carries a reason string
per exemption — add one only with an explanation of why the call cannot be
offloaded.

## Regression suite for security findings

`tests/test_audit_2026_08_29_regressions.py` holds one behavioural test per
audit finding that had an exploitable behaviour — each written to fail
against the pre-fix code. If you touch the auth middleware, the settings
mask, `shell_exec`, the HITL gate, or tenant scoping, run it:

```bash
pytest tests/test_audit_2026_08_29_regressions.py -q
```

## Testing modules that became packages

Several former god modules are now packages behind unchanged facades
(`routes_direct`, `sse_chat`, `i18n`, `tool_builtins`). Two consequences for
tests:

- **Source-grep assertions** must use `tests/_module_source.py`.
  `module_source(path)` reads a `.py` file *or* concatenates a whole package,
  and `module_exists(path)` is the matching guard — a plain `path.exists()`
  returns `False` for a module that became a package, which silently turns an
  assertion into a no-op.
- **Monkeypatching moved with the code.** Patch a seam where it is *defined*
  (e.g. `kazma_ui.sse_chat._helpers`), not on the package facade — a
  `from X import y` binding in another submodule will not see a patch applied
  to the package namespace.
