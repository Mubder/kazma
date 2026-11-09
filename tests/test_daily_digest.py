"""The daily digest: making silence mean something.

Incident alerts tell you when something breaks. They cannot distinguish "a
quiet day" from "the alerting itself is broken" -- and after an audit whose
central finding was silent failure, that distinction is the whole point. If
the only signal is failure, an agent that has stopped looks exactly like an
agent with nothing to report.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from kazma_core.observability import daily_digest, ops_alerts

from tests._emitted_lines import emitted_app_lines, guard_events, product_roots


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ops_alerts.reset_alert_state()
    monkeypatch.delenv("KAZMA_DAILY_DIGEST", raising=False)
    yield
    ops_alerts.reset_alert_state()


def _guard_log(tmp_path, events, age_hours=1.0):
    path = tmp_path / "guard.log"
    ts = (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat()
    path.write_text(
        "\n".join(json.dumps({"ts": ts, "level": "info", "event": e})
                  for e in events),
        encoding="utf-8",
    )
    return path


def _app_log(tmp_path, messages, age_hours=1.0, name="kazma.log"):
    """*messages*: a message (logged at INFO) or a ``(level, message)`` pair."""
    path = tmp_path / name
    ts = (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat()
    records = [m if isinstance(m, tuple) else ("INFO", m) for m in messages]
    path.write_text(
        "\n".join(json.dumps({"timestamp": ts, "level": level, "message": m})
                  for level, m in records),
        encoding="utf-8",
    )
    return path


def _plain_app_log(tmp_path, records, age_hours=1.0, name="kazma.log"):
    """The same lines in the file formatter's plain shape (logging_config
    "standard", local time): what a server wrote when it set up logging
    while the settings store could not answer (2026-10-30/31)."""
    path = tmp_path / name
    when = (datetime.now() - timedelta(hours=age_hours)).strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(
        "\n".join(f"{when} [{level}] {logger}: {message}"
                  for level, logger, message in records) + "\n",
        encoding="utf-8",
    )
    return path


def _alert_lines_of(caplog) -> list[tuple[str, str]]:
    """The ``[ops_alert]`` lines alert() wrote, with the level it chose."""
    return [(r.levelname, r.getMessage()) for r in caplog.records
            if r.getMessage().startswith("[ops_alert] ")]


def _guard_log_at(tmp_path, events):
    """*events*: (seconds ago, event) pairs, in the guard's own line shape."""
    now = datetime.now(UTC)
    path = tmp_path / "guard.log"
    path.write_text(
        "\n".join(json.dumps({"ts": (now - timedelta(seconds=ago)).isoformat(),
                              "level": "info", "event": e})
                  for ago, e in events),
        encoding="utf-8",
    )
    return path


def _turn_line(key, platform="web", failed="no"):
    """The line close_turn writes for a finished turn (kazma_ui.turn_runtime)."""
    return f"[turn] Turn finished: platform={platform} key={key} failed={failed}"


# ── content ───────────────────────────────────────────────────────────


def test_a_quiet_day_says_so_explicitly(tmp_path, monkeypatch):
    """The single most important line: silence, stated positively.

    Without it the operator cannot tell a healthy quiet day from a dead
    agent, which is the failure this whole project exists to prevent.
    """
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [])))
    text = daily_digest.build_digest(hours=24)
    assert "No failures, no restarts, no alerts." in text


