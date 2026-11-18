"""A single-valued fact stays single-valued whatever a writer labels it
(2026-11, the memory framework audit's F7).

``mutate_belief`` took the caller's label before Kazma's own list of
single-valued names, and the agent's memory tools label a fact "set" (a
list) by default. The user said "I live in London"; an agent write of
``lives_in Tokyo`` labelled a list was added beside it, past the trust gate
(the same write labelled single-valued was refused). Any later
single-valued agent write then ran the clean-up of extra current values,
which kept the newest row and closed the user's own fact.

Now Kazma's class wins over the label, and the clean-up keeps the user's
own value over a newer one from a lower source.
"""

from __future__ import annotations

import sqlite3

import pytest
from kazma_core.memory import belief_mutation
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.schema_v2 import ensure_primary_schema

DAY = 86400.0
T0 = 1_790_000_000.0


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    connection = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    ensure_primary_schema(connection)
    yield connection
    connection.close()


def _write(conn, value: str, label: str, source: str, day: int, predicate: str = "lives_in") -> dict:
    return mutate_belief(conn, "user", predicate, value, predicate_type=label, confidence=0.9,
                         extraction_method=source, tenant_id="t", now=T0 + day * DAY, private=True)


def _current(conn, predicate: str = "lives_in") -> list[tuple[str, str]]:
    return sorted(conn.execute(
        "SELECT object, extraction_method FROM beliefs WHERE tenant_id='t' AND subject='user' AND predicate=? "
        "AND valid_until IS NULL AND invalidated_at IS NULL", (predicate,)).fetchall())


@pytest.mark.parametrize("label", ["functional", "set", "state", None])
def test_an_agent_write_never_stands_beside_the_users_own_fact(conn, label):
    _write(conn, "London", "functional", "user_stated", 0)
    result = _write(conn, "Tokyo", label, "agent_asserted", 1)
    assert result.get("blocked") == "lower_trust_source", result
    assert _current(conn) == [("London", "user_stated")]
    _write(conn, "Osaka", "functional", "agent_asserted", 2)
    assert _current(conn) == [("London", "user_stated")]


def test_the_users_own_words_still_replace_their_fact(conn):
    _write(conn, "London", "functional", "user_stated", 0)
    _write(conn, "Lisbon", "set", "user_stated", 1)
    assert _current(conn) == [("Lisbon", "user_stated")]


@pytest.mark.parametrize("predicate", ["note", "visited", "has_pet"])
def test_a_name_kazma_does_not_classify_keeps_the_writers_label(conn, predicate):
    """Lists stay lists: the label decides only where Kazma has no class."""
    _write(conn, "a", "set", "user_stated", 0, predicate)
    _write(conn, "b", "set", "agent_asserted", 1, predicate)
    assert _current(conn, predicate) == [("a", "user_stated"), ("b", "agent_asserted")]


def test_a_store_an_older_build_left_keeps_the_users_value(conn):
    """Two current homes, the agent's newer than the user's, as the old
    order wrote them: the next write keeps the user's and closes the
    agent's, and is judged against the user's."""
    _write(conn, "London", "functional", "user_stated", 0)
    conn.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
        "structural_importance, source_trust_weight, valid_from, ingested_at, extraction_method, metadata_json) "
        "VALUES ('b_tokyo', 't', 'user', 'lives_in', 'set', 'Tokyo', 0.9, 3, 0.5, ?, ?, 'agent_asserted', '{}')",
        (T0 + DAY, T0 + DAY))
    conn.commit()
    assert len(_current(conn)) == 2
    result = _write(conn, "Osaka", "functional", "agent_asserted", 2)
    assert result.get("blocked") == "lower_trust_source"
    assert _current(conn) == [("London", "user_stated")]
    ended = conn.execute("SELECT valid_from, valid_until FROM beliefs WHERE id='b_tokyo'").fetchone()
    assert ended[1] == ended[0]  # it never held in the user's value's place


# ── negative controls: the order and the clean-up as they were ───────────


def _old_classify(predicate, explicit, **_source):
    p = (predicate or "").strip().lower().replace(" ", "_")
    if explicit in ("functional", "set", "state"):
        return explicit
    if p in belief_mutation._FUNCTIONAL_PREDICATES:
        return "functional"
    return "set"


