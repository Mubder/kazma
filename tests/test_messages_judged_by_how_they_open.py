"""A system message is judged by how it opens, never by a phrase in its text.

Found measuring what a chat turn sends (2026-11-15): the language lock's
replace step removed every system message holding the words "LANGUAGE LOCK",
and Kazma's base prompt holds them ("A LANGUAGE LOCK system message for this
turn is absolute"). So the first model call of every turn, on every surface,
went without the base prompt -- Kazma's identity, product knowledge, the
dialect directive and the date -- and only a tool round had it. The prompt
cache's packer read the same words and filed the base prompt as per-turn text.
The base prompt also carried the workspace block it saw at server start: a
second copy beside the web's current one, and a stale one after Switch Repo.

Every filter now asks ``turn_notes.opens_with``; the base prompt here names
every opening and marker a filter knows, through the real supervisor graph,
in English and Arabic, with a tool round. The old rule is the negative control.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest
from kazma_core.llm_provider import LLMResponse, ToolCall

REPO = Path(__file__).resolve().parents[1]
ENV_OPENING = "## You are Kazma (this process)"

#: Checks of a phrase inside a message's text that are not about system notes.
DECLARED = {
    # A refusal Kazma itself wrote into a TOOL result ("Error: Your previous
    # response was TRUNCATED ..."): never a system message.
    ("kazma-core/kazma_core/agent/graph_tool_worker.py", "_severed_again"),
}


def _planted_phrases() -> list[str]:
    from kazma_core import prompt_cache
    from kazma_core.agent.turn_input import WORKING_MEMORY_MARKER
    from kazma_core.turn_notes import IDENTITY_OPENINGS, TURN_NOTE_KINDS

    phrases = [p for prefixes in TURN_NOTE_KINDS.values() for p in prefixes]
    phrases += list(prompt_cache._DYNAMIC_MARKERS) + [WORKING_MEMORY_MARKER]
    phrases += [p for p in IDENTITY_OPENINGS if p != "[KAZMA_BASE_PROMPT]"]
    return sorted(set(phrases))


BASE_HEAD = "You are Kazma, the planted base prompt."


def _base() -> str:
    lines = [BASE_HEAD]
    lines += [f"- It names {p} in passing." for p in _planted_phrases()]
    lines.append("CRITICAL LANGUAGE RULE: a LANGUAGE LOCK system message for this turn is absolute.")
    return "\n".join(lines)


class _Scripted:
    """Asks for one tool call, then answers."""

    def __init__(self) -> None:
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, *, messages, tools=None, model=None, **kwargs):
        self.seen.append([dict(m) for m in messages])
        if len(self.seen) % 2 == 1 and tools:
            return LLMResponse(content="", finish_reason="tool_calls", model="stub", cost_usd=0.0,
                               usage={"total_tokens": 10},
                               tool_calls=[ToolCall(id=f"call_{len(self.seen)}", name="lookup_note",
                                                    arguments={"topic": "x"})])
        return LLMResponse(content="Done.", tool_calls=[], finish_reason="stop", model="stub",
                           usage={"total_tokens": 5}, cost_usd=0.0)


def _graph(monkeypatch, llm: _Scripted, *, checkpointer: Any = None) -> Any:
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.runtime import live_llm
    from kazma_core.tracing import KazmaTracer

    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setattr(live_llm, "resolve_live_client", lambda fallback, **kw: (llm, None))

    def lookup_note(topic: str = "") -> str:
        """Look up a note."""
        return "the note says hello"

    registry = LocalToolRegistry(include_builtins=False)
    registry.register_function("lookup_note", lookup_note, description="Look up a note.", category="test")
    return build_supervisor_graph(
        llm=llm, system_prompt=_base(), tool_definitions=registry.get_tool_definitions(),
        tool_executor=registry, cost_breaker=create_cost_breaker(), checkpointer=checkpointer,
        authority=create_authority(model="test", window=128000), tracer=KazmaTracer(backend="console"),
    )


def _run(graph: Any, messages: list[dict[str, Any]], thread_id: str) -> dict[str, Any]:
    return asyncio.run(graph.ainvoke({"messages": messages, "iteration": 0},
                                     {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}))


def _bases(call: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in call if m.get("role") == "system" and BASE_HEAD in str(m.get("content"))]


def _env_notes(call: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from kazma_core.turn_notes import opens_with

    return [m for m in call if opens_with(m, ENV_OPENING)]


# ── the real graph ──────────────────────────────────────────────────────


@pytest.mark.parametrize("question", ["look up my note", "ابحث عن ملاحظتي"])
def test_every_call_carries_the_base_prompt_first(monkeypatch, question):
    llm = _Scripted()
    out = _run(_graph(monkeypatch, llm), [{"role": "user", "content": question}], f"base-{len(question)}")
    assert out["messages"][-1]["content"] == "Done."
    assert len(llm.seen) == 2, "a first call and a tool round"
    for call in llm.seen:
        bases = _bases(call)
        assert len(bases) == 1, "the base prompt, exactly once, on every call"
        assert call[0] is bases[0] and str(call[0]["content"]).startswith("[KAZMA_BASE_PROMPT]")
        assert _base() in str(call[0]["content"]), "the whole base prompt"
        assert sum(1 for m in call if str(m.get("content", "")).startswith("LANGUAGE LOCK")) <= 1
    assert any(str(m.get("content", "")).startswith("LANGUAGE LOCK") for m in llm.seen[0])


def test_control_the_old_rule_dropped_the_base_prompt_from_the_first_call(monkeypatch):
    """Negative control: judged by a phrase anywhere in its text, the first
    call goes without the base prompt (live, only the lock's step did it, so a
    tool round had it; with every phrase planted, every filter does)."""
    from kazma_core.agent import graph_supervisor

    def phrase_anywhere(message: Any, *openings: str) -> bool:
        return (isinstance(message, dict) and message.get("role") in ("system", "developer")
                and any(o in str(message.get("content") or "") for o in openings))

    monkeypatch.setattr(graph_supervisor, "opens_with", phrase_anywhere)
    llm = _Scripted()
    _run(_graph(monkeypatch, llm), [{"role": "user", "content": "look up my note"}], "base-control")
    assert _bases(llm.seen[0]) == []


def test_a_turn_without_a_workspace_block_gets_one_current_copy(monkeypatch):
    """The chat apps' shape: the supervisor adds the workspace block once."""
    llm = _Scripted()
    _run(_graph(monkeypatch, llm), [{"role": "user", "content": "look up my note"}], "env-none")
    for call in llm.seen:
        assert len(_env_notes(call)) == 1, [str(m.get("content"))[:40] for m in call]


