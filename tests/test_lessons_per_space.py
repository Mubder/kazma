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
runs for, exactly as before. Every install reads it the same way: one
current, fenced copy per call, added by the supervisor, nothing baked into
the base prompt (S1-10 review). The tests named ``control`` take a guard out
(or rebuild the old order) and show what it stops; the ``without_spaces``
tests show that an install which does not enforce Spaces keeps its one Soul.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from types import SimpleNamespace

import pytest
from kazma_core.memory import recall as _recall
from kazma_core.skills import self_improvement as si
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

#: The real recall search (the fixture stubs it; one test puts it back).
_REAL_SEARCH = _recall.search

A, B = "alpha", "beta"
BASE = "BASE-PROMPT-MARKER: you are Kazma."
LESSON_A = "Alpha likes its reports as three short bullets."
LESSON_B = "Beta wants every figure with its source."
LESSON_OWNER = "The owner reads answers in Arabic first."


class _Refiner:
    def __init__(self) -> None:
        self.answer = LESSON_A
        self.calls = 0
        self.prompts: list[str] = []

    async def chat(self, messages):
        self.calls += 1
        self.prompts.append("\n".join(str(m.get("content", "")) for m in messages))
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
    return si._lessons(si._agent_evolution_block(space=space))[1]


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


def test_without_spaces_there_is_one_soul_whatever_the_turns_tenant(env) -> None:
    env.mp.setattr(scope, "_enforced", False)
    _close(A)  # a turn of an older multi-user tenant
    assert _lessons(A) == _lessons(B) == _lessons(OWNER_SPACE_ID) == [LESSON_A]
    assert env.store.get(si._AGENT_EVO_KEY)["agents"]["supervisor"]["soul"]
    assert env.store.get(si._SPACE_EVO_KEY) is None


def _scopes_the_refiner_ran_in(env) -> list[object]:
    """What the refiner's own memory search ran as, each time it asked."""
    seen: list[object] = []

    def search(*_a, **_k):
        try:
            seen.append(scope.require_scope())
        except scope.NoSpaceBound:
            seen.append("refused")
        return []

    env.mp.setattr("kazma_core.memory.recall.search", search)
    return seen


def test_a_lesson_is_learned_as_a_whole_scope(env) -> None:
    """Enforced, the background lesson task runs as the turn's space and the
    person the turn ran for; with nobody bound at the close, as the space's
    own account (its maker here)."""
    seen = _scopes_the_refiner_ran_in(env)
    with scope.bind_scope(A, "sara"):
        _close(A)
    _close(B)
    assert set(seen) == {scope.Scope(A, "sara"), scope.Scope(B, OWNER_ACCOUNT_ID)}


def test_control_a_lesson_task_bound_by_its_tenant_alone_is_refused(env) -> None:  # the old binding
    """The binding S1-10 first wrote: the tenant alone, a half scope."""
    from kazma_core.tenant_context import tenant_scope

    seen = _scopes_the_refiner_ran_in(env)

    async def as_it_was(question, answer, *, session, turn, tenant_id, asker=None):
        with tenant_scope(tenant_id):
            return await si.analyze_and_apply_chat_turn(
                user_message=question, success=True, output_snippet=answer[:600],
                source={"chat": session, "turn": turn}, space=tenant_id)

    env.mp.setattr(si, "_learn_from_turn", as_it_was)
    _close(A)
    assert "refused" in seen


def test_the_refiner_recalls_the_memory_of_the_turns_own_space(env, memory_db) -> None:
    """A real recall, not a stub: the lesson task of a turn of A asks memory
    as A, and the refiner is shown A's memory and never B's."""
    env.mp.setattr(_recall, "search", _REAL_SEARCH)
    for space, word in ((A, "alphaword"), (B, "betaword")):
        _remembered(space, f"plant-{space}", 1, f"zebra quokka pipeline supervisor {word}")
    _close(A, question="zebra quokka")
    [prompt] = env.refiner.prompts
    assert "alphaword" in prompt and "betaword" not in prompt