def test_turns_are_counted_from_every_transport_once(tmp_path, monkeypatch):
    """Turns were counted from "SSE turn complete", which only the web stream
    writes: Telegram, Discord and Slack turns never counted (2026-10-03). A
    turn closed twice -- the stream's end, then a late settle, or a close
    after a restart -- is one turn."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    lines = [_turn_line(f"w{i}") for i in range(3)]
    lines += [_turn_line(f"t{i}", platform="telegram") for i in range(2)]
    lines += [
        _turn_line("w0"),
        # The web stream's own line is not a second count of the same turn.
        "SSE turn complete: tokens=1 cost=$0.0000 duration=1ms content_len=1 interrupted=False",
    ]
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, lines)))
    assert "Turns completed: 5 (web 3, telegram 2)" in daily_digest.build_digest(hours=24)


def test_a_turn_that_ended_in_an_error_needs_attention(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(
        tmp_path, [_turn_line("a"), _turn_line("b", failed="yes")])))
    text = daily_digest.build_digest(hours=24)
    assert "Turns completed: 1 (web 1)" in text
    assert "turns that ended in an error: 1" in text.split("Needs attention:", 1)[1]


def test_the_rotated_log_of_the_window_is_read(tmp_path, monkeypatch):
    """The app log rotates at local midnight. The digest read the live file
    alone: sent at 05:17 UTC (08:17 in Kuwait) it saw eight hours of its 24
    and reported "Turns completed: 0" over a day with turns (2026-10-03)."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    live = _app_log(tmp_path, [_turn_line("today")], age_hours=1)
    _app_log(tmp_path, [_turn_line("yesterday-1"), _turn_line("yesterday-2")],
             age_hours=10, name="kazma.log.2026-10-02")
    monkeypatch.setenv("KAZMA_LOG_FILE", str(live))
    assert "Turns completed: 3 (web 3)" in daily_digest.build_digest(hours=24)


def test_a_rotated_log_last_written_before_the_window_is_not_opened(tmp_path):
    live = tmp_path / "kazma.log"
    live.write_text("", encoding="utf-8")
    recent = tmp_path / "kazma.log.2026-10-02"
    recent.write_text("", encoding="utf-8")
    old = tmp_path / "kazma.log.2026-09-01"
    old.write_text("", encoding="utf-8")
    week_ago = time.time() - 7 * 86400
    os.utime(old, (week_ago, week_ago))
    since = time.time() - 86400
    assert daily_digest._window_files(live, since) == [live, recent]


def test_maintenance_pauses_are_planned_work_not_a_problem(tmp_path, monkeypatch):
    """Two `kazma update` pauses of 41 s and 11 s, both ended, were sent as
    "Needs attention: maintenance pauses: 2" (2026-10-03). The server is
    stopped while a pause lasts, so the digest only ever sees ended ones."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log_at(tmp_path, [
        (5000, "maintenance.active"), (4959, "maintenance.resumed"),
        (3000, "guard.operator_reload"),
        (2000, "maintenance.active"), (1989, "maintenance.resumed"),
        (1000, "guard.operator_reload"),
    ])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [])))
    text = daily_digest.build_digest(hours=24)
    assert "Planned work:\n  reloads: 2\n  maintenance pauses: 2 (longest 41 s)" in text
    assert "Needs attention" not in text
    assert "No failures, no restarts, no alerts." in text


def test_a_pause_acknowledged_twice_is_one_pause(tmp_path, monkeypatch):
    """A guard that restarts during a pause logs maintenance.active again; a
    pause whose end is not in the log says so."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log_at(tmp_path, [
        (900, "maintenance.active"), (600, "maintenance.active"),
        (300, "maintenance.resumed"), (200, "maintenance.active"),
    ])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [])))
    text = daily_digest.build_digest(hours=24)
    assert "maintenance pauses: 2 (longest 10 min; 1 with no end in the log)" in text


# -- approvals: the registry, not log lines --------------------------------


@pytest.fixture()
def gates(tmp_path):
    from kazma_core.safety import hitl_gates

    hitl_gates.set_db_path_for_tests(str(tmp_path / "gates.db"))
    yield hitl_gates
    hitl_gates.set_db_path_for_tests(None)


