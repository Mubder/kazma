"""A turn open with nothing working on it must page the operator.

Every failure in this area reached the operator as a symptom. The stuck turn of
2026-09-13 sat as "Action required" while the server had logged the reopening
write and said nothing. The lattice in `reply_sink` makes *that* write harmless;
this watchdog does not care why a turn hangs, so it covers the next cause too.

The invariant: a reply turn may be open only while something is working on it,
or while it waits for a human. Open with neither is impossible — and impossible
states are worth an alert, not a log line.
"""

from __future__ import annotations

import pytest

from kazma_ui import turn_liveness


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    turn_liveness._idle_since.clear()
    turn_liveness._reported.clear()
    monkeypatch.setenv("KAZMA_TURN_LIVENESS_GRACE_S", "60")
    sent: list[tuple] = []
    monkeypatch.setattr(
        "kazma_core.observability.ops_alerts.alert",
        lambda key, title, detail="", **kw: sent.append((key, title, detail)),
    )
    return sent


@pytest.fixture
def alerts(_clean):
    return _clean


def _arrange(monkeypatch, *, open_turns, running=False, awaiting=False):
    monkeypatch.setattr(turn_liveness, "_is_running", lambda t: running)
    monkeypatch.setattr(turn_liveness, "_is_awaiting_human", lambda t: awaiting)
    monkeypatch.setattr(
        "kazma_ui.reply_sink.open_turns_snapshot", lambda: dict(open_turns)
    )


class TestItFiresOnlyWhenTheTurnIsTrulyAbandoned:
    def test_an_abandoned_turn_is_reported(self, monkeypatch, alerts):
        _arrange(monkeypatch, open_turns={"thread-a": "turn-1"})
        assert turn_liveness.check_once(now=1000.0) == []      # clock starts
        out = turn_liveness.check_once(now=1000.0 + 61)
        assert len(out) == 1 and out[0]["thread_id"] == "thread-a"
        assert alerts and alerts[0][0] == "turn.stuck_open"

    def test_a_running_turn_is_never_reported(self, monkeypatch, alerts):
        """A slow tool or a long model call is ordinary work."""
        _arrange(monkeypatch, open_turns={"thread-a": "turn-1"}, running=True)
        turn_liveness.check_once(now=1000.0)
        assert turn_liveness.check_once(now=1000.0 + 10_000) == []
        assert alerts == []

    def test_a_turn_waiting_for_approval_is_never_reported(self, monkeypatch, alerts):
        """It is legitimately open and the HITL watchdog owns its timeout.
        Paging here would alarm an operator about the card in front of them."""
        _arrange(monkeypatch, open_turns={"thread-a": "turn-1"}, awaiting=True)
        turn_liveness.check_once(now=1000.0)
        assert turn_liveness.check_once(now=1000.0 + 10_000) == []
        assert alerts == []

    def test_inside_the_grace_period_it_stays_quiet(self, monkeypatch, alerts):
        _arrange(monkeypatch, open_turns={"thread-a": "turn-1"})
        turn_liveness.check_once(now=1000.0)
        assert turn_liveness.check_once(now=1000.0 + 59) == []
        assert alerts == []

    def test_it_pages_once_not_every_sweep(self, monkeypatch, alerts):
        _arrange(monkeypatch, open_turns={"thread-a": "turn-1"})
        turn_liveness.check_once(now=1000.0)
        turn_liveness.check_once(now=1061.0)
        for tick in range(5):
            turn_liveness.check_once(now=1100.0 + tick * 30)
        assert len(alerts) == 1

    def test_a_turn_that_resumes_resets_the_clock(self, monkeypatch, alerts):
        """A socket reconnecting mid-turn must not inherit an old idle stamp
        and page the moment it goes quiet again."""
        _arrange(monkeypatch, open_turns={"thread-a": "turn-1"})
        turn_liveness.check_once(now=1000.0)
        _arrange(monkeypatch, open_turns={"thread-a": "turn-1"}, running=True)
        turn_liveness.check_once(now=1050.0)
        _arrange(monkeypatch, open_turns={"thread-a": "turn-1"})
        assert turn_liveness.check_once(now=1080.0) == []       # clock restarted
        assert alerts == []

    def test_a_closed_turn_is_forgotten(self, monkeypatch, alerts):
        _arrange(monkeypatch, open_turns={"thread-a": "turn-1"})
        turn_liveness.check_once(now=1000.0)
        _arrange(monkeypatch, open_turns={})
        turn_liveness.check_once(now=1010.0)
        assert "thread-a" not in turn_liveness._idle_since


class TestItDoesNotGuess:
    def test_healing_is_off_unless_asked_for(self, monkeypatch, alerts):
        """A close is what the bug did wrong. The watchdog reports by default
        and only closes when an operator turns it on."""
        monkeypatch.delenv("KAZMA_TURN_LIVENESS_HEAL", raising=False)
        closed: list[str] = []
        monkeypatch.setattr("kazma_ui.reply_sink.close_reply_turn", lambda t, *a, **k: closed.append(t))
        _arrange(monkeypatch, open_turns={"thread-a": "turn-1"})
        turn_liveness.check_once(now=1000.0)
        out = turn_liveness.check_once(now=1061.0)
        assert out[0]["healed"] is False
        assert closed == []

    def test_healing_closes_the_turn_when_enabled(self, monkeypatch, alerts):
        monkeypatch.setenv("KAZMA_TURN_LIVENESS_HEAL", "1")
        closed: list[str] = []
        monkeypatch.setattr("kazma_ui.reply_sink.close_reply_turn", lambda t, *a, **k: closed.append(t))
        _arrange(monkeypatch, open_turns={"thread-a": "turn-1"})
        turn_liveness.check_once(now=1000.0)
        out = turn_liveness.check_once(now=1061.0)
        assert out[0]["healed"] is True
        assert closed == ["thread-a"]

    def test_an_unknown_liveness_answer_is_treated_as_busy(self, monkeypatch, alerts):
        """If the watchdog cannot tell, it stays quiet. Reporting because a
        check errored is how an alert becomes noise."""
        monkeypatch.setattr(
            "kazma_ui.active_turns.is_turn_running",
            lambda t: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert turn_liveness._is_running("thread-a") is True

    def test_a_sweep_never_raises(self, monkeypatch, alerts):
        monkeypatch.setattr(
            "kazma_ui.reply_sink.open_turns_snapshot",
            lambda: (_ for _ in ()).throw(RuntimeError("store down")),
        )
        assert turn_liveness.check_once(now=1000.0) == []
