"""The Postgres memory mirror stays whole (2026-09-27).

Found on the live install: 116 turns still "working" in the mirror after the
working-turn promotion moved them locally (it never told the mirror), 17 facts
that never reached it, and 53 rows only the mirror held -- test data written
before the database shield, and memories removed here since -- which recall's
top-up could bring back into an answer ("User prefers dark mode").

Held here, each with its negative control:

* every product statement that changes a memory's tier or text reaches the
  mirror (enumerated from the source, like ``test_memory_deletes.py``), and the
  working-turn promotion does it by behaviour;
* the sync pass pushes what the mirror is missing or holds stale, newest
  first and bounded, converges, and never deletes or copies back a row only
  the mirror holds;
* recall's top-up adds only rows another install wrote; one this install
  wrote and no longer holds stays out;
* the digests the sync compares are the ones Postgres computes (real Postgres).
"""

from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

from kazma_core.memory import state_backend as sb
from kazma_core.memory.schema_v2 import ensure_primary_schema

REPO = Path(__file__).resolve().parents[1]
THIS = "a" * 32
OTHER = "b" * 32


class _Mirror:
    """An in-memory Postgres mirror: what the real one stores and digests."""

    name = "postgres"
    write_ready = True
    available = True

    def __init__(self) -> None:
        self.episodes: dict[str, dict] = {}
        self.beliefs: dict[str, dict] = {}
        self.writes = 0

    def mirror_episode(self, row: dict) -> bool:
        self.writes += 1
        self.episodes[str(row["id"])] = {
            **{k: row.get(k) for k in ("user_text", "assistant_text", "summary_text")},
            "tier": row.get("tier") or "episodic",
            "metadata_json": sb._stamp_region_meta(row),
        }
        return True

    def mirror_belief(self, row: dict) -> bool:
        self.writes += 1
        self.beliefs[str(row["id"])] = {k: row.get(k) for k in ("object", "valid_until", "invalidated_at")}
        return True

    def episode_digest(self) -> dict:
        return {
            eid: sb._episode_digest(r["tier"], r["user_text"], r["assistant_text"], r["summary_text"])
            for eid, r in self.episodes.items()
        }

    def belief_digest(self) -> dict:
        import hashlib

        return {
            bid: (r["valid_until"] is None and r["invalidated_at"] is None,
                  hashlib.md5(str(r["object"] or "").encode(), usedforsecurity=False).hexdigest())
            for bid, r in self.beliefs.items()
        }

    def row_regions(self, table: str) -> dict:
        rows = self.episodes if table == "kazma_episodes" else self.beliefs
        out = {}
        for rid, r in rows.items():
            region = json.loads(r.get("metadata_json") or "{}").get("region")
            if region:
                out[rid] = region
        return out


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    mirror = _Mirror()
    monkeypatch.setattr(sb, "get_state_backend", lambda: mirror)
    monkeypatch.setattr(sb, "is_state_primary", lambda cfg=None: False)
    monkeypatch.setattr(sb, "_this_install_id", lambda: THIS)
    sb._last_sync.clear()
    yield conn, mirror
    conn.close()


def _episode(conn, eid, text, *, tier="episodic", age=0.0, session="s1"):
    conn.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
        "tier, created_at, metadata_json) VALUES (?, 'default', ?, 1, ?, 'answer', ?, ?, '{}')",
        (eid, session, text, tier, time.time() - age),
    )
    conn.commit()


def _belief(conn, bid, obj, *, age=0.0):
    now = time.time() - age
    conn.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, object, predicate_type, valid_from, "
        "ingested_at, confidence) VALUES (?, 'default', 'user', 'likes', ?, 'functional', ?, ?, 0.9)",
        (bid, obj, now, now),
    )
    conn.commit()


# ── every change reaches the mirror ───────────────────────────────────────

_CHANGE = re.compile(
    r"UPDATE\s+episodes\s+SET\b(?:(?!\bWHERE\b).)*?\b(tier|user_text|assistant_text|summary_text)\s*=",
    re.IGNORECASE | re.DOTALL,
)