def test_approvals_are_read_from_the_registry(tmp_path, monkeypatch, gates):
    """One registry row is one question (AGENTS §30). The digest counted
    "HITL interrupt" log lines, and the WebSocket wrote one more each time a
    page connected while a question waited."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(
        tmp_path, ["[WS-Chat] HITL interrupt emitted over WS on connect"] * 4)))
    row = gates.GateRow
    for i, decision in enumerate(["approve", "approve", "deny"]):
        gates.register_gate(row(gate_id=f"g{i}", thread_id=f"t{i}", tool="shell_exec"))
        gates.claim_gate(f"g{i}", decision, "owner")
        gates.settle_gate(f"g{i}")
    gates.register_gate(row(gate_id="err", thread_id="te", tool="file_write"))
    gates.claim_gate("err", "approve", "owner")
    gates.fail_gate("err", "resume raised")
    # The same pause under its second id is not a second question.
    gates.register_gate(row(gate_id="twin", thread_id="tw", tool="x_post"))
    gates.supersede_gate("twin", "g0")
    # Asked before the window.
    gates.register_gate(row(gate_id="old", thread_id="to", tool="shell_exec"))
    conn = sqlite3.connect(str(tmp_path / "gates.db"))
    try:
        with conn:
            conn.execute("UPDATE hitl_gates SET created_at = ? WHERE gate_id = 'old'",
                         (time.time() - 3 * 86400,))
    finally:
        conn.close()
    text = daily_digest.build_digest(hours=24)
    assert "approvals asked: 4 (approved 2, could not resume 1, denied 1)" in text
    assert "approvals that could not resume: 1" in text.split("Needs attention:", 1)[1]


def test_an_approval_nobody_answered_is_not_a_denial(tmp_path, monkeypatch, gates):
    """The approval-timeout watchdog settles a question nobody answered as
    ``deny`` (so the tool does not run), by the actor ``watchdog:timeout``.
    The 2026-11-01 digest said "approved 51, denied 3": two of the three
    were cards nobody saw in time."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [])))
    row = gates.GateRow
    for i, (decision, actor) in enumerate([
        ("approve", "web:admin"), ("deny", "web:admin"),
        ("deny", gates.TIMEOUT_ACTOR), ("deny", gates.TIMEOUT_ACTOR),
    ]):
        gates.register_gate(row(gate_id=f"g{i}", thread_id=f"t{i}", tool="file_write"))
        gates.claim_gate(f"g{i}", decision, actor)
        gates.settle_gate(f"g{i}")
    text = daily_digest.build_digest(hours=24)
    assert "approvals asked: 4 (expired unanswered 2, approved 1, denied 1)" in text
    assert gates.gate_outcomes_since(0) == {"approve": 1, "deny": 1, "timeout": 2}


def test_a_registry_that_is_off_or_unreadable_is_said(tmp_path, monkeypatch, gates):
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    assert "approvals: not counted (the approval registry is switched off)" in (
        daily_digest.build_digest(hours=24))
    monkeypatch.delenv("KAZMA_GATE_REGISTRY")
    gates.set_db_path_for_tests(str(tmp_path))  # a folder: SQLite cannot open it
    assert "approvals: not counted (the approval registry could not be read)" in (
        daily_digest.build_digest(hours=24))


# -- the line it counts turns by -------------------------------------------


class _Graph:
    def __init__(self, values, next_nodes=()):
        self.snap = SimpleNamespace(values=values, next=tuple(next_nodes), tasks=[])

    async def aget_state(self, config):
        return self.snap


def _close(graph, thread, **kwargs):
    from kazma_ui.turn_runtime import close_turn

    return asyncio.run(close_turn(graph, {"configurable": {"thread_id": thread}},
                                  thread_id=thread, **kwargs))


def _finished_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == "kazma_ui.turn_runtime" and "Turn finished" in r.getMessage()]


_TURN = [{"role": "user", "content": "book the table"},
         {"role": "assistant", "content": "Booked for 8."}]


