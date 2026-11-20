"""Kazma's log readers count a marker only where its writer puts it.

The daily digest, the weekly report (``firing_ledger``) and its targets
(``weekly_targets``) count lines Kazma writes: a finished turn, a backup, an
alert, a guard restart. Each matched its markers ANYWHERE -- in the message
(``.search``, ``in``) or in the whole raw line -- and a message carries text
Kazma did not write: an unauthenticated caller's session id, a file name, a
tool's error. A caller who put ``[turn] Turn finished: ... failed=yes`` or
``"event": "guard.restarting"`` into one had it counted: a failed turn, a
backup that never ran, a restart (SonarCloud S5145, 2026-11-16; the JSON log,
Kazma's default format, escapes line breaks, so this was open there whatever
the writer did).

Now one module reads a line into its record (``log_lines``), an app marker
counts at the START of the message (``opens_with``) and a guard event only in
a guard record's ``event`` field. Here every marker the readers count is
planted after a prefix, in a JSON record and in a plain one: nothing counts.
The same markers at the start of a message, and real guard records, count.
The negative control is the readers of 41009f5 (before the change), which
count the planted records. The static half holds the reader modules to it.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
from kazma_core.observability import daily_digest
from kazma_core.observability import firing_ledger as fl
from kazma_core.observability.weekly_targets import measure_targets

from tests._emitted_lines import REPO, emitted_app_lines, product_roots

#: The readers before the change (their last change was 842326b5).
BEFORE = "41009f5607948b4bacb80bb1e12e18a84f101a24"
OBSERVABILITY = "kazma-core/kazma_core/observability"
#: What a caller controls: the route's own words, then the caller's text.
PREFIX = "[WS-Chat] Unauthenticated connection attempt for session="

#: The digest's and the weekly targets' markers, each as its writer puts it.
_READER_MARKERS = (
    "[turn] Turn finished: platform=web key=planted-turn failed=yes",
    "[cron] outcome job=planted-job outcome=delivery_undeliverable",
    "[HITL] ran without asking: tool=send_file thread=planted-thread by=yolo",
    "[SECURITY] YOLO ENABLED thread=planted-thread actor=web:admin:x ttl=600s",
    "[ops_alert] planted.key | a planted alert",
    "[memory] operator action=POST_/api/memory/v2/episodes/{episode_id}/forget actor=web:admin:x "
    "tenant=default ids=e1 status=200",
    "[universal-backup] complete: 26 DBs",
    "[shutdown] the stop ran over its 50 s budget: 61.2 s; slowest: x 30 s",
    "[SSE] Backfilled unanswered turn from checkpoint for planted-thread",
    "[Supervisor] LLM call attempt 1/3 failed",
    "[MCP] 2 server(s) failed to connect",
)
#: Each guard signature's event, and the field it needs.
_GUARD_RECORDS = (
    ("guard.restarting", {"reason": "unhealthy (probe error: timed out)"}),
    ("health.recovered", {}),
    ("health.dependency_down", {}),
    ("health.probe_unrunnable", {}),
    ("guard.crash_loop", {}),
    ("orphan.reaped", {}),
    ("maintenance.active", {}),
    ("child.foreign_server_holds_port", {}),
    ("port.stale_before_spawn", {}),
)


def _quoted_guard_event(event: str, fields: dict) -> str:
    """A guard record's text as a message could quote it."""
    return json.dumps({"event": event, **fields})[1:-1]


def _ledger_samples() -> list[str]:
    """An emitted line each app signature reads, from the source."""
    lines = emitted_app_lines(product_roots())
    samples = []
    for sig in fl.FIRING_SIGNATURES:
        if sig.source == fl.APP:
            samples.append(next(line for line in lines if sig.in_message(line)))
    return samples


@pytest.fixture(scope="module")
def markers() -> list[str]:
    return [*_READER_MARKERS, *_ledger_samples(),
            *(_quoted_guard_event(event, fields) for event, fields in _GUARD_RECORDS)]


def _json_line(message: str, ago: float = 60.0) -> str:
    stamp = datetime.fromtimestamp(time.time() - ago, UTC).isoformat()
    return json.dumps({"timestamp": stamp, "level": "WARNING", "logger": "kazma_ui.routes.ws_chat",
                       "message": message})


def _plain_line(message: str, ago: float = 60.0) -> str:
    stamp = datetime.fromtimestamp(time.time() - ago).strftime("%Y-%m-%d %H:%M:%S")
    return f"{stamp} [WARNING] kazma_ui.routes.ws_chat: {message}"


