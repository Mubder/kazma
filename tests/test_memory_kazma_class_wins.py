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


def _old_classify(predicate, explicit):
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
