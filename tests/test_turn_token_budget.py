"""A chat turn's token budget and the daily cost limit (plan E1, 2026-11-05).

Live 2026-11-04 a research turn read 1.9 million tokens of pages in one turn
and cost more than the rest of the day together; nothing in Kazma could have
said "enough". ``agent.turn_token_budget`` (default 500,000, ``0`` none)
now bounds every model call of a chat turn: at 70 % the next call carries a
"SYSTEM BUDGET CHECK" note, at the limit the turn stops before its next call
and says how to go on, and a reply that continues grants half the limit
again past what the turn had used. ``agent.daily_cost_limit_usd`` (off by
default) refuses new turns once today's measured spend reached it, with one
ops alert a day at 80 %.

The turns run through the real supervisor graph, its real tool worker and
respond node, on a scripted model. Negative controls: the limit switched
off (the turn runs on), and the continue grant measured from the limit
alone (a continued turn whose last call ran past 1.5x its limit stops again
before a single call).
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, datetime
from typing import Any

import pytest
from kazma_core.agent import turn_token_budget as ttb
from kazma_core.llm_provider import LLMResponse, ToolCall
from starlette.requests import Request

# ── parsing and the settings ────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    (500000, 500000), ("250000", 250000), (" 0 ", 0), (100_000_000, 100_000_000),
    (100_000_001, None), (-1, None), ("lots", None), (True, None), (None, None), ("1.5", None),
])
def test_the_turn_budget_is_a_whole_number_in_range(raw, expected):
    assert ttb.parse_turn_token_budget(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (25, 25.0), ("2.5", 2.5), (0, 0.0), ("0.123456", 0.1235), (1_000_000, 1_000_000.0),
    (1_000_001, None), (-1, None), ("nan", None), ("inf", None), (False, None), ("ten", None),
])
def test_the_daily_limit_is_dollars_in_range(raw, expected):
    assert ttb.parse_daily_cost_limit(raw) == expected


def test_settings_validation_refuses_what_the_reader_would_not_take():
    from kazma_core.settings_validation import validate_setting

    assert validate_setting(ttb.TURN_TOKEN_BUDGET_KEY, "300000")[0] == 300000
    assert validate_setting(ttb.TURN_TOKEN_BUDGET_KEY, 0)[0] == 0
    assert validate_setting(ttb.DAILY_COST_LIMIT_KEY, "12.5")[0] == 12.5
    for key, bad in ((ttb.TURN_TOKEN_BUDGET_KEY, -5), (ttb.TURN_TOKEN_BUDGET_KEY, "many"),
                     (ttb.DAILY_COST_LIMIT_KEY, -1), (ttb.DAILY_COST_LIMIT_KEY, "nan")):
        with pytest.raises(Exception) as refused:
            validate_setting(key, bad)
        assert type(refused.value).__name__ == "SettingRejected"


class _Store(dict):
    def get(self, key, default=None):  # noqa: D401 -- the settings store's read
        return super().get(key, default)


def test_the_setting_reads_the_default_the_owners_value_and_a_bad_value():
    assert ttb.turn_token_budget_setting(_Store()) == {"tokens": 500_000, "source": "default"}
    assert ttb.turn_token_budget_setting(_Store({ttb.TURN_TOKEN_BUDGET_KEY: ""}))["source"] == "default"
    assert ttb.turn_token_budget_setting(_Store({ttb.TURN_TOKEN_BUDGET_KEY: 80000})) == {
        "tokens": 80000, "source": "setting"}
    assert ttb.turn_token_budget_setting(_Store({ttb.TURN_TOKEN_BUDGET_KEY: 0}))["tokens"] == 0
    # A value the reader cannot take is the default, never "no limit".
    assert ttb.turn_token_budget_setting(_Store({ttb.TURN_TOKEN_BUDGET_KEY: "-3"})) == {
        "tokens": 500_000, "source": "default"}
    assert ttb.daily_cost_limit_setting(_Store()) == {"usd": 0.0, "source": "default"}
    assert ttb.daily_cost_limit_setting(_Store({ttb.DAILY_COST_LIMIT_KEY: "7.25"})) == {
        "usd": 7.25, "source": "setting"}


def test_long_task_and_zero_mean_no_limit_and_an_unreadable_store_the_default(monkeypatch):
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    store.set(ttb.TURN_TOKEN_BUDGET_KEY, 0, category="agent")
    assert ttb.resolve_turn_token_budget("t-zero") is None
    store.set(ttb.TURN_TOKEN_BUDGET_KEY, 90000, category="agent")
    assert ttb.resolve_turn_token_budget("t-set") == 90000
    monkeypatch.setattr("kazma_core.agent.long_task.long_task_status",
                        lambda thread_id: {"active": thread_id == "t-long"})
    assert ttb.resolve_turn_token_budget("t-long") is None
    assert ttb.resolve_turn_token_budget("t-other") == 90000

    def broken(*a, **k):
        raise RuntimeError("store away")

    monkeypatch.setattr(ttb, "turn_token_budget_setting", broken)
    assert ttb.resolve_turn_token_budget("t-other") == ttb.DEFAULT_TURN_TOKEN_BUDGET


# ── a turn's accounting ─────────────────────────────────────────────────


def test_a_new_message_starts_at_zero_with_the_whole_budget():
    stopped = {"turn_budget_stopped": True, "turn_tokens": 900, "turn_token_budget_granted": 0}
    assert ttb.start_of_turn(stopped, "summarize my inbox", 500) == {
        "turn_tokens": 0, "turn_budget_stopped": False, "turn_token_budget_granted": 0}
    # "continue" after a turn that was NOT stopped is a new turn too.
    assert ttb.start_of_turn({"turn_tokens": 400}, "continue", 500)["turn_tokens"] == 0
    # No budget: nothing to continue.
    assert ttb.start_of_turn(stopped, "continue", None)["turn_tokens"] == 0


@pytest.mark.parametrize("reply", ["continue", "كمل", "كمّل", "اكمل", "go on"])
def test_a_continue_grants_the_promised_tokens_past_what_was_used(reply):
    # The turn's last call ran far past its limit (one call read a page set).
    state = {"turn_budget_stopped": True, "turn_tokens": 900, "turn_token_budget_granted": 0}
    patch = ttb.start_of_turn(state, reply, 500)
    more = math.ceil(500 * ttb.CONTINUE_FACTOR) - 500
    assert patch == {"turn_budget_stopped": False, "turn_token_budget_granted": 900 + more}
    assert "turn_tokens" not in patch  # the turn's tokens so far are kept
    # A turn that stopped right at its limit gets 1.5x the limit.
    at_limit = {"turn_budget_stopped": True, "turn_tokens": 510, "turn_token_budget_granted": 0}
    assert ttb.start_of_turn(at_limit, reply, 500)["turn_token_budget_granted"] == 760
    # A second continue grows from the grant the turn had.
    again = {"turn_budget_stopped": True, "turn_tokens": 1200, "turn_token_budget_granted": 1150}
    assert ttb.start_of_turn(again, reply, 500)["turn_token_budget_granted"] == 1200 + 575


def test_the_limit_is_the_grant_else_the_base():
    assert ttb.effective_limit({}, None) is None
    assert ttb.effective_limit({"turn_token_budget_granted": 900}, None) is None
    assert ttb.effective_limit({}, 500) == 500
    assert ttb.effective_limit({"turn_token_budget_granted": 1150}, 500) == 1150


def test_a_calls_tokens_are_its_total_else_prompt_plus_completion():
    assert ttb.add_call_tokens(10, {"total_tokens": 90}) == 100
    assert ttb.add_call_tokens(10, {"prompt_tokens": 50, "completion_tokens": 7}) == 67
    assert ttb.add_call_tokens(10, None) == 10
    assert ttb.add_call_tokens(10, {"total_tokens": "garbage"}) == 10
    assert ttb.add_call_tokens(10, {"total_tokens": -40}) == 10


def test_the_stop_and_the_refusal_speak_the_users_language():
    en = ttb.stop_text("research the history of Kuwait", 512_000, 500_000)
    assert "I stopped before finishing" in en and "512,000" in en and "500,000" in en
    assert "**continue**" in en and "250,000 more tokens" in en
    ar = ttb.stop_text("ابحث في تاريخ الكويت", 512_000, 500_000)
    assert "توقفت قبل أن أنتهي" in ar and "**كمل**" in ar and "I stopped" not in ar
    refusal = ttb.daily_refusal_text("hello there", 10.5, 10.0)
    assert "Today's model cost limit is reached" in refusal and "$10.50" in refusal
    assert "بلغت تكلفة النموذج اليوم حدها" in ttb.daily_refusal_text("مرحبا", 10.5, 10.0)


# ── the real graph ──────────────────────────────────────────────────────


class _Scripted:
    """A model whose first answer asks for one tool call that costs a lot,
    and every later one finishes."""

    def __init__(self, first_call_tokens: int) -> None:
        self.first_call_tokens = first_call_tokens
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, *, messages, tools=None, model=None, **kwargs):
        self.seen.append([dict(m) for m in messages])
        if len(self.seen) == 1 and tools:
            return LLMResponse(
                content="", finish_reason="tool_calls", model="stub", cost_usd=0.0,
                usage={"prompt_tokens": self.first_call_tokens - 20, "completion_tokens": 20,
                       "total_tokens": self.first_call_tokens},
                tool_calls=[ToolCall(id="call_1", name="lookup_note", arguments={"topic": "x"})],
            )
        return LLMResponse(content="Done.", tool_calls=[], finish_reason="stop", model="stub",
                           usage={"total_tokens": 12}, cost_usd=0.0)


def _graph(monkeypatch, llm, *, budget: int, checkpointer=None):
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.authority import create_authority
    from kazma_core.config_store import get_config_store
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.runtime import live_llm
    from kazma_core.tracing import KazmaTracer

    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setattr(live_llm, "resolve_live_client", lambda fallback, **kw: (llm, None))
    get_config_store().set(ttb.TURN_TOKEN_BUDGET_KEY, budget, category="agent")
    get_config_store().set(ttb.DAILY_COST_LIMIT_KEY, 0, category="agent")

    def lookup_note(topic: str = "") -> str:
        """Look up a note."""
        return "the note says hello"

    registry = LocalToolRegistry(include_builtins=False)
    registry.register_function("lookup_note", lookup_note, description="Look up a note.",
                               category="test")
    return build_supervisor_graph(
        llm=llm, system_prompt="base", tool_definitions=registry.get_tool_definitions(),
        tool_executor=registry, cost_breaker=create_cost_breaker(), checkpointer=checkpointer,
        authority=create_authority(model="test", window=128000), tracer=KazmaTracer(backend="console"),
    )


def _run(graph, messages, thread_id: str) -> dict[str, Any]:
    return asyncio.run(graph.ainvoke(
        {"messages": messages, "iteration": 0},
        {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}))


def _has_budget_note(messages) -> bool:
    return any(m.get("role") == "system" and str(m.get("content", "")).startswith(
        "SYSTEM BUDGET CHECK") for m in messages)


def test_past_seventy_percent_the_next_call_is_told_to_finish(monkeypatch):
    llm = _Scripted(first_call_tokens=800)
    out = _run(_graph(monkeypatch, llm, budget=1000),
               [{"role": "user", "content": "look up my note"}], "e1-note")
    assert out["messages"][-1]["content"] == "Done."
    assert not _has_budget_note(llm.seen[0])  # nothing spent yet
    assert _has_budget_note(llm.seen[1])
    assert not out.get("turn_budget_stopped")
    # The note is call-local: the checkpoint does not keep it.
    assert not _has_budget_note(out["messages"])


def test_at_the_limit_the_turn_stops_before_its_next_call(monkeypatch):
    llm = _Scripted(first_call_tokens=800)
    out = _run(_graph(monkeypatch, llm, budget=500),
               [{"role": "user", "content": "look up my note"}], "e1-stop")
    reply = out["messages"][-1]["content"]
    assert "I stopped before finishing" in reply and "800" in reply and "500" in reply
    assert out["turn_budget_stopped"] is True and out["turn_tokens"] == 800
    # One model call: no second step and no respond synthesis over the stop.
    assert len(llm.seen) == 1
    assert not out.get("turn_failed")


def test_with_no_limit_the_same_turn_runs_on(monkeypatch):
    """Negative control: the limit switched off, the turn makes its second call."""
    monkeypatch.setattr(ttb, "effective_limit", lambda state, base: None)
    llm = _Scripted(first_call_tokens=800)
    out = _run(_graph(monkeypatch, llm, budget=500),
               [{"role": "user", "content": "look up my note"}], "e1-off")
    assert out["messages"][-1]["content"] == "Done." and len(llm.seen) == 2


def _stop_then_continue(monkeypatch, thread_id: str) -> tuple[_Scripted, dict[str, Any]]:
    from langgraph.checkpoint.memory import MemorySaver

    llm = _Scripted(first_call_tokens=900)
    graph = _graph(monkeypatch, llm, budget=500, checkpointer=MemorySaver())
    first = _run(graph, [{"role": "user", "content": "look up my note"}], thread_id)
    assert first["turn_budget_stopped"] is True and len(llm.seen) == 1
    second = _run(graph, list(first["messages"]) + [{"role": "user", "content": "continue"}],
                  thread_id)
    return llm, second


def test_continue_picks_up_the_stopped_turn_with_more_tokens(monkeypatch):
    llm, out = _stop_then_continue(monkeypatch, "e1-continue")
    assert out["messages"][-1]["content"] == "Done."
    assert len(llm.seen) == 2
    # 900 used of a 1,150 grant: past 70 %, so the call was told to finish.
    assert out["turn_token_budget_granted"] == 900 + 250
    assert _has_budget_note(llm.seen[1])
    assert out["turn_tokens"] == 912 and out["turn_budget_stopped"] is False


def test_a_grant_measured_from_the_limit_alone_stops_again_at_once(monkeypatch):
    """Negative control: 1.5x the limit (750) is below the 900 the turn had
    used, so the continued turn stops before a single call."""

    def old_start(state, user_text, base):
        from kazma_core.agent.long_task import is_continuation_reply

        if base and state.get("turn_budget_stopped") and is_continuation_reply(user_text or ""):
            had = int(state.get("turn_token_budget_granted") or 0) or int(base)
            return {"turn_budget_stopped": False,
                    "turn_token_budget_granted": math.ceil(had * ttb.CONTINUE_FACTOR)}
        return {"turn_tokens": 0, "turn_budget_stopped": False, "turn_token_budget_granted": 0}

    monkeypatch.setattr(ttb, "start_of_turn", old_start)
    llm, out = _stop_then_continue(monkeypatch, "e1-old-grant")
    assert len(llm.seen) == 1 and out["turn_budget_stopped"] is True


def test_a_new_message_after_a_stop_gets_the_whole_budget(monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver

    llm = _Scripted(first_call_tokens=900)
    graph = _graph(monkeypatch, llm, budget=500, checkpointer=MemorySaver())
    first = _run(graph, [{"role": "user", "content": "look up my note"}], "e1-new")
    second = _run(graph, list(first["messages"]) + [
        {"role": "user", "content": "what time is it in Kuwait right now?"}], "e1-new")
    assert second["messages"][-1]["content"] == "Done."
    assert second["turn_tokens"] == 12 and second["turn_token_budget_granted"] == 0


# ── the daily cost limit ────────────────────────────────────────────────


def test_a_turn_past_the_daily_limit_is_refused_before_any_call(monkeypatch):
    llm = _Scripted(first_call_tokens=10)
    graph = _graph(monkeypatch, llm, budget=500_000)
    monkeypatch.setattr(ttb, "daily_limit_check", lambda: {
        "limit": 10.0, "spent": 10.42, "measured": True, "over": True, "date": "2026-11-05"})
    out = _run(graph, [{"role": "user", "content": "look up my note"}], "e1-daily")
    reply = out["messages"][-1]["content"]
    assert "Today's model cost limit is reached" in reply and "$10.42" in reply
    assert llm.seen == []


def test_under_the_daily_limit_the_turn_runs(monkeypatch):
    llm = _Scripted(first_call_tokens=10)
    graph = _graph(monkeypatch, llm, budget=500_000)
    monkeypatch.setattr(ttb, "daily_limit_check", lambda: {
        "limit": 10.0, "spent": 3.0, "measured": True, "over": False})
    out = _run(graph, [{"role": "user", "content": "look up my note"}], "e1-daily-ok")
    assert out["messages"][-1]["content"] == "Done."


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    from kazma_core.observability import llm_ledger

    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(tmp_path / "llm_calls.db"))
    yield conn
    llm_ledger.close_llm_ledger()
    monkeypatch.setattr(llm_ledger, "_conn", None)


def _spent_today(conn, cost: float, tokens: int = 1000) -> None:
    # The ledger's own row shape: ``ts`` is ISO text in UTC, as record_llm_call writes it.
    conn.execute("INSERT INTO llm_calls (ts, total_tokens, cost_usd) VALUES (?, ?, ?)",
                 (datetime.now(UTC).isoformat(), tokens, cost))
    conn.commit()


def _daily_world(monkeypatch, limit: float) -> list[tuple]:

    from kazma_core.config_store import get_config_store

    get_config_store().set(ttb.DAILY_COST_LIMIT_KEY, limit, category="agent")
    get_config_store().delete(ttb.DAILY_ALERT_STAMP)
    monkeypatch.setattr("kazma_core.cron.scheduler.resolve_cron_zone",
                        lambda: (UTC, "UTC"))
    alerts: list[tuple] = []
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert",
                        lambda key, title, body, **kw: alerts.append((key, title, kw)))
    return alerts


def test_eighty_percent_of_the_daily_limit_alerts_once_a_day(ledger, monkeypatch):
    alerts = _daily_world(monkeypatch, 10.0)
    _spent_today(ledger, 7.0)
    first = ttb.daily_limit_check()
    assert first["measured"] and not first["over"] and first["spent"] == pytest.approx(7.0)
    assert alerts == []  # 70 %: no alert yet
    _spent_today(ledger, 1.5)
    assert not ttb.daily_limit_check()["over"]
    assert len(alerts) == 1 and alerts[0][0] == "cost.daily_limit"
    assert alerts[0][2]["severity"] == "warn"
    _spent_today(ledger, 2.0)
    verdict = ttb.daily_limit_check()
    assert verdict["over"] is True and verdict["spent"] == pytest.approx(10.5)
    assert len(alerts) == 1  # one alert a day, even past the limit


def test_no_daily_limit_and_an_unmeasured_day_refuse_nothing(ledger, monkeypatch):
    alerts = _daily_world(monkeypatch, 0)
    _spent_today(ledger, 999.0)
    assert ttb.daily_limit_check()["over"] is False  # no limit set
    _daily_world(monkeypatch, 1.0)

    class Off:
        ledger_enabled = False

    monkeypatch.setattr("kazma_core.agent.nonstop.get_nonstop_config", lambda *a, **k: Off())
    verdict = ttb.daily_limit_check()
    assert verdict["over"] is False and verdict["measured"] is False and alerts == []
    # A ledger that cannot be read refuses nothing either.
    monkeypatch.undo()
    _daily_world(monkeypatch, 1.0)
    monkeypatch.setattr("kazma_core.observability.llm_ledger.usage_since", lambda since: None)
    assert ttb.daily_limit_check()["over"] is False


def test_a_broken_check_never_refuses(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("settings away")

    monkeypatch.setattr(ttb, "daily_cost_limit_setting", broken)
    assert ttb.daily_limit_check()["over"] is False


# ── the pages ───────────────────────────────────────────────────────────


def test_settings_shows_both_values_and_where_they_come_from(tmp_path):
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok", encoding="utf-8")
    store = ConfigStore(db_path=str(tmp_path / "api.db"))
    app = FastAPI()
    app.include_router(create_settings_router(
        MagicMock(), store, Jinja2Templates(directory=str(tmp_path / "templates"))))
    client = TestClient(app)
    body = client.get("/api/settings/turn-spending").json()
    assert body["tokens"] == 500_000 and body["tokens_source"] == "default"
    assert body["tokens_default"] == 500_000 and body["tokens_max"] == ttb.MAX_TURN_TOKEN_BUDGET
    assert body["daily_usd"] == 0.0 and body["daily_source"] == "default"
    store.set(ttb.TURN_TOKEN_BUDGET_KEY, 200000, category="agent")
    store.set(ttb.DAILY_COST_LIMIT_KEY, 12.5, category="agent")
    body = client.get("/api/settings/turn-spending").json()
    assert (body["tokens"], body["tokens_source"]) == (200000, "setting")
    assert (body["daily_usd"], body["daily_source"]) == (12.5, "setting")
    store.close()


def _request(path: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": [],
                    "query_string": b""})


def test_the_dashboard_shows_todays_spend_against_the_limit(ledger, monkeypatch):
    import kazma_ui.auth as auth
    from kazma_ui.dashboard import spend_today

    _daily_world(monkeypatch, 20.0)
    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "")
    _spent_today(ledger, 1.25, tokens=4321)
    body = json.loads(spend_today(_request("/api/dashboard/spend-today")).body)
    assert body["available"] is True
    assert body["cost"] == pytest.approx(1.25) and body["tokens"] == 4321 and body["calls"] == 1
    assert body["limit_usd"] == 20.0 and body["zone"] == "UTC"


@pytest.mark.parametrize("role,status", [("viewer", 403), ("operator", 403), ("admin", 200)])
def test_todays_spend_is_admin_only(ledger, monkeypatch, role, status):
    import kazma_ui.auth as auth
    from kazma_ui.dashboard import spend_today

    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "configured")
    monkeypatch.setattr(auth, "is_authenticated", lambda request, secret: True)
    monkeypatch.setattr(auth, "get_request_principal",
                        lambda request: {"role": role, "source": "session"})
    assert spend_today(_request("/api/dashboard/spend-today")).status_code == status