#: (file, enclosing function) -> how the change reaches the mirror.
DECLARED = {
    ("kazma-core/kazma_core/memory/consolidator.py", "promote_working_memory"):
        "remirror_episodes on the promoted ids",
    ("kazma-core/kazma_core/memory/macro_sleep.py", "run_macro_sleep"):
        "_propagate_episode_moves -> remirror_episodes",
    ("kazma-core/kazma_core/memory/macro_sleep.py", "<module>"):
        "_ARCHIVE_EPISODE_SQL, run by run_macro_sleep, whose moves go the same way",
    ("kazma-core/kazma_core/memory/forget.py", "_tombstone"):
        "forget_episode remirrors every id it tombstones",
    ("kazma-core/kazma_core/memory/rehydrate.py", "_restore"):
        "run_rehydrate_pass remirrors each restored row",
}


def memory_changes(sources: dict[str, str]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        def visit(node: ast.AST, function: str) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node.name
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _CHANGE.search(node.value):
                found.add((rel, function))
            for child in ast.iter_child_nodes(node):
                visit(child, function)

        visit(tree, "<module>")
    return found


def _product_sources() -> dict[str, str]:
    out = {}
    for pkg in REPO.glob("kazma-*/kazma_*"):
        for path in pkg.rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" not in rel:
                out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def test_every_tier_or_text_change_is_declared_with_its_mirror_path():
    found = memory_changes(_product_sources())
    assert found - set(DECLARED) == set(), "a memory change with no declared way to the mirror"
    assert set(DECLARED) - found == set(), "a declared change no longer exists -- drop its entry"


def test_the_enumeration_sees_an_undeclared_change():
    """Negative control: a synthetic statement is found."""
    src = 'def sneaky(conn):\n    conn.execute("UPDATE episodes SET tier = \'recall\' WHERE id = ?", ("x",))\n'
    assert memory_changes({"x.py": src}) == {("x.py", "sneaky")}
    assert memory_changes({"y.py": 'SQL = "UPDATE episodes SET embedding = NULL WHERE id = ?"'}) == set()


def test_the_working_turn_promotion_tells_the_mirror(world):
    conn, mirror = world
    _episode(conn, "e_last", "what did we decide", tier="working", session="chat-1")
    _episode(conn, "e_other", "other chat", tier="working", session="chat-2")
    from kazma_core.memory.consolidator import promote_working_memory

    assert promote_working_memory("chat-1", tenant_id="default") == 1
    assert mirror.episodes["e_last"]["tier"] == "episodic"
    assert "e_other" not in mirror.episodes


def test_without_the_remirror_the_mirror_keeps_working(world, monkeypatch):
    """Negative control: the promotion as it was, without telling the mirror."""
    conn, mirror = world
    mirror.mirror_episode({"id": "e_last", "user_text": "q", "tier": "working"})
    _episode(conn, "e_last", "q", tier="working", session="chat-1")
    monkeypatch.setattr(sb, "remirror_episodes", lambda conn, ids: 0)
    from kazma_core.memory.consolidator import promote_working_memory

    promote_working_memory("chat-1", tenant_id="default")
    assert mirror.episodes["e_last"]["tier"] == "working"


# ── the sync pass ─────────────────────────────────────────────────────────


def test_the_sync_pushes_what_the_mirror_misses_and_leaves_its_own_rows(world):
    conn, mirror = world
    _episode(conn, "e_missing", "never mirrored")
    _episode(conn, "e_stale", "moved here")
    mirror.mirror_episode({"id": "e_stale", "user_text": "moved here", "assistant_text": "answer",
                           "tier": "working"})
    _belief(conn, "b_missing", "tea")
    mirror.episodes["e_orphan"] = {"user_text": "User prefers dark mode", "assistant_text": "",
                                   "summary_text": "", "tier": "episodic", "metadata_json": "{}"}

    stats = sb.sync_state_mirror(conn)
    assert stats["episodes_pushed"] == 2 and stats["beliefs_pushed"] == 1 and stats["failed"] == 0
    assert mirror.episodes["e_stale"]["tier"] == "episodic" and "e_missing" in mirror.episodes
    assert mirror.beliefs["b_missing"]["object"] == "tea"
    assert "e_orphan" in mirror.episodes and stats["mirror_only_episodes"] == 1  # counted, left alone
    assert sb.last_mirror_sync()["mirror_only_episodes"] == 1

    writes = mirror.writes
    again = sb.sync_state_mirror(conn)
    assert again["episodes_pushed"] == again["beliefs_pushed"] == 0 and mirror.writes == writes


def test_the_sync_is_bounded_and_newest_first(world):
    conn, mirror = world
    for i in range(5):
        _episode(conn, f"e{i}", f"turn {i}", age=i * 60)
    stats = sb.sync_state_mirror(conn, limit=2)
    assert set(mirror.episodes) == {"e0", "e1"} and stats["episodes_behind"] == 3


def test_a_fact_that_ended_here_ends_in_the_mirror(world):
    conn, mirror = world
    _belief(conn, "b1", "tea")
    sb.sync_state_mirror(conn)
    conn.execute("UPDATE beliefs SET invalidated_at = ? WHERE id = 'b1'", (time.time(),))
    conn.commit()
    sb.sync_state_mirror(conn)
    assert mirror.beliefs["b1"]["invalidated_at"] is not None


def test_no_mirror_no_pass(world, monkeypatch):
    conn, _ = world
    monkeypatch.setattr(sb, "get_state_backend", lambda: sb._NullStateBackend())
    assert sb.sync_state_mirror(conn) == {"skipped": "no mirror"}
    monkeypatch.setattr(sb, "get_state_backend", lambda: _Mirror())
    monkeypatch.setattr(sb, "is_state_primary", lambda cfg=None: True)
    assert sb.sync_state_mirror(conn) == {"skipped": "postgres is the primary store"}


def test_the_sync_runs_on_the_maintenance_cadence():
    from kazma_core.memory import worker_bootstrap as wb

    assert ("memory mirror sync", wb._sync_memory_mirror) in wb._MAINTENANCE_SWEEPS


def test_a_nul_byte_is_mirrored_as_postgres_can_store_it():
    assert sb._pg_text("a\x00b") == "ab" and sb._pg_text(None) is None
    assert sb._episode_digest("recall", "a\x00b", None, "") == sb._episode_digest("recall", "ab", "", "")


# ── the top-up reads only other installs' rows ────────────────────────────


def test_a_mirror_write_names_the_install(world):
    meta = json.loads(sb._stamp_region_meta({"metadata_json": '{"source": "x"}'}))
    assert meta["install"] == THIS and meta["source"] == "x"


@pytest.mark.parametrize(("meta", "elsewhere"), [
    ({"install": OTHER}, True),
    ({"install": THIS}, False),  # removed here: stays out
    ({}, False),                 # mirrored before writers were tagged: this install's
])
def test_written_elsewhere(world, meta, elsewhere):
    assert sb.written_elsewhere({"metadata_json": json.dumps(meta)}) is elsewhere


def _topup(world, monkeypatch, rows):
    conn, _ = world
    monkeypatch.setattr(sb, "search_state_episodes", lambda q, **k: rows)
    monkeypatch.setattr(sb, "search_state_beliefs", lambda q, **k: [])
    from kazma_core.memory.recall import _merge_remote_state_hits

    eps, _ = _merge_remote_state_hits("Do I prefer dark mode?", tenant_id="default", limit=5,
                                      episodes=[], beliefs=[], explain=False, local_conn=conn)
    return [h.id for h in eps]


def _row(eid, install):
    return {"id": eid, "user_text": "User prefers dark mode", "assistant_text": "", "summary_text": "",
            "tier": "episodic", "metadata_json": json.dumps({"install": install} if install else {})}


def test_the_top_up_leaves_out_what_this_install_removed(world, monkeypatch):
    assert _topup(world, monkeypatch, [_row("e_mine", THIS), _row("e_untagged", None)]) == []


def test_the_top_up_still_reads_another_installs_memory(world, monkeypatch):
    """Negative control: the same row, written by another install, is added."""
    assert _topup(world, monkeypatch, [_row("e_theirs", OTHER)]) == ["e_theirs"]


# ── digest parity with a real Postgres ────────────────────────────────────


# ── region conflict policies (origin_wins and fail_closed differ) ─────────


def _theirs(mirror: _Mirror, conn, n: int, *, region: str = "eu-1") -> list[str]:
    """Rows another region wrote to the mirror, changed here since."""
    ids = [f"theirs{i}" for i in range(n)]
    for rid in ids:
        _episode(conn, rid, f"changed here {rid}")
        mirror.episodes[rid] = {"user_text": "their text", "assistant_text": "answer",
                                "summary_text": None, "tier": "episodic",
                                "metadata_json": json.dumps({"region": region})}
    return ids


@pytest.fixture()
def pages(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert",
                        lambda *a, **k: sent.append((a, k)) or True)
    return sent


def _policy(monkeypatch, policy: str, region: str = "us-1") -> None:
    monkeypatch.setattr(sb, "state_conflict_policy", lambda cfg=None: policy)
    monkeypatch.setattr(sb, "state_region", lambda cfg=None: region)


def test_origin_wins_keeps_the_other_regions_row_quietly(world, monkeypatch, pages, caplog):
    conn, mirror = world
    _policy(monkeypatch, "origin_wins")
    theirs = _theirs(mirror, conn, 3)
    _episode(conn, "mine0", "new here")

    caplog.set_level("WARNING", logger=sb.__name__)
    stats = sb.sync_state_mirror(conn)

    assert stats["kept_by_origin"] == 3 and stats["failed"] == 0 and "region_conflicts" not in stats
    assert all(mirror.episodes[t]["user_text"] == "their text" for t in theirs)
    assert "mine0" in mirror.episodes
    assert pages == [] and not [r for r in caplog.records if r.name == sb.__name__]


def test_fail_closed_refuses_the_same_rows_and_says_so(world, monkeypatch, pages, caplog):
    conn, mirror = world
    _policy(monkeypatch, "fail_closed")
    theirs = _theirs(mirror, conn, 3)

    caplog.set_level("WARNING", logger=sb.__name__)
    stats = sb.sync_state_mirror(conn)

    assert stats["region_conflicts"] == 3 and stats["failed"] == 0 and "kept_by_origin" not in stats
    assert all(mirror.episodes[t]["user_text"] == "their text" for t in theirs)
    assert [a[0] for a, _k in pages] == ["memory.region_conflict"]
    assert any("fail_closed" in r.getMessage() for r in caplog.records if r.name == sb.__name__)
    assert sb.last_mirror_sync()["region_conflicts"] == 3, "memory health shows it"


def test_a_refused_row_spends_none_of_the_push_budget(world, monkeypatch, pages):
    """Counted as pushes, 500 refused rows would stop every other push."""
    conn, mirror = world
    _policy(monkeypatch, "origin_wins")
    _theirs(mirror, conn, 3)
    _episode(conn, "mine0", "a")
    _episode(conn, "mine1", "b")
    stats = sb.sync_state_mirror(conn, limit=2)
    assert stats["episodes_pushed"] == 2 and {"mine0", "mine1"} <= set(mirror.episodes)


def test_negative_control_last_write_wins_pushes_them(world, monkeypatch, pages):
    conn, mirror = world
    _policy(monkeypatch, "last_write_wins")
    theirs = _theirs(mirror, conn, 3)
    stats = sb.sync_state_mirror(conn)
    assert stats["episodes_pushed"] == 3 and pages == []
    assert all(mirror.episodes[t]["user_text"].startswith("changed here") for t in theirs)


def test_the_same_region_is_never_a_conflict(world, monkeypatch, pages):
    conn, mirror = world
    _policy(monkeypatch, "fail_closed", region="eu-1")
    _theirs(mirror, conn, 2, region="eu-1")
    stats = sb.sync_state_mirror(conn)
    assert stats["episodes_pushed"] == 2 and stats["region_conflicts"] == 0 and pages == []


@pytest.mark.postgres
def test_the_region_of_a_mirrored_row_is_read_from_postgres():
    """``row_regions`` reads the writer's region from the stored metadata,
    and a row whose metadata is not JSON does not fail the read."""
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn:
        pytest.skip("no Postgres configured")
    backend = sb.PostgresStateBackend(dsn)
    if not backend.available:
        pytest.skip("Postgres not reachable")
    tag = uuid.uuid4().hex[:8]
    rows = {f"e_region_{tag}_a": '{"region": "eu-1", "install": "x"}',
            f"e_region_{tag}_b": '{"install": "x"}',
            f"e_region_{tag}_c": 'not json "region": at all {'}
    pg = backend._connect()
    try:
        backend._ensure(pg)
        cur = pg.cursor()
        for rid, meta in rows.items():
            cur.execute("INSERT INTO kazma_episodes (id, tenant_id, session_id, turn_number, user_text, "
                        "assistant_text, summary_text, tier, structural_importance, created_at, metadata_json) "
                        "VALUES (%s, 'default', 's', 1, 'q', 'a', NULL, 'episodic', 1, %s, %s)",
                        (rid, time.time(), meta))
        pg.commit()
        got = {k: v for k, v in backend.row_regions("kazma_episodes").items() if k.startswith(f"e_region_{tag}")}
        assert got == {f"e_region_{tag}_a": "eu-1"}
        assert backend.row_regions("not_a_mirror_table") == {}
    finally:
        cur = pg.cursor()
        cur.execute("DELETE FROM kazma_episodes WHERE id = ANY(%s)", (list(rows),))
        pg.commit()
        pg.close()


@pytest.mark.postgres
def test_the_sync_digests_match_what_postgres_computes(tmp_path, monkeypatch):
    """Texts with Arabic, emoji, a NUL and NULL columns: after one push the
    real mirror's digests equal the local ones, so a second pass pushes
    nothing. Needs KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1."""
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn:
        pytest.skip("no Postgres configured")
    backend = sb.PostgresStateBackend(dsn)
    if not backend.available:
        pytest.skip("Postgres not reachable")
    monkeypatch.setattr(sb, "get_state_backend", lambda: backend)
    monkeypatch.setattr(sb, "is_state_primary", lambda cfg=None: False)
    monkeypatch.setattr(sb, "_this_install_id", lambda: THIS)
    conn = sqlite3.connect(str(tmp_path / "m.db"))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    tag = uuid.uuid4().hex[:8]
    ids = [f"e_sync_{tag}_{i}" for i in range(3)]
    conn.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
                 "summary_text, tier, created_at, metadata_json) VALUES (?, 'default', 's', 1, ?, ?, NULL, "
                 "'recall', ?, '{}')", (ids[0], "تذكر: الاجتماع يوم الأحد 🗓", "حاضر", time.time()))
    conn.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
                 "summary_text, tier, created_at, metadata_json) VALUES (?, 'default', 's', 2, ?, NULL, '', "
                 "'working', ?, '{}')", (ids[1], "a\x00b", time.time()))
    conn.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
                 "summary_text, tier, created_at, metadata_json) VALUES (?, 'default', 's', 3, NULL, NULL, "
                 "'only a summary', 'archived', ?, '{}')", (ids[2], time.time()))
    bid = f"b_sync_{tag}"
    conn.execute("INSERT INTO beliefs (id, tenant_id, subject, predicate, object, predicate_type, valid_from, "
                 "ingested_at, confidence) VALUES (?, 'default', 'user', 'lives_in', 'الكويت', 'functional', ?, ?, 0.9)",
                 (bid, time.time(), time.time()))
    conn.commit()
    try:
        sb.sync_state_mirror(conn)
        remote = backend.episode_digest()
        for eid in ids:
            row = conn.execute("SELECT tier, user_text, assistant_text, summary_text FROM episodes WHERE id = ?",
                               (eid,)).fetchone()
            assert remote[eid] == sb._episode_digest(*row)
        again = sb.sync_state_mirror(conn)
        assert again["episodes_pushed"] == 0 and again["beliefs_pushed"] == 0
    finally:
        pg = backend._connect()
        try:
            cur = pg.cursor()
            cur.execute("DELETE FROM kazma_episodes WHERE id = ANY(%s)", (ids,))
            cur.execute("DELETE FROM kazma_beliefs WHERE id = %s", (bid,))
            pg.commit()
        finally:
            pg.close()
        conn.close()
