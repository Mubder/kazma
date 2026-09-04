"""Operational alerting: the failures that used to log and die.

Phase 2. The audit's finding was that Kazma fails *quietly* — reply-persist
failures, MCP servers dropping out, turns ending with no answer all logged
at WARNING and stopped there, and the operator found out by scrolling.

The property that decides whether this is useful or actively harmful is
deduplication. MCP failed 60 times in eight days; sixty Telegram messages
is the same as zero, because the channel gets muted.
"""

from __future__ import annotations

import time

import pytest
from kazma_core.observability import ops_alerts


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ops_alerts.reset_alert_state()
    monkeypatch.delenv("KAZMA_OPS_ALERTS", raising=False)
    yield
    ops_alerts.reset_alert_state()


@pytest.fixture
def sent(monkeypatch):
    """Capture dispatched messages instead of delivering them."""
    out: list[str] = []
    monkeypatch.setattr(ops_alerts, "_dispatch", out.append)
    return out


# ── throttling ────────────────────────────────────────────────────────


def test_first_occurrence_sends_immediately(sent):
    assert ops_alerts.alert("mcp.down", "MCP server unreachable") is True
    assert len(sent) == 1
    assert "MCP server unreachable" in sent[0]


def test_repeats_inside_the_cooldown_are_counted_not_sent(sent):
    """Sixty messages is the same as zero: the channel gets muted."""
    for _ in range(60):
        ops_alerts.alert("mcp.down", "MCP server unreachable", cooldown_s=900)
    assert len(sent) == 1, "one alert, not sixty"
    assert ops_alerts.alert_state()["mcp.down"]["total"] == 60


def test_the_next_alert_reports_how_many_were_suppressed(sent, monkeypatch):
    base = time.time()
    monkeypatch.setattr(ops_alerts.time, "time", lambda: base)
    for _ in range(9):
        ops_alerts.alert("mcp.down", "MCP server unreachable", cooldown_s=60)
    assert len(sent) == 1

    monkeypatch.setattr(ops_alerts.time, "time", lambda: base + 61)
    ops_alerts.alert("mcp.down", "MCP server unreachable", cooldown_s=60)
    assert len(sent) == 2
    assert "+8 more" in sent[1], "the operator must learn it kept happening"


def test_distinct_keys_do_not_throttle_each_other(sent):
    ops_alerts.alert("mcp.down", "MCP down")
    ops_alerts.alert("persist.failed", "Reply not saved")
    assert len(sent) == 2


def test_key_describes_the_condition_not_the_instance(sent):
    """A key containing a timestamp or id would make every event unique and
    defeat deduplication entirely — this documents the contract."""
    for i in range(5):
        ops_alerts.alert("mcp.server_down", f"server {i} unreachable")
    assert len(sent) == 1


# ── safety ────────────────────────────────────────────────────────────


def test_alert_never_raises_even_when_delivery_explodes(monkeypatch):
    def _boom(_text):
        raise RuntimeError("bus is gone")

    monkeypatch.setattr(ops_alerts, "_dispatch", _boom)
    # Called from exception handlers on hot paths: it must swallow this.
    assert ops_alerts.alert("x", "y") is False


def test_alert_never_raises_when_state_is_broken(monkeypatch):
    monkeypatch.setattr(
        ops_alerts, "_should_send",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad state")),
    )
    assert ops_alerts.alert("x", "y") is False


def test_can_be_disabled_without_touching_lifecycle_alerts(sent, monkeypatch):
    monkeypatch.setenv("KAZMA_OPS_ALERTS", "0")
    assert ops_alerts.alert("mcp.down", "MCP down") is False
    assert sent == []


def test_every_occurrence_is_logged_even_when_throttled(sent, caplog):
    with caplog.at_level("WARNING"):
        for _ in range(4):
            ops_alerts.alert("mcp.down", "MCP down", cooldown_s=900)
    lines = [r.message for r in caplog.records if "ops_alert" in r.message]
    assert len(lines) == 4, "the log is the record; the alert is the interrupt"
    assert sum("throttled" in x for x in lines) == 3


# ── message shape ─────────────────────────────────────────────────────


def test_detail_is_truncated(sent):
    ops_alerts.alert("x", "title", "y" * 5000)
    assert len(sent[0]) < 1200, "a wall of stack trace is unreadable on a phone"