def test_control_a_lesson_task_with_nothing_bound_shows_the_refiner_no_memory(env, memory_db) -> None:
    """The task as it ran before S1-10 bound it: in the context close_turn
    left, nothing bound. The search is refused, quietly (DEBUG), and the
    refiner sees no memory at all."""
    env.mp.setattr(_recall, "search", _REAL_SEARCH)
    _remembered(A, "plant-a", 1, "zebra quokka pipeline supervisor alphaword")

    async def as_it_was(question, answer, *, session, turn, tenant_id, asker=None):
        return await si.analyze_and_apply_chat_turn(
            user_message=question, success=True, output_snippet=answer[:600],
            source={"chat": session, "turn": turn}, space=tenant_id)

    env.mp.setattr(si, "_learn_from_turn", as_it_was)
    _close(A, question="zebra quokka")
    [prompt] = env.refiner.prompts
    assert "alphaword" not in prompt


def test_the_account_a_spaces_own_work_acts_for(env) -> None:
    from kazma_core.config_store import get_config_store

    assert identity.acting_account(A) == OWNER_ACCOUNT_ID  # its maker
    assert identity.acting_account("telegram") == OWNER_ACCOUNT_ID  # no space: the admin

    def personal(reg):
        reg["accounts"]["sara"] = {"id": "sara", "name": "Sara", "role": "member", "enabled": True}
        reg["spaces"]["sara-own"] = {"id": "sara-own", "name": "", "kind": "sandboxed",
                                     "personal_of": "sara", "created_by": OWNER_ACCOUNT_ID}
        reg["memberships"].append({"account": "sara", "space": "sara-own", "role": "owner"})

    identity.update_registry(personal)
    assert identity.acting_account("sara-own") == "sara"  # a personal space: its person
    assert get_config_store() is not None


def test_two_spaces_taught_one_after_another_keep_both_souls(env) -> None:
    """One after another only: what this shows is that each space's lessons
    are kept apart in the one document. What another process writing at the
    same time does is the next test's."""
    assert si.apply_agent_mutation("supervisor", LESSON_A, space=A)
    assert si.apply_agent_mutation("supervisor", LESSON_B, space=B)
    assert si.apply_agent_mutation("supervisor", LESSON_OWNER, space=OWNER_SPACE_ID)
    assert (_lessons(A), _lessons(B), _lessons(OWNER_SPACE_ID)) == ([LESSON_A], [LESSON_B], [LESSON_OWNER])


#: What another process writes while this one is between its read and its
#: write: a lesson of its own, in the same space.
OTHER = "Another process's lesson: dates as YYYY-MM-DD."


def _another_process_writes_first(env, key: str, space: str) -> None:
    """Every ``atomic_update`` of *key* starts with another process's lesson
    written just before it (``_agent_evo_lock`` is this process's alone)."""
    store = env.store
    real = store.atomic_update

    def other_lesson(value):
        doc = value if isinstance(value, dict) else {}
        target = doc if space == OWNER_SPACE_ID else doc.setdefault("spaces", {}).setdefault(space, {})
        entry = target.setdefault("agents", {}).setdefault("supervisor", {"soul": "", "history": []})
        base, lessons = si._lessons(entry.get("soul") or "")
        entry["soul"] = si._render(base, [*lessons, OTHER])
        return doc

    def slipped_in(k, updater, *args, **kwargs):
        if k == key:
            real(k, other_lesson)
        return real(k, updater, *args, **kwargs)

    env.mp.setattr(store, "atomic_update", slipped_in)


@pytest.mark.parametrize("space", [A, OWNER_SPACE_ID])
def test_a_lesson_written_by_another_process_meanwhile_is_kept(env, space) -> None:
    """The read is inside the store's one step: another process's lesson,
    written after this one began, is read and kept (one space's document, the
    owner's key)."""
    key = si._AGENT_EVO_KEY if space == OWNER_SPACE_ID else si._SPACE_EVO_KEY
    assert si.apply_agent_mutation("supervisor", LESSON_B, space=space)
    _another_process_writes_first(env, key, space)
    assert si.apply_agent_mutation("supervisor", LESSON_A, space=space)
    assert _lessons(space) == [LESSON_B, OTHER, LESSON_A]


