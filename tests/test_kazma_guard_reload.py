"""kazma_guard --reload is the operator deploy path (not a hand-kill)."""

from __future__ import annotations

import ast
from pathlib import Path

import importlib.util

_GUARD = Path("scripts/service/kazma_guard.py")
_spec = importlib.util.spec_from_file_location("kazma_guard_under_test", _GUARD)
_guard = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_guard)
parse_tasklist_image = _guard.parse_tasklist_image
_local_port_of = _guard._local_port_of


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
    assert "START_TIMEOUT_S" in body
    assert "_kick_os_supervisor" in body
    # Must not give up at 180s — cold start is 3–5 minutes.
    assert "min(START_TIMEOUT_S, 180" not in body
    # Flag must be written before the kill, or the running guard climbs
    # the crash backoff (live 2026-08-31: 300s of connection-refused).
    assert body.index("request_reload") < body.index("_stop_recorded_child")


def test_operator_reload_flag_is_consumed_once(tmp_path, monkeypatch) -> None:
    marker = tmp_path / "guard.reload"
    monkeypatch.setenv("KAZMA_GUARD_RELOAD_FILE", str(marker))
    assert _guard.reload_requested() is False
    assert _guard.consume_reload_request() is False
    _guard.request_reload()
    assert marker.is_file()
    assert _guard.reload_requested() is True
    assert _guard.consume_reload_request() is True
    assert _guard.reload_requested() is False
    assert _guard.consume_reload_request() is False


def test_run_loop_skips_crash_backoff_on_operator_reload() -> None:
    src = Path("scripts/service/kazma_guard.py").read_text(encoding="utf-8")
    body = src.split("def run(self)", 1)[1].split("def _await_resume", 1)[0]
    assert "consume_reload_request" in body
    assert "guard.operator_reload" in body
    # Negative control: a real crash still increments the ladder.
    assert "self.restarts += 1" in body
    assert body.index("consume_reload_request") < body.index("self.restarts += 1")


def test_tasklist_info_line_is_not_a_process_name() -> None:
    assert parse_tasklist_image("INFO: No tasks are running which match the specified criteria.") == ""
    assert parse_tasklist_image("") == ""
    csv = '"python.exe","95052","Console","1","123,456 K"\n'
    assert parse_tasklist_image(csv).lower() == "python.exe"
    # Negative control: the old split()[0] bug.
    bogus = "INFO: No tasks are running which match the specified criteria."
    assert bogus.split()[0].lower() == "info:"
    assert parse_tasklist_image(bogus) != bogus.split()[0]


def test_local_port_does_not_match_superset() -> None:
    assert _local_port_of("127.0.0.1:9090") == "9090"
    assert _local_port_of("[::]:9090") == "9090"
    assert _local_port_of("127.0.0.1:19090") == "19090"


def test_guard_cli_has_install_alias() -> None:
    src = Path("scripts/service/kazma_guard.py").read_text(encoding="utf-8")
    assert "--install" in src
    assert "install_service.py" in src
    body = src.split("def _cmd_install()", 1)[1].split("\ndef ", 1)[0]
    assert "install_service.py" in body
