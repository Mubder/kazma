"""The Postgres memory mirror syncs what changed, not everything (Memory audit
M31, MEM-L16).

Every 15 minutes the sync read every local memory's full text and every
mirror digest, and pushed each row on a Postgres connection of its own. Now:

* triggers queue each episode or fact written, or changed in a column the
  mirror holds, in ``memory_mirror_outbox`` (same transaction as the change);
* a pass pushes the queued rows as they are now and leaves the digests
  alone; a full comparison runs once in ``memory.v2.mirror_verify_hours``;
* a failed push stays queued and is retried, then left to the full
  comparison; a row another region owns is never pushed from the queue;
* the backend borrows connections from a pool.

Each with its negative control. The scale measurements are
``scripts/memory_scale_bench.py`` (results in the guide).
"""

from __future__ import annotations

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


class _Mirror:
    """An in-memory mirror that counts what a pass reads and writes."""

    name = "postgres"
    write_ready = True
    available = True

    def __init__(self) -> None:
        self.episodes: dict[str, dict] = {}
        self.beliefs: dict[str, dict] = {}
        self.writes = 0
        self.digest_reads = 0
        self.refuse: set[str] = set()

    def mirror_episode(self, row: dict) -> bool:
        if str(row["id"]) in self.refuse:
            return False
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

    def delete_belief(self, row_id: str) -> bool:
        self.writes += 1
        self.beliefs.pop(row_id, None)
        return True

    def episode_digest(self) -> dict:
        self.digest_reads += 1
        return {eid: sb._episode_digest(r["tier"], r["user_text"], r["assistant_text"], r["summary_text"])
                for eid, r in self.episodes.items()}

    def belief_digest(self) -> dict:
        import hashlib

        self.digest_reads += 1
        return {bid: (r["valid_until"] is None and r["invalidated_at"] is None,
                      hashlib.md5(str(r["object"] or "").encode(), usedforsecurity=False).hexdigest())
                for bid, r in self.beliefs.items()}

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
    monkeypatch.setattr(sb, "mirrors_to_postgres", lambda cfg=None: True)
    monkeypatch.setattr(sb, "is_state_primary", lambda cfg=None: False)
    monkeypatch.setattr(sb, "_this_install_id", lambda: "a" * 32)
    _forget_stamps()
    sb._last_sync.clear()
    yield conn, mirror
    sb._last_sync.clear()
    _forget_stamps()
    conn.close()


def _forget_stamps() -> None:
    """The settings store is shared by every test when it is on Postgres."""
    from kazma_core.config_store import get_config_store

    for key in (sb._FULL_VERIFY_KEY, sb._FINDINGS_KEY):
        get_config_store().delete(key)


def _episode(conn, eid, text, *, tier="episodic"):
    conn.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
        "tier, created_at, metadata_json) VALUES (?, 'default', 's1', 1, ?, 'answer', ?, ?, '{}')",
        (eid, text, tier, time.time()),
    )
    conn.commit()


def _belief(conn, bid, obj):
    now = time.time()
    conn.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
        "structural_importance, valid_from, ingested_at) VALUES (?, 'default', 'user', 'likes', 'set', ?, "
        "0.9, 1, ?, ?)", (bid, obj, now, now))
    conn.commit()


def _outbox(conn) -> list[tuple[str, str]]:
    return [(r[0], r[1]) for r in conn.execute("SELECT kind, row_id FROM memory_mirror_outbox ORDER BY seq")]


# ── what queues a row ─────────────────────────────────────────────────────


def test_writes_and_mirrored_changes_queue_the_row(world):
    conn, _mirror = world
    _episode(conn, "e1", "first")
    _belief(conn, "b1", "tea")
    conn.execute("UPDATE episodes SET tier = 'archived' WHERE id = 'e1'")
    conn.execute("UPDATE beliefs SET valid_until = 1 WHERE id = 'b1'")
    conn.execute("DELETE FROM beliefs WHERE id = 'b1'")
    conn.commit()
    assert _outbox(conn) == [("episode", "e1"), ("belief", "b1"), ("episode", "e1"), ("belief", "b1"),
                             ("belief", "b1")]


def test_bookkeeping_queues_nothing(world):
    """An access bump or a vector repair is not something the mirror holds."""
    conn, _mirror = world
    _episode(conn, "e1", "first")
    _belief(conn, "b1", "tea")
    conn.execute("DELETE FROM memory_mirror_outbox")
    conn.execute("UPDATE episodes SET access_count = access_count + 1, last_accessed = 5, embedding = x'00' "
                 "WHERE id = 'e1'")
    conn.execute("UPDATE beliefs SET access_count = 3, last_accessed = 5, embedding = x'00' WHERE id = 'b1'")
    conn.commit()
    assert _outbox(conn) == []


