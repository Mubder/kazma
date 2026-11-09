"""Every "it works again" note survives a restart of Kazma.

A mechanism that pages an outage and promises a note when it ends used to
keep the promise in memory: a reload during the outage started a process
that knew nothing of the page, and the owner never heard that it was over
(the 2026-11-05 digest: "Research/crawl monitoring unavailable", no
recovery). Each such mechanism now marks the page in
:mod:`kazma_core.observability.outage_pages` and settles the mark after its
recovery note, or keeps its outage state in a store of its own.

The gate reads every product module for a recovery note -- an alert key
ending ``_recovered`` or ``.resolved``, or a text ending "recovered" handed
to a call that is not a log line -- and requires the module to use the
outage marks, or to be declared below with where its state lives.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_NOTE = re.compile(r"(?:^|[_. ])recovered$|\.resolved$")
_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
_MARKS = "kazma_core.observability.outage_pages"

#: Modules with a recovery note whose outage state is durable without the
#: marks, each with where it lives.
DECLARED: dict[str, str] = {
    "kazma-core/kazma_core/web_acquire/canary.py":
        "the canary's last outcome (failing_since, reason) is in the settings store "
        "(acquisition.canary.state); the next run after a restart reads it",
    "kazma-core/kazma_core/runs/notify.py":
        "a run incident's recovery note follows its row in run_incidents.db, which "
        "outlives a restart (runs/incidents.py)",
    "kazma-core/kazma_core/observability/alert_catalog.py":
        "the alert catalog only names each alert and the recovery that closes it "
        "(a key ending .resolved); it sends nothing, and each sender keeps its own "
        "outage state",
    "kazma-core/kazma_core/observability/recent_alerts.py":
        "Settings -> Alerts' recent alerts list Kazma Watch's past recovery notes "
        "(watch.recovered) from its /v1/summary; it sends nothing, and the incidents "
        "live in the observer's D1",
}


def _product_modules() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted({ROOT / p for p in out})


def _text_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def _is_log_call(call: ast.Call) -> bool:
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr in _LOG_METHODS


def recovery_notes(source: str) -> list[int]:
    """Lines of the calls that hand on a recovery note."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or _is_log_call(node):
            continue
        for arg in node.args:
            text = _text_of(arg)
            if text and _NOTE.search(text.strip()):
                lines.append(node.lineno)
                break
    return lines


def uses_the_marks(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == _MARKS:
            return True
        if isinstance(node, ast.Import) and any(a.name == _MARKS for a in node.names):
            return True
    return False


def findings(modules: list[Path]) -> tuple[list[str], set[str]]:
    missing, seen = [], set()
    for path in modules:
        rel = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        notes = recovery_notes(source)
        if not notes:
            continue
        seen.add(rel)
        if rel in DECLARED or uses_the_marks(source):
            continue
        missing.append(f"{rel}:{notes[0]}")
    return missing, seen


def test_every_recovery_note_survives_a_restart() -> None:
    missing, _ = findings(_product_modules())
    assert not missing, (
        "These modules send an 'it works again' note but keep the outage only in "
        "memory, so a restart during the outage loses the note. Mark the page with "
        f"{_MARKS} (note_outage_page / settle_outage_page) or declare where the state "
        "lives in DECLARED:\n  " + "\n  ".join(missing))


def test_the_gate_sees_the_mechanisms_it_was_built_for() -> None:
    _, seen = findings(_product_modules())
    for rel in ("kazma-core/kazma_core/web_acquire/monitor.py",
                "kazma-core/kazma_core/runs/supervisor.py",
                "kazma-core/kazma_core/mcp/reconnect.py",
                "kazma-core/kazma_core/x_api/health.py",
                "kazma-core/kazma_core/observability/model_fallback.py"):
        assert rel in seen, f"the gate no longer sees the recovery note in {rel}"


def test_every_declaration_still_names_a_recovery_note() -> None:
    _, seen = findings(_product_modules())
    stale = sorted(set(DECLARED) - seen)
    assert not stale, f"declared modules with no recovery note any more: {stale}"


@pytest.mark.parametrize("planted, caught", [
    ('from kazma_core.observability.ops_alerts import alert\n'
     'def back():\n    alert("thing.worker_recovered", "Works again", "ok")\n', True),
    ('def back(what):\n    card("Ops", "success", f"X {what} recovered", "ok")\n', True),
    ('def back(key):\n    alert(f"llm.fallback.{key}.resolved", "ok", "ok")\n', True),
    ('import logging\nlogger = logging.getLogger(__name__)\n'
     'def later():\n    logger.error("the fire will be recovered")\n', False),
    ('from kazma_core.observability.outage_pages import settle_outage_page\n'
     'def back():\n    alert("thing.worker_recovered", "Works again", "ok")\n', False),
])
def test_a_planted_module_is_judged(tmp_path: Path, monkeypatch, planted: str, caught: bool) -> None:
    """Negative control: a module keeping its outage in memory fails the gate."""
    pkg = tmp_path / "kazma-core" / "kazma_core"
    pkg.mkdir(parents=True)
    module = pkg / "planted.py"
    module.write_text(planted, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    missing, _ = findings([module])
    assert bool(missing) is caught