def test_a_turn_with_the_webs_workspace_block_keeps_that_one(monkeypatch):
    web_block = f"{ENV_OPENING}\n- Workspace root: `web-turn-root`"
    llm = _Scripted()
    _run(_graph(monkeypatch, llm), [{"role": "system", "content": web_block},
                                    {"role": "user", "content": "look up my note"}], "env-web")
    for call in llm.seen:
        envs = _env_notes(call)
        assert len(envs) == 1 and "web-turn-root" in str(envs[0]["content"])


def test_after_switch_repo_the_next_turn_names_the_new_workspace(monkeypatch, tmp_path):
    """Two chat-app turns with a workspace switch between: each names its own."""
    from kazma_core.agent.turn_input import build_turn_messages
    from kazma_core.stores import get_workspace_store
    from langgraph.checkpoint.memory import MemorySaver

    store = get_workspace_store()
    first, second = tmp_path / "repo-first", tmp_path / "repo-second"
    first.mkdir()
    second.mkdir()
    store.set_active_workspace(store.create_workspace("first", str(first))["id"])
    llm = _Scripted()
    graph = _graph(monkeypatch, llm, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "switch"}, "recursion_limit": 40}

    async def turn(text: str) -> None:
        messages = await build_turn_messages(graph, config, user_text=text, system_messages=[])
        await graph.ainvoke({"messages": messages, "iteration": 0}, config)

    asyncio.run(turn("look up my note"))
    store.set_active_workspace(store.create_workspace("second", str(second))["id"])
    asyncio.run(turn("look it up again"))
    first_turn, second_turn = llm.seen[0], llm.seen[2]
    assert [str(first) in str(m["content"]) for m in _env_notes(first_turn)] == [True]
    envs = _env_notes(second_turn)
    assert len(envs) == 1 and str(second) in str(envs[0]["content"])
    assert str(first) not in str(envs[0]["content"])


def test_the_agents_base_prompt_holds_no_workspace_block(tmp_path):
    from kazma_core.agent import AgentConfig, KazmaAgent
    from kazma_core.config_store import ConfigStore, reset_config_store, set_config_store

    cfg = AgentConfig(name="t", version="0", language="en", rtl=False)
    cfg.config_path = str(tmp_path / "kazma.yaml")
    reset_config_store()
    set_config_store(ConfigStore(db_path=str(tmp_path / "s.db")))
    try:
        prompt = KazmaAgent(config=cfg).system_prompt
    finally:
        reset_config_store()
    assert ENV_OPENING not in prompt
    assert "LANGUAGE LOCK" in prompt, "the base prompt still names the lock: the case the rule is for"