def test_severity_changes_the_icon(sent):
    ops_alerts.alert("a", "t", severity="error")
    ops_alerts.alert("b", "t", severity="warn")
    assert sent[0][0] != sent[1][0]


def test_messages_are_attributed_to_ops(sent):
    """Distinguishable from lifecycle (System) and guard (Guard)."""
    ops_alerts.alert("x", "t")
    text = sent[0]
    assert text.rstrip().endswith("Ops")
    assert 'Role: "Ops"' not in text
    assert '"Warn"' not in text
    assert "[guard]" not in text.lower()
    assert "Kazma — Warn" in text


# ── the conditions that must never be silent again ────────────────────


class TestSilentFailuresAreWired:
    """Structural guard on Phase 2.

    Every condition below already logged at WARNING or ERROR and stopped
    there — that is what made the operator find data loss by scrolling. A
    future refactor that drops the alert call restores the silence, and
    nothing else in the suite would notice.
    """

    _ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]

    def _src(self, rel: str) -> str:
        # module_source: a target may now be a package dir (audit O5).
        from tests._module_source import module_source

        return module_source(self._ROOT / rel)

    def test_reply_persist_failure_alerts(self):
        """The exact failure that lost four answers on 2026-08-28."""
        src = self._src("kazma-ui/kazma_ui/reply_sink.py")
        assert "reply.persist_failed" in src
        assert "ops_alerts import alert" in src

    def test_detached_persist_failure_alerts(self):
        src = self._src("kazma-ui/kazma_ui/sse_chat.py")
        assert "reply.detached_persist_failed" in src

    def test_empty_turn_alerts(self):
        src = self._src("kazma-ui/kazma_ui/sse_chat.py")
        assert "turn.empty" in src

    def test_mcp_unavailable_alerts(self):
        """60 failures in eight days, surfaced nowhere the operator looked."""
        src = self._src("kazma-core/kazma_core/mcp/manager.py")
        assert "mcp.servers_unavailable" in src

    def test_turn_timeout_alerts(self):
        src = self._src("kazma-ui/kazma_ui/routes/ws_chat.py")
        assert "turn.timed_out" in src

    def test_every_alert_call_site_is_exception_guarded(self):
        """These sit inside except blocks on hot paths. An alert that can
        raise turns a reported failure into a second, worse one."""
        import re

        for rel in (
            "kazma-ui/kazma_ui/reply_sink.py",
            "kazma-ui/kazma_ui/sse_chat.py",
            "kazma-core/kazma_core/mcp/manager.py",
            "kazma-ui/kazma_ui/routes/ws_chat.py",
        ):
            src = self._src(rel)
            for m in re.finditer(r"from kazma_core\.observability\.ops_alerts import", src):
                # the import must be preceded by a `try:` within a few lines
                head = src[max(0, m.start() - 200):m.start()]
                assert "try:" in head, f"unguarded ops_alerts import in {rel}"


# ── delivery must never silently succeed ──────────────────────────────


class TestDeliveryIsNeverSilent:
    """The bug the author shipped and then claimed to have verified.

    alert() returned True meaning "the throttle decided to send". In any
    process the gateway did not initialise -- a worker, a CLI command, a
    script -- the bus adapter is NullBusAdapter, and the first version of
    _deliver simply RETURNED there. Success reported, nothing delivered:
    the same "looks fine, does nothing" failure this project exists to
    eliminate, reproduced inside the tool built to report it.
    """

    def test_no_bus_and_no_credentials_reports_failure_loudly(
        self, monkeypatch, caplog
    ):
        import asyncio

        class _Null:
            pass

        monkeypatch.setattr(
            ops_alerts, "_telegram_direct", lambda text: False
        )
        monkeypatch.setattr(
            "kazma_core.swarm.bus.get_message_bus",
            lambda: type("B", (), {"adapter": _Null()})(),
        )
        with caplog.at_level("WARNING"):
            delivered = asyncio.run(ops_alerts._deliver("x"))
        assert delivered is False, "must not claim success"

    def test_falls_back_to_direct_send_when_there_is_no_bus(self, monkeypatch):
        """An alert raised from a worker still has to reach the operator."""
        import asyncio

        from kazma_core.swarm.bus import NullBusAdapter

        monkeypatch.setattr(
            "kazma_core.swarm.bus.get_message_bus",
            lambda: type("B", (), {"adapter": NullBusAdapter()})(),
        )
        used: list[str] = []
        monkeypatch.setattr(
            ops_alerts, "_telegram_direct",
            lambda text: (used.append(text), True)[1],
        )
        assert asyncio.run(ops_alerts._deliver("hello")) is True
        assert used == ["hello"], "the direct path must actually be used"

    def test_missing_credentials_logs_what_is_missing(self, monkeypatch, caplog):
        """A silent no-op is what made this invisible; name the gap."""
        class _CS:
            def get(self, *_a, **_k):
                return ""

        monkeypatch.setattr(
            "kazma_core.config_store.get_config_store", lambda: _CS()
        )
        with caplog.at_level("WARNING"):
            assert ops_alerts._telegram_direct("x") is False
        assert any("NOT DELIVERED" in r.message for r in caplog.records)


