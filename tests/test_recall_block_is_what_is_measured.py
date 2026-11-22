"""The recall block a chat turn's model is given is the block every measure
builds (the memory plan's finding 8, decided 2026-11-22).

Kazma shipped ``memory.v2.explain_recall`` on, and the supervisor passed it
to ``format_recall_block``: a turn's block ended each memory with the
channels it was found by ("_(via fts5, dense)_"), while the product
measures, the bounds script and LongMemEval's block condition passed
``explain=False``. What was measured was not what a turn sent (the tags cost
about 23 tokens a turn and pushed memories out of the budget). The tags
explain retrieval to a person, so they stay in the "Memory used" payload;
``format_recall_block`` lost the option, and no line carries them.

* The supervisor's injected block (Kazma's real turn, a scripted model) is
  the LongMemEval harness's block and the product measures' block, with
  explain_recall on; no line carries a tag; the person's payload still names
  the channels.
* Control: today's tags-in-block put back (the old rule) is caught: the
  injected block carries tags, and is not the block the measures built.
* Every block measure and the supervisor call the one function the one way.
"""

from __future__ import annotations

import ast
import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
QUESTION = "Which degree did I graduate with?"


@pytest.fixture(scope="module")
def scripts():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import longmemeval_bench
        import longmemeval_turn
        import memory_product_measures
    finally:
        sys.path.pop(0)
    return longmemeval_bench, longmemeval_turn, memory_product_measures


@pytest.fixture
def explained(monkeypatch, tmp_path):
    """A private install with explain_recall on (the shipped default, said
    here so the test does not depend on it)."""
    from kazma_core.config_store import get_config_store
    from tests._no_models import keep_models_out

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.delenv("KAZMA_MEMORY_STATE_DB", raising=False)
    store = get_config_store()
    missing = object()
    held = store.get("memory.v2.explain_recall", missing)
    store.set("memory.v2.explain_recall", True)
    try:
        yield
    finally:
        if held is missing:
            store.delete("memory.v2.explain_recall")
        else:
            store.set("memory.v2.explain_recall", held)


def _item() -> dict:
    texts = ["I graduated with a degree in Business Administration.", "How do I boil an egg?",
             "Recommend a film about space.", "Which degree is best for data science?"]
    return {"question_id": "q1", "question_type": "single-session-user", "question": QUESTION,
            "answer": "Business Administration", "question_date": "2023/06/01 (Thu) 10:00",
            "haystack_sessions": [[{"role": "user", "content": t},
                                   {"role": "assistant", "content": "Noted, thanks for telling me."}] for t in texts],
            "haystack_session_ids": [f"s{n}" for n in range(len(texts))],
            "haystack_dates": [f"2023/05/{10 + n:02d} (Wed) 09:00" for n in range(len(texts))],
            "answer_session_ids": ["s0"]}


class _Model:
    def can_turn_off_reasoning(self) -> bool:
        return False

    async def chat(self, *, messages, tools=None, model=None, **kwargs):
        from kazma_core.llm_provider import LLMResponse

        return LLMResponse(content="Business Administration.", finish_reason="stop", model="stand-in",
                           usage={"prompt_tokens": 1, "completion_tokens": 1})


def _injected(scripts, item: dict) -> str:
    """The block Kazma's real turn put before its model."""
    lme, turn, _pm = scripts
    rows = lme.history_rows(item)
    outcome = asyncio.run(turn.run_turn(
        QUESTION, now=lme.parse_date(item["question_date"]), tenant=lme.TENANT,
        seed=lambda conn: lme._seed_history(conn, rows), client=turn.CountingClient(_Model()),
        system_prompt="BASE", thread_id="lme-turn-gate"))
    assert outcome.error is None
    return outcome.block


def _product_measure(scripts, item: dict) -> tuple[Any, str]:
    """The block the product measures count, on a store seeded the same way."""
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    lme, _turn, pm = scripts
    with tempfile.TemporaryDirectory() as folder:
        conn = sqlite3.connect(str(Path(folder) / "m.db"))
        conn.row_factory = sqlite3.Row
        try:
            ensure_primary_schema(conn)
            lme._seed_history(conn, lme.history_rows(item))
            result, block, _seconds = pm.recall_block(conn, QUESTION, tenant_id=lme.TENANT,
                                                      now=lme.parse_date(item["question_date"]))
        finally:
            conn.close()
    return result, block