def test_control_the_old_order_loses_the_other_processs_lesson(env) -> None:
    """The order S1-10 first wrote: read the document, then write it back in
    one atomic step. Another process's lesson written in between is lost."""
    assert si.apply_agent_mutation("supervisor", LESSON_B, space=A)
    data = si._load_agent_evolution(A)  # the read, outside the store's step
    _another_process_writes_first(env, si._SPACE_EVO_KEY, A)
    data["agents"]["supervisor"]["soul"] += "\n\n[SelfImprovement] " + LESSON_A

    def put(every):
        every = dict(every) if isinstance(every, dict) else {}
        every["spaces"] = {**(every.get("spaces") or {}), A: data}
        return every

    env.store.atomic_update(si._SPACE_EVO_KEY, put)
    assert OTHER not in _lessons(A)


def test_with_no_space_bound_nothing_is_read_or_learned(env) -> None:
    assert si.apply_agent_mutation("supervisor", LESSON_A, space=A)
    assert scope.current_scope() is None
    assert si._agent_evolution_block() == ""
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


def _old_base_prompt() -> str:
    """The base prompt an older agent built: the Soul it read at start baked
    in (``agent_runner`` before the S1-10 review)."""
    return BASE + "\n\n" + si.space_lessons_note(OWNER_SPACE_ID)


STALE = "The owner wanted long answers (an older lesson, since replaced)."


@pytest.mark.asyncio
async def test_without_spaces_the_model_gets_one_current_copy_of_the_lessons(env) -> None:
    """On an install that does not enforce Spaces too: the supervisor adds
    the one Soul, current and fenced, once a call, on every surface."""
    env.mp.setattr(scope, "_enforced", False)
    assert si.apply_agent_mutation("supervisor", LESSON_OWNER)
    plain = _all_text(await _sent_messages([{"role": "user", "content": "hello"}]))
    assert plain.count(LESSON_OWNER) == 1
    assert plain.count('<kazma:data source="self_improvement"') == 1


@pytest.mark.asyncio
async def test_an_old_base_prompt_and_a_transports_copy_still_make_one_current_copy(env) -> None:
    """An agent built by an older Kazma baked the Soul it read at start, and a
    transport added its own: the model gets the current lessons, once."""
    env.mp.setattr(scope, "_enforced", False)
    assert si.apply_agent_mutation("supervisor", STALE)
    baked = _old_base_prompt()  # read at start: holds the stale lesson
    forgot = si.forget_text("long answers")
    assert forgot
    assert si.apply_agent_mutation("supervisor", LESSON_OWNER)
    turn = [_transport_note(OWNER_SPACE_ID), {"role": "user", "content": "hello"}]
    text = _all_text(await _sent_messages(turn, base=baked))
    assert text.count(LESSON_OWNER) == 1 and STALE not in text
    assert text.count('<kazma:data source="self_improvement"') == 1
    assert BASE in text


@pytest.mark.asyncio
async def test_control_without_the_cut_the_old_base_prompt_brings_a_second_stale_copy(env) -> None:
    env.mp.setattr(scope, "_enforced", False)
    assert si.apply_agent_mutation("supervisor", STALE)
    baked = _old_base_prompt()
    si.forget_text("long answers")
    assert si.apply_agent_mutation("supervisor", LESSON_OWNER)
    env.mp.setattr(si, "without_baked_soul", lambda prompt: prompt)  # the cut, out
    text = _all_text(await _sent_messages([{"role": "user", "content": "hello"}], base=baked))
    assert STALE in text
    assert text.count('<kazma:data source="self_improvement"') == 2


