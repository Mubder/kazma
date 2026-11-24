"""A forgotten or replaced fact's entity is asked for by its id, not by the
fact's text (2026-11-24, found by the Spaces stage 3 work).

A fact keeps the user's own words ("Lisbon") and its entity the id they make
("lisbon", ``entity_resolution.slug``). ``hygiene.invalidate_belief`` and the
supersede in ``mutate_belief`` asked ``retire_empty_entities`` -- and the
entity counts -- by the text: an Arabic name is its own id, so it went with
its last fact, and an English name with a capital stayed on the memory map as
a bare entity, on every install. Every caller that holds a fact asks
``entity_counts.entity_ids_named`` now.

Through the product: the quick pass writes the fact (and mints the entity),
then the turn is forgotten, the fact replaced, invalidated, or said to be no
longer true (``end_belief``, which retired nothing until the second review of
dated event lines, 2026-11-24).
Negative control: the ids taken from the text alone, which loses English only.
"""

from __future__ import annotations

import ast
import sqlite3

import pytest
from kazma_core.memory import belief_extractor, entity_counts
from kazma_core.memory.entity_resolution import slug
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

from tests.test_memory_entity_key import _product_sources

TOLD = 1780221600.0

#: What the user says, the fact's value as kept, and what replaces it.
SAID = [
    ("I live in Lisbon.", "Lisbon", "I live in Porto."),
    ("I live in Doha.", "Doha", "I live in Porto."),
    ("أسكن في لشبونة.", "لشبونة", "أسكن في بورتو."),
    ("أسكن في الدوحة.", "الدوحة", "أسكن في بورتو."),
]


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), isolation_level=None)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"), isolation_level=None)
    ensure_ops_schema(ops)
    yield conn, ops
    conn.close()
    ops.close()


def _say(mem, text: str, turn: int = 1) -> None:
    conn, ops = mem
    belief_extractor.extract_and_apply_beliefs_sync(conn, ops, text, session_id="chat-1", turn=turn,
                                                    tenant_id="default", now=TOLD + turn, private=True)


def _entities(conn) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT id FROM entities WHERE type = 'concept'")}