def test_the_block_injected_is_the_block_every_measure_builds(scripts, explained):
    from kazma_core.memory.recall import build_memory_explain_payload

    lme = scripts[0]
    item = _item()
    injected = _injected(scripts, item)
    result, product = _product_measure(scripts, item)
    longmemeval = lme.kazma_history(item, lme.history_rows(item))[0]
    assert injected and "Business Administration" in injected
    assert injected == product == longmemeval
    assert "_(via" not in injected
    # The explanation is the person's: the "Memory used" payload names the
    # channel each memory was found by.
    payload = build_memory_explain_payload(query=QUESTION, result=result, kb_hits=[], explain=True)
    assert "fts5" in str(payload)


def _tagged(real):
    """Today's rule put back: with explain_recall on (or ``explain=True``),
    each memory's line ends with the channels it was found by."""
    from kazma_core.memory.config import read_memory_cfg

    def format_recall_block(result, *, explain=None, **kwargs):
        block = real(result, **kwargs)
        on = explain if explain is not None else ((read_memory_cfg() or {}).get("v2") or {}).get("explain_recall")
        if not on:
            return block
        return "\n".join(f"{line}  _(via fts5)_" if line.startswith("- ") else line for line in block.split("\n"))

    return format_recall_block


def test_control_todays_tags_in_the_block_are_caught(scripts, explained, monkeypatch):
    from kazma_core.memory import recall as recall_module

    real = recall_module.format_recall_block
    monkeypatch.setattr(recall_module, "format_recall_block", _tagged(real))
    item = _item()
    injected = _injected(scripts, item)
    result, _product = _product_measure(scripts, item)
    # The supervisor's block carries the tags, and is not the block the
    # measures built (they passed explain=False).
    assert "_(via fts5)_" in injected
    assert injected != recall_module.format_recall_block(result, explain=False)


def _name(node: ast.AST) -> str | None:
    return getattr(node, "id", None) or getattr(node, "attr", None)


def _calls(path: Path) -> list[tuple[list[ast.expr], list[ast.keyword]]]:
    """Each call of format_recall_block, direct or through ``to_thread``: its
    arguments and keywords."""
    out = []
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(n, ast.Call):
            continue
        if _name(n.func) == "format_recall_block":
            out.append((list(n.args), list(n.keywords)))
        elif _name(n.func) == "to_thread" and n.args and _name(n.args[0]) == "format_recall_block":
            out.append((list(n.args[1:]), list(n.keywords)))
    return out


#: The supervisor and the measures: the block from the result alone.
ONE_WAY = ("kazma-core/kazma_core/agent/graph_supervisor.py", "scripts/longmemeval_bench.py",
           "scripts/memory_product_measures.py")
#: Varies the block's budget on purpose: that is what it measures.
BUDGET_VARIANTS = {"scripts/recall_block_bounds.py": {"max_tokens"}}


def _plain(call: tuple[list[ast.expr], list[ast.keyword]], allowed: set[str]) -> bool:
    args, keywords = call
    return len(args) == 1 and all(k.arg in allowed for k in keywords)


def test_every_block_measure_and_the_supervisor_build_it_one_way():
    for rel in ONE_WAY:
        calls = _calls(REPO / rel)
        assert calls, f"{rel}: no call of format_recall_block"
        assert all(_plain(c, set()) for c in calls), f"{rel}: format_recall_block with an option"
    for rel, allowed in BUDGET_VARIANTS.items():
        assert all(_plain(c, allowed) for c in _calls(REPO / rel)), rel
    # Negative control: a measure that passes an option is found.
    for planted in ("format_recall_block(result, explain=False)",
                    "asyncio.to_thread(format_recall_block, result, explain=True)"):
        node = ast.parse(planted).body[0].value
        args = node.args[1:] if _name(node.func) == "to_thread" else node.args
        assert not _plain((list(args), list(node.keywords)), set()), planted