def test_close_turn_writes_one_line_per_finished_turn(caplog, monkeypatch, tmp_path):
    """close_turn is the closer every transport runs, and it can run more
    than once for one turn. The digest reads what it writes."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    caplog.set_level(logging.INFO, logger="kazma_ui.turn_runtime")
    thread = f"gw-telegram-{uuid.uuid4().hex[:8]}"
    graph = _Graph({"messages": list(_TURN), "_gateway": {"platform": "telegram"}})
    _close(graph, thread, streamed_text="Booked for 8.")
    _close(graph, thread)  # a late settle closes the same turn again
    lines = _finished_lines(caplog)
    assert len(lines) == 1, lines
    turn = daily_digest._TURN_LINE.search(lines[0])
    assert turn and turn.group(1) == "telegram" and turn.group(3) == "no"
    assert "book the table" not in lines[0] and thread not in lines[0]


def test_a_paused_or_stopped_turn_is_not_finished_and_a_failed_one_says_so(
        caplog, monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    caplog.set_level(logging.INFO, logger="kazma_ui.turn_runtime")
    thread = f"web-{uuid.uuid4().hex[:8]}"
    web = {"platform": "web"}
    _close(_Graph({"messages": list(_TURN), "_gateway": web},
                  next_nodes=("tool_worker",)), thread)  # paused for approval
    _close(_Graph({"messages": list(_TURN), "_gateway": web}), thread, interrupted=True)
    assert _finished_lines(caplog) == []
    _close(_Graph({"messages": list(_TURN), "_gateway": web, "turn_failed": True}), thread)
    lines = _finished_lines(caplog)
    assert len(lines) == 1
    turn = daily_digest._TURN_LINE.search(lines[0])
    assert turn and turn.group(1) == "web" and turn.group(3) == "yes"


# -- the lines it reads are lines the code writes --------------------------


def _unread(lines, app_lines=None, literals=(), turn_line=None) -> list[str]:
    """What the digest reads that no line the code writes holds: a literal
    marker (anywhere in a line), an app line's pattern (at a line's start),
    the needle of each line its pattern takes (the quick scan drops a line
    without it) and the turn line."""
    app_lines = daily_digest._APP_LINES if app_lines is None else app_lines
    turn_line = daily_digest._TURN_LINE if turn_line is None else turn_line
    missing = [m for m in literals if not any(m in line for line in lines)]
    for needle, pattern, _label in app_lines:
        taken = [line for line in lines if pattern.match(line)]
        if not taken:
            missing.append(pattern.pattern)
        missing.extend(f"{needle!r} not in {line!r}" for line in taken if needle not in line)
    if not any(turn_line.search(line) for line in lines):
        missing.append(turn_line.pattern)
    return missing


def test_every_line_the_digest_reads_is_one_the_code_writes():
    """A marker no code writes counts nothing, forever: "turns finished after
    you left" counted "Detached turn completed", a line removed the day the
    digest shipped (2026-08-28). Read from the source
    (tests/_emitted_lines.py), like the weekly report's signatures."""
    lines = emitted_app_lines(product_roots())
    assert len(lines) > 1000, "the source walk found almost nothing"
    assert _unread(lines, literals=[daily_digest._ALERT_MARKER]) == []


def test_the_marker_gate_catches_a_line_no_code_writes():
    """Negative control (§28): the retired line, a needle the line does not
    hold, and a renamed turn line."""
    import re

    lines = ["[turn] Turn finished: platform=1 key=1 failed=1",
             "[SSE] Backfilled unanswered turn from checkpoint for 1"]
    stale = (("Detached turn completed", re.compile(r"\[SSE\] Detached turn completed"), "x"),
             ("Backfilled unanswered turn", re.compile(r"\[SSE\] Backfilled unanswered turn"), "y"))
    assert _unread(lines, stale) == [r"\[SSE\] Detached turn completed"]
    wrong_needle = (("Backfilled turn", re.compile(r"\[SSE\] Backfilled unanswered turn"), "y"),)
    assert len(_unread(lines, wrong_needle)) == 1
    assert _unread(["[turn] Turn done: 1"], ()) == [daily_digest._TURN_LINE.pattern]


#: What the health check logged on live while Docker was stopped
#: (2026-11-03/04): Docker's own words, carried in a variable.
_DOCKER_HEALTH_LINE = (
    "[Health] the Docker daemon is not running (docker info exit 1): failed to "
    "connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; "
    "check if the path is correct and if the daemon is running")


def _counted(msg: str) -> list[str]:
    """The labels the digest counts for one message, as it reads it."""
    return [label for _needle, pattern, label in daily_digest._APP_LINES if pattern.match(msg)]


def test_a_counted_line_quoted_inside_another_line_is_not_counted():
    """The digest reads a line by how its message opens. A marker matched
    anywhere took text a variable carried: on 2026-11-05 it reported five
    "MCP connection failures" that were the health check quoting Docker
    ("failed to connect to the docker API"), every MCP server connected.
    Class gate: every line the code writes that the digest counts is counted
    as written, and not once quoted inside another line."""
    lines = emitted_app_lines(product_roots())
    counted = [line for line in lines if _counted(line)]
    assert len(counted) >= len(daily_digest._APP_LINES)
    quoted = [line for line in counted
              if _counted(f"[Health] the check failed: {line}")
              or _counted(f"[MCP] test of server '1' failed: {line}")]
    assert quoted == []
    assert _counted(_DOCKER_HEALTH_LINE) == []