def test_a_baked_soul_is_cut_whole() -> None:
    from kazma_core.safety.prompt_fence import format_untrusted_block

    block = format_untrusted_block("[SelfImprovement] one\n\n[SelfImprovement] two", source="self_improvement")
    other = format_untrusted_block("a page", source="web")
    assert si.without_baked_soul(f"{BASE}\n\n{block}") == BASE
    assert si.without_baked_soul(f"{BASE}\n\n{block}\n\n{other}\n\n{block}") == f"{BASE}\n\n{other}"
    assert si.without_baked_soul(BASE) == BASE and si.without_baked_soul("") == ""
    # It runs on every install: what is not a whole block is left as it is.
    truncated = f"{BASE}\n\n{block[:120]}"  # an opener with no close
    assert si.without_baked_soul(truncated) == truncated
    named = f"{BASE}\n- The lessons arrive as {si._SOUL_FENCE_OPEN} blocks.\nMore rules.\n\n"
    assert si.without_baked_soul(named) == named


@pytest.mark.parametrize("enforced", [False, True])
def test_the_agent_bakes_no_soul_into_its_base_prompt(env, enforced) -> None:
    """The agent itself, built with lessons stored: its base prompt holds
    none (the agent before the S1-10 review baked the owner's Soul on an
    install that does not enforce Spaces)."""
    from kazma_core.agent_runner import KazmaAgent

    env.mp.setattr(scope, "_enforced", enforced)
    if enforced:
        _teach_all()
    else:
        assert si.apply_agent_mutation("supervisor", LESSON_OWNER)
    agent = KazmaAgent()
    assert "self_improvement" not in agent.system_prompt
    assert not any(lesson in agent.system_prompt for lesson in (LESSON_A, LESSON_B, LESSON_OWNER))


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


def test_without_spaces_a_forget_edits_the_one_soul(env) -> None:
    env.mp.setattr(scope, "_enforced", False)
    assert si.apply_agent_mutation("supervisor", "Use the codeword BLUE-HERON in the header.")
    assert si.forget_text("blue-heron", tenant_id="some-older-tenant") == 2
    assert _lessons(OWNER_SPACE_ID) == []


@pytest.fixture()
def memory_db(env):
    """Memory's own writer and forget on the test's database."""
    import os

    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema

    ops = sqlite3.connect(os.environ["KAZMA_MEMORY_OPS_DB"])
    ensure_ops_schema(ops)
    ops.commit()
    ops.close()
    env.mp.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    dual_write._reset_mirror()
    yield
    dual_write._reset_mirror()


def _remembered(space: str, chat: str, turn: int, question: str) -> str:
    from kazma_core.memory.dual_write import mirror_episode

    eid = mirror_episode(session_id=chat, turn_number=turn, user_text=question,
                         assistant_text="noted", tenant_id=space)
    assert eid
    return eid


def test_forgetting_a_turn_in_memory_takes_back_its_spaces_lesson(env, memory_db) -> None:
    """Memory's own forget, run: forgetting a turn of A takes back the lesson
    that turn taught A, and B's lesson from a chat of the same key stays;
    forgetting B's chat takes back B's."""
    from kazma_core.memory.forget import forget_chat, forget_episode

    for space, lesson in ((A, LESSON_A), (B, LESSON_B)):
        assert si.apply_agent_mutation("supervisor", lesson, space=space,
                                       source={"chat": "chat-1", "turn": 1})
    episode = _remembered(A, "chat-1", 1, "how should the weekly report look?")
    _remembered(B, "chat-1", 1, "how should the weekly report look?")
    with _in(A):
        forget_episode(episode, tenant_id=A)
    assert _lessons(A) == [] and _lessons(B) == [LESSON_B]
    with _in(B):
        forget_chat("chat-1", tenant_id=B)
    assert _lessons(B) == []


