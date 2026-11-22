"""A move to a name no word calls a place asks the user; it never replaces
their home (the memory plan's finding 15, 2026-11-22).

"We moved to Slack." is common in real chats, and after finding 10 the
English quick pass read "I/We moved to X" as the user's home, as the Arabic
pass already read "انتقلنا إلى X": Slack replaced the user's home at the
user's own trust, in both languages ("انتقلنا إلى سلاك"). The main
session's decision, equal in both languages:

* "moved to X" with no word saying X is a place is Kazma's reading: a
  question over a home the user stated (a pending change, "Yes, change it"
  / "No, keep it"), Kazma's reading when none is held;
* it updates at once only when the statement itself says it is a home or a
  place: "moved home / house / our flat to", "the city of", "سكنت في",
  "نقلنا بيتنا إلى", an Arabic place head ("مدينة");
* "I live in X" / "أسكن في X" stays an update, as before;
* the paired negatives never touch the home: they ask, or store nothing.

Each statement goes through the product's quick pass
(``extract_and_apply_beliefs_sync``) into a private memory database; the
facts and the change log are read back. Today's rule (every move the user's
own statement) is the negative control.
"""

from __future__ import annotations

import sqlite3

import pytest
from kazma_core.memory import belief_extractor, changes
from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

TENANT = "default"
#: (the home held, a move the words do not call a place): the move asks.
ASKS = [
    (("I live in Paris.", "Paris"), ("We moved to Rotterdam last month.", "Rotterdam")),
    (("ساكن في الكويت.", "الكويت"), ("انتقلنا إلى الرياض الشهر الماضي.", "الرياض")),
    (("I live in Paris.", "Paris"), ("We moved to Slack.", "Slack")),
    (("ساكن في الكويت.", "الكويت"), ("انتقلنا إلى سلاك.", "سلاك")),
    (("I live in Paris.", "Paris"), ("We moved to Teams last week.", "Teams")),
    (("ساكن في الكويت.", "الكويت"), ("انتقلنا إلى تيمز الأسبوع الماضي.", "تيمز")),
]
#: (the home held, a move that says it is a home or a place): it updates.
UPDATES = [
    (("I live in Paris.", "Paris"), ("We moved house to Rotterdam.", "Rotterdam")),
    (("ساكن في الكويت.", "الكويت"), ("نقلنا بيتنا إلى الرياض.", "الرياض")),
    (("I live in Paris.", "Paris"), ("I moved to the city of Porto.", "Porto")),
    (("ساكن في الكويت.", "الكويت"), ("انتقلت إلى مدينة بورتو.", "مدينة بورتو")),
    (("I live in Paris.", "Paris"), ("I live in Rotterdam now.", "Rotterdam")),
    (("ساكن في الكويت.", "الكويت"), ("سكنت في الرياض.", "الرياض")),
]
#: Never a home, held or not, in either language: nothing is stored.
NOTHING = [
    "We moved to Python 3.12.", "انتقلنا إلى بايثون 3.12.",
    "We moved to the next item.", "انتقلنا إلى البند التالي.",
    "We moved to a new office.", "انتقلنا إلى مكتب جديد.",
    "We moved to the Dubai office.", "انتقلنا إلى مكتب دبي.",
]


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)


@pytest.fixture
def store(tmp_path):
    primary = sqlite3.connect(tmp_path / "memory_state.db", isolation_level=None)
    primary.row_factory = sqlite3.Row
    ensure_primary_schema(primary)
    ops = sqlite3.connect(tmp_path / "memory_ops.db", isolation_level=None)
    ensure_ops_schema(ops)
    told = {"n": 0}

    def tell(text: str) -> None:
        told["n"] += 1
        extract_and_apply_beliefs_sync(primary, ops, text, "Noted.", session_id="s1", turn=told["n"],
                                       tenant_id=TENANT, now=1_790_000_000.0 + told["n"] * 86400, private=True)

    def homes() -> list[tuple[str, str]]:
        return [(r["object"], r["extraction_method"]) for r in primary.execute(
            "SELECT object, extraction_method FROM beliefs WHERE subject = 'user' AND predicate = 'lives_in' "
            "AND valid_until IS NULL AND invalidated_at IS NULL")]

    def waiting(kind: str) -> list[str]:
        return [c["new_value"] for c in changes.list_changes(primary, tenant_id=TENANT) if c["kind"] == kind]

    yield tell, homes, waiting, (primary, ops)
    primary.close()
    ops.close()


