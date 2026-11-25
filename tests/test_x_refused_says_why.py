"""A post X never got says why, and one that cannot go out gets no card.

Live 2026-11-25 (Telegram): publishing to X was paused after a restore, the
card still asked the owner to approve ``x_post``, the owner approved, Kazma
refused in 7 ms without contacting X -- and the turn ended with "The tool
reported a failure after invocation. Its effects are unknown". The reason
was in the recorded result and nobody was told it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from kazma_core.agent.state import initial_supervisor_state
from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.safety import call_preflight
from kazma_core.x_api import policy
from kazma_core.x_api.config import XConfig, XCredentials
from kazma_skills.native.x_publisher import tools as x_tools

PAUSED = "X publishing is paused after restore. Verify the account and explicitly resume in X Studio."


def _cfg(**kw: Any) -> XConfig:
    base: dict[str, Any] = dict(
        enabled=True, handle="@kazma", credentials=XCredentials("ck", "cs", "at", "ats"),
        max_posts_per_day=5, max_posts_per_month=100, max_mentions=2, max_cashtags=1, max_hashtags=3,
        max_chars=280, duplicate_window_days=30, kill_switch=False, account_id="1",
    )
    return XConfig(**{**base, **kw})


# ── the account's one answer ─────────────────────────────────────────────


@pytest.mark.parametrize(("kw", "words"), [
    ({"restore_paused": True}, "paused after restore"),
    ({"kill_switch": True}, "KAZMA_X_POST"),
    ({"enabled": False}, "connector is off"),
    ({"credentials": XCredentials("", "", "", "")}, "credentials incomplete"),
])
def test_the_post_the_delete_and_the_card_ask_one_question(monkeypatch, kw, words):
    cfg = _cfg(**kw)
    reason = policy.publishing_refusal(cfg)
    assert reason and words in reason
    assert policy.evaluate_post("hello", cfg=cfg).reason == reason
    assert policy.evaluate_delete(cfg=cfg).reason == reason


def test_a_publishable_account_is_not_refused():
    assert policy.publishing_refusal(_cfg()) is None
    assert policy.evaluate_delete(cfg=_cfg()).allow


# ── before the card ──────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", sorted(call_preflight.X_PUBLISH_TOOLS))
async def test_a_paused_account_gets_no_card(monkeypatch, tool):
    from kazma_core.agent.graph_tool_worker import _refuse_unrunnable_calls

    monkeypatch.setattr(policy, "get_x_config", lambda: _cfg(restore_paused=True))
    call = {"id": "c1", "name": tool, "arguments": {"text": "hello", "tweet_id": "1", "when": "2h"}}
    kept, refused = await _refuse_unrunnable_calls([call])
    assert kept == [] and PAUSED in refused[0]["content"] and refused[0]["outcome"] == "policy"
    # A publishable account's call keeps its card.
    monkeypatch.setattr(policy, "get_x_config", lambda: _cfg())
    kept, refused = await _refuse_unrunnable_calls([call])
    assert kept == [call] and refused == []


#: The X tools that ask for approval and are not held by the account's pause.
_NOT_PUBLISHING = {
    "x_cancel_scheduled_post": "it only takes a booking back; a paused account may want exactly that",
    "x_api_act": "any other X API write (a like, a follow, a message): the pause is of publishing",
}


def test_every_x_tool_that_asks_is_checked_before_its_card():
    from kazma_core.safety.hitl import TOOL_TIERS

    asking = {name for name, tier in TOOL_TIERS.items() if name.startswith("x_") and tier == "danger"}
    assert len(asking) >= 5, asking
    assert asking == call_preflight.X_PUBLISH_TOOLS | set(_NOT_PUBLISHING)
    assert call_preflight.X_PUBLISH_TOOLS <= call_preflight.PREFLIGHT_TOOLS


# ── the answer of a call that ran ────────────────────────────────────────


def _registry(monkeypatch, tool: str) -> LocalToolRegistry:
    from kazma_core.agent.tool_registry import get_tool_registry
    from kazma_core.swarm import safety as swarm_safety

    monkeypatch.setattr(swarm_safety, "get_safety", lambda: type("S", (), {"enabled": False})())

    async def runnable(*_a: Any, **_k: Any) -> None:
        return None  # the pause came after the check, or a direct call

    monkeypatch.setattr(call_preflight, "call_refusal", runnable)
    reg = LocalToolRegistry()
    reg._tools[tool] = get_tool_registry()._tools[tool]
    return reg


def _answering(monkeypatch, name: str, payload: dict[str, Any], ok: bool = False) -> None:
    async def answered(**_kw: Any) -> tuple[bool, dict[str, Any]]:
        return ok, dict(payload)

    monkeypatch.setattr(x_tools, name, answered)


@pytest.mark.parametrize("outcome", ["not_sent", "rejected", "not_on_x"])
async def test_a_post_that_certainly_did_not_happen_is_a_refusal_with_its_reason(monkeypatch, outcome):
    _answering(monkeypatch, "publish_x_post", {"posted": False, "outcome": outcome, "error": PAUSED})
    with pytest.raises(RefusedBeforeEffect) as refused:
        await x_tools.x_post("hello", proposal_id="prop_1:1")
    said = json.loads(refused.value.content())
    assert said["error"] == PAUSED and said["proposal_id"] == "prop_1:1" and said["ok"] is False
    _answering(monkeypatch, "delete_x_post", {"deleted": False, "outcome": outcome, "error": PAUSED})
    with pytest.raises(RefusedBeforeEffect):
        await x_tools.x_delete_post("1")


@pytest.mark.parametrize("payload", [
    {"posted": None, "outcome": "unknown", "error": "Send interrupted; verify on X."},
    {"posted": False, "error": "no outcome named"},
])
async def test_an_outcome_nobody_knows_stays_a_failure(monkeypatch, payload):
    _answering(monkeypatch, "publish_x_post", payload)
    assert json.loads(await x_tools.x_post("hello"))["ok"] is False  # never a refusal


async def test_a_post_that_went_out_answers_as_before(monkeypatch):
    _answering(monkeypatch, "publish_x_post", {"posted": True, "outcome": "confirmed", "tweet_id": "5"}, ok=True)
    said = json.loads(await x_tools.x_post("hello", proposal_id="prop_1:1"))
    assert said["ok"] is True and said["tweet_id"] == "5" and said["proposal_id"] == "prop_1:1"


async def test_the_turn_is_told_the_reason_and_nothing_is_called_unknown(monkeypatch):
    """Through the registry and the effect journal, as the live turn ran it."""
    from kazma_core.agent.effect_journal import execute_effect

    _answering(monkeypatch, "publish_x_post", {"posted": False, "outcome": "not_sent", "error": PAUSED})
    reg = _registry(monkeypatch, "x_post")

    class _Executor:
        async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            return await reg.execute(name, args)

    state = {**initial_supervisor_state(thread_id="t-x"), "thread_id": "t-x", "created_at": "2026-11-25",
             "iteration": 0, "tenant_id": "default"}
    monkeypatch.setattr("kazma_core.safety.commitment.authorize_effect", _allow)
    result = await execute_effect(_Executor(), state, {"id": "c1", "name": "x_post"}, {"text": "hello"})
    assert result.get("refused") and PAUSED in result["content"] and not result.get("effect_uncertain")


def _allow(*_a: Any, **kw: Any) -> Any:
    from kazma_core.safety.commitment.authorize import EffectDecision

    return EffectDecision(decision="allow", reason="stand-in", profile=None)  # type: ignore[arg-type]


async def test_a_booking_that_was_not_made_is_a_refusal(monkeypatch):
    import kazma_core.x_api.booking as booking

    monkeypatch.setattr(booking, "book_x_post", lambda **_kw: (False, {"error": PAUSED}))
    with pytest.raises(RefusedBeforeEffect) as refused:
        await x_tools.x_schedule_post("hello", "2h")
    assert PAUSED in refused.value.content()
    # A booking that is a row, and held, is not called a refusal.
    monkeypatch.setattr(booking, "book_x_post", lambda **_kw: (False, {
        "code": "booking_held", "state": "awaiting_approval", "error": "held", "operation_id": "op"}))
    assert json.loads(await x_tools.x_schedule_post("hello", "2h"))["ok"] is False
    with pytest.raises(RefusedBeforeEffect):
        await x_tools.x_cancel_scheduled_post("not a number")  # type: ignore[arg-type]
