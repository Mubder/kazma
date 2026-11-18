"""Lessons (the Soul) are per space (Spaces S1-10).

A lesson Kazma learns from a finished turn joins every later prompt. There
was one Soul for the install: a lesson learned in one person's chat reached
everyone's prompts. Once Spaces is enforced each space has its own, and:

* a turn teaches the Soul of its own space (through the real hand-off,
  ``close_turn``, with a scripted refiner);
* a turn reads the lessons of its own space and no other (through the real
  supervisor graph, with a recording model), whatever a transport added and
  whatever Soul the agent baked into its base prompt when it was built;
* a forget in one space edits that space's lessons only;
* a lesson held for confirmation is listed, confirmed and rejected in its
  own space.

An install that does not enforce Spaces has one Soul, whatever tenant a turn
runs for, exactly as before: that is each rule's negative control.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from types import SimpleNamespace

import pytest
from kazma_core.skills import self_improvement as si
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

A, B = "alpha", "beta"
BASE = "BASE-PROMPT-MARKER: you are Kazma."
LESSON_A = "Alpha likes its reports as three short bullets."
LESSON_B = "Beta wants every figure with its source."
LESSON_OWNER = "The owner reads answers in Arabic first."


class _Refiner:
    def __init__(self) -> None:
        self.answer = LESSON_A
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        return SimpleNamespace(content=self.answer)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from kazma_core.config_store import get_config_store
    from kazma_core.memory import consolidator
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "1")
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setattr(consolidator, "_schedule_post_turn_memory", lambda *a, **k: None)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    ensure_primary_schema(db)
    db.close()
    refiner = _Refiner()
    monkeypatch.setattr(si, "_llm_for_delta", lambda: refiner)
    monkeypatch.setattr("kazma_core.memory.recall.search", lambda *a, **k: [])
    monkeypatch.setattr(si, "_learned", type(si._learned)())
    monkeypatch.setattr(si, "_soul_requires_confirm", lambda: False)
    store = get_config_store()
    for key in (identity.REGISTRY_KEY, si._AGENT_EVO_KEY, si._SPACE_EVO_KEY):
        store.delete(key)
    for space_id in (A, B):
        identity.create_space(space_id.title(), created_by=OWNER_ACCOUNT_ID, space_id=space_id)
    monkeypatch.setattr(scope, "_enforced", True)
    yield SimpleNamespace(refiner=refiner, mp=monkeypatch, store=store)
    for key in (identity.REGISTRY_KEY, si._AGENT_EVO_KEY, si._SPACE_EVO_KEY):
        store.delete(key)


def _in(space: str):
    return scope.bind_scope(space, OWNER_ACCOUNT_ID)


def _lessons(space: str) -> list[str]:
    return si._lessons(si.get_agent_evolution_block(space=space))[1]


# ── learning: the real hand-off ──────────────────────────────────────────


class _Graph:
    def __init__(self, values):
        self.snap = SimpleNamespace(values=values, next=(), tasks=[])

    async def aget_state(self, config):
        return self.snap


def _close(space: str, question: str = "how should the weekly report look?") -> str:
    """``close_turn`` as every transport runs it for a finished turn of
    *space*, then the lesson task it started. Returns the chat's id."""
    from kazma_core.background import background_tasks
    from kazma_ui.turn_runtime import close_turn

    thread = f"web-{uuid.uuid4().hex[:8]}"
    values = {
        "messages": [{"role": "user", "content": question},
                     {"role": "assistant", "content": "Three bullets, each one line."}],
        "_post_turn_memory": {"session_id": thread, "turn": 1, "tenant_id": space},
    }

    async def run():
        await close_turn(_Graph(values), {"configurable": {"thread_id": thread}}, thread_id=thread)
        mine = [t for t in background_tasks() if t.get_name().startswith("self-improvement")]
        await asyncio.gather(*mine)

    asyncio.run(run())
    return thread


def test_a_turn_teaches_the_soul_of_its_own_space(env) -> None:
    thread = _close(A)
    assert env.refiner.calls == 1
    assert _lessons(A) == [LESSON_A]
    assert _lessons(B) == [] and _lessons(OWNER_SPACE_ID) == []
    # The owner's Soul keeps its own settings key; the spaces share one document.
    assert env.store.get(si._AGENT_EVO_KEY) in (None, {}, {"agents": {}})
    held = env.store.get(si._SPACE_EVO_KEY)["spaces"]
    assert list(held) == [A]
    assert held[A]["agents"]["supervisor"]["history"][-1]["source"] == {"chat": thread, "turn": 1}