def test_control_a_marker_matched_anywhere_counts_the_docker_line():
    """Negative control: the old rule (a substring anywhere in the message)
    counted the health check's line as an MCP failure."""
    old_markers = {"Backfilled unanswered turn": "answers recovered from checkpoint",
                   "LLM call attempt": "LLM retries",
                   "failed to connect": "MCP connection failures"}
    assert [label for marker, label in old_markers.items()
            if marker in _DOCKER_HEALTH_LINE] == ["MCP connection failures"]


def test_mcp_failures_are_the_servers_mcp_said_failed(tmp_path, monkeypatch):
    """Through the whole digest: the Docker health lines count nothing, and
    the MCP summary counts the servers it names (one line per connect, so a
    server is never counted twice with its own error line)."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(
        tmp_path, [("WARNING", _DOCKER_HEALTH_LINE)] * 5)))
    assert "MCP connection failures" not in daily_digest.build_digest(hours=24)

    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [
        ("WARNING", _DOCKER_HEALTH_LINE),
        ("ERROR", "[MCP] Failed to connect server 'github': timed out"),
        ("ERROR", "[MCP] Failed to connect server 'files': refused"),
        ("WARNING", "[MCP] 2 server(s) failed to connect: github (timed out); "
                    "files (refused) — tools from these servers are unavailable "
                    "until fixed (see /mcp settings page)"),
    ], name="kazma2.log")))
    assert "MCP connection failures: 2" in daily_digest.build_digest(hours=24)


def test_recoveries_are_separated_from_problems(tmp_path, monkeypatch):
    """A restart that worked and a crash loop that did not are different
    news, and burying one under the other wastes the operator's attention."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(
        tmp_path, ["guard.restarting", "orphan.reaped", "guard.crash_loop"])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [])))
    text = daily_digest.build_digest(hours=24)
    assert "Recovered without you:" in text
    assert "orphans cleaned up: 1" in text
    assert "Needs attention:" in text
    assert "crash loops: 1" in text


def test_guard_pages_that_never_left_need_attention(tmp_path, monkeypatch):
    """The guard's pager must work when the app cannot. From 2026-09-24 22:30
    it skipped every page for a day, recorded only in guard.log at INFO; the
    digest is the app-side channel that can say so."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(
        tmp_path, ["guard.restarting", "notify.skipped", "notify.skipped", "notify.failed"])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [])))
    text = daily_digest.build_digest(hours=24)
    attention = text.split("Needs attention:", 1)[1]
    assert "guard alerts not delivered (no credentials): 2" in attention
    assert "guard alerts that failed to send: 1" in attention
    assert "No failures" not in text


def _digest_guard_events() -> set[str]:
    return {*daily_digest._GUARD_EVENTS, daily_digest._PAUSE_START,
            daily_digest._PAUSE_END, daily_digest._RELOAD}


def test_the_guard_events_the_digest_counts_are_ones_the_guard_emits():
    """A label for an event the guard never logs would count nothing, forever."""
    assert _digest_guard_events() - guard_events() == set()


def test_the_guard_event_gate_catches_an_event_the_guard_never_logs():
    """Negative control (§28)."""
    assert {"guard.restarted", "maintenance.resumed"} - guard_events() == {"guard.restarted"}


def test_events_outside_the_window_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(
        tmp_path, ["guard.restarting"], age_hours=100)))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [])))
    text = daily_digest.build_digest(hours=24)
    assert "server restarts" not in text


def test_alert_totals_and_held_back_repeats_are_reported(tmp_path, monkeypatch, caplog):
    """Held-back repeats are invisible by design; the digest is where the
    operator learns a throttled condition kept happening all day. Counted
    from the lines alert() itself writes, so a restart in the window loses
    nothing: the digest read this process's own bookkeeping, and the live
    install reloads several times a day."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    # The lines alert() writes, captured from the real function. conftest.py
    # turns ops alerts OFF for the suite (audit 2026-09-16: an unretained
    # delivery thread segfaulted CPython in pytest's capture teardown), so
    # opt back in, as tests/test_ops_alerts.py does; `_dispatch` is stubbed,
    # so nothing is delivered and no thread starts.
    monkeypatch.delenv("KAZMA_OPS_ALERTS", raising=False)
    monkeypatch.setattr(ops_alerts, "_dispatch", lambda *_a, **_k: None)
    caplog.set_level(logging.INFO, logger="kazma_core.observability.ops_alerts")
    for _ in range(12):
        ops_alerts.alert("mcp.down", "MCP down", cooldown_s=3600)
    for _ in range(2):  # a title holding the separator
        ops_alerts.alert("backup.restic_snapshot_failed", "A snapshot | was missed")
    written = _alert_lines_of(caplog)
    assert len(written) == 14
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, written)))
    ops_alerts.reset_alert_state()  # a restart: this process's memory is gone
    text = daily_digest.build_digest(hours=24)
    assert "Alerts raised:\n  MCP down: 12 (11 repeats held back)\n" \
           "  A snapshot | was missed: 2 (1 repeat held back)" in text
    assert "No failures" not in text


