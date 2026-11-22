"""The S2-12 review, finding 4: a member's forget (N36) is never left
unfinished.

* The forget of a leaving or removed member's entry is journaled BEFORE the
  membership changes, so a stop or a failure after the change leaves an
  entry the forget convergence applies.
* The convergence pass also forgets any ``member_*`` entry whose account is
  no longer a member of that space: a forget that was never journaled (an
  older build), and a deleted account.
* A change that is refused journals nothing, and an entry whose person is
  still a member (a change that did not happen, or a person who came back)
  forgets nothing.
* The removed member's notice says their facts were forgotten only once
  they were.

Controls: the flow of before (no journal before the change, no sweep of
departed entries) leaves the facts for good.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from kazma_core.memory import forget, forget_journal
from kazma_core.spaces import identity, people
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests.test_memory_speakers import OMAR, SARA, TEAM, team  # noqa: F401
from tests.test_space_membership import _both_said_where_they_live, _entry_facts, _members


def _broken(*_a: Any, **_k: Any) -> dict[str, Any]:
    raise sqlite3.OperationalError("database is locked")


def _converge(team) -> None:  # noqa: F811 -- the fixture
    forget.converge(team.db)
    team.db.commit()


def test_a_forget_that_failed_after_the_change_is_finished_by_convergence(team, monkeypatch) -> None:  # noqa: F811 -- the fixture
    _both_said_where_they_live(team)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(forget, "forget_entry", _broken)
        with pytest.raises(people.PeopleRefused) as refused:
            people.leave_space(TEAM, by=SARA)
    assert refused.value.code == "forget_failed" and SARA not in _members()
    assert _entry_facts(team.db, SARA) == 1, "the change happened; the forget did not"
    pending = [e for e in forget_journal.pending(team.db) if e.kind == "entry"]
    assert [(e.tenant_id, e.session_key) for e in pending] == [(TEAM, f"member_{SARA}")], "journaled first"
    _converge(team)
    assert _entry_facts(team.db, SARA) == 0


def test_control_the_flow_of_before_leaves_the_facts_for_good(team, monkeypatch) -> None:  # noqa: F811 -- the fixture
    _both_said_where_they_live(team)
    monkeypatch.setattr(people, "_journal_entry_forget", lambda *_a: None)
    monkeypatch.setattr(forget, "_forget_departed_entries", lambda *_a, **_k: {"forgotten": 0})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(forget, "forget_entry", _broken)
        with pytest.raises(people.PeopleRefused):
            people.leave_space(TEAM, by=SARA)
    _converge(team)
    assert _entry_facts(team.db, SARA) == 1


def test_a_departed_entry_never_journaled_is_forgotten_by_convergence(team, monkeypatch) -> None:  # noqa: F811 -- the fixture
    """An older build's leave, or a deleted account: no journal entry, the
    facts still there, the person no longer in the space."""
    _both_said_where_they_live(team)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(people, "_journal_entry_forget", lambda *_a: None)
        patch.setattr(forget, "forget_entry", lambda *_a, **_k: {"ok": True, "facts": []})
        people.delete_person(OMAR, by=OWNER_ACCOUNT_ID)
    assert _entry_facts(team.db, OMAR) == 1
    _converge(team)
    assert _entry_facts(team.db, OMAR) == 0
    assert _entry_facts(team.db, SARA) == 1, "a member's entry stays"


def test_a_refused_change_journals_nothing_and_a_member_keeps_their_facts(team) -> None:  # noqa: F811 -- the fixture
    _both_said_where_they_live(team)
    before = len(forget_journal.pending(team.db))
    with pytest.raises(people.PeopleRefused):
        people.remove_member(TEAM, SARA, by=OMAR)  # not the owner, not the admin
    assert len(forget_journal.pending(team.db)) == before, "nothing journaled"
    # An entry journaled for a change that did not happen forgets nothing.
    forget_journal.append("entry", tenant_id=TEAM, session_key=f"member_{SARA}")
    _converge(team)
    assert SARA in _members() and _entry_facts(team.db, SARA) == 1


def test_the_removed_members_notice_says_forgotten_only_once_it_is(team) -> None:  # noqa: F811 -- the fixture
    _both_said_where_they_live(team)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(forget, "forget_entry", _broken)
        with pytest.raises(people.PeopleRefused):
            people.remove_member(TEAM, OMAR, by=OWNER_ACCOUNT_ID)
    assert [n["forgotten"] for n in people.notices_for(OMAR)] == [False]
    _converge(team)
    assert [n["forgotten"] for n in people.notices_for(OMAR)] == [True]


def test_with_spaces_off_the_sweep_forgets_nothing(team, monkeypatch) -> None:  # noqa: F811 -- the fixture
    from kazma_core.spaces import scope

    _both_said_where_they_live(team)
    identity.remove_member(TEAM, OMAR)
    monkeypatch.setattr(scope, "_enforced", False)
    assert forget._forget_departed_entries(team.db) == {"forgotten": 0}
    assert _entry_facts(team.db, OMAR) == 1