def test_control_without_spaces_there_is_one_soul_whatever_the_turns_tenant(env) -> None:
    env.mp.setattr(scope, "_enforced", False)
    _close(A)  # a turn of an older multi-user tenant
    assert _lessons(A) == _lessons(B) == _lessons(OWNER_SPACE_ID) == [LESSON_A]
    assert env.store.get(si._AGENT_EVO_KEY)["agents"]["supervisor"]["soul"]
    assert env.store.get(si._SPACE_EVO_KEY) is None


def test_two_spaces_taught_one_after_another_keep_both_souls(env) -> None:
    assert si.apply_agent_mutation("supervisor", LESSON_A, space=A)
    assert si.apply_agent_mutation("supervisor", LESSON_B, space=B)
    assert si.apply_agent_mutation("supervisor", LESSON_OWNER, space=OWNER_SPACE_ID)
    assert (_lessons(A), _lessons(B), _lessons(OWNER_SPACE_ID)) == ([LESSON_A], [LESSON_B], [LESSON_OWNER])


def test_with_no_space_bound_nothing_is_read_or_learned(env) -> None:
    assert si.apply_agent_mutation("supervisor", LESSON_A, space=A)
    assert scope.current_scope() is None
    assert si.get_agent_evolution_block() == ""
    assert si.apply_agent_mutation("supervisor", LESSON_B) is False
    assert env.store.get(si._AGENT_EVO_KEY) in (None, {}, {"agents": {}})


# ── reading: the real supervisor graph ───────────────────────────────────


async def _sent_messages(messages: list[dict], base: str = BASE) -> list[dict]:
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.llm_provider import LLMResponse
    from kazma_core.tracing import KazmaTracer

    seen: list[list[dict]] = []

    class RecordingLLM:
        async def chat(self, *, messages, tools=None, model=None, **kwargs):
            seen.append([dict(m) for m in messages])
            return LLMResponse(content="Done.", tool_calls=[], finish_reason="stop",
                               model="stub", usage={"total_tokens": 1}, cost_usd=0.0)

    graph = build_supervisor_graph(
        llm=RecordingLLM(), system_prompt=base, tool_definitions=[],
        tool_executor=LocalToolRegistry(include_builtins=False),
        cost_breaker=create_cost_breaker(), authority=create_authority(model="test", window=128000),
        tracer=KazmaTracer(backend="console"),
    )
    await graph.ainvoke({"messages": messages}, {"configurable": {"thread_id": f"t-{uuid.uuid4().hex[:6]}"}})
    assert seen, "the model was not called"
    return seen[0]


def _all_text(sent: list[dict]) -> str:
    return "\n".join(str(m.get("content")) for m in sent)


def _teach_all() -> None:
    assert si.apply_agent_mutation("supervisor", LESSON_A, space=A)
    assert si.apply_agent_mutation("supervisor", LESSON_B, space=B)
    assert si.apply_agent_mutation("supervisor", LESSON_OWNER, space=OWNER_SPACE_ID)


def _transport_note(space: str) -> dict:
    """The system note a transport adds: the fenced Soul it read."""
    return {"role": "system", "content": si.space_lessons_note(space)}


@pytest.mark.asyncio
async def test_a_lesson_learned_in_one_space_is_never_in_anothers_prompt(env) -> None:
    _teach_all()
    turn = [{"role": "user", "content": "write the weekly report"}]
    for space, own, others in ((A, LESSON_A, (LESSON_B, LESSON_OWNER)),
                               (B, LESSON_B, (LESSON_A, LESSON_OWNER)),
                               (OWNER_SPACE_ID, LESSON_OWNER, (LESSON_A, LESSON_B))):
        with _in(space):
            text = _all_text(await _sent_messages(list(turn)))
        assert text.count(own) == 1, space
        assert not any(other in text for other in others), space
        assert '<kazma:data source="self_improvement"' in text  # fenced: data, not instructions


