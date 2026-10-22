"""No fixed token cap cuts the conversation below its context window.

The owner's decision (2026-10-18): the context budget is the window
(``turn_input.resolve_trim_token_budget``). A second pruner,
``prune_messages_if_exceeding_cap`` (2026-08-02), still ran first on every
supervisor call with a fixed 30,000 tokens: past it, every tool output was cut
to 2,000 characters -- the running turn's too, which §29H protects -- and the
cut list was saved to the checkpoint. Live 2026-10-22 a model reading a
54-page OCR'd document with ``document_read`` (20,000 characters) found 2,000
on its next step, re-read the document in ever smaller windows ("the tool
truncates each response to roughly 1,800-2,000 characters regardless of
max_chars") and ended the turn without the text. Negative control: the old
pruner, inlined, cuts the same output.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "kazma-core" / "kazma_core" / "agent"


def _old_prune(messages, max_tokens=30000):
    """The removed pruner's tool-output rule, as it was."""
    from kazma_core.summarizer import estimate_tokens

    if estimate_tokens(messages) <= max_tokens:
        return messages
    out = []
    for m in messages:
        m = dict(m)
        if m.get("role") == "tool" and isinstance(m.get("content"), str) and len(m["content"]) > 2000:
            m["content"] = m["content"][:2000] + "\n[Tool output truncated for context limits]"
        out.append(m)
    return out


def _turn(chars: int) -> list[dict]:
    history = [{"role": "user", "content": "earlier question " * 400},
               {"role": "assistant", "content": "earlier answer " * 4000}]
    return [
        {"role": "system", "content": "You are Kazma."},
        *history * 6,
        {"role": "user", "content": "Read the OCR'd document and make a Word file"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "document_read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ص" * chars},
    ]


def test_the_supervisor_has_no_fixed_cap() -> None:
    source = (AGENT / "graph_supervisor.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {node.func.id for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "prune_messages_if_exceeding_cap" not in called
    helpers = (AGENT / "graph_helpers.py").read_text(encoding="utf-8")
    assert "def prune_messages_if_exceeding_cap" not in helpers


def test_the_running_turns_read_survives_the_window_budget(monkeypatch) -> None:
    from kazma_core.agent.turn_input import resolve_trim_token_budget
    from kazma_core.summarizer import estimate_tokens, prune_tool_outputs

    messages = _turn(20_000)
    assert estimate_tokens(messages) > 30_000, "the fixture must be past the old cap"
    budget = resolve_trim_token_budget(last_model="deepseek-flash")
    assert budget > 100_000
    kept = prune_tool_outputs(messages, max_tokens=budget)
    assert len(kept[-1]["content"]) == 20_000


def test_negative_control_the_old_cap_cut_the_running_turns_read() -> None:
    messages = _old_prune(_turn(20_000))
    assert len(messages[-1]["content"]) < 2100
