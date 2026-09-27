"""The memory a turn was shown is stored with the turn before it is published
(restored 2026-09-27).

The chat's "Memory context" panel drew nothing from 2026-09-20 (the unified
turn block's Phase 5 deleted its markup) to 2026-09-27, and its frame was
never stored, so even a working panel would have lost it on reload. Now the
streamer records the turn's ``memory`` part (``turn_document.memory_part``)
and commits it BEFORE the ``memory_explain`` frame goes out -- the rule tool
rows follow (persist, then publish) -- and the page draws the stored part the
same way it draws the live one (shared fixture ``memory_used.json``,
``tests/js/test_memory_row.js``).

Held: ``_note_memory`` notes and commits the part, and nothing for no payload;
every ``memory_explain`` emission in the streamer is preceded by it (from the
source, with a negative control).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

STREAMER = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "sse_chat" / "_streaming.py"

PAYLOAD = {
    "beliefs": [{"content": "user lives_in kuwait", "sources": ["fts5"]}],
    "episodes": [],
    "weekly_summaries": [],
    "knowledge": [],
}


class _Durable:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def note(self, part: dict) -> None:
        self.events.append(("note", part))

    async def commit(self, text: str = "", *, force: bool = False) -> bool:
        self.events.append(("commit", text))
        return True


def test_the_memory_part_is_noted_and_committed():
    from kazma_ui.sse_chat._streaming import _note_memory

    durable = _Durable()
    asyncio.run(_note_memory(durable, PAYLOAD, "partial answer"))
    assert [e[0] for e in durable.events] == ["note", "commit"]
    part = durable.events[0][1]
    assert part["type"] == "memory" and part["counts"]["fact"] == 1
    assert durable.events[1] == ("commit", "partial answer")


def test_no_payload_no_part():
    from kazma_ui.sse_chat._streaming import _note_memory

    durable = _Durable()
    asyncio.run(_note_memory(durable, None, ""))
    assert durable.events == []


_EMIT = re.compile(r"emit_j\(\s*\"memory_explain\"")


def unpersisted_emissions(source: str) -> list[int]:
    """Lines emitting ``memory_explain`` whose previous statement is not
    ``await _note_memory(...)``."""
    lines = source.splitlines()
    bad = []
    for m in _EMIT.finditer(source):
        at = source.count("\n", 0, m.start())
        # The statement opening the emission (``yield await emit_j(`` may
        # sit on the line above the frame name).
        start = at if "emit_j(" in lines[at] else at - 1
        prev = next((lines[i].strip() for i in range(start - 1, -1, -1) if lines[i].strip()), "")
        if not prev.startswith("await _note_memory("):
            bad.append(at + 1)
    return bad


def test_every_memory_frame_is_stored_first():
    source = STREAMER.read_text(encoding="utf-8")
    assert len(_EMIT.findall(source)) >= 2  # the instrument finds the emissions
    assert unpersisted_emissions(source) == []


def test_an_emission_that_skips_the_store_is_caught():
    """Negative control."""
    src = 'x = 1\n            yield await emit_j(\n                "memory_explain", payload\n            )\n'
    assert unpersisted_emissions(src) == [2]  # the emit_j( line
    stored = 'await _note_memory(d, payload, t)\n' + src.split("\n", 1)[1]
    assert unpersisted_emissions(stored) == []
