"""SSE stream segment break — one narration per LLM invocation, un-glued.

2026-08-27 report: during multi-iteration turns (batch checks etc.) the live
bubble showed "…(both TLDs):Batch 1/4: 🔥…" — narrations from SEPARATE LLM
invocations glued with no separator, while the final reply rendered clean.
The renderer already breaks single newlines; the streamed deltas simply had
no newline at invocation boundaries. Fix pin: a paragraph break is emitted
BETWEEN model invocations only, never mid-word (mid-word chunks belong to
one invocation).
"""

from __future__ import annotations

from tests._module_source import module_source

from pathlib import Path

_SSE = module_source(Path(__file__).resolve().parents[1]
    / "kazma-ui" / "kazma_ui" / "sse_chat.py")


def test_segment_break_emitted_between_model_calls() -> None:
    assert "_first_token_of_model_call" in _SSE
    # Break fires on the FIRST token of a NEW invocation only…
    assert "if _first_token_of_model_call:" in _SSE
    assert "_first_token_of_model_call = False" in _SSE
    # …armed by on_chat_model_start…
    assert 'kind == "on_chat_model_start"' in _SSE
    assert "_first_token_of_model_call = True" in _SSE
    # …only when the previous narration lacks trailing whitespace, and it
    # streams as a real token so the client accumulates it identically.
    assert 'content_acc.endswith(("\\n", " ", "\\t"))' in _SSE
    assert 'yield await emit_j("token", {"content": sep})' in _SSE