def test_with_the_label_first_an_agent_list_stands_beside_the_users_home(conn, monkeypatch):
    monkeypatch.setattr(belief_mutation, "_classify_predicate", _old_classify)
    _write(conn, "London", "functional", "user_stated", 0)
    _write(conn, "Tokyo", "set", "agent_asserted", 1)
    assert _current(conn) == [("London", "user_stated"), ("Tokyo", "agent_asserted")]


def test_keeping_the_newest_closes_the_users_own_fact(conn, monkeypatch):
    monkeypatch.setattr(belief_mutation, "_classify_predicate", _old_classify)
    monkeypatch.setattr(belief_mutation, "_survivor", lambda rows: rows[0] if rows else None)
    _write(conn, "London", "functional", "user_stated", 0)
    _write(conn, "Tokyo", "set", "agent_asserted", 1)
    _write(conn, "Osaka", "functional", "agent_asserted", 2)
    assert ("London", "user_stated") not in _current(conn)


# ── the review of F7 (2026-11): names of the same words, the user's own list ──


def _all_current(conn) -> list[tuple[str, str, str]]:
    return sorted(conn.execute(
        "SELECT predicate, object, extraction_method FROM beliefs WHERE tenant_id='t' AND subject='user' "
        "AND valid_until IS NULL AND invalidated_at IS NULL").fetchall())


@pytest.mark.parametrize("variant", ["live_in", "in_lives", "lives_in_the", "LIVES_IN", "in_live"])
@pytest.mark.parametrize("label", ["set", "functional", None])
def test_a_name_of_the_same_words_is_kazmas_name(conn, variant, label):
    """The class was read from the exact name: "live_in" or "in_lives"
    labelled a list stood beside the user's home, past the trust gate."""
    _write(conn, "London", "functional", "user_stated", 0)
    result = _write(conn, "Tokyo", label, "agent_asserted", 1, variant)
    assert result.get("blocked") == "lower_trust_source", result
    assert _all_current(conn) == [("lives_in", "London", "user_stated")]


@pytest.mark.parametrize("variant", ["name", "is_name", "the_name_is"])
@pytest.mark.parametrize("label", ["set", "functional"])
def test_a_name_of_the_same_words_beside_the_users_name(conn, variant, label):
    _write(conn, "Sam", "functional", "user_stated", 0, "name_is")
    result = _write(conn, "Bob", label, "agent_asserted", 1, variant)
    assert result.get("blocked") == "lower_trust_source", result
    assert _all_current(conn) == [("name_is", "Sam", "user_stated")]


def test_the_users_write_under_another_name_of_the_same_words_lands_on_kazmas(conn):
    _write(conn, "London", "functional", "user_stated", 0)
    _write(conn, "Lisbon", "set", "user_stated", 1, "in_lives")
    assert _all_current(conn) == [("lives_in", "Lisbon", "user_stated")]


@pytest.mark.parametrize("predicate", ["works_at", "active_project", "current_role"])
@pytest.mark.parametrize("source", ["user_explicit", "user_stated"])
def test_the_users_own_list_stays_a_list(conn, predicate, source):
    """Two jobs, projects in parallel: on these names a list from the user's
    own words is kept (F7 had collapsed it to the last)."""
    for day, value in enumerate(("Acme", "Initech", "Globex")):
        _write(conn, value, "set", source, day, predicate)
    assert sorted(value for value, _ in _current(conn, predicate)) == ["Acme", "Globex", "Initech"]


@pytest.mark.parametrize("predicate", ["works_at", "active_project", "current_role"])
@pytest.mark.parametrize("source", ["agent_asserted", "llm_inferred", "system_tool"])
def test_a_list_from_anyone_else_is_still_single_valued(conn, predicate, source):
    _write(conn, "Acme", "functional", "user_stated", 0, predicate)
    result = _write(conn, "Initech", "set", source, 1, predicate)
    assert result.get("blocked") == "lower_trust_source", result
    assert _current(conn, predicate) == [("Acme", "user_stated")]


def test_a_refused_write_leaves_the_users_list_whole(conn):
    """The clean-up of extra current values closed the user's other jobs
    before the trust gate refused the agent's write."""
    _write(conn, "Acme", "set", "user_explicit", 0, "works_at")
    _write(conn, "Initech", "set", "user_explicit", 1, "works_at")
    result = _write(conn, "Globex", "functional", "agent_asserted", 2, "works_at")
    assert result.get("blocked") == "lower_trust_source", result
    assert sorted(value for value, _ in _current(conn, "works_at")) == ["Acme", "Initech"]


