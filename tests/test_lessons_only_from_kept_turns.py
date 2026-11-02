"""Self-improvement learns only from turns memory would keep (FA-212).

The 2026-11 functional audit: after every chat turn the Meta-Refiner wrote a
lesson into the supervisor's Soul, which joins every later prompt -- also
after a failed turn (with the refiner down as well, the template line
"[SelfImprovement] 1/1 stages failed."), after a turn in a chat kept out of
memory, and from whatever the failed turn's model said (a fault server's
echo). The Soul keeps twelve lessons, so six failed or test turns evicted
half of the owner's; each rewrite also added a blank line to every older one.

Every case runs through the real hand-off: ``close_turn`` with a finished
graph state, then the background task it starts. The refiner is scripted.
"""

from __future__ import annotations

import ast
import asyncio
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core.memory import forget
from kazma_core.memory.schema_v2 import ensure_primary_schema
from kazma_core.skills import self_improvement as si

REPO = Path(__file__).resolve().parents[1]
LESSON = "Cite the tool result that answers the question."


class _Refiner:
    def __init__(self, answer: str = LESSON) -> None:
        self.answer = answer
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        return SimpleNamespace(content=self.answer)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from kazma_core.memory import consolidator
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "1")  # the one model path under test, scripted
    # The memory hand-off beside it is not under test here (it writes episodes).
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
    return SimpleNamespace(refiner=refiner)


class _Graph:
    def __init__(self, values):
        self.snap = SimpleNamespace(values=values, next=(), tasks=[])

    async def aget_state(self, config):
        return self.snap


