"""A memory task that fails says why (2026-11-20).

The Running work card showed "Memory task: failed -- native_pg_offsite" with
nothing more: a handler that answered ``False`` was recorded as "handler
returned False", while the cause (the local restic repository was missing)
was one WARNING in kazma.log. A handler now fails with its reason,
``return failed_because("...")``, and the task's ``error_log`` -- which the
Running work card, the Dashboard's incident and the Memory page's queue
show -- holds it.
"""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

from kazma_core.memory import task_queue as tq

from tests.test_memory_queue_retries import _rows, _run_worker, mem  # noqa: F401 -- the fixture

ROOT = Path(__file__).resolve().parents[1]


def _dead(mem, handler) -> dict:
    mem.mp.setattr(tq, "_retry_delay", lambda attempts: 0.0)
    mem.mp.setattr("kazma_core.observability.ops_alerts.alert", lambda *a, **k: True)
    mem.mp.setitem(tq._HANDLERS, "probe_reason", handler)
    assert tq.enqueue_task("probe_reason", {})
    _run_worker(1.0, max_concurrency=1)
    return _rows(mem)[0]


def test_a_failed_task_keeps_its_reason(mem):
    async def down(payload):
        return tq.failed_because("The Postgres dump was not copied into: local.")

    row = _dead(mem, down)
    assert row["status"] == "failed"
    assert row["error_log"] and "was not copied into: local" in row["error_log"]


def test_a_reason_given_in_a_worker_thread_is_kept(mem):
    async def down(payload):
        return await asyncio.to_thread(tq.failed_because, "said in a thread")

    assert "said in a thread" in (_dead(mem, down)["error_log"] or "")


def test_a_reason_carries_no_secret(mem):
    async def down(payload):
        return tq.failed_because("restic said: postgresql://kazma:hunter2-secret@db:5432/kazma refused")

    assert "hunter2-secret" not in (_dead(mem, down)["error_log"] or "")


def test_control_a_bare_false_says_nothing_useful(mem):
    """What live showed."""
    async def down(payload):
        return False

    assert "handler returned False" in (_dead(mem, down)["error_log"] or "")


# ── the class: no handler answers a bare False ────────────────────────────


def _bare_false_in_handlers(source: str) -> list[str]:
    """``name:line`` of each ``return False`` in a function that a
    ``register_handler("...", <name>)`` call in *source* registers."""
    handlers = set(re.findall(r"register_handler\(\s*[\"'][a-z_]+[\"']\s*,\s*([A-Za-z_][A-Za-z_0-9]*)", source))
    out = []
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in handlers:
            out += [f"{fn.name}:{node.lineno}" for node in ast.walk(fn)
                    if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
                    and node.value.value is False]
    return out


def test_no_queue_handler_fails_without_a_reason():
    found = []
    for path in (ROOT / "kazma-core" / "kazma_core").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "register_handler(" in source:
            found += [f"{path.name}:{hit}" for hit in _bare_false_in_handlers(source)]
    assert found == [], ("a memory-queue handler answers a bare False: return failed_because(...) "
                         f"with the reason instead: {found}")


def test_the_gate_finds_a_planted_bare_false():
    planted = (
        "async def _handle_probe(payload):\n"
        "    if not payload:\n"
        "        return False\n"
        "    return True\n"
        "register_handler('probe', _handle_probe)\n"
    )
    assert _bare_false_in_handlers(planted) == ["_handle_probe:3"]
