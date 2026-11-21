"""Spaces S2-02: a daily spending limit per person (decision N8).

Each person but the admin has a limit counted from the per-call ledger by
the person the calls were made for (``llm_calls.asker``, S1-22): tokens
always, dollars where the price is known.

* The turn of a person who reached their limit is refused before any model
  call, through the real supervisor graph; another person's turn runs. The
  control: the install-wide sum in its place refuses the other person too.
* A model call outside a turn (``resilient_chat``: a swarm worker's,
  research's) is refused the same way, before any provider.
* Tokens cover a model of unknown price; dollars stop a priced day.
* A spend that cannot be counted (the ledger off) refuses: a person's limit
  is never taken as unspent.
* An install that does not enforce Spaces, and a person with no limit (the
  admin), read nothing more: no ledger read.
* The install's own limit (``agent.daily_cost_limit_usd``) works as before.
* The admin sets limits on People (``/api/people``), recorded; a member
  cannot. The browser half is ``tests/e2e/test_person_daily_limit_browser.py``.
* The precondition of a second account (S1-33) holds while the rule applies
  to every person, and not with the ledger off.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from kazma_core.observability import llm_ledger
from kazma_core.spaces import daily_limits, people, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._space_matrix import Matrix


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    from tests._no_models import finish_turn_memory, keep_models_out

    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()


@pytest.fixture
def matrix(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def ledger(tmp_path, monkeypatch) -> sqlite3.Connection:
    """A real ledger of the test's own."""
    conn = sqlite3.connect(str(tmp_path / "llm_calls.db"), check_same_thread=False)
    conn.executescript(llm_ledger._SCHEMA)
    llm_ledger._migrate(conn)
    monkeypatch.setattr(llm_ledger, "_get_conn", lambda db_path=None: conn)
    yield conn
    conn.close()


def _spend(bound: Any, tokens: int, *, cost: float = 0.0, priced: bool = True) -> None:
    """Calls made today for the person *bound* binds."""
    with bound:
        llm_ledger.record_llm_call(model="m", prompt_tokens=tokens, cost_usd=cost, priced=priced)


def _limit_for_people(tokens: int, usd: float = 0.0) -> None:
    people.set_default_daily_limit({"tokens": tokens, "usd": usd}, by=OWNER_ACCOUNT_ID)


class _Model:
    """A scripted model that counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *, messages, tools=None, model=None, **kwargs):
        from kazma_core.llm_provider import LLMResponse

        self.calls += 1
        return LLMResponse(content="It is now.", tool_calls=[], finish_reason="stop", model="stub",
                           usage={"total_tokens": 2}, cost_usd=0.0)


def _graph(model: _Model):
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.tracing import KazmaTracer

    return build_supervisor_graph(
        llm=model, system_prompt="You are Kazma.", tool_definitions=[], tool_executor=None,
        cost_breaker=create_cost_breaker(), authority=create_authority(model="test", window=128000),
        tracer=KazmaTracer(backend="console"),
    )


def _turn(space: str, account: str) -> tuple[int, str]:
    """One real turn in *space* opened by *account*: the model's calls and
    the answer."""
    from kazma_core.agent.turn import run_agent_turn

    model = _Model()
    thread_id = f"t-limit-{uuid.uuid4().hex[:8]}"
    threads.record_thread(thread_id, space, account)
    result = asyncio.run(run_agent_turn(graph=_graph(model), thread_id=thread_id,
                                        state={"messages": [{"role": "user", "content": "what time is it?"}]},
                                        persist=False))
    assert not result.error, result.error
    return model.calls, str(result.text or "")


# ── the turn ─────────────────────────────────────────────────────────────


def test_a_person_at_their_limit_is_refused_before_any_model_call(matrix, enforced, ledger) -> None:
    _limit_for_people(100)
    _spend(matrix.as_a(), 150)
    calls, said = _turn(Matrix.SPACE_A, matrix.account_a)
    assert calls == 0, "a model was called for a person past their limit"
    assert "daily limit" in said and "150" in said
    # Another person is not affected.
    calls, said = _turn(Matrix.SPACE_B, matrix.account_b)
    assert calls == 1 and said == "It is now."


def test_control_the_install_wide_sum_refuses_the_other_person_too(matrix, enforced, ledger,
                                                                    monkeypatch) -> None:
    """Counted as the install's own limit counts -- everyone's calls -- B's
    turn is refused for A's spend: the per-person count is what keeps B's day
    B's."""
    monkeypatch.setattr(llm_ledger, "usage_of", lambda asker, since: llm_ledger.usage_since(since))
    _limit_for_people(100)
    _spend(matrix.as_a(), 150)
    calls, said = _turn(Matrix.SPACE_B, matrix.account_b)
    assert calls == 0 and "daily limit" in said


