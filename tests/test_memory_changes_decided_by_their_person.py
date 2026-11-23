"""Stage 3 review, L13: in a shared space a change to a member's fact is
decided by that member or an owner of the space.

``/memory confirm|undo|dismiss`` (and the Memory page's routes) acted on any
change of the space's memory: Rami could confirm, undo or dismiss a change
to a fact filed under Dana's own entry (``speakers``: ``member_<account>``).
``changes.confirm``, ``dismiss`` and ``undo`` -- the one choke both
surfaces pass -- now refuse it (``not_theirs``) unless the person bound now
is the member the fact is about or an owner of the space.

Through the real gateway handler, Spaces enforced, a Telegram group bound to
Garden (the S3-06 follow-up's world): Rami's ``/memory dismiss`` of Dana's
change is refused and the change stays open; Dana's own, and the space
owner's, are taken. A change to a fact under no member's entry is as before.

Negative control: the decisions of before, with no check.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any
from uuid import uuid4

import pytest
from kazma_core.memory import changes
from kazma_core.memory.speakers import entry_id

from tests.test_chat_turn_control_per_person import _said, world  # noqa: F401  (the fixture)


def _waiting_change(w: Any, subject: str) -> str:
    """An open pending change in Garden's memory to *subject*'s home."""
    change_id = f"chg{uuid4().hex[:12]}"
    conn = changes._open()
    try:
        with conn:
            conn.execute(
                "INSERT INTO memory_changes (id, tenant_id, kind, state, subject, predicate, old_belief_id, "
                "old_value, new_belief_id, new_value, created_at) VALUES (?, ?, ?, 'open', ?, 'lives_in', "
                "'', 'Lisbon', '', 'Porto', ?)",
                (change_id, w.garden["id"], changes.PENDING, subject, time.time()))
    finally:
        conn.close()
    return change_id


def _state(change_id: str) -> str:
    conn = changes._open()
    try:
        return str(conn.execute("SELECT state FROM memory_changes WHERE id = ?", (change_id,)).fetchone()[0])
    except sqlite3.Error:
        return ""
    finally:
        conn.close()


def _decide(w: Any, who: str, change_id: str) -> str:
    async def run() -> str:
        await _said(w, "telegram", "dana", "/space Garden")
        return await _said(w, "telegram", who, f"/memory dismiss {changes.short_ref(change_id)}")

    return asyncio.run(run())


def test_another_member_does_not_decide_her_change(world) -> None:
    change_id = _waiting_change(world, entry_id(world.who["dana"]))
    said = _decide(world, "rami", change_id)
    assert "another member's fact" in said, said
    assert _state(change_id) == "open", "Rami decided a change to Dana's fact"


def test_she_decides_her_own_change(world) -> None:
    change_id = _waiting_change(world, entry_id(world.who["dana"]))
    said = _decide(world, "dana", change_id)
    assert "Nothing changed" in said, said
    assert _state(change_id) != "open"


def test_the_space_s_owner_decides_it(world) -> None:
    change_id = _waiting_change(world, entry_id(world.who["dana"]))
    said = _decide(world, "owner", change_id)
    assert "Nothing changed" in said, said
    assert _state(change_id) != "open"


def test_a_fact_under_no_member_s_entry_is_as_before(world) -> None:
    change_id = _waiting_change(world, "garden_shed")
    said = _decide(world, "rami", change_id)
    assert "Nothing changed" in said, said


def test_negative_control_the_decisions_of_before(world, monkeypatch) -> None:
    monkeypatch.setattr(changes, "_not_theirs_to_decide", lambda row, tenant: None)
    with pytest.raises(AssertionError, match="Rami decided a change to Dana's fact|another member's fact"):
        test_another_member_does_not_decide_her_change(world)