def _forget_turn(conn, said: str) -> None:
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.forget import forget_episode

    row = episode_row(session_id="chat-1", turn_number=1, user_text=said, assistant_text="Noted.",
                      tenant_id="default", source="test", created_at=TOLD + 1)
    have = {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}
    cols = {k: v for k, v in row.items() if k in have}
    conn.execute(f"INSERT INTO episodes ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                 list(cols.values()))
    assert forget_episode(str(row["id"]), tenant_id="default", conn=conn)["ok"]


def _end(mem, how: str, said: str, then: str) -> None:
    conn, _ops = mem
    if how == "forgotten":
        _forget_turn(conn, said)
    elif how == "replaced":
        _say(mem, then, turn=2)
    elif how == "ended":
        from kazma_core.memory.belief_mutation import end_belief

        bid = conn.execute("SELECT id FROM beliefs WHERE valid_until IS NULL AND predicate != 'related_to'").fetchone()
        assert end_belief(conn, str(bid[0]), tenant_id="default", extraction_method="user_explicit", now=TOLD + 9,
                          ops_conn=mem[1])["action"] == "ended"
    else:
        from kazma_core.memory.hygiene import invalidate_belief

        bid = conn.execute("SELECT id FROM beliefs WHERE valid_until IS NULL AND predicate != 'related_to'").fetchone()
        assert invalidate_belief(str(bid[0]), conn=conn, tenant_id="default")["ok"]


@pytest.mark.parametrize("how", ["forgotten", "replaced", "invalidated", "ended"])
@pytest.mark.parametrize(("said", "value", "then"), SAID)
def test_an_entity_goes_with_its_last_fact(mem, how, said, value, then):
    conn, _ops = mem
    _say(mem, said)
    assert slug(value) in _entities(conn), (value, _entities(conn))
    _end(mem, how, said, then)
    assert slug(value) not in _entities(conn)


@pytest.mark.parametrize("how", ["forgotten", "replaced", "invalidated"])
def test_control_asked_by_the_text_the_english_names_stay(mem, monkeypatch, how):
    """Negative control: the old call."""
    monkeypatch.setattr(entity_counts, "entity_ids_named",
                        lambda conn, values, *, tenant_id: [str(v) for v in values if v])
    for said, value, then in (SAID[0], SAID[2]):
        conn, _ops = mem
        conn.execute("DELETE FROM beliefs")
        conn.execute("DELETE FROM episodes")
        _say(mem, said)
        _end(mem, how, said, then)
        assert (slug(value) in _entities(conn)) is value.isascii(), value


@pytest.mark.parametrize(("value", "other"), [("Lisbon", "LISBON"), ("Abu Dhabi", "abu-dhabi")])
def test_a_live_fact_in_another_spelling_keeps_the_entity(mem, value, other):
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.entity_resolution import resolve_entity
    from kazma_core.memory.hygiene import invalidate_belief

    conn, ops = mem
    resolve_entity(conn, value, entity_type="concept", tenant_id="default")
    mutate_belief(conn, "user", "lives_in", value, ops_conn=ops, private=True, now=TOLD)
    mutate_belief(conn, "user", "visited", other, ops_conn=ops, predicate_type="set", private=True, now=TOLD)
    bid = conn.execute("SELECT id FROM beliefs WHERE object = ?", (value,)).fetchone()[0]
    assert invalidate_belief(str(bid), conn=conn, tenant_id="default")["ok"]
    assert slug(value) in _entities(conn)


def test_the_counts_follow_the_fact_at_once(mem):
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.hygiene import invalidate_belief

    conn, ops = mem
    _say(mem, "I live in Lisbon.")
    mutate_belief(conn, "user", "visited", "Lisbon", ops_conn=ops, predicate_type="set", private=True, now=TOLD)
    count = lambda: conn.execute("SELECT belief_count FROM entities WHERE id = 'lisbon'").fetchone()[0]  # noqa: E731
    assert count() == 2
    bid = conn.execute("SELECT id FROM beliefs WHERE predicate = 'visited'").fetchone()[0]
    invalidate_belief(str(bid), conn=conn, tenant_id="default")
    assert count() == 1


# ── The class: no caller asks by a fact's text ───────────────────────────

ASKED_BY_ID = {"recompute_entity_counts", "retire_empty_entities"}
#: The callers that hold entities' ids, not a fact: each with where they come from.
HOLD_IDS = {
    ("kazma-core/kazma_core/agent/tool_builtins/memory.py", "_mem_merge_entities"): "the two entities merged",
    ("kazma-core/kazma_core/agent/tool_builtins/memory.py", "_mem_purge_empty_entities"): "plain_empty_shells",
    ("kazma-core/kazma_core/memory/entity_counts.py", "repair_entity_counts"): "the entities whose counts differ",
    ("kazma-core/kazma_core/memory/entity_repair.py", "repair_collapsed"): "the collapsed id",
    ("kazma-core/kazma_core/memory/entity_resolution.py", "decide_entity_merge"): "the two entities merged",
    ("kazma-core/kazma_core/memory/schema_v2.py", "_backfill_entity_counts"): "the entities with no counts yet",
    ("kazma-ui/kazma_ui/memory_api.py", "_merge_entities_sync"): "the two entities merged",
    ("kazma-ui/kazma_ui/memory_api.py", "_purge_empty_entities_sync"): "plain_empty_shells",
}


def _own_calls(fn: ast.AST):
    """The calls *fn* makes itself, not those of the functions defined in it."""
    for child in ast.iter_child_nodes(fn):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(child, ast.Call):
            yield child
        yield from _own_calls(child)


def _callers(sources) -> dict[tuple[str, str], bool]:
    """Each function that asks by id -> whether it asks ``entity_ids_named`` too."""
    out: dict[tuple[str, str], bool] = {}
    for rel, text in sources.items():
        if not any(name in text for name in ASKED_BY_ID):
            continue
        tree = ast.parse(text)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {getattr(n.func, "id", getattr(n.func, "attr", "")) for n in _own_calls(fn)}
            if names & ASKED_BY_ID and fn.name not in ASKED_BY_ID:
                out[(rel, fn.name)] = "entity_ids_named" in names
    return out


def _problems(sources) -> list[str]:
    callers = _callers(sources)
    return sorted([f"{rel}:{fn} asks by a value that may be a fact's text" for (rel, fn), asks in callers.items()
                   if not asks and (rel, fn) not in HOLD_IDS]
                  + [f"{rel}:{fn} is declared and asks nothing by id" for rel, fn in HOLD_IDS
                     if (rel, fn) not in callers])


def test_every_caller_asks_by_the_id():
    assert _problems(_product_sources()) == []


def test_a_planted_caller_by_text_is_found():
    body = "def f(conn, row):" + chr(10) + "    retire_empty_entities(conn, [row['object']], tenant_id='t')" + chr(10)
    sources = {**_product_sources(), "kazma-core/kazma_core/memory/planted.py": body}
    assert [p for p in _problems(sources) if "planted" in p]