def _raise_alerts(monkeypatch, caplog, calls) -> list[tuple[str, str]]:
    """Run the real alert() for each ``(key, title, severity)``; its lines."""
    monkeypatch.delenv("KAZMA_OPS_ALERTS", raising=False)
    monkeypatch.setattr(ops_alerts, "_dispatch", lambda *_a, **_k: None)
    caplog.set_level(logging.INFO, logger="kazma_core.observability.ops_alerts")
    for key, title, severity in calls:
        ops_alerts.alert(key, title, severity=severity, cooldown_s=0)
    ops_alerts.reset_alert_state()
    return _alert_lines_of(caplog)


def test_alerts_read_in_their_own_words_and_successes_are_notices(
        tmp_path, monkeypatch, caplog):
    """The 2026-11-01 digest listed keys, success beside failure, under
    "Alerts raised": "acquisition.completed: 2" (a crawl) and
    "acquisition.done: 1" (a research run) read as one thing counted twice,
    and as problems. An alert reads by its title; a success or an info
    notice is listed under "Notices" and leaves a day quiet."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    written = _raise_alerts(monkeypatch, caplog, [
        ("acquisition.partial", "Partial results - review missing sources", "warn"),
        ("acquisition.completed", "Crawl completed", "success"),
        ("acquisition.completed", "Crawl completed", "success"),
        ("acquisition.done", "Research completed", "success"),
        ("acquisition.resumed", "Progress resumed", "info"),
    ])
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, written)))
    text = daily_digest.build_digest(hours=24)
    assert "Alerts raised:\n  Partial results - review missing sources: 1\n" in text
    assert text.split("Notices:", 1)[1].startswith(
        "\n  Crawl completed: 2\n  Research completed: 1\n  Progress resumed: 1")
    assert "acquisition." not in text

    # Notices alone leave the day quiet.
    quiet = [line for line in written if line[0] == "INFO"]
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, quiet)))
    text = daily_digest.build_digest(hours=24)
    assert "Alerts raised" not in text
    assert "No failures, no restarts, no alerts." in text


def test_a_success_line_from_before_the_change_is_still_counted(tmp_path, monkeypatch):
    """Every alert was a WARNING before successes were logged at INFO: such a
    line in the window is counted where its level puts it, never dropped."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [
        ("WARNING", "[ops_alert] acquisition.completed | Crawl completed")])))
    assert "Alerts raised:\n  Crawl completed: 1" in daily_digest.build_digest(hours=24)


def test_an_alert_whose_title_varies_is_named_by_its_key(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [
        ("WARNING", "[ops_alert] mcp.down | MCP server github is down"),
        ("WARNING", "[ops_alert] mcp.down | MCP server filesystem is down"),
    ])))
    assert "Alerts raised:\n  mcp.down: 2" in daily_digest.build_digest(hours=24)