@pytest.mark.asyncio
async def test_a_transports_copy_of_another_spaces_lessons_is_replaced(env) -> None:
    """A transport adds the Soul it read for its request. Whatever it read,
    the turn's own space decides."""
    _teach_all()
    turn = [_transport_note(B), {"role": "user", "content": "write the weekly report"}]
    with _in(A):
        text = _all_text(await _sent_messages(turn))
    assert LESSON_B not in text and text.count(LESSON_A) == 1


@pytest.mark.asyncio
async def test_a_soul_baked_into_the_base_prompt_reaches_no_other_space(env) -> None:
    """The agent bakes the Soul it read at build time into its base prompt.
    Built before Spaces was enforced, that is the owner's."""
    _teach_all()
    baked = BASE + "\n\n" + si.space_lessons_note(OWNER_SPACE_ID)
    assert LESSON_OWNER in baked
    with _in(B):
        sent = await _sent_messages([{"role": "user", "content": "hello"}], base=baked)
    text = _all_text(sent)
    assert LESSON_OWNER not in text and text.count(LESSON_B) == 1
    assert BASE in str(sent[0].get("content"))  # the base prompt itself is still first


@pytest.mark.asyncio
async def test_a_space_with_no_lessons_gets_none(env) -> None:
    assert si.apply_agent_mutation("supervisor", LESSON_A, space=A)
    with _in(B):
        text = _all_text(await _sent_messages([_transport_note(A), {"role": "user", "content": "hello"}]))
    assert LESSON_A not in text and "self_improvement" not in text


@pytest.mark.asyncio
async def test_control_without_spaces_the_supervisor_changes_nothing(env) -> None:
    """One Soul: the transport's note and the baked copy are what the model
    gets, as before Spaces."""
    env.mp.setattr(scope, "_enforced", False)
    assert si.apply_agent_mutation("supervisor", LESSON_OWNER)
    note = _transport_note(OWNER_SPACE_ID)
    baked = BASE + "\n\n" + note["content"]
    sent = await _sent_messages([note, {"role": "user", "content": "hello"}], base=baked)
    assert _all_text(sent).count(LESSON_OWNER) == 2  # the baked copy and the transport's
    bare = await _sent_messages([{"role": "user", "content": "hello"}])
    assert LESSON_OWNER not in _all_text(bare)  # the supervisor adds none itself


def test_a_baked_soul_is_cut_whole() -> None:
    from kazma_core.safety.prompt_fence import format_untrusted_block

    block = format_untrusted_block("[SelfImprovement] one\n\n[SelfImprovement] two", source="self_improvement")
    other = format_untrusted_block("a page", source="web")
    assert si.without_baked_soul(f"{BASE}\n\n{block}") == BASE
    assert si.without_baked_soul(f"{BASE}\n\n{block}\n\n{other}\n\n{block}") == f"{BASE}\n\n{other}"
    assert si.without_baked_soul(BASE) == BASE and si.without_baked_soul("") == ""
    cut = si.without_baked_soul(f"{BASE}\n\n{block[:120]}")  # an opener with no end
    assert cut == BASE


def test_the_agent_bakes_no_soul_once_spaces_is_enforced() -> None:
    """Source check on the one place a Soul is baked into the base prompt."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "kazma-core/kazma_core/agent_runner.py").read_text(
        encoding="utf-8")
    at = source.index('get_agent_evolution_block("supervisor")')
    assert "spaces_enforced()" in source[at - 120:at]


# ── forgetting ───────────────────────────────────────────────────────────


def test_a_forget_in_one_space_leaves_the_others_lessons(env) -> None:
    for space in (A, B):
        assert si.apply_agent_mutation("supervisor", "Use the codeword BLUE-HERON in the header.", space=space,
                                       source={"chat": "same-key", "turn": 1})
    assert si.forget_text("blue-heron", tenant_id=A) == 2  # the lesson and its history entry
    assert _lessons(A) == [] and len(_lessons(B)) == 1
    assert si.forget_lessons_from(["same-key"], None, tenant_id=OWNER_SPACE_ID) == 0
    assert len(_lessons(B)) == 1
    assert si.forget_lessons_from(["same-key"], 1, tenant_id=B) == 1
    assert _lessons(B) == []


def test_kazmas_own_forget_reaches_every_soul(env) -> None:
    """The forget journal's replay names no tenant: every Soul is read."""
    for space in (A, B, OWNER_SPACE_ID):
        assert si.apply_agent_mutation("supervisor", "Mention ZEBRA-9 in every answer.", space=space)
    assert si.forget_text("zebra-9") == 6
    assert _lessons(A) == _lessons(B) == _lessons(OWNER_SPACE_ID) == []