def _guard_line(event: str, fields: dict, ago: float = 60.0) -> str:
    stamp = datetime.fromtimestamp(time.time() - ago, UTC).isoformat()
    return json.dumps({"ts": stamp, "level": "warn", "event": event, **fields})


class _Logs:
    """An install's logs: the JSON app log, a plain rotated sibling, the guard's."""

    def __init__(self, tmp_path: Path, monkeypatch, messages: list[str], guard: list[str]):
        self.app = tmp_path / "kazma.log"
        self.plain = tmp_path / "kazma.log.2026-11-16"
        self.guard = tmp_path / "guard.log"
        self.app.write_text("\n".join(_json_line(m) for m in messages) + "\n", encoding="utf-8")
        self.plain.write_text("\n".join(_plain_line(m) for m in messages) + "\n", encoding="utf-8")
        self.guard.write_text("\n".join(guard) + ("\n" if guard else ""), encoding="utf-8")
        monkeypatch.setenv("KAZMA_LOG_FILE", str(self.app))
        monkeypatch.setenv("KAZMA_GUARD_LOG", str(self.guard))

    def app_lines(self) -> list[str]:
        return [*self.app.read_text(encoding="utf-8").splitlines(),
                *self.plain.read_text(encoding="utf-8").splitlines()]

    def guard_lines(self) -> list[str]:
        return self.guard.read_text(encoding="utf-8").splitlines()


def _digest(module) -> tuple[Counter, dict, dict, dict, Counter]:
    unread: Counter = Counter()
    counts, turns, alerts, chats = module._scan_app_log(time.time() - 3600, unread)
    return counts, turns, alerts, chats, unread


def _targets(module_measure, logs: _Logs) -> dict:
    targets = module_measure(logs.app_lines(), logs.guard_lines(), now=time.time())
    return {t.name: t.value for t in targets}


def _ledger(module, logs: _Logs, monkeypatch) -> tuple[dict[str, int], int]:
    monkeypatch.setattr(module, "_log_paths", lambda: [logs.app, logs.plain, logs.guard])
    monkeypatch.setattr(module, "stall_blockers", lambda cutoff: {})
    report = module.scan_log(hours=1)
    return {e.mechanism: e.count for e in report.entries}, report.suppressed_alerts


# -- planted after a prefix: nothing counts ------------------------------------


def test_a_marker_after_a_prefix_counts_nothing(tmp_path, monkeypatch, markers):
    planted = [PREFIX + marker for marker in markers]
    # A guard record whose fields quote another event is not that event.
    guard = [_guard_line("health.failed", {"detail": _quoted_guard_event(*_GUARD_RECORDS[0])})]
    logs = _Logs(tmp_path, monkeypatch, planted, guard)

    counts, turns, alerts, chats, unread = _digest(daily_digest)
    assert (dict(counts), turns, alerts, chats, dict(unread)) == ({}, {}, {}, {}, {})

    values = _targets(measure_targets, logs)
    assert values["Turns that succeed"] is None
    assert values["Backups fresh"] == 0
    assert values["Stops over budget"] == 0
    assert values["Alerts per week"] == 0
    assert values["Restarts not asked for"] == 0

    ledger, suppressed = _ledger(fl, logs, monkeypatch)
    assert {name: n for name, n in ledger.items() if n} == {}
    assert suppressed == 0


# -- the same markers at the start, and real guard records: they count ---------


def test_the_same_markers_at_the_start_count(tmp_path, monkeypatch, markers):
    guard = [_guard_line(event, fields) for event, fields in _GUARD_RECORDS]
    logs = _Logs(tmp_path, monkeypatch, list(markers), guard)

    counts, turns, alerts, chats, unread = _digest(daily_digest)
    # (platform, failed, the person it ran for: none named on this line)
    assert turns == {"planted-turn": ("web", True, None)}
    assert counts["scheduled messages not delivered"] == 2  # the JSON and the plain record
    assert counts["memory forgets"] == 2
    assert {"answers recovered from checkpoint", "LLM retries", "MCP connection failures"} <= set(counts)
    assert alerts["planted.key"]["sent"] == 2
    assert chats["planted-thread"]["calls"] == {"yolo": Counter({"send_file": 2})}
    assert dict(unread) == {}

    values = _targets(measure_targets, logs)
    assert values["Turns that succeed"] == 0.0
    assert values["Backups fresh"] == 1
    assert values["Stops over budget"] == 2
    assert values["Alerts per week"] >= 2
    assert values["Restarts not asked for"] == 1

    ledger, _suppressed = _ledger(fl, logs, monkeypatch)
    assert [name for name, n in ledger.items() if not n] == []