def _trigger_columns(conn, name: str) -> set[str]:
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (name,)).fetchone()[0]
    cols = re.search(r"UPDATE OF (.*?) ON", sql, re.S).group(1)
    return {c.strip() for c in cols.split(",")}


def _mirrored_columns() -> tuple[set[str], set[str]]:
    """The columns the mirror's upserts write (read from their SQL), minus the
    ones a row never changes (its id, tenant, chat, turn and time)."""
    src = (REPO / "kazma-core/kazma_core/memory/state_backend.py").read_text(encoding="utf-8")
    fixed = {"id", "tenant_id", "session_id", "turn_number", "created_at"}
    out = []
    for table in ("kazma_episodes", "kazma_beliefs"):
        m = re.search(rf"INSERT INTO {table} \((.*?)\) VALUES", src, re.S)
        out.append({c.strip() for c in m.group(1).split(",")} - fixed)
    return out[0], out[1]


def test_every_column_the_mirror_holds_queues_its_row(world):
    """Class gate: a column the mirror's upsert writes that no trigger watches
    would change here and never reach the mirror until the next full pass."""
    conn, _mirror = world
    ep_cols, bel_cols = _mirrored_columns()
    assert len(ep_cols) >= 6 and len(bel_cols) >= 10
    assert ep_cols - _trigger_columns(conn, "episodes_mirror_au") == set()
    assert bel_cols - _trigger_columns(conn, "beliefs_mirror_au") == set()


def test_the_column_gate_sees_an_unwatched_column(world):
    """Negative control: a trigger without the tier column fails the gate."""
    conn, _mirror = world
    conn.executescript("""
        DROP TRIGGER episodes_mirror_au;
        CREATE TRIGGER episodes_mirror_au AFTER UPDATE OF user_text, assistant_text ON episodes BEGIN
          INSERT INTO memory_mirror_outbox(kind, row_id) VALUES ('episode', new.id);
        END;
    """)
    ep_cols, _bel = _mirrored_columns()
    assert "tier" in ep_cols - _trigger_columns(conn, "episodes_mirror_au")


# ── what a pass does ──────────────────────────────────────────────────────


def test_a_pass_pushes_only_what_changed_and_reads_no_digest(world):
    conn, mirror = world
    for i in range(50):
        _episode(conn, f"e{i}", f"memory {i}")
    first = sb.sync_state_mirror(conn)  # no stamp yet: a full comparison
    assert first["mode"] == "full" and len(mirror.episodes) == 50 and _outbox(conn) == []
    reads, writes = mirror.digest_reads, mirror.writes

    conn.execute("UPDATE episodes SET tier = 'archived' WHERE id = 'e3'")
    conn.execute("UPDATE episodes SET user_text = 'changed' WHERE id = 'e7'")
    conn.commit()
    _episode(conn, "e50", "new one")
    stats = sb.sync_state_mirror(conn)
    assert stats["mode"] == "incremental" and stats["episodes_pushed"] == 3
    assert mirror.writes - writes == 3 and mirror.digest_reads == reads
    assert mirror.episodes["e3"]["tier"] == "archived" and mirror.episodes["e7"]["user_text"] == "changed"
    assert _outbox(conn) == []
    # The full comparison's findings stand in the incremental pass's report.
    assert stats["verified_at"] == first["verified_at"] and stats["episodes_behind"] == 0


def test_a_full_pass_reads_everything(world):
    """Negative control: the old every-pass behaviour, kept for the periodic
    verification, reads both digests whatever changed."""
    conn, mirror = world
    _episode(conn, "e1", "one")
    sb.sync_state_mirror(conn)
    reads = mirror.digest_reads
    sb.sync_state_mirror(conn, full=True)
    assert mirror.digest_reads == reads + 2


def test_a_row_changed_many_times_is_pushed_once(world):
    conn, mirror = world
    _episode(conn, "e1", "one")
    sb.sync_state_mirror(conn)
    writes = mirror.writes
    for i in range(5):
        conn.execute("UPDATE episodes SET user_text = ? WHERE id = 'e1'", (f"v{i}",))
    conn.commit()
    sb.sync_state_mirror(conn)
    assert mirror.writes - writes == 1 and mirror.episodes["e1"]["user_text"] == "v4"


