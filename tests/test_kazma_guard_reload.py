"""kazma_guard --reload is the operator deploy path (not a hand-kill)."""

from __future__ import annotations

import ast
from pathlib import Path


def test_guard_cli_has_reload() -> None:
    src = Path("scripts/service/kazma_guard.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_cmd_reload" in names
    assert "--reload" in src
    assert "clear_pause" in src
    # Negative control: the old hand-kill of uvicorn is not this command.
    assert "uvicorn*kazma" not in src


def test_reload_clears_pause_and_reaps_port() -> None:
    src = Path("scripts/service/kazma_guard.py").read_text(encoding="utf-8")
    body = src.split("def _cmd_reload()", 1)[1].split("\ndef ", 1)[0]
    assert "clear_pause" in body
    assert "reap_port_holder" in body
    assert "_stop_recorded_child" in body
    assert "/health" in body or "_live_commit" in body
