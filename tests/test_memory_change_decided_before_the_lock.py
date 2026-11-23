"""The second review of the stage 3 fixes: who may decide a change is asked
before the memory mutation lock, and an unreadable identity store refuses.

L13 put ``changes._not_theirs_to_decide`` -- the identity store's
``owns_space``/``read_registry`` -- inside ``with _mutation_lock:`` in
``dismiss`` and ``undo``: a slow call under that lock (§15 D2), and an
identity store that could not answer escaped as a 500. Now
``changes._decided_before_the_lock`` asks before the lock (a change's
subject never changes), and ``IdentityUnavailable`` is the refusal
``identity_unavailable``.

Through the real memory route (``POST /api/memory/v2/changes/{id}/dismiss``)
behind the real tenant middleware (S1-09's ``memory_world``), Spaces
enforced: person B a member of A's space, deciding a change to A's own fact.

Negative controls: today's order (the check inside the lock) and today's
500 (``IdentityUnavailable`` escaping).
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
from kazma_core.memory import changes
from kazma_core.memory.belief_mutation import _mutation_lock
from kazma_core.memory.speakers import entry_id
from kazma_core.spaces import identity

from tests._memory_space_cases import SPACE_A, memory_world
from tests._space_matrix import Matrix


@pytest.fixture
def ctx():
    with Matrix() as matrix:
        identity.create_space("Alpha", created_by=matrix.account_a, space_id=SPACE_A)
        identity.add_member(SPACE_A, matrix.account_b)
        with memory_world(matrix, who="b", space=SPACE_A) as w:
            yield w


def _change_to_a_s_fact(w) -> str:
    change_id = f"chg{uuid4().hex[:12]}"
    conn = changes._open()
    try:
        with conn:
            conn.execute(
                "INSERT INTO memory_changes (id, tenant_id, kind, state, subject, predicate, old_belief_id, "
                "old_value, new_belief_id, new_value, created_at) VALUES (?, ?, ?, 'open', ?, 'lives_in', "
                "'', 'Lisbon', '', 'Porto', ?)",
                (change_id, SPACE_A, changes.PENDING, entry_id(w.matrix.account_a), time.time()))
    finally:
        conn.close()
    return change_id


def _dismiss(w, change_id: str):
    return w.client.post(f"/api/memory/v2/changes/{change_id}/dismiss")


def _watch_the_lock(monkeypatch) -> list[bool]:
    """Every identity read while deciding, and whether the memory mutation
    lock was held at that moment."""
    held: list[bool] = []
    real = identity.read_registry

    def watched():
        held.append(_mutation_lock.locked())
        return real()

    monkeypatch.setattr(identity, "read_registry", watched)
    return held


def test_who_decides_is_asked_before_the_mutation_lock(ctx, monkeypatch) -> None:
    change_id = _change_to_a_s_fact(ctx)
    held = _watch_the_lock(monkeypatch)
    answer = _dismiss(ctx, change_id)
    assert answer.json().get("error") == "not_theirs", answer.text
    assert held, "the identity store was not asked"
    assert not any(held), "the identity store was read under the memory mutation lock"


def test_an_unreadable_identity_store_is_a_refusal_not_a_500(ctx, monkeypatch) -> None:
    change_id = _change_to_a_s_fact(ctx)

    def away(*_a, **_k):
        raise identity.IdentityUnavailable("the settings store is away")

    monkeypatch.setattr(identity, "owns_space", away)
    answer = _dismiss(ctx, change_id)
    assert answer.status_code != 500, f"an unreadable identity store was a 500: {answer.text}"
    assert answer.json().get("error") == "identity_unavailable", answer.text


def test_negative_control_today_s_order(ctx, monkeypatch) -> None:
    """Planted: today's order, no check before the lock and the check made
    inside it (the row read under it decides). The watch sees the lock
    held."""
    real_row, real_check = changes._row, changes._not_theirs_to_decide
    monkeypatch.setattr(changes, "_decided_before_the_lock", lambda *a, **k: None)

    def row_then_check(conn, change_id, tenant_id):
        row = real_row(conn, change_id, tenant_id)
        if row is not None and _mutation_lock.locked():
            real_check(row, tenant_id)
        return row

    monkeypatch.setattr(changes, "_row", row_then_check)
    with pytest.raises(AssertionError, match="read under the memory mutation lock"):
        test_who_decides_is_asked_before_the_mutation_lock(ctx, monkeypatch)


def test_negative_control_today_s_500(ctx, monkeypatch) -> None:
    """Planted: today's check, which let ``IdentityUnavailable`` escape."""
    from kazma_core.memory.speakers import account_of_entry
    from kazma_core.spaces.scope import bound_asker

    def today(row, tenant_id):
        member = account_of_entry(row.get("subject"))
        person = bound_asker() or ""
        if member is not None and person != member and not identity.owns_space(person, tenant_id):
            raise changes.ChangeRefusedError("not_theirs")

    monkeypatch.setattr(changes, "_not_theirs_to_decide", today)
    with pytest.raises(AssertionError, match="was a 500"):
        test_an_unreadable_identity_store_is_a_refusal_not_a_500(ctx, monkeypatch)
