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
    path = tmp_path / name
    ts = (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat()
    path.write_text(
        "\n".join(json.dumps({"timestamp": ts, "level": "INFO", "message": m})
                  for m in messages),
        encoding="utf-8",
    )
    return path


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


def _unread_markers(lines, markers=None, turn_line=None) -> list[str]:
    markers = daily_digest._APP_MARKERS if markers is None else markers
    turn_line = daily_digest._TURN_LINE if turn_line is None else turn_line
    missing = [m for m in markers if not any(m in line for line in lines)]
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
    read = {**daily_digest._APP_MARKERS, daily_digest._ALERT_MARKER: "alerts"}
    assert _unread_markers(lines, read) == []


def test_the_marker_gate_catches_a_line_no_code_writes():
    """Negative control (§28): the retired marker, and a renamed turn line."""
    lines = ["[turn] Turn finished: platform=1 key=1 failed=1",
             "[SSE] Backfilled unanswered turn from checkpoint for 1"]
    stale = {"Detached turn completed": "x", "Backfilled unanswered turn": "y"}
    assert _unread_markers(lines, stale) == ["Detached turn completed"]
    assert _unread_markers(["[turn] Turn done: 1"], {}) == [daily_digest._TURN_LINE.pattern]


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
    monkeypatch.setattr(ops_alerts, "_dispatch", lambda _t: None)
    caplog.set_level(logging.WARNING, logger="kazma_core.observability.ops_alerts")
    for _ in range(12):
        ops_alerts.alert("mcp.down", "MCP down", cooldown_s=3600)
    for _ in range(2):  # a title holding the separator
        ops_alerts.alert("backup.restic_snapshot_failed", "A snapshot | was missed")
    written = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[ops_alert] ")]
    assert len(written) == 14
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, written)))
    ops_alerts.reset_alert_state()  # a restart: this process's memory is gone
    text = daily_digest.build_digest(hours=24)
    assert "Alerts raised:\n  mcp.down: 12 (11 repeats held back)\n" \
           "  backup.restic_snapshot_failed: 2 (1 repeat held back)" in text
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
    assert src.index("asyncio.sleep") < src.index("_send_due_digest")


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


def test_cron_fires_overdue_jobs_after_downtime():
    """Corrects an audit finding rather than implementing one.

    The audit reported "no evidence a schedule missed during downtime is
    ever recovered", based on grepping the logs for catch-up lines. There
    are none -- because the recovery is implicit: due means now >= next_run,
    so an overdue job fires on the next poll. It works and says nothing.
    """
    import sys
    from datetime import UTC, datetime, timedelta
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kazma-core"))
    from kazma_core.cron.scheduler import CronScheduler

    now = datetime.now(UTC)
    missed = (now - timedelta(hours=8)).isoformat()
    assert CronScheduler._is_due(missed, now) is True, (
        "a job whose fire time passed while Kazma was down must still fire"
    )
    assert CronScheduler._is_due((now + timedelta(hours=1)).isoformat(), now) is False