# ── the prompt cache ────────────────────────────────────────────────────


def _packed() -> list[dict[str, Any]]:
    from kazma_core.prompt_cache import pack_system_messages

    return pack_system_messages([
        {"role": "system", "content": f"[KAZMA_BASE_PROMPT]\n{_base()}"},
        {"role": "system", "content": "[KAZMA_WORKING_MEMORY]\n## Active goal: reply"},
        {"role": "user", "content": "hi"},
    ])


def test_the_base_prompt_stays_in_the_cached_prefix():
    out = _packed()
    assert [m["role"] for m in out] == ["system", "system", "user"]
    assert str(out[0]["content"]).startswith("[KAZMA_BASE_PROMPT]")
    assert "[KAZMA_WORKING_MEMORY]\n## Active goal" not in str(out[0]["content"])
    assert str(out[1]["content"]).startswith("[KAZMA_WORKING_MEMORY]")


def test_control_judged_by_its_markers_the_base_prompt_left_the_prefix(monkeypatch):
    from kazma_core import prompt_cache

    monkeypatch.setattr(prompt_cache, "opens_with", lambda message, *openings: False)
    out = _packed()
    assert [m["role"] for m in out] == ["system", "user"], "no cached prefix at all"
    assert "[KAZMA_WORKING_MEMORY]\n## Active goal" in str(out[0]["content"]), "the base in the per-turn blob"


def test_the_identity_openings_are_the_markers():
    from kazma_core.agent.graph_helpers import _BASE_MARKER, _PERSONALITY_MARKER
    from kazma_core.memory.profile import ABOUT_MARKER
    from kazma_core.turn_notes import IDENTITY_OPENINGS

    assert IDENTITY_OPENINGS == (_BASE_MARKER, _PERSONALITY_MARKER, ABOUT_MARKER)


# ── the source ──────────────────────────────────────────────────────────


def _is_content_read(node: ast.AST) -> bool:
    """``x.get("content", ...)``, bare or inside ``str(...)``."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str" and node.args:
        node = node.args[0]
    if isinstance(node, ast.BoolOp):
        return any(_is_content_read(v) for v in node.values)
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            and bool(node.args) and isinstance(node.args[0], ast.Constant) and node.args[0].value == "content")


def _phrase_checks(source: str) -> list[tuple[str, int]]:
    """(function, line) of every ``<phrase> in <a message's content>``."""
    found: list[tuple[str, int]] = []

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.func = "<module>"

        def visit_FunctionDef(self, node: Any) -> None:
            outer, self.func = self.func, node.name
            self.generic_visit(node)
            self.func = outer

        def visit_AsyncFunctionDef(self, node: Any) -> None:
            self.visit_FunctionDef(node)

        def visit_Compare(self, node: ast.Compare) -> None:
            for op, right in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and _is_content_read(right):
                    found.append((self.func, node.lineno))
            self.generic_visit(node)

    V().visit(ast.parse(source))
    return found


def _product_files() -> list[Path]:
    out: list[Path] = []
    for pkg in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway"):
        out += sorted((REPO / pkg).rglob("*.py"))
    return out


def test_no_message_is_judged_by_a_phrase_in_its_text():
    offenders = []
    for path in _product_files():
        rel = path.relative_to(REPO).as_posix()
        for func, line in _phrase_checks(path.read_text(encoding="utf-8")):
            if (rel, func) not in DECLARED:
                offenders.append(f"{rel}:{line} ({func})")
    assert offenders == [], "use turn_notes.opens_with: " + ", ".join(offenders)


def test_every_declaration_still_names_a_check():
    names = {(p.relative_to(REPO).as_posix(), f) for p in _product_files()
             for f, _ in _phrase_checks(p.read_text(encoding="utf-8"))}
    assert DECLARED <= names, DECLARED - names


def test_control_the_old_lines_are_found():
    old = (
        'def lock(messages):\n'
        '    return [m for m in messages if not (m.get("role") == "system"\n'
        '            and "LANGUAGE LOCK" in str(m.get("content", "")))]\n'
        'def personality(msgs):\n'
        '    return [m for m in msgs if MARKER not in m.get("content", "")]\n'
    )
    assert [f for f, _ in _phrase_checks(old)] == ["lock", "personality"]
