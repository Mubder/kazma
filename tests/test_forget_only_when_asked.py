"""A turn is kept out of memory only when the user asked to forget (the
memory plan's finding 16, 2026-11-22).

A memory tool that forgets keeps its own turn out of memory
(``forget.keep_turn_out``): written after the forget, "Forget every memory
that mentions BLUE-HERON-42" became a memory holding the codeword. Found in
finding 10's real-use runs: told "انتقلت إلى بورتو الشهر الماضي", the model
retired two older memories on its own (``memory_invalidate`` with their
ids), and the move itself was kept out -- the quick pass wrote no new home
("no facts from ... turn 1: kept out of memory").

Now the turn is kept out only when the user's own words ask to forget
(``memory.forget_request``, English and Arabic, read through
``memory.own_words``). Each case runs a real supervisor graph with a scripted
model that calls the real memory tool, then closes the turn through
``close_turn`` as every transport does, and reads the memory database back.
Today's rule (every forget keeps the turn out) is the negative control: the
update turn loses its new home.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from kazma_core.memory.forget_request import _asks_to_forget, is_forget_request
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

# ── the reader ──────────────────────────────────────────────────────────────

#: (English, Arabic): each asks Kazma to forget.
ASKS = [
    ("Forget that I live in Paris.", "انسى إني ساكن في باريس."),
    ("Please forget my old address.", "انسى عنواني القديم لو سمحت."),
    ("Can you forget what I said about Dana?", "تقدر تنسى اللي قلته عن دانة؟"),
    ("Delete my phone number from your memory.", "احذف رقمي من ذاكرتك."),
    ("Don't save this.", "لا تحفظ هذا."),
    ("Kazma, forget the codeword.", "كازما، انسى كلمة السر."),
]
#: (English, Arabic): none asks Kazma to forget anything.
DOES_NOT_ASK = [
    ("I moved to Porto last month.", "انتقلت إلى بورتو الشهر الماضي."),
    ("I live in Rotterdam now.", "ساكن في الرياض الحين."),
    ("I forget things easily.", "أنا أنسى كثير."),
    ("Did I forget my keys at the office?", "هل نسيت مفاتيحي في المكتب؟"),
    ("Don't forget my birthday.", "لا تنسى عيد ميلادي."),
    ("Delete the file report.txt.", "امسح الملف."),
    ("How do I forget a Wi-Fi network on my phone?", "كيف أخلي الجوال ينسى شبكة الواي فاي؟"),
]


@pytest.mark.parametrize(("english", "arabic"), ASKS)
def test_a_request_to_forget_is_read_in_both_languages(english, arabic):
    assert is_forget_request(english) and is_forget_request(arabic)


@pytest.mark.parametrize(("english", "arabic"), DOES_NOT_ASK)
def test_a_statement_or_question_asks_nothing_in_both_languages(english, arabic):
    assert not is_forget_request(english) and not is_forget_request(arabic)


@pytest.mark.parametrize("text", [
    "My sister said: forget it, he is not worth it.",
    "Translate to French: forget that.",
    "قالت أختي: انسى الموضوع.",
    "ترجم: انسى الموضوع.",
])
def test_only_the_users_own_words_count(text):
    """A "forget" inside a quote or a text to translate is not the user's
    request; read raw it would be (the control)."""
    assert not is_forget_request(text)
    assert _asks_to_forget(text)


# ── the real turn ───────────────────────────────────────────────────────────

#: (the home held, the update turn, the new home): English and Arabic.
UPDATES = [
    ("I live in Lisbon.", "Lisbon", "I live in Rotterdam now.", "Rotterdam"),
    ("ساكن في الكويت.", "الكويت", "ساكن في الرياض الحين.", "الرياض"),
]
#: (the home held, the turn asking to forget it): English and Arabic.
FORGETS = [
    ("I live in Lisbon.", "Lisbon", "Forget that I live in Lisbon."),
    ("ساكن في الكويت.", "الكويت", "انسى إني ساكن في الكويت."),
]


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    for var in ("KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    # A chat's keys come from the chat store; these chats are only in memory.
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key])
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db)
    from tests._no_models import finish_turn_memory

    finish_turn_memory()
    dual_write._reset_mirror()
    ops.close()
    db.close()


def _held(said: str, home: str) -> tuple[str, str]:
    """The home the user stated in an earlier chat: its memory and its fact."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import mirror_episode

    episode = mirror_episode(session_id="old-chat", turn_number=1, user_text=said,
                             assistant_text="Noted.", tenant_id="default")
    with sqlite3.connect(__import__("os").environ["KAZMA_MEMORY_STATE_DB"]) as conn:
        conn.row_factory = sqlite3.Row
        fact = mutate_belief(conn, "user", "lives_in", home, predicate_type="functional",
                             extraction_method="user_stated", source_session="old-chat", source_turn=1)
    return episode, fact["belief_id"]


class _Script:
    """A model that makes one call, then answers."""

    def __init__(self, name: str, args: dict):
        self.call = (name, args)
        self.made = False

    async def chat(self, *, messages, tools=None, model=None, **kwargs):
        from kazma_core.llm_provider import LLMResponse, ToolCall

        if not self.made:
            self.made = True
            name, args = self.call
            return LLMResponse(content="", tool_calls=[ToolCall(id="call-1", name=name, arguments=args)],
                               finish_reason="tool_calls", model="stub", usage={"total_tokens": 1},
                               cost_usd=0.0)
        return LLMResponse(content="Done.", tool_calls=[], finish_reason="stop", model="stub",
                           usage={"total_tokens": 1}, cost_usd=0.0)