class TestOperatorCardFormat:
    def test_ops_and_lifecycle_cards_have_no_dummy_quotes(self, sent):
        from kazma_core.observability.alert_card import format_operator_card
        from kazma_core import lifecycle_notifier

        ops_alerts.alert("k", "MCP server down", "transient: still retrying")
        ops_text = sent[0]
        assert "Kazma — Warn" in ops_text
        assert ops_text.rstrip().endswith("Ops")
        assert '"Warn"' not in ops_text
        assert 'Role: "Ops"' not in ops_text

        life = format_operator_card(
            "System", "success", lifecycle_notifier._EVENTS["started"]["label"],
            "Adapters: telegram",
        )
        assert "Kazma — Success" in life
        assert life.rstrip().endswith("System")
        assert '"Success"' not in life
        assert 'Role: "System"' not in life

        guard = format_operator_card(
            "gaurd", "warn", "Kazma stopped", "unhealthy (database: x)"
        )
        assert guard.rstrip().endswith("Guard")
        assert "[guard]" not in guard
        assert "gaurd" not in guard

    @pytest.mark.asyncio
    async def test_telegram_bus_does_not_clip_operator_card_at_300(self):
        from kazma_core.observability.alert_card import format_operator_card
        from kazma_core.swarm.bus import BusMessage
        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter

        marker = "UNIQUETOKENSEQTHINKING"
        detail = ("x" * 400) + marker + ("y" * 50)
        card = format_operator_card("Ops", "warn", "MCP server sequential-thinking down", detail)
        assert len(card) > 300

        captured: dict = {}
        adapter = TelegramBusAdapter("1:a", 1)

        async def fake_post(payload, method="sendMessage"):
            captured["payload"] = payload
            return {}

        adapter._post = fake_post  # type: ignore[method-assign]
        await adapter.send(BusMessage(
            worker_name="Kazma", worker_role="ops", content=card, level="warn",
        ))
        posted = captured["payload"]["text"]
        assert marker in posted
        assert len(posted) > 300

    @pytest.mark.asyncio
    async def test_discord_and_slack_do_not_rewrap_operator_card(self):
        from kazma_core.observability.alert_card import format_operator_card
        from kazma_core.swarm.bus import BusMessage
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter

        card = format_operator_card("Ops", "warn", "MCP down", "transient: still retrying")
        msg = BusMessage(worker_name="Kazma", worker_role="ops", content=card, level="warn")

        discord_got: dict = {}
        slack_got: dict = {}
        d = DiscordBusAdapter("t", "1")
        s = SlackBusAdapter("xoxb-x", "C1")

        async def d_post(payload):
            discord_got["payload"] = payload
            return {}

        async def s_post(payload):
            slack_got["payload"] = payload
            return {}

        d._post_message = d_post  # type: ignore[method-assign]
        s._post_message = s_post  # type: ignore[method-assign]
        await d.send(msg)
        await s.send(msg)
        d_text = discord_got["payload"]["content"]
        s_text = slack_got["payload"]["text"]
        assert d_text.startswith(card.split("\n", 1)[0]) or "Kazma — Warn" in d_text
        assert "**Kazma**:" not in d_text
        assert "*Kazma*:" not in s_text
        assert "Kazma — Warn" in s_text