def test_a_deleted_episode_is_dequeued_not_counted_as_pushed(world):
    conn, mirror = world
    sb.sync_state_mirror(conn)
    _episode(conn, "e1", "one")
    conn.execute("DELETE FROM episodes WHERE id = 'e1'")
    conn.commit()
    writes = mirror.writes
    stats = sb.sync_state_mirror(conn)
    assert stats["episodes_pushed"] == 0 and stats["failed"] == 0 and mirror.writes == writes
    assert _outbox(conn) == []


def test_a_deleted_fact_leaves_the_mirror(world):
    conn, mirror = world
    _belief(conn, "b1", "tea")
    sb.sync_state_mirror(conn)
    assert "b1" in mirror.beliefs
    conn.execute("DELETE FROM beliefs WHERE id = 'b1'")
    conn.commit()
    sb.sync_state_mirror(conn)
    assert "b1" not in mirror.beliefs


def test_a_failed_push_is_retried_then_left_to_the_full_comparison(world):
    conn, mirror = world
    sb.sync_state_mirror(conn)  # the stamp
    mirror.refuse.add("e1")
    _episode(conn, "e1", "one")
    for attempt in range(1, sb._OUTBOX_MAX_ATTEMPTS + 1):
        stats = sb.sync_state_mirror(conn)
        assert stats["failed"] == 1
        assert bool(_outbox(conn)) == (attempt < sb._OUTBOX_MAX_ATTEMPTS)
    assert stats["outbox_dropped"] == 1
    mirror.refuse.clear()
    full = sb.sync_state_mirror(conn, full=True)
    assert full["episodes_pushed"] == 1 and "e1" in mirror.episodes


def test_a_mirror_that_is_down_keeps_the_queue(world, monkeypatch):
    conn, mirror = world
    sb.sync_state_mirror(conn)
    _episode(conn, "e1", "one")
    monkeypatch.setattr(mirror, "available", False)
    assert sb.sync_state_mirror(conn)["skipped"] == "mirror unavailable"
    assert _outbox(conn) == [("episode", "e1")]
    monkeypatch.setattr(mirror, "available", True)
    assert sb.sync_state_mirror(conn)["episodes_pushed"] == 1 and "e1" in mirror.episodes


def test_an_install_with_no_mirror_keeps_no_queue(world, monkeypatch):
    conn, _mirror = world
    _episode(conn, "e1", "one")
    monkeypatch.setattr(sb, "get_state_backend", lambda: sb._NullStateBackend())
    monkeypatch.setattr(sb, "mirrors_to_postgres", lambda cfg=None: False)
    assert sb.sync_state_mirror(conn) == {"skipped": "no mirror"}
    assert _outbox(conn) == []


def test_a_row_another_region_owns_is_not_pushed_from_the_queue(world, monkeypatch):
    conn, mirror = world
    monkeypatch.setattr(sb, "state_conflict_policy", lambda cfg=None: "fail_closed")
    monkeypatch.setattr(sb, "state_region", lambda cfg=None: "us-1")
    pages = []
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", lambda *a, **k: pages.append(a) or True)
    sb.sync_state_mirror(conn)
    mirror.episodes["theirs"] = {"user_text": "their text", "assistant_text": "answer", "summary_text": None,
                                 "tier": "episodic", "metadata_json": json.dumps({"region": "eu-1"})}
    _episode(conn, "theirs", "changed here")
    _episode(conn, "mine", "new here")
    stats = sb.sync_state_mirror(conn)
    assert stats["mode"] == "incremental" and stats["outbox_conflicts"] == 1 and stats["failed"] == 0
    assert mirror.episodes["theirs"]["user_text"] == "their text" and "mine" in mirror.episodes
    assert [a[0] for a in pages] == ["memory.region_conflict"] and _outbox(conn) == []


def test_the_full_comparison_comes_round_on_its_interval(world, monkeypatch):
    from kazma_core.observability import cadence

    conn, mirror = world
    assert sb.sync_state_mirror(conn)["mode"] == "full"
    assert sb.sync_state_mirror(conn)["mode"] == "incremental"
    cadence.stamp_run(sb._FULL_VERIFY_KEY, time.time() - 25 * 3600)  # default: 24 hours
    assert sb.sync_state_mirror(conn)["mode"] == "full"


def test_the_findings_survive_a_restart(world):
    conn, mirror = world
    mirror.episodes["only-there"] = {"user_text": "x", "assistant_text": "", "summary_text": None,
                                     "tier": "episodic", "metadata_json": "{}"}
    full = sb.sync_state_mirror(conn)
    assert full["mirror_only_episodes"] == 1
    sb._last_sync.clear()  # a restart
    stats = sb.sync_state_mirror(conn)
    assert stats["mode"] == "incremental" and stats["mirror_only_episodes"] == 1
    assert stats["verified_at"] == full["verified_at"]