def test_control_a_forget_that_names_no_space_reads_every_soul(env) -> None:
    """What the tenant a forget names decides: with none (Kazma's own
    replay), both spaces' lessons from that chat go."""
    for space, lesson in ((A, LESSON_A), (B, LESSON_B)):
        assert si.apply_agent_mutation("supervisor", lesson, space=space,
                                       source={"chat": "chat-1", "turn": 1})
    assert si.forget_lessons_from(["chat-1"], 1) == 2
    assert _lessons(A) == _lessons(B) == []


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


def _held(env, lesson: str = LESSON_OWNER) -> str:
    env.mp.setattr(scope, "_enforced", False)
    env.mp.setattr(si, "_soul_requires_confirm", lambda: True)
    held = si.mint_soul_commitment(lesson, agent_id="supervisor")
    assert held
    return held


def test_an_applied_lesson_is_not_rejected(env) -> None:
    """Reject acts on a held lesson only: one already confirmed is in the
    Soul, and "rejected" over it said what had not happened."""
    from kazma_core.safety.commitment.store import get_commitment

    held = _held(env)
    client = _commitment_app()
    assert client.post(f"/api/commitment/soul/{held}/confirm").json()["applied"] is True
    refused = client.post(f"/api/commitment/soul/{held}/reject")
    assert refused.status_code == 409 and refused.json()["code"] == "not_held"
    assert refused.json()["status"] == "committed"
    assert get_commitment(held).status == "committed" and _lessons(OWNER_SPACE_ID) == [LESSON_OWNER]


def test_a_second_confirm_applies_nothing_twice(env) -> None:
    held = _held(env)
    client = _commitment_app()
    assert client.post(f"/api/commitment/soul/{held}/confirm").status_code == 200
    again = client.post(f"/api/commitment/soul/{held}/confirm")
    assert again.status_code == 409 and again.json()["code"] == "not_held"
    assert _lessons(OWNER_SPACE_ID) == [LESSON_OWNER]
    # A rejected lesson is not confirmed afterwards either.
    rejected = _held(env, LESSON_A)
    assert client.post(f"/api/commitment/soul/{rejected}/reject").status_code == 200
    late = client.post(f"/api/commitment/soul/{rejected}/confirm")
    assert late.status_code == 409 and late.json()["status"] == "aborted"
    assert LESSON_A not in _lessons(OWNER_SPACE_ID)


def test_a_decision_moves_a_lesson_only_from_held(env) -> None:
    """The store's compare-and-set: a confirm racing a reject leaves the
    lesson in one state, the one the first decision chose."""
    from kazma_core.safety.commitment.store import get_commitment, update_status

    held = _held(env)
    assert update_status(held, "aborted", event_type="soul_rejected", expected="needs_confirm").status == "aborted"
    assert si.confirm_soul_delta(held) is False
    assert get_commitment(held).status == "aborted"


def test_control_without_the_status_check_an_applied_lesson_is_rejected(env) -> None:
    """The rule before the review: the act and the space, not the status."""
    from kazma_core.safety.commitment.store import get_commitment, update_status

    held = _held(env)
    client = _commitment_app()
    assert client.post(f"/api/commitment/soul/{held}/confirm").json()["applied"] is True
    update_status(held, "aborted", event_type="soul_rejected")  # what the old route did
    assert get_commitment(held).status == "aborted" and _lessons(OWNER_SPACE_ID) == [LESSON_OWNER]


def test_one_account_keeps_its_one_queue(env) -> None:
    env.mp.setattr(scope, "_enforced", False)
    env.mp.setattr(si, "_soul_requires_confirm", lambda: True)
    held = si.mint_soul_commitment(LESSON_OWNER, agent_id="supervisor", space="an-older-tenant")
    client = _commitment_app()
    listed = client.get("/api/commitment/soul/pending").json()
    assert [row["commitment_id"] for row in listed["pending"]] == [held]
    assert client.post(f"/api/commitment/soul/{held}/confirm").json()["applied"] is True
    assert _lessons(OWNER_SPACE_ID) == [LESSON_OWNER]