def test_a_person_under_their_limit_runs(matrix, enforced, ledger) -> None:
    _limit_for_people(1000)
    _spend(matrix.as_a(), 150)
    assert _turn(Matrix.SPACE_A, matrix.account_a)[0] == 1


def test_a_person_s_own_limit_wins_over_the_limit_for_people(matrix, enforced, ledger) -> None:
    _limit_for_people(100)
    people.set_daily_limit(matrix.account_a, {"tokens": 1000}, by=OWNER_ACCOUNT_ID)
    _spend(matrix.as_a(), 150)
    assert _turn(Matrix.SPACE_A, matrix.account_a)[0] == 1
    # Taken away, the limit for people applies again.
    people.set_daily_limit(matrix.account_a, None, by=OWNER_ACCOUNT_ID)
    assert _turn(Matrix.SPACE_A, matrix.account_a)[0] == 0


def test_tokens_cover_a_model_of_unknown_price(matrix, enforced, ledger) -> None:
    _limit_for_people(100, usd=50.0)
    _spend(matrix.as_a(), 150, priced=False)  # no dollars counted, its tokens are
    found = _verdict(matrix)
    assert found["over"] and found["unpriced"] == 1 and found["cost"] == 0.0
    assert _turn(Matrix.SPACE_A, matrix.account_a)[0] == 0


def test_dollars_stop_a_priced_day(matrix, enforced, ledger) -> None:
    _limit_for_people(1_000_000, usd=1.0)
    _spend(matrix.as_a(), 10, cost=1.25)
    assert _verdict(matrix)["over"]
    assert _turn(Matrix.SPACE_A, matrix.account_a)[0] == 0


def test_yesterday_does_not_count(matrix, enforced, ledger) -> None:
    _limit_for_people(100)
    _spend(matrix.as_a(), 150)
    yesterday = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    ledger.execute("UPDATE llm_calls SET ts = ?", (yesterday,))
    ledger.commit()
    assert not _verdict(matrix)["over"]


def _verdict(matrix: Matrix) -> dict[str, Any]:
    with matrix.as_a():
        found = daily_limits.verdict()
    assert found is not None
    return found


def test_a_spend_that_cannot_be_counted_refuses(matrix, enforced, ledger, monkeypatch) -> None:
    from kazma_core.agent import nonstop

    real = nonstop.get_nonstop_config()
    real.ledger_enabled = False
    monkeypatch.setattr(nonstop, "get_nonstop_config", lambda: real)
    found = _verdict(matrix)
    assert found["over"] and not found["measured"] and found["reason"] == "ledger_off"
    calls, said = _turn(Matrix.SPACE_A, matrix.account_a)
    assert calls == 0 and "cannot be counted" in said


def test_a_limit_that_cannot_be_read_refuses(matrix, enforced, ledger, monkeypatch) -> None:
    """Whose limit it is cannot be read (the identity store away): never
    taken as no limit."""
    from kazma_core.spaces import identity

    def away():
        raise identity.IdentityUnavailable("the identity store could not be read")

    monkeypatch.setattr(identity, "read_registry", away)
    found = _verdict(matrix)
    assert found["over"] and found["reason"] == "identity_unavailable"
    with matrix.as_a():
        assert "cannot be counted" in daily_limits.call_refusal()