def test_every_alert_is_named_none_left_to_the_log(tmp_path, monkeypatch):
    """"and 1 more (each is in the log)" hid acquisition.stalled from the
    2026-11-01 digest, which is read on a phone."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    lines = [("WARNING", f"[ops_alert] k{i:02d} | Title {i:02d}") for i in range(14)]
    lines.append(lines[0])  # k00 twice: ranked first
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, lines)))
    text = daily_digest.build_digest(hours=24)
    assert "  Title 00: 2\n" in text
    assert "in the log" not in text
    assert "  and 2 more: k12 1, k13 1" in text


def test_a_log_written_in_plain_lines_is_read(tmp_path, monkeypatch):
    """At a Windows boot Kazma started before Postgres, set up logging while
    the settings store could not answer, and wrote plain lines for 31 hours
    (2026-10-30/31). The digest read JSON only, and the 2026-10-31 digest
    said "Turns completed: 0 ... No failures, no restarts, no alerts" over a
    day of turns and alerts."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_plain_app_log(tmp_path, [
        ("INFO", "kazma_ui.turn_runtime", _turn_line("p1")),
        ("INFO", "kazma_ui.turn_runtime", _turn_line("p2", failed="yes")),
        ("WARNING", "kazma_core.observability.ops_alerts",
         "[ops_alert] acquisition.failed | Crawl failed - review saved details"),
        ("INFO", "kazma_core.observability.ops_alerts",
         "[ops_alert] acquisition.completed | Crawl completed"),
    ])))
    text = daily_digest.build_digest(hours=24)
    assert "Turns completed: 1 (web 1)" in text
    assert "turns that ended in an error: 1" in text
    assert "Alerts raised:\n  Crawl failed - review saved details: 1" in text
    assert "Notices:\n  Crawl completed: 1" in text
    assert "could not read" not in text


def test_a_log_the_digest_cannot_read_is_reported_not_called_quiet(tmp_path, monkeypatch):
    """Negative control: the JSON-only reader over the same plain log said
    nothing at all. A reader that cannot read a turn or alert line says so."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_plain_app_log(tmp_path, [
        ("INFO", "kazma_ui.turn_runtime", _turn_line("p1")),
        ("WARNING", "kazma_core.observability.ops_alerts",
         "[ops_alert] acquisition.failed | Crawl failed"),
    ])))

    def json_only(line):
        try:
            rec = json.loads(line)
            return datetime.fromisoformat(rec["timestamp"]).timestamp(), rec
        except (ValueError, KeyError, TypeError):
            return None

    monkeypatch.setattr(daily_digest, "_app_line", json_only)
    text = daily_digest.build_digest(hours=24)
    assert "Turns completed: 0" in text
    assert "log lines this digest could not read: 2" in text.split("Needs attention:", 1)[1]
    assert "No failures" not in text


# ── robustness ────────────────────────────────────────────────────────


def test_missing_logs_do_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(tmp_path / "nope.log"))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(tmp_path / "also-nope.log"))
    # The header is "[Ops] Daily digest" -- it stopped carrying the word
    # "Kazma" when the ops prefix was added. What this test is actually
    # about is that a missing log file produces a digest instead of an
    # exception, so assert the digest, not a brand name.
    assert "Daily digest" in daily_digest.build_digest(hours=24)


def test_corrupt_log_lines_are_skipped(tmp_path, monkeypatch):
    path = tmp_path / "guard.log"
    ts = datetime.now(UTC).isoformat()
    path.write_text(
        "not json at all\n"
        + json.dumps({"ts": ts, "event": "orphan.reaped"}) + "\n"
        + '{"ts": "garbage", "event": "guard.crash_loop"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(path))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [])))
    text = daily_digest.build_digest(hours=24)
    assert "orphans cleaned up: 1" in text
    assert "crash loops" not in text, "an unparseable timestamp must be skipped"


# -- delivery: stamped only when a channel took it ---------------------------


@pytest.fixture()
def due(monkeypatch):
    """One due digest: build and stamp recorded, delivery chosen per test."""
    from kazma_core.observability import cadence

    calls = {"built": 0, "stamped": 0}

    def build(_hours):
        calls["built"] += 1
        return "digest"

    monkeypatch.setattr(daily_digest, "build_digest", build)
    monkeypatch.setattr(cadence, "stamp_run", lambda _key: calls.__setitem__(
        "stamped", calls["stamped"] + 1))
    monkeypatch.setattr(ops_alerts, "_has_any_sink", lambda: True)
    return calls


def _deliver_returning(result):
    async def deliver(_text, outcome=None):
        if isinstance(result, Exception):
            raise result
        return result

    return deliver


def test_a_delivered_digest_is_stamped(due, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="kazma_core.observability.daily_digest")
    monkeypatch.setattr(ops_alerts, "_deliver", _deliver_returning(True))
    assert asyncio.run(daily_digest._send_due_digest()) == 0.0
    assert due == {"built": 1, "stamped": 1}
    assert "[digest] daily digest delivered (6 chars)" in caplog.text


@pytest.mark.parametrize("result", [False, RuntimeError("bus down")])
def test_a_digest_no_channel_took_is_tried_again_not_stamped(due, monkeypatch, caplog, result):
    """It was handed to a background delivery and stamped as sent whatever
    happened: a digest no channel took was never tried again, and the
    silence read as a quiet day."""
    caplog.set_level(logging.WARNING, logger="kazma_core.observability.daily_digest")
    monkeypatch.setattr(ops_alerts, "_deliver", _deliver_returning(result))
    assert asyncio.run(daily_digest._send_due_digest()) == daily_digest._RETRY_S
    assert due == {"built": 1, "stamped": 0}
    assert "[digest]" in caplog.text


def test_with_no_channel_at_all_the_digest_waits_for_tomorrow(due, monkeypatch):
    """Nothing configured to take ops messages: an hourly retry would only
    fill the log. Stamped, so the next one is a day away."""
    monkeypatch.setattr(ops_alerts, "_has_any_sink", lambda: False)
    monkeypatch.setattr(ops_alerts, "_deliver", _deliver_returning(True))
    assert asyncio.run(daily_digest._send_due_digest()) == 0.0
    assert due == {"built": 1, "stamped": 1}


def test_digest_can_be_disabled(due, monkeypatch):
    monkeypatch.setenv("KAZMA_DAILY_DIGEST", "0")
    assert daily_digest.digest_enabled() is False
    assert asyncio.run(daily_digest._send_due_digest()) == daily_digest._RETRY_S
    assert due == {"built": 0, "stamped": 0}


def test_scheduler_sleeps_before_the_first_send():
    """A digest on every boot would fire on each restart -- during an
    incident, exactly when the operator least needs another message."""
    import inspect

    src = inspect.getsource(daily_digest.digest_scheduler)
    # The wait ends at a stop too (2026-11, FA-265: shutdown.sleep_or_shutdown).
    assert src.index("sleep_or_shutdown") < src.index("_send_due_digest")


def test_scheduler_is_registered_at_boot_and_held():
    """An unreferenced asyncio task can be garbage-collected mid-loop -- the
    'scheduler existed but never ran' failure this codebase already hit."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "kazma-core" / "kazma_core" / "memory"
           / "worker_bootstrap.py").read_text(encoding="utf-8")
    assert "_start_daily_digest_scheduler()" in src
    fn = src.split("def _start_daily_digest_scheduler()", 1)[1][:1500]
    assert "_scheduler_tasks.add(task)" in fn


