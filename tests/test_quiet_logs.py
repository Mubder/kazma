"""Quiet logs (resilience plan G1): a recorded live day, replayed against the code.

The live install's log of 2026-11-05 held 32,090 lines: 12,459 "Batch set"
lines and 11,200 access lines of polls, with the 100 that mattered buried
among them. ``tests/fixtures/log_noise/live_day.json`` records that day's
line shapes and counts (no content). Each recorded line is asked of today's
code -- the access filter for uvicorn's lines, the level of the call that
writes an app line -- and no routine line may still be written more than
:data:`PER_HOUR` times an hour, nor the day's total pass :data:`PER_DAY`.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path

import pytest

from kazma_core.observability.log_noise import QuietPolls

ROOT = Path(__file__).resolve().parents[1]
DAY = json.loads((ROOT / "tests/fixtures/log_noise/live_day.json").read_text(encoding="utf-8"))
#: No routine line more often than this an hour (one every two minutes).
PER_HOUR = 30
#: The recorded day's lines still written, at most.
PER_DAY = 8000

#: Where each recorded app line is written: logger -> module file.
_MODULES = {
    "kazma_core.config_store": "kazma-core/kazma_core/config_store.py",
    "kazma_core.x_api.mentions_fire": "kazma-core/kazma_core/x_api/mentions_fire.py",
    "kazma_core.llm_provider": "kazma-core/kazma_core/llm_provider.py",
    "kazma_core.agent.graph_tool_worker": "kazma-core/kazma_core/agent/graph_tool_worker.py",
    "kazma_ui.turn_runtime": "kazma-ui/kazma_ui/turn_runtime.py",
    "kazma_core.agent.graph_supervisor": "kazma-core/kazma_core/agent/graph_supervisor.py",
    "kazma_core.tracing": "kazma-core/kazma_core/tracing/__init__.py",
}


_PLACEHOLDER = r"%[-#0 +]*\d*(?:\.\d+)?[sdrfix]"


def _calls(module: str) -> list[tuple[str, str]]:
    """(method, literal text before the first placeholder, digits as N) of
    every logger call in *module* with a literal format."""
    out = []
    for node in ast.walk(ast.parse((ROOT / module).read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("debug", "info", "warning", "error", "exception", "critical")
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            head = re.split(_PLACEHOLDER, node.args[0].value)[0]
            out.append((node.func.attr, re.sub(r"\d+", "N", head)))
    return out


def _shape(entry: dict) -> str:
    return re.sub(r"N+", "N", entry["line"].replace("�", "—"))


def _writers(entry: dict) -> list[str]:
    """The methods of the calls that write this recorded app line."""
    shape = _shape(entry)
    return [method for method, head in _calls(_MODULES[entry["logger"]])
            if len(head) >= 10 and shape.startswith(head)]


def _written_at_info(entry: dict, *, filters: bool = True, recorded_levels: bool = False) -> bool:
    """Whether today's code writes this recorded line at INFO or above."""
    if entry["logger"] == "uvicorn.access":
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                                   '%s - "%s %s HTTP/%s" %d',
                                   ("127.0.0.1:5000", entry["method"], entry["path"], "1.1", entry["status"]), None)
        return not filters or QuietPolls().filter(record)
    return recorded_levels or any(method != "debug" for method in _writers(entry))


def _noisiest(**kw) -> list[tuple[int, str]]:
    kept = [(e["count"], e.get("path") or e.get("line")) for e in DAY["lines"] if _written_at_info(e, **kw)]
    return sorted(kept, reverse=True)


def test_no_routine_line_is_written_every_two_minutes():
    loud = [(n, line) for n, line in _noisiest()[:10] if n / 24 > PER_HOUR]
    assert loud == [], f"still written more than {PER_HOUR} times an hour: {loud}"
    assert sum(n for n, _ in _noisiest()) <= PER_DAY


def test_negative_control_the_recorded_day_without_the_change_fails():
    loud = [(n, line) for n, line in _noisiest(filters=False, recorded_levels=True)[:10] if n / 24 > PER_HOUR]
    assert ("Batch set: N keys updated atomically" in {line for _, line in loud}
            and "/api/alerts/recent" in {line for _, line in loud})


def test_every_recorded_app_line_is_found_in_its_module():
    for entry in DAY["lines"]:
        if entry["logger"] != "uvicorn.access":
            assert entry["logger"] in _MODULES, entry
            assert _writers(entry), f"no call writes {entry['line']!r}: the fixture is stale"


@pytest.mark.parametrize("path, status, kept", [
    ("/api/alerts/recent", 200, False),
    ("/api/alerts/recent?limit=5", 200, False),
    ("/health/ready", 503, True),          # an error is always kept
    ("/api/chat/sessions/abc/status", 200, False),
    ("/api/chat/sessions/abc/messages", 200, True),  # a page load, not a timer
    ("/api/chat/stream", 200, True),
])
def test_the_filter_drops_only_answered_polls(path, status, kept):
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:5000", "GET", path, "1.1", status), None)
    assert QuietPolls().filter(record) is kept


def test_the_filter_is_installed_on_the_access_logger():
    import inspect

    from kazma_core import logging_config

    assert "QuietPolls()" in inspect.getsource(logging_config.setup_logging)