# ── nothing more where no limit applies ──────────────────────────────────


def _no_ledger(monkeypatch) -> None:
    def refuse(*_a, **_k):
        raise AssertionError("the ledger was read for a turn with no limit")

    monkeypatch.setattr(llm_ledger, "usage_of", refuse)


def test_an_install_of_one_account_reads_nothing_more(matrix, ledger, monkeypatch) -> None:
    """Spaces not enforced: the person's day is never worked out (no identity
    read, no ledger read), and the turn runs."""
    def refuse(*_a, **_k):
        raise AssertionError("a person's day was worked out on an install that does not enforce Spaces")

    _no_ledger(monkeypatch)
    monkeypatch.setattr(daily_limits, "verdict", refuse)
    _spend(matrix.as_a(), 10_000_000)
    with matrix.as_a():
        assert not daily_limits.may_apply()
    calls, _said = _turn(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    assert calls == 1


def test_the_admin_with_no_limit_of_their_own_reads_no_ledger(matrix, enforced, ledger, monkeypatch) -> None:
    _limit_for_people(1)
    _no_ledger(monkeypatch)
    calls, _said = _turn(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    assert calls == 1


def test_work_bound_to_no_person_reads_nothing(enforced, monkeypatch) -> None:
    _no_ledger(monkeypatch)
    assert not daily_limits.may_apply()
    assert daily_limits.verdict() is None


def test_the_install_s_own_limit_works_as_before(matrix, enforced, ledger) -> None:
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    store.set("agent.daily_cost_limit_usd", 1.0, category="agent")
    try:
        _spend(matrix.as_b(), 10, cost=2.0)
        calls, said = _turn(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)  # the admin: no limit of their own
        assert calls == 0 and "Today's model cost limit is reached" in said
    finally:
        store.delete("agent.daily_cost_limit_usd")


# ── a model call outside a turn ──────────────────────────────────────────


class _Client:
    def __init__(self) -> None:
        self.calls = 0
        self.config = None

    async def chat(self, messages, **_kw):
        from kazma_core.llm_provider import LLMResponse

        self.calls += 1
        return LLMResponse(content="ok", tool_calls=[], finish_reason="stop", model="stub",
                           usage={"total_tokens": 1}, cost_usd=0.0)


def _call(bound: Any) -> tuple[_Client, BaseException | None]:
    from kazma_core.agent.resilient_chat import resilient_chat

    client = _Client()
    with bound:
        try:
            asyncio.run(resilient_chat(client, messages=[{"role": "user", "content": "go"}], max_attempts=1))
        except Exception as exc:  # noqa: BLE001 -- the refusal is the answer
            return client, exc
    return client, None


def test_a_call_outside_a_turn_is_refused_for_the_person_and_runs_for_another(matrix, enforced,
                                                                              ledger) -> None:
    from kazma_core.llm_provider import LLMError

    _limit_for_people(100)
    _spend(matrix.as_a(), 150)
    client, refused = _call(matrix.as_a())
    assert client.calls == 0 and isinstance(refused, LLMError)
    assert refused.kind == "daily_limit" and not refused.transient
    client, refused = _call(matrix.as_b())
    assert client.calls == 1 and refused is None


# ── the admin's page ─────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.people_api import create_people_router

    who: dict[str, Any] = {"principal": {"source": "secret", "role": "admin", "user_id": "",
                                         "actor": "secret", "username": "admin"}}
    monkeypatch.setattr(auth, "get_request_principal", lambda request: who["principal"])
    monkeypatch.setattr(auth, "admin_decision",
                        lambda request: "ok" if who["principal"].get("role") == "admin" else "forbidden")
    app = FastAPI()
    app.include_router(create_people_router())
    with TestClient(app) as made:
        made.who = who
        yield made


def test_the_admin_sets_limits_and_sees_today_s_spend(matrix, enforced, ledger, client) -> None:
    _spend(matrix.as_a(), 150, cost=0.5)
    listed = client.get("/api/people").json()
    assert listed["limit_for_people"]["limit"] == daily_limits.SHIPPED_DEFAULT.as_dict()
    a = next(p for p in listed["people"] if p["id"] == matrix.account_a)
    assert a["limit_source"] == "default" and a["spent_today"]["tokens"] == 150
    assert a["spent_today"]["cost"] == pytest.approx(0.5)
    owner = next(p for p in listed["people"] if p["owner"])
    assert owner["limit"] is None and owner["limit_source"] == "none"

    assert client.put("/api/people/default-limit", json={"limit": {"tokens": 5000, "usd": 2}}).json()["ok"]
    assert client.put(f"/api/people/{matrix.account_a}/daily-limit",
                      json={"limit": {"tokens": 900}}).json()["ok"]
    listed = client.get("/api/people").json()
    assert listed["limit_for_people"] == {"limit": {"tokens": 5000, "usd": 2.0}, "set": True,
                                          "shipped": daily_limits.SHIPPED_DEFAULT.as_dict()}
    a = next(p for p in listed["people"] if p["id"] == matrix.account_a)
    assert a["limit"] == {"tokens": 900, "usd": 0.0} and a["limit_source"] == "own"
    b = next(p for p in listed["people"] if p["id"] == matrix.account_b)
    assert b["limit"] == {"tokens": 5000, "usd": 2.0} and b["limit_source"] == "default"
    # Each change is in the record.
    from kazma_core.spaces import identity

    kinds = [e["kind"] for e in identity.read_registry()["events"]]
    assert kinds[-2:] == ["default_daily_limit_set", "daily_limit_set"]
    assert people.broken_at() is None


@pytest.mark.parametrize("limit, code", [
    ({"tokens": 0}, "bad_tokens"), ({"tokens": "lots"}, "bad_tokens"), ({"tokens": True}, "bad_tokens"),
    ({"tokens": 10, "usd": -1}, "bad_usd"), ({"tokens": 10, "usd": "nan"}, "bad_usd"),
])
def test_a_limit_that_is_not_one_is_refused(matrix, enforced, client, limit, code) -> None:
    answer = client.put(f"/api/people/{matrix.account_a}/daily-limit", json={"limit": limit})
    assert answer.status_code == 400 and answer.json()["error"] == code


def test_a_member_cannot_set_a_limit(matrix, enforced, client) -> None:
    client.who["principal"] = dict(matrix.principal_a)
    answer = client.put(f"/api/people/{matrix.account_a}/daily-limit", json={"limit": {"tokens": 10**9}})
    assert answer.status_code == 403


def test_deleting_a_person_takes_their_limit(matrix, enforced) -> None:
    from kazma_core.spaces import identity

    person = people.create_person("dana-limit", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    people.set_daily_limit(person["id"], {"tokens": 10}, by=OWNER_ACCOUNT_ID)
    people.delete_person(person["id"], by=OWNER_ACCOUNT_ID)
    assert person["id"] not in identity.read_registry()["policies"]


# ── the precondition of a second account (S1-33) ─────────────────────────


@pytest.mark.real_preconditions
def test_the_precondition_holds_while_every_person_has_a_limit(matrix) -> None:
    from kazma_core.spaces import preconditions

    holds, why = daily_limits.precondition()
    assert holds and "1,000,000 tokens" in why
    ok, detail, _fix = preconditions._built("daily_limits", "a daily spending limit per person")
    assert ok, detail


@pytest.mark.real_preconditions
def test_the_precondition_does_not_hold_with_the_ledger_off(matrix, monkeypatch) -> None:
    from kazma_core.agent import nonstop

    real = nonstop.get_nonstop_config()
    real.ledger_enabled = False
    monkeypatch.setattr(nonstop, "get_nonstop_config", lambda: real)
    holds, why = daily_limits.precondition()
    assert not holds and "ledger" in why