# ── scheduler coverage (Phase 3) ──────────────────────────────────────


class TestSchedulerHealth:
    """Kazma has TWO schedulers, and that is deliberate.

    CronScheduler wakes the agent with a PROMPT at time T; the X fire loop
    publishes a fixed PAYLOAD at time T. One is non-deterministic and costs
    LLM tokens, the other is deterministic and costs nothing. They are not
    duplicates -- but a partial outage of either is invisible without a
    check, and "scheduled work silently stopped" is the failure mode this
    whole project is about.
    """

    def _check(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kazma-ui"))
        from kazma_ui.health import check_schedulers

        return check_schedulers

    def test_reports_both_schedulers(self):
        out = self._check()()
        assert "cron" in out and "x_posts" in out

    def test_a_finished_x_loop_is_degraded_not_ok(self, monkeypatch):
        """A fire loop that has exited is silent, permanent, and looks
        exactly like 'nothing scheduled' from outside."""
        class _Done:
            def done(self):
                return True

        monkeypatch.setattr(
            "kazma_core.x_api.scheduled_fire.get_scheduled_x_task",
            lambda: _Done(),
        )
        out = self._check()()
        assert out["x_posts"] == "stopped"
        assert out["status"] == "degraded"

    def test_never_raises_when_a_scheduler_is_missing(self, monkeypatch):
        monkeypatch.setattr(
            "kazma_core.cron.scheduler.get_cron_scheduler",
            lambda: (_ for _ in ()).throw(RuntimeError("gone")),
        )
        out = self._check()()
        assert out["cron"] == "error"