def test_control_without_spaces_a_forget_edits_the_one_soul(env) -> None:
    env.mp.setattr(scope, "_enforced", False)
    assert si.apply_agent_mutation("supervisor", "Use the codeword BLUE-HERON in the header.")
    assert si.forget_text("blue-heron", tenant_id="some-older-tenant") == 2
    assert _lessons(OWNER_SPACE_ID) == []


def test_forgetting_a_turn_in_memory_takes_back_its_spaces_lesson(env) -> None:
    """Through memory's own forget: the tenant it names is the Soul's space."""
    import inspect

    from kazma_core.memory import forget

    source = inspect.getsource(forget)
    assert source.count("forget_lessons_from(keys, turn, tenant_id=owner)") == 1
    assert source.count("forget_lessons_from(keys, None, tenant_id=owner)") == 1
    assert source.count("forget_text(needle, tenant_id=caller)") == 1


# ── lessons held for confirmation ────────────────────────────────────────


def test_a_held_lesson_is_its_spaces(env) -> None:
    from kazma_core.safety.commitment.store import get_commitment, list_pending_soul

    env.mp.setattr(si, "_soul_requires_confirm", lambda: True)
    held = si.mint_soul_commitment(LESSON_A, agent_id="supervisor", space=A)
    assert get_commitment(held).tenant_id == A
    assert [c.commitment_id for c in list_pending_soul(A)] == [held]
    assert list_pending_soul(B) == [] and list_pending_soul(OWNER_SPACE_ID) == []
    # Held: not applied until confirmed, then applied to its own space.
    assert si.apply_agent_mutation("supervisor", LESSON_A, commitment_id=held, space=A) is False
    assert si.confirm_soul_delta(held)
    assert si.apply_agent_mutation("supervisor", LESSON_A, commitment_id=held, space=A)
    assert _lessons(A) == [LESSON_A] and _lessons(B) == []


def _commitment_app():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.commitment_api import create_commitment_router

    app = FastAPI()
    app.include_router(create_commitment_router())
    return TestClient(app, raise_server_exceptions=False)


def test_reject_aborts_only_a_held_lesson(env) -> None:
    """The route aborted whatever commitment the id named: a reminder being
    confirmed, any other act."""
    from kazma_core.safety.commitment.store import Commitment, create_commitment, get_commitment

    env.mp.setattr(scope, "_enforced", False)  # one account: the weakness needed no Spaces
    other = Commitment(thread_id="t1", act="remind", goal_text="remind me at nine",
                       tool_name="schedule_task", args_digest="", tenant_id=OWNER_SPACE_ID, slots={})
    other.status = "needs_confirm"
    cid = create_commitment(other)
    client = _commitment_app()
    answer = client.post(f"/api/commitment/soul/{cid}/reject")
    assert answer.status_code == 404 and answer.json()["code"] == "not_found"
    assert get_commitment(cid).status == "needs_confirm"
    env.mp.setattr(si, "_soul_requires_confirm", lambda: True)
    held = si.mint_soul_commitment(LESSON_OWNER, agent_id="supervisor")
    assert client.post(f"/api/commitment/soul/{held}/reject").status_code == 200
    assert get_commitment(held).status == "aborted"


def test_control_one_account_keeps_its_one_queue(env) -> None:
    env.mp.setattr(scope, "_enforced", False)
    env.mp.setattr(si, "_soul_requires_confirm", lambda: True)
    held = si.mint_soul_commitment(LESSON_OWNER, agent_id="supervisor", space="an-older-tenant")
    client = _commitment_app()
    listed = client.get("/api/commitment/soul/pending").json()
    assert [row["commitment_id"] for row in listed["pending"]] == [held]
    assert client.post(f"/api/commitment/soul/{held}/confirm").json()["applied"] is True
    assert _lessons(OWNER_SPACE_ID) == [LESSON_OWNER]