def _finished(thread: str, question: str, answer: str, **extra) -> dict:
    messages = [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
    return {"messages": messages,
            "_post_turn_memory": {"session_id": thread, "turn": 1, "tenant_id": "default"},
            **extra}


def _close(values: dict, thread: str, times: int = 1) -> None:
    """``close_turn`` as every transport runs it, then the lesson task it started."""
    from kazma_core.background import background_tasks
    from kazma_ui.turn_runtime import close_turn

    async def run():
        for _ in range(times):
            await close_turn(_Graph(values), {"configurable": {"thread_id": thread}}, thread_id=thread)
        mine = [t for t in background_tasks() if t.get_name().startswith("self-improvement")]
        await asyncio.gather(*mine)

    asyncio.run(run())


def _lessons() -> list[str]:
    return si._lessons(si.get_agent_evolution_block())[1]


def _thread() -> str:
    return f"web-{uuid.uuid4().hex[:8]}"


def test_a_turn_memory_keeps_teaches_once(env):
    thread = _thread()
    _close(_finished(thread, "which PRs are open?", "Three: #12, #14 and #15."), thread, times=2)
    assert _lessons() == [LESSON]
    assert env.refiner.calls == 1  # closed twice (a disconnect, a late settle), taught once
    history = si._load_agent_evolution()["agents"]["supervisor"]["history"]
    assert history[-1]["source"] == {"chat": thread, "turn": 1}


def test_a_failed_turn_teaches_nothing(env):
    thread = _thread()
    _close(_finished(thread, "summarize my inbox", "⚠️ The model provider did not answer.",
                     turn_failed=True), thread)
    assert _lessons() == [] and env.refiner.calls == 0


def test_a_chat_kept_out_of_memory_teaches_nothing(env):
    thread = _thread()
    forget.set_chat_remembered(thread, False, tenant_id="default")
    _close(_finished(thread, "my test codeword is PEBBLE-9", "Noted."), thread)
    assert _lessons() == [] and env.refiner.calls == 0


def test_a_turn_kept_out_of_memory_teaches_nothing(env):
    """The turn in which the user asks to forget something is kept out."""
    thread = _thread()
    question = "forget everything that mentions PEBBLE-9"
    forget.keep_turn_out(thread, 1, question, tenant_id="default")
    _close(_finished(thread, question, "Done: 3 memories forgotten."), thread)
    assert _lessons() == [] and env.refiner.calls == 0


@pytest.mark.parametrize("answer", ["", "   ", "x" * 1500, "Task successfully completed (100% rate)."])
def test_no_lesson_when_the_refiner_answers_none_that_fits(env, answer):
    env.refiner.answer = answer
    thread = _thread()
    _close(_finished(thread, "list my open PRs", "Three are open."), thread)
    assert _lessons() == [] and env.refiner.calls == 1


def test_no_template_line_when_the_refiner_is_down(env, monkeypatch):
    def down():
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(si, "_llm_for_delta", down)
    thread = _thread()
    _close(_finished(thread, "list my open PRs", "Three are open."), thread)
    assert _lessons() == []


def test_the_transports_rule_would_have_taught(env):
    """Negative control: what the transports did -- the gateway judged a turn
    by its reply alone (a failure's "⚠️" text is not empty) and neither asked
    the ledger -- writes a lesson from a failed turn of a kept-out chat."""
    thread = _thread()
    forget.set_chat_remembered(thread, False, tenant_id="default")
    reply = "⚠️ The model provider did not answer."
    old_success = bool(reply.strip())  # the gateway's rule
    asyncio.run(si.analyze_and_apply_chat_turn(user_message="summarize my inbox",
                                               success=old_success, output_snippet=reply))
    assert _lessons() == [LESSON]


# ── The cap keeps whole lessons ────────────────────────────────────────────


def _old_cap(base_prompt: str, new_delta: str) -> str:
    """The cap before 2026-11, for the negative control."""
    marker = "[SelfImprovement]"
    soul, _, rest = base_prompt.partition(marker)
    soul = soul.rstrip()
    existing = [f"\n\n{marker}{blk}" for blk in rest.split(marker) if blk.strip()]
    delta = new_delta.strip()
    if marker not in delta:
        delta = f"{marker} {delta}"
    if not delta.startswith("\n\n"):
        delta = "\n\n" + delta
    if len(delta) > 1000:
        delta = delta[:1000].rstrip() + "…"
    return soul + "".join(existing + [delta])


def test_the_cap_keeps_whole_lessons_and_their_spacing():
    soul = "You are Kazma."
    for n in range(14):
        soul = si._cap_evolution_prompt(soul, f"[SelfImprovement] Lesson {n}.")
    _, lessons = si._lessons(soul)
    assert lessons == [f"Lesson {n}." for n in range(2, 14)]  # the newest twelve, whole
    assert "\n\n\n" not in soul  # one separator, however many rewrites
    long = "[SelfImprovement] " + "word " * 300
    assert si._cap_evolution_prompt(soul, long) == soul  # refused, never cut
    assert si.apply_agent_mutation("supervisor", long) is False


def test_the_old_cap_grew_blank_lines_and_cut_a_lesson():
    """Negative control."""
    soul = "You are Kazma."
    for n in range(3):
        soul = _old_cap(soul, f"[SelfImprovement] Lesson {n}.")
    assert "\n\n\n" in soul
    assert _old_cap(soul, "[SelfImprovement] " + "word " * 300).endswith("…")


def test_template_lines_already_stored_leave_the_prompt(env):
    """A Soul written before the fix still holds them; the prompt reads without."""
    from kazma_core.config_store import get_config_store

    stored = ("You are Kazma.\n\n[SelfImprovement] Keep answers short.\n\n\n\n"
              "[SelfImprovement] 1/1 stages failed.\n\n"
              "[SelfImprovement] Task successfully completed (100% rate).")
    get_config_store().set(si._AGENT_EVO_KEY, {"agents": {"supervisor": {"soul": stored, "history": []}}})
    block = si.get_agent_evolution_block()
    assert block == "You are Kazma.\n\n[SelfImprovement] Keep answers short."
    assert "stages failed" in stored  # the control: the stored text holds them


# ── One hand-off ───────────────────────────────────────────────────────────


def _calls_to(source: str, name: str) -> int:
    return sum(1 for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
               and (getattr(node.func, "id", None) == name or getattr(node.func, "attr", None) == name))


def test_one_hand_off_from_the_closer():
    """close_turn alone hands turns to self-improvement, and only the
    hand-off reaches the chat analyzer: a transport that schedules lessons
    from its reply's text is how failed and kept-out turns taught."""
    sites: dict[str, list[str]] = {"learn_from_finished_turn": [], "analyze_and_apply_chat_turn": []}
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                    "kazma-tui/kazma_tui", "kazma-cli/kazma_cli", "kazma-skills/kazma_skills"):
        for path in sorted((REPO / package).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for name in sites:
                if _calls_to(source, name):
                    sites[name].append(path.relative_to(REPO).as_posix())
    assert sites == {
        "learn_from_finished_turn": ["kazma-ui/kazma_ui/turn_runtime.py"],
        "analyze_and_apply_chat_turn": ["kazma-core/kazma_core/skills/self_improvement.py"],
    }
    rogue = "async def done(reply):\n    await analyze_and_apply_chat_turn(user_message='q', success=True)\n"
    assert _calls_to(rogue, "analyze_and_apply_chat_turn") == 1  # negative control
