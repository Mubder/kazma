"""The daily digest: making silence mean something.

Incident alerts tell you when something breaks. They cannot distinguish "a
quiet day" from "the alerting itself is broken" -- and after an audit whose
central finding was silent failure, that distinction is the whole point. If
the only signal is failure, an agent that has stopped looks exactly like an
agent with nothing to report.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from kazma_core.observability import daily_digest, ops_alerts


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


def _app_log(tmp_path, messages, age_hours=1.0):
    path = tmp_path / "kazma.log"
    ts = (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat()
    path.write_text(
        "\n".join(json.dumps({"timestamp": ts, "level": "INFO", "message": m})
                  for m in messages),
        encoding="utf-8",
    )
    return path


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


def test_turns_are_counted(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(
        tmp_path, ["SSE turn complete: tokens=1"] * 7)))
    assert "Turns completed: 7" in daily_digest.build_digest(hours=24)


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


def test_events_outside_the_window_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(
        tmp_path, ["guard.restarting"], age_hours=100)))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [])))
    text = daily_digest.build_digest(hours=24)
    assert "server restarts" not in text


def test_alert_totals_and_suppressions_are_reported(tmp_path, monkeypatch):
    """Suppressed repeats are invisible by design; the digest is where the
    operator learns a throttled condition kept happening all day."""
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(_guard_log(tmp_path, [])))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(_app_log(tmp_path, [])))
    monkeypatch.setattr(ops_alerts, "_dispatch", lambda _t: None)
    for _ in range(12):
        ops_alerts.alert("mcp.down", "MCP down", cooldown_s=3600)
    text = daily_digest.build_digest(hours=24)
    assert "mcp.down: 12" in text
    assert "suppressed" in text


# ── robustness ────────────────────────────────────────────────────────


def test_missing_logs_do_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_GUARD_LOG", str(tmp_path / "nope.log"))
    monkeypatch.setenv("KAZMA_LOG_FILE", str(tmp_path / "also-nope.log"))
    assert "Kazma" in daily_digest.build_digest(hours=24)


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


def test_digest_can_be_disabled(monkeypatch):
    monkeypatch.setenv("KAZMA_DAILY_DIGEST", "0")
    assert daily_digest.digest_enabled() is False
    assert daily_digest.send_digest() is False


def test_send_never_raises(monkeypatch):
    monkeypatch.setattr(
        daily_digest, "build_digest",
        lambda **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert daily_digest.send_digest() is False


def test_scheduler_sleeps_before_the_first_send():
    """A digest on every boot would fire on each restart -- during an
    incident, exactly when the operator least needs another message."""
    import inspect

    src = inspect.getsource(daily_digest.digest_scheduler)
    assert src.index("asyncio.sleep") < src.index("send_digest")


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
