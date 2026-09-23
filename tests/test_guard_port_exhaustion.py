"""The guard names who holds the ports when a probe cannot get one.

169 of 257 failed health probes in the week to 2026-09-23 were WinError 10048
-- the machine had no free local port -- and each was logged as a bare
"unreachable". A snapshot taken hours later showed nothing unusual, because
the burst was gone. The only moment to look is the failure itself.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_GUARD = Path(__file__).resolve().parents[1] / "scripts" / "service" / "kazma_guard.py"
_spec = importlib.util.spec_from_file_location("kazma_guard_port_exhaustion", _GUARD)
guard = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = guard
_spec.loader.exec_module(guard)

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1200
  TCP    127.0.0.1:9090         0.0.0.0:0              LISTENING       19280
  TCP    127.0.0.1:50001        127.0.0.1:9090         TIME_WAIT       0
  TCP    127.0.0.1:50002        127.0.0.1:9090         TIME_WAIT       0
  TCP    192.168.50.10:50003    172.66.0.227:443       ESTABLISHED     19280
  TCP    0.0.0.0:50100          0.0.0.0:0              BOUND           44328
  TCP    0.0.0.0:50101          0.0.0.0:0              BOUND           44328
  TCP    0.0.0.0:50102          0.0.0.0:0              BOUND           44328
"""


def test_snapshot_counts_states_and_names_the_heaviest_holder():
    snap = guard.tcp_snapshot_from_netstat(NETSTAT, {"44328": "com.docker.backend", "19280": "python.exe"})
    assert snap["total"] == 8
    assert snap["states"] == {"BOUND": 3, "LISTENING": 2, "TIME_WAIT": 2, "ESTABLISHED": 1}
    assert snap["top_owners"][0] == "com.docker.backend (pid 44328): 3"
    assert all("pid 1200" not in o for o in snap["top_owners"]), "listeners are not port consumers"


def test_only_port_exhaustion_triggers_it_and_it_is_rate_limited(monkeypatch):
    logged: list[tuple] = []
    monkeypatch.setattr(guard.os, "name", "nt")
    monkeypatch.setattr(guard, "_last_tcp_snapshot", 0.0)
    monkeypatch.setattr(guard, "_process_names", lambda: {})
    monkeypatch.setattr(
        guard.subprocess, "run",
        lambda *a, **k: type("R", (), {"stdout": NETSTAT})(),
    )
    log = lambda level, event, **f: logged.append((event, f))  # noqa: E731

    assert not guard.maybe_log_tcp_snapshot(log, "probe error: timed out", now=1000.0)
    assert guard.maybe_log_tcp_snapshot(
        log, "unreachable: [WinError 10048] Only one usage of each socket address", now=1000.0
    )
    assert not guard.maybe_log_tcp_snapshot(log, "[WinError 10048]", now=1000.0 + 60)
    assert guard.maybe_log_tcp_snapshot(log, "[WinError 10055]", now=1000.0 + 601)
    assert [e for e, _ in logged] == ["health.port_exhaustion"] * 2
    assert logged[0][1]["states"]["BOUND"] == 3


def test_the_supervisor_calls_it_on_every_failed_probe():
    import ast

    src = _GUARD.read_text(encoding="utf-8")
    sup = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "_supervise")
    calls = {
        getattr(n.func, "id", getattr(n.func, "attr", ""))
        for n in ast.walk(sup) if isinstance(n, ast.Call)
    }
    assert "maybe_log_tcp_snapshot" in calls