# ── batches ───────────────────────────────────────────────────────────────


class _BatchMirror(_Mirror):
    """A mirror that takes rows in batches and counts the calls."""

    def __init__(self) -> None:
        super().__init__()
        self.batches = 0

    def mirror_episode_rows(self, rows: list[dict]) -> list[str]:
        self.batches += 1
        return [str(r["id"]) for r in rows if self.mirror_episode(r)]


def test_a_sweeps_moves_reach_the_mirror_in_batches(world, monkeypatch):
    """Macro sleep moved 16,000 memories at 10x the live size and pushed each
    on its own connection: 336 s. ``remirror_episodes`` sends 500 a batch."""
    conn, _old = world
    mirror = _BatchMirror()
    monkeypatch.setattr(sb, "get_state_backend", lambda: mirror)
    for i in range(1200):
        _episode(conn, f"e{i}", f"memory {i}")
    assert sb.remirror_episodes(conn, [f"e{i}" for i in range(1200)]) == 1200
    assert mirror.batches == 3 and len(mirror.episodes) == 1200
    # What reached the mirror left the outbox: the next pass pushes nothing.
    assert _outbox(conn) == [] and sb.sync_state_mirror(conn, full=False)["episodes_pushed"] == 0


def test_without_batches_each_row_is_its_own_push(world):
    """Negative control: a backend that takes one row at a time (the old
    shape) is called once a row."""
    conn, mirror = world
    for i in range(30):
        _episode(conn, f"e{i}", f"memory {i}")
    writes = mirror.writes
    assert sb.remirror_episodes(conn, [f"e{i}" for i in range(30)]) == 30
    assert mirror.writes - writes == 30


def test_a_change_queued_after_the_read_stays_queued(world, monkeypatch):
    conn, _old = world
    mirror = _BatchMirror()
    monkeypatch.setattr(sb, "get_state_backend", lambda: mirror)
    _episode(conn, "e1", "first")
    real = sb._episode_rows

    def read_then_change(c, ids):
        rows = real(c, ids)
        c.execute("UPDATE episodes SET user_text = 'second' WHERE id = 'e1'")
        c.commit()
        return rows

    monkeypatch.setattr(sb, "_episode_rows", read_then_change)
    sb.remirror_episodes(conn, ["e1"])
    assert mirror.episodes["e1"]["user_text"] == "first"
    assert _outbox(conn) == [("episode", "e1")]  # the second write is still to push
    monkeypatch.setattr(sb, "_episode_rows", real)
    sb.sync_state_mirror(conn, full=False)
    assert mirror.episodes["e1"]["user_text"] == "second"


# ── the pool (real Postgres) ──────────────────────────────────────────────