def test_a_new_single_value_from_the_user_replaces_their_list(conn):
    _write(conn, "Acme", "set", "user_stated", 0, "works_at")
    _write(conn, "Initech", "set", "user_stated", 1, "works_at")
    _write(conn, "Globex", "functional", "user_stated", 2, "works_at")
    assert _current(conn, "works_at") == [("Globex", "user_stated")]


@pytest.mark.parametrize("predicate", ["name_is", "preferred_name", "lives_in", "located_in", "favorite_color",
                                       "color_favorite", "grok_next_reset"])
def test_a_name_that_holds_one_value_holds_one_whatever_the_user_labels(conn, predicate):
    """Confirmed single-valued: one name, one home, one location, one
    favourite, one next reset per service."""
    _write(conn, "a", "set", "user_explicit", 0, predicate)
    _write(conn, "b", "set", "user_explicit", 1, predicate)
    assert len(_all_current(conn)) == 1 and _all_current(conn)[0][1:] == ("b", "user_explicit")


@pytest.mark.parametrize("predicate", ["names", "favorite_foods", "favorite_colors", "colors_favorite",
                                       "preferred_names"])
def test_a_plural_name_is_a_list(conn, predicate):
    """A plural of a thing Kazma keeps one of is a list, whatever the writer
    labels it (the security review of F1's second half, 2026-11-18: "names"
    replaced the user's name, "favorite_foods" kept one food)."""
    assert belief_mutation._classify_predicate(predicate, "functional") == "set"
    _write(conn, "a", "functional", "user_stated", 0, predicate)
    _write(conn, "b", "functional", "user_stated", 1, predicate)
    assert _current(conn, predicate) == [("a", "user_stated"), ("b", "user_stated")]


@pytest.mark.parametrize(("predicate", "named"), [
    ("lives_in", "lives_in"), ("live_in", "lives_in"), ("works_at", "works_at"), ("work_at", "works_at"),
    ("is_name", "name_is"), ("the_name", "name_is"), ("name", "name_is"), ("favorite_color", "favorite_color"),
])
def test_a_verbs_s_is_no_plural(predicate, named):
    """"lives_in" and "works_at" keep their own "s"; a name that only adds
    "the" or "is" is still Kazma's (one value: F7)."""
    assert belief_mutation._kazma_name(predicate) == named
    assert belief_mutation._classify_predicate(predicate, "set") == "functional"


def test_control_without_the_plural_rule_names_holds_one_value(conn, monkeypatch):
    monkeypatch.setattr(belief_mutation, "_names_a_list", lambda predicate: False)
    monkeypatch.setattr(belief_mutation, "_plural_of", lambda predicate, named: False)
    _write(conn, "a", "set", "user_stated", 0, "names")
    _write(conn, "b", "set", "user_stated", 1, "names")
    assert _current(conn, "names") == [("b", "user_stated")]
    assert belief_mutation._classify_predicate("favorite_foods", "set") == "functional"


def test_every_single_valued_name_is_declared_listable_or_not():
    """A new single-valued name says whether a person holds several at once."""
    from kazma_core.memory.predicates import _predicate_key

    names = sorted(belief_mutation._FUNCTIONAL_PREDICATES | belief_mutation._STATE_PREDICATES)
    keys = [_predicate_key(n) for n in names]
    assert len(keys) == len(set(keys)), "two of Kazma's names are the same words"
    assert belief_mutation._LISTABLE <= belief_mutation._FUNCTIONAL_PREDICATES


# ── negative controls: the review's findings on the F7 code ──────────────


def test_control_classified_by_the_exact_name_a_variant_stands_beside(conn, monkeypatch):
    declared = belief_mutation._FUNCTIONAL_PREDICATES | belief_mutation._STATE_PREDICATES
    monkeypatch.setattr(belief_mutation, "_kazma_name", lambda predicate: predicate if predicate in declared else None)
    _write(conn, "London", "functional", "user_stated", 0)
    _write(conn, "Tokyo", "set", "agent_asserted", 1, "in_lives")
    assert ("in_lives", "Tokyo", "agent_asserted") in _all_current(conn)


def test_control_without_the_users_list_it_collapses_to_the_last(conn, monkeypatch):
    monkeypatch.setattr(belief_mutation, "_LISTABLE", frozenset())
    for day, value in enumerate(("Acme", "Initech", "Globex")):
        _write(conn, value, "set", "user_explicit", day, "works_at")
    assert _current(conn, "works_at") == [("Globex", "user_explicit")]