@pytest.mark.parametrize(("held", "moved"), ASKS)
def test_a_move_no_word_calls_a_place_asks_and_keeps_the_home(store, held, moved):
    tell, homes, waiting, _ = store
    tell(held[0])
    tell(moved[0])
    assert homes() == [(held[1], "user_stated")]
    assert waiting("pending") == [moved[1]]
    assert waiting("update") == []


@pytest.mark.parametrize(("held", "moved"), ASKS[:2])
def test_answered_yes_the_move_updates_and_can_be_undone(store, held, moved):
    """"Yes, change it": the move replaces the home, the change log shows
    the update with Undo, and Undo brings the home back."""
    tell, homes, waiting, (primary, ops) = store
    tell(held[0])
    tell(moved[0])
    pending = [c for c in changes.list_changes(primary, tenant_id=TENANT) if c["kind"] == "pending"]
    changes.confirm(primary, pending[0]["id"], tenant_id=TENANT, actor="test", private=True)
    assert [h for h, _ in homes()] == [moved[1]]
    updates = [c for c in changes.list_changes(primary, tenant_id=TENANT) if c["kind"] == "update"]
    assert [(c["old_value"], c["new_value"]) for c in updates] == [(held[1], moved[1])]
    changes.undo(primary, updates[0]["id"], tenant_id=TENANT, actor="test", ops_conn=ops)
    assert [h for h, _ in homes()] == [held[1]]


@pytest.mark.parametrize(("held", "moved"), UPDATES)
def test_a_move_that_says_it_is_a_home_updates_at_once(store, held, moved):
    tell, homes, waiting, _ = store
    tell(held[0])
    tell(moved[0])
    assert homes() == [(moved[1], "user_stated")]
    assert waiting("pending") == []
    assert waiting("update") == [moved[1]]


@pytest.mark.parametrize("moved", ["We moved to Rotterdam last month.", "انتقلنا إلى الرياض الشهر الماضي."])
def test_with_no_home_held_a_bare_move_is_kazmas_reading(store, moved):
    tell, homes, waiting, _ = store
    tell(moved)
    assert [m for _, m in homes()] == ["llm_inferred"]
    assert waiting("pending") == []


@pytest.mark.parametrize("text", NOTHING)
def test_the_negatives_store_nothing_held_or_not(store, text):
    tell, homes, waiting, _ = store
    tell(text)
    assert homes() == [] and waiting("pending") == [] and waiting("reading") == []
    tell("I live in Paris." if text.isascii() else "ساكن في الكويت.")
    tell(text)
    assert [h for h, _ in homes()] == (["Paris"] if text.isascii() else ["الكويت"])
    assert waiting("pending") == [] and waiting("update") == []


def test_control_today_s_rule_lets_a_tool_replace_the_home(store, monkeypatch):
    """Every move the user's own statement (v8): Slack and سلاك replace the
    user's home at their own trust, and nothing is asked."""
    tell, homes, waiting, _ = store
    monkeypatch.setattr(belief_extractor, "_kazmas_reading", lambda belief: False)
    tell("I live in Paris.")
    tell("We moved to Slack.")
    assert homes() == [("Slack", "user_stated")] and waiting("pending") == []
    tell("ساكن في الكويت.")
    tell("انتقلنا إلى سلاك.")
    assert homes() == [("سلاك", "user_stated")] and waiting("pending") == []
