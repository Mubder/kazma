"""A recovery note lasts one recovery attempt (audit 2026-10-26 AUD-122).

When the non-stop envelope recovers a turn, ``supervised_invoke`` writes a
``[KAZMA RECOVERY]`` system note into the checkpoint for the model. It was not
a registered turn note, so the checkpoint kept it and every later turn of the
chat sent it to the model again ("the model provider had a transient
failure... do NOT repeat the exact action"), and each further attempt in the
same turn stacked another. It is now a turn note (stripped at the next fresh
turn, ``turn_notes.strip_turn_notes``) and the watchdog keeps only the newest.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from kazma_core.agent.nonstop import HealingConfig, NonStopConfig
from kazma_core.agent.supervisor_watchdog import IncidentClass, _reflection_text, supervised_invoke
from kazma_core.llm_provider import LLMError
from kazma_core.turn_notes import TURN_NOTE_KINDS, note_kind, strip_turn_notes

STALE = _reflection_text(IncidentClass.TRANSIENT_LLM, 1, 3)


def _recovery_notes(messages: list[dict[str, Any]]) -> list[str]:
    return [m["content"] for m in messages
            if m.get("role") == "system" and str(m.get("content", "")).startswith("[KAZMA RECOVERY]")]


def _history() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "You are Kazma."},
        {"role": "user", "content": "summarize the report"},
        {"role": "system", "content": STALE},
        {"role": "assistant", "content": "Here is the summary."},
    ]


def test_a_new_turn_starts_without_an_earlier_recovery_note() -> None:
    assert note_kind({"role": "system", "content": STALE}) == "recovery"
    assert _recovery_notes(strip_turn_notes(_history())) == []


def test_unregistered_the_note_stayed_for_every_later_turn(monkeypatch) -> None:
    """Negative control: without the kind, as before."""
    monkeypatch.delitem(TURN_NOTE_KINDS, "recovery")
    assert _recovery_notes(strip_turn_notes(_history())) == [STALE]


class _Graph:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages

    async def aget_state(self, _config):
        return SimpleNamespace(values={"messages": list(self.messages)})

    async def aupdate_state(self, _config, values):
        self.messages = list(values["messages"])


def _recover_twice(graph: _Graph) -> None:
    attempts = {"n": 0}

    async def _invoke(_graph, _state, _config):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise LLMError("provider dropped the stream", transient=True)
        return {"messages": graph.messages}

    cfg = NonStopConfig(enabled=True, healing=HealingConfig(
        max_recovery_attempts=3, backoff_base_seconds=0.0, backoff_max_seconds=0.0,
    ))
    asyncio.run(supervised_invoke(
        graph, {"messages": []}, {"configurable": {"thread_id": "aud-122"}},
        nonstop_config=cfg, turn_timeout=0, invoke_fn=_invoke,
    ))


def test_the_watchdog_keeps_one_recovery_note() -> None:
    graph = _Graph(_history())
    _recover_twice(graph)
    notes = _recovery_notes(graph.messages)
    assert len(notes) == 1 and "recovery attempt 2/3" in notes[0]


def test_unregistered_the_notes_stacked(monkeypatch) -> None:
    """Negative control: the old watchdog appended to whatever was there."""
    monkeypatch.delitem(TURN_NOTE_KINDS, "recovery")
    graph = _Graph(_history())
    _recover_twice(graph)
    assert len(_recovery_notes(graph.messages)) == 3
