"""A turn of small talk queues no deep pass (2026-11, the review of the memory
framework audit's F8).

Every finished turn queues a ``micro_consolidation`` task -- the LLM deep
pass, one model call -- unless its question is filler. F8 decided filler by
its words, but the list missed the words a person answers with ("نعم",
"لا", "ايوه", "كمل", "يلا", "خلاص", "go ahead", "proceed", "yes please",
"sounds good", "I'm good"), and the post-turn path queued the deep pass
without asking at all: each such reply cost a model call that could find
nothing.

Counted here through the real post-turn function, with the queue recorded;
short statements of fact still queue one. The path without the filler check
is the negative control.
"""

from __future__ import annotations

import pytest
from kazma_core.memory import consolidator
from kazma_core.memory.belief_extractor import is_filler_turn

#: Replies that carry nothing a deep pass could keep: the review's list, in
#: both languages.
FILLER = [
    "نعم", "لا", "اي", "ايوه", "كمل", "يلا", "خلاص", "تم", "ماشي", "صح", "الحمدلله", "ان شاء الله",
    "حياك", "ابشر", "تسلم ايدك", "الله يعافيك",
    "go ahead", "proceed", "continue", "yes please", "done", "sounds good", "np", "yup", "I'm good",
    "ok np", "thx bro", "thanks man", "nah", "sure thing", "kk", "lmao", "nvm", "ok do it", "ok will do",
    "fine", "I'm fine thanks", "I'm ok",
]

#: Short statements: each is a fact, and queues its deep pass.
STATEMENTS = ["I'm vegan", "I'm diabetic", "Call me Sam", "No, Lisbon", "أنا نباتي", "عندي سكري",
              "I'm 34", "I'm a nurse", "I'm in Dubai", "Kuwaiti", "اسمي سام", "I'm good at chess"]


def _queued(monkeypatch, user_text: str) -> list[str]:
    import kazma_core.memory.task_queue as task_queue

    queued: list[str] = []
    monkeypatch.setattr(task_queue, "enqueue_task", lambda kind, payload, **kw: queued.append(kind))
    monkeypatch.setattr(consolidator, "promote_working_memory", lambda *a, **k: 0)
    monkeypatch.setattr(consolidator, "_mirror_turn_to_v2", lambda *a, **k: None)
    monkeypatch.setattr(consolidator, "_v2_extract_sync", lambda *a, **k: None)
    messages = [{"role": "user", "content": user_text}, {"role": "assistant", "content": "Noted."}]
    consolidator._run_turn_memory(messages, session_id=None, turn=1, tenant_id="t")
    return [kind for kind in queued if kind == "micro_consolidation"]


@pytest.mark.parametrize("text", FILLER)
def test_small_talk_queues_no_deep_pass(monkeypatch, text):
    assert is_filler_turn(text), text
    assert _queued(monkeypatch, text) == []


@pytest.mark.parametrize("text", STATEMENTS)
def test_a_short_statement_queues_its_deep_pass(monkeypatch, text):
    assert not is_filler_turn(text), text
    assert _queued(monkeypatch, text) == ["micro_consolidation"]


def test_control_without_the_filler_check_every_reply_costs_a_model_call(monkeypatch):
    import kazma_core.memory.belief_extractor as belief_extractor

    monkeypatch.setattr(belief_extractor, "is_filler_turn", lambda text: False)
    assert sum(len(_queued(monkeypatch, text)) for text in FILLER) == len(FILLER)


# ── every place that queues a turn's deep pass asks first ─────────────────

#: Functions that queue a deep pass without asking, with why.
DECLARED = {
    ("kazma-core/kazma_core/memory/entity_repair.py", "repair_collapsed"):
        "re-queues turns whose facts the old id rule collapsed: each turn held a fact",
}


def _call_name(call) -> str:
    import ast

    func = call.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _deep_pass_queuers(sources: dict[str, str]) -> set[tuple[str, str]]:
    """Product functions that queue a micro_consolidation task and never ask
    is_filler_turn."""
    import ast

    found: set[tuple[str, str]] = set()
    for rel, text in sources.items():
        for fn in ast.walk(ast.parse(text)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
            queues = any(_call_name(c).startswith(("enqueue", "_enqueue")) and c.args
                         and isinstance(c.args[0], ast.Constant) and c.args[0].value == "micro_consolidation"
                         for c in calls)
            asks = any((isinstance(c.func, ast.Name) and c.func.id == "is_filler_turn")
                       or (isinstance(c.func, ast.Attribute) and c.func.attr == "is_filler_turn") for c in calls)
            if queues and not asks:
                found.add((rel, fn.name))
    return found


def test_every_deep_pass_queuer_asks_whether_the_turn_is_filler():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sources = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
               for p in (root / "kazma-core" / "kazma_core").rglob("*.py")}
    assert _deep_pass_queuers(sources) == set(DECLARED), (
        "a function queues the deep pass (one model call a turn) without asking is_filler_turn; "
        "ask it, or declare why in DECLARED")


def test_control_a_planted_queuer_is_found():
    planted = {"x.py": "def f(e):\n    enqueue_task('micro_consolidation', {'episode_id': e})\n"}
    assert _deep_pass_queuers(planted) == {("x.py", "f")}