def _graph(script: _Script):
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.tool_builtins.memory import register_memory_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.tracing import KazmaTracer

    registry = LocalToolRegistry(include_builtins=False)
    register_memory_tools(registry)
    return build_supervisor_graph(
        llm=script, system_prompt="base", tool_definitions=registry.get_tool_definitions(),
        tool_executor=registry, cost_breaker=create_cost_breaker(),
        authority=create_authority(model="test", window=128000), tracer=KazmaTracer(backend="console"),
        checkpointer=InMemorySaver(),
    )


def _turn(text: str, call: tuple[str, dict], thread: str) -> dict:
    """One turn through the real graph, closed by ``close_turn``; the memory
    tool's answer."""
    from kazma_core.agent.state import initial_supervisor_state
    from kazma_ui.turn_runtime import close_turn

    from tests._no_models import finish_turn_memory

    graph = _graph(_Script(*call))
    config = {"configurable": {"thread_id": thread}}

    async def run():
        state = {**initial_supervisor_state(thread_id=thread, max_iterations=15),
                 "messages": [{"role": "user", "content": text}]}
        await graph.ainvoke(state, config)
        await close_turn(graph, config, thread_id=thread)
        return (await graph.aget_state(config)).values["messages"]

    messages = asyncio.run(run())
    finish_turn_memory()
    answers = [m.get("content") for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert answers, messages
    return json.loads(answers[0])


def _homes(db, thread: str) -> list[str]:
    return [r["object"] for r in db.execute(
        "SELECT object FROM beliefs WHERE subject = 'user' AND predicate = 'lives_in' "
        "AND source_session = ? AND valid_until IS NULL AND invalidated_at IS NULL", (thread,))]


def _turn_kept(db, thread: str) -> bool:
    from kazma_core.memory.forget import FORGOTTEN_TIER

    tiers = [r[0] for r in db.execute("SELECT tier FROM episodes WHERE session_id = ?", (thread,))]
    return bool(tiers) and FORGOTTEN_TIER not in tiers


def _retire_the_old_memory(said: str, home: str) -> tuple[str, dict]:
    """The live case: the model retires the old memory by its id."""
    episode, _fact = _held(said, home)
    return "memory_invalidate", {"belief_id": episode}


@pytest.mark.parametrize(("said", "home", "update", "new_home"), UPDATES)
def test_an_update_whose_model_retires_the_old_memory_stores_the_new_home(mem, said, home, update, new_home):
    thread = f"update-{new_home}"
    answer = _turn(update, _retire_the_old_memory(said, home), thread)
    assert answer["ok"] and answer["this_turn_kept_out"] is False
    assert _homes(mem.db, thread) == [new_home]
    assert _turn_kept(mem.db, thread)


@pytest.mark.parametrize(("said", "home", "update", "new_home"), UPDATES)
def test_an_update_whose_model_retires_the_old_fact_and_its_turn_stores_the_new_home(
        mem, said, home, update, new_home):
    """The other tool path: the old fact retired with its source turn."""
    thread = f"update-fact-{new_home}"
    _episode, fact = _held(said, home)
    answer = _turn(update, ("memory_invalidate", {"belief_id": fact, "forget_source_turn": True}), thread)
    assert answer["ok"] and answer["this_turn_kept_out"] is False
    assert _homes(mem.db, thread) == [new_home]
    assert _turn_kept(mem.db, thread)


@pytest.mark.parametrize(("said", "home", "ask"), FORGETS)
def test_a_turn_asking_to_forget_is_still_kept_out(mem, said, home, ask):
    thread = f"forget-{home}"
    _episode, fact = _held(said, home)
    answer = _turn(ask, ("memory_invalidate", {"belief_id": fact, "forget_source_turn": True}), thread)
    assert answer["ok"] and answer["this_turn_kept_out"] is True
    assert _homes(mem.db, thread) == []
    assert not _turn_kept(mem.db, thread)


@pytest.mark.parametrize(("said", "home", "update", "new_home"), UPDATES)
def test_control_today_s_rule_keeps_the_update_out(mem, monkeypatch, said, home, update, new_home):
    """Every forget keeps its turn out (before 2026-11-22): the move itself
    is kept out and no new home is written, in either language."""
    monkeypatch.setattr("kazma_core.memory.forget_request.is_forget_request", lambda text: True)
    thread = f"control-{new_home}"
    answer = _turn(update, _retire_the_old_memory(said, home), thread)
    assert answer["this_turn_kept_out"] is True
    assert _homes(mem.db, thread) == []
    assert not _turn_kept(mem.db, thread)


def test_every_memory_tool_that_keeps_a_turn_out_asks_the_reader():
    """The one place that keeps a forgetting turn out asks whether the user
    asked; no memory tool calls ``keep_turn_out`` around it."""
    from pathlib import Path

    from kazma_core.agent.tool_builtins import memory as tools_mod

    source = Path(tools_mod.__file__).read_text(encoding="utf-8")
    assert source.count("keep_turn_out(") == 1
    body = source[source.index("def _keep_this_turn_out"):]
    body = body[:body.index("\n    def ", 1)]
    assert "is_forget_request(question)" in body and "keep_turn_out(" in body