@pytest.mark.postgres
def test_the_backend_reuses_its_connections(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    pytest.importorskip("psycopg_pool")
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn:
        pytest.skip("no Postgres configured")

    opened = []
    real = psycopg.Connection.connect.__func__

    def counting(cls, *a, **k):
        opened.append(1)
        return real(cls, *a, **k)

    real_module = psycopg.connect

    def counting_module(*a, **k):
        opened.append(1)
        return real_module(*a, **k)

    def run(pooled: bool) -> int:
        backend = sb.PostgresStateBackend(dsn)
        if not pooled:
            monkeypatch.setattr(backend, "_get_pool", lambda: None)
        tenant = f"m31_{uuid.uuid4().hex[:8]}"
        opened.clear()
        # The pool calls Connection.connect; the unpooled path psycopg.connect.
        monkeypatch.setattr(psycopg.Connection, "connect", classmethod(counting))
        monkeypatch.setattr(psycopg, "connect", counting_module)
        try:
            for i in range(40):
                assert backend.mirror_episode({"id": f"{tenant}-{i}", "tenant_id": tenant, "session_id": "s",
                                               "turn_number": i, "user_text": "u", "assistant_text": "a",
                                               "tier": "episodic", "created_at": time.time()})
            assert len(backend.episode_digest()) >= 40
            return len(opened)
        finally:
            monkeypatch.setattr(psycopg.Connection, "connect", classmethod(real))
            monkeypatch.setattr(psycopg, "connect", real_module)
            conn = backend._connect()
            try:
                conn.execute("DELETE FROM kazma_episodes WHERE tenant_id = %s", (tenant,))
                conn.commit()
            finally:
                conn.close()
            backend.close()

    assert run(pooled=True) <= 4
    # Negative control: a connection per operation, as before.
    assert run(pooled=False) >= 41


def test_a_closed_backend_opens_no_pool():
    backend = sb.PostgresStateBackend("postgresql://nobody@127.0.0.1:1/none")
    backend.close()
    assert backend._get_pool() is None


# ── A mirror that went back (a restore of the shared database) ───────────


class _ProbedMirror(_Mirror):
    """A mirror that can say which rows it holds (``present_ids``)."""

    def present_ids(self, table: str, ids: list[str]) -> set[str]:
        rows = self.episodes if table == "kazma_episodes" else self.beliefs
        return {i for i in ids if i in rows}


@pytest.fixture()
def probed(world, monkeypatch):
    conn, _ = world
    mirror = _ProbedMirror()
    monkeypatch.setattr(sb, "get_state_backend", lambda: mirror)
    sb._forced_full_at.clear()
    yield conn, mirror
    sb._forced_full_at.clear()


def test_a_mirror_that_went_back_is_compared_in_full_at_once(probed, monkeypatch):
    """The restore drill's finding: rows a restore took out of the mirror had
    been pushed already, so no pass pushed them until the daily comparison."""
    conn, mirror = probed
    for i in range(4):
        _episode(conn, f"e{i}", f"turn {i}")
    sb.sync_state_mirror(conn, full=True)
    assert set(mirror.episodes) == {"e0", "e1", "e2", "e3"}
    monkeypatch.setattr(sb, "_full_verify_due", lambda now: False)  # verified a moment ago
    for eid in ("e2", "e3"):  # the shared database restored to before them
        mirror.episodes.pop(eid)
    stats = sb.sync_state_mirror(conn)
    assert stats["mode"] == "full" and set(mirror.episodes) == {"e0", "e1", "e2", "e3"}
    # Asked again at the next pass: the mirror holds them, nothing is forced.
    assert sb.sync_state_mirror(conn)["mode"] == "incremental"


def test_without_the_question_a_rolled_back_mirror_waits_a_day(world, monkeypatch):
    """Negative control: a mirror that cannot be asked (the old pass) stays
    behind until the daily comparison."""
    conn, mirror = world
    for i in range(4):
        _episode(conn, f"e{i}", f"turn {i}")
    sb.sync_state_mirror(conn, full=True)
    monkeypatch.setattr(sb, "_full_verify_due", lambda now: False)
    mirror.episodes.pop("e3")
    assert sb.sync_state_mirror(conn)["mode"] == "incremental" and "e3" not in mirror.episodes


def test_a_row_waiting_in_the_outbox_is_not_a_rollback(probed, monkeypatch):
    conn, mirror = probed
    _episode(conn, "e0", "turn 0")
    sb.sync_state_mirror(conn, full=True)
    monkeypatch.setattr(sb, "_full_verify_due", lambda now: False)
    mirror.refuse.add("e1")
    _episode(conn, "e1", "turn 1")  # queued, its push refused: still in the outbox
    assert sb.sync_state_mirror(conn)["mode"] == "incremental"


def test_a_forced_comparison_is_asked_at_most_once_an_hour(probed, monkeypatch):
    conn, mirror = probed
    for i in range(3):
        _episode(conn, f"e{i}", f"turn {i}")
    mirror.refuse.update({"e0", "e1", "e2"})  # rows the mirror keeps refusing
    monkeypatch.setattr(sb, "_full_verify_due", lambda now: False)
    _drop_outbox(conn)
    assert sb.sync_state_mirror(conn)["mode"] == "full"
    assert sb.sync_state_mirror(conn)["mode"] == "incremental"  # within the hour


def test_a_comparison_still_pushing_runs_again_next_pass(world, monkeypatch):
    """A restore can take thousands of rows: the comparison is not stamped
    while it is still pushing with more behind, so the next pass goes on."""
    conn, mirror = world
    for i in range(5):
        _episode(conn, f"e{i}", f"turn {i}")
    _drop_outbox(conn)
    first = sb.sync_state_mirror(conn, limit=2)
    assert first["mode"] == "full" and first["catching_up"] and first["episodes_behind"] == 3
    second = sb.sync_state_mirror(conn, limit=2)
    assert second["mode"] == "full" and second["episodes_pushed"] == 2
    third = sb.sync_state_mirror(conn, limit=2)
    assert third["mode"] == "full" and not third["catching_up"] and len(mirror.episodes) == 5
    assert sb.sync_state_mirror(conn, limit=2)["mode"] == "incremental"  # caught up: a day


def _drop_outbox(conn) -> None:
    conn.execute("DELETE FROM memory_mirror_outbox")
    conn.commit()