# -- the negative control: the readers before the change count them -------------


def _source_before(name: str) -> str:
    return subprocess.run(["git", "show", f"{BEFORE}:{OBSERVABILITY}/{name}.py"], cwd=REPO,
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


def _load(name: str, source: str):
    module = type(sys)(name)
    sys.modules[name] = module
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)  # noqa: S102 -- the repo's own file
    return module


@pytest.fixture
def readers_before(monkeypatch):
    """The digest, the targets and the ledger as they were, each reading the
    finished-turn line as it was."""
    names = ("_before_turn_line", "_before_daily_digest", "_before_weekly_targets", "_before_firing_ledger")
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    _load("_before_turn_line", _source_before("turn_line"))
    rewired = {
        name: _source_before(name).replace("kazma_core.observability.turn_line", "_before_turn_line")
        for name in ("daily_digest", "weekly_targets", "firing_ledger")
    }
    loaded = {name: _load(f"_before_{name}", source) for name, source in rewired.items()}
    yield loaded
    for name in names:
        sys.modules.pop(name, None)


def test_control_the_readers_before_count_the_planted_records(tmp_path, monkeypatch, markers,
                                                             readers_before):
    planted = [PREFIX + marker for marker in markers]
    logs = _Logs(tmp_path, monkeypatch, planted, [])

    counts, turns, _alerts, chats, _unread = _digest(readers_before["daily_digest"])
    assert turns, "the old digest read no planted turn"
    assert counts["scheduled messages not delivered"] >= 1
    assert chats, "the old digest read no planted call"

    values = _targets(readers_before["weekly_targets"].measure_targets, logs)
    assert values["Backups fresh"] >= 1 and values["Turns that succeed"] is not None

    ledger, _suppressed = _ledger(readers_before["firing_ledger"], logs, monkeypatch)
    assert ledger["universal backup"] >= 1
    # A guard event quoted in an app message was a restart.
    assert ledger["guard restart"] >= 1


# -- the static half: no reader matches a message anywhere ----------------------


#: The reader modules.
READERS = ("daily_digest", "weekly_targets", "firing_ledger", "turn_line", "log_lines")
#: Functions that search text that is not a log message, with why.
NOT_A_MESSAGE = {
    ("firing_ledger", "_loop_stack"): "a stall dump's thread stacks (.kazma/stall-*.txt)",
    ("firing_ledger", "_blocker"): "a stall dump's frame lines and their paths",
}
#: Names a message is held in, in these modules.
_MESSAGE_NAMES = {"message", "messages", "msg", "msgs", "text", "m"}
_SEARCHES = {"search", "findall", "finditer"}


def _names_a_message(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in _MESSAGE_NAMES:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "message":
            return True
        if isinstance(sub, ast.Constant) and sub.value == "message":
            return True  # rec.get("message")
    return False


def _matches_anywhere(module: str, source: str) -> list[str]:
    """Each place *source* matches text anywhere: a ``.search`` (or
    ``findall``/``finditer``) call, or ``in`` on a message."""
    findings: list[str] = []

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = (*scope, node.name)
        declared = any((module, name) in NOT_A_MESSAGE for name in scope)
        if not declared and isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in _SEARCHES:
                findings.append(f"{module}:{node.lineno} .{node.func.attr}(")
        if not declared and isinstance(node, ast.Compare):
            for op, right in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and _names_a_message(right):
                    findings.append(f"{module}:{node.lineno} in {ast.unparse(right)}")
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    walk(ast.parse(source), ())
    return findings


def test_no_reader_matches_a_message_anywhere():
    found = []
    for name in READERS:
        path = REPO / OBSERVABILITY / f"{name}.py"
        found += _matches_anywhere(name, path.read_text(encoding="utf-8"))
    assert found == [], "match a marker at the start of the message (log_lines.opens_with)"


def test_control_the_static_half_finds_the_readers_before():
    for name in ("daily_digest", "weekly_targets", "firing_ledger", "turn_line"):
        assert _matches_anywhere(name, _source_before(name)), name
    planted = (
        'def counted(message):\n    return "[turn] Turn finished:" in message\n'
        'def searched(rec):\n    return _LINE.search(str(rec.get("message", "")))\n'
    )
    assert len(_matches_anywhere("planted", planted)) == 2


def test_every_declared_function_exists():
    for module, function in NOT_A_MESSAGE:
        tree = ast.parse((REPO / OBSERVABILITY / f"{module}.py").read_text(encoding="utf-8"))
        assert any(isinstance(n, ast.FunctionDef) and n.name == function for n in ast.walk(tree)), function
