"""The restore drill for the Postgres-primary memory profile (Memory audit
Stage 4), with what comes back (RPO) and how long it takes (RTO).

With Postgres as the primary store, recall reads the mirror tables
(``kazma_episodes``, ``kazma_beliefs``), which the scheduled Postgres backup
dumps with Kazma's other tables (``db/pg_backup.KAZMA_PG_TABLES``). The
drill: memories written and pushed, the tables dumped the way that backup
dumps them, more memories and a forget after the dump, the dump restored
(``pg_restore --clean``, the runbook's step) -- the shared database rolled
back -- and the maintenance passes run. It measures:

* RPO: every memory the local store holds is in the mirror again, and
  recall on the primary path finds one written after the dump;
* the forget made after the dump holds, in recall and in the mirror;
* RTO: the seconds from the restore to a mirror equal to the local store.

Found by the first run (2026-10-14): each pass pushed only what changed
since the last (the outbox, M31); the rows a restore had taken from the
mirror had been pushed already, so nothing pushed them again until the daily
full comparison -- up to 24 hours in which recall on the primary path missed
every memory written after the dump. A pass now asks the mirror for the
newest rows it has already pushed (``_mirror_went_back``), and runs the full
comparison when none is there. The negative control takes that question out.

Needs a disposable Postgres (``@pytest.mark.postgres``) and ``pg_dump`` /
``pg_restore`` on PATH or in ``KAZMA_DB_CONTAINER``. Size:
``KAZMA_TEST_MEMORY_RESTORE_DRILL_TURNS`` (default 40).
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid

import pytest
from kazma_core.memory import state_backend as sb

pytestmark = pytest.mark.postgres

TURNS = max(4, int(os.environ.get("KAZMA_TEST_MEMORY_RESTORE_DRILL_TURNS", "40")))


def _word(i: int) -> str:
    return "qy" + "".join("abcdefghij"[int(d)] for d in str(i).zfill(4))


def _question(i: int) -> str:
    return f"pg drill question {i} about the {_word(i)} plan"


@pytest.fixture()
def drill(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn:
        pytest.skip("no Postgres configured")
    try:
        from kazma_core.migration.pg_bridge import PgToolNotFound, resolve_pg_dump, resolve_pg_restore

        resolve_pg_dump()
        resolve_pg_restore()
    except PgToolNotFound as exc:
        pytest.skip(f"pg_dump/pg_restore not reachable: {exc}")
    backend = sb.PostgresStateBackend(dsn)
    if not backend.available:
        pytest.skip("Postgres not reachable")
    tenant = f"drill_{uuid.uuid4().hex[:10]}"
    for var in ("KAZMA_MEMORY_ENFORCE_TENANT", "KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    monkeypatch.setattr(sb, "get_state_backend", lambda: backend)
    monkeypatch.setattr(sb, "is_state_primary", lambda cfg=None: True)
    monkeypatch.setattr(sb, "_this_install_id", lambda: "d" * 32)
    # The daily full comparison ran a moment ago: the drill is the day after.
    monkeypatch.setattr(sb, "_full_verify_due", lambda now: False)
    sb._last_sync.clear()
    sb._forced_full_at.clear()
    dual_write._reset_mirror()
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    yield conn, backend, tenant, dsn, tmp_path
    dual_write._reset_mirror()
    conn.close()
    pg = backend._connect()
    try:
        cur = pg.cursor()
        cur.execute("DELETE FROM kazma_episodes WHERE tenant_id = %s", (tenant,))
        cur.execute("DELETE FROM kazma_beliefs WHERE tenant_id = %s", (tenant,))
        pg.commit()
    finally:
        pg.close()
        backend.close()


def _write(tenant: str, i: int) -> str:
    from kazma_core.memory.dual_write import mirror_episode

    eid = mirror_episode(session_id="pg-drill", turn_number=i + 1, user_text=_question(i),
                         assistant_text=f"Answer {i}.", tenant_id=tenant,
                         created_at=time.time() - 3600 + i)
    assert eid
    return eid


def _mirror_ids(backend, tenant: str) -> dict[str, str]:
    pg = backend._connect()
    try:
        cur = pg.cursor()
        cur.execute("SELECT id, tier FROM kazma_episodes WHERE tenant_id = %s", (tenant,))
        return {str(r[0]): str(r[1]) for r in cur.fetchall()}
    finally:
        pg.close()


def _scenario(drill, monkeypatch, *, probe: bool = True) -> dict:
    from kazma_core.memory import forget
    from kazma_core.memory.recall import recall
    from kazma_core.migration.pg_bridge import dump_database, restore_database

    conn, backend, tenant, dsn, tmp = drill
    if not probe:  # negative control: no question about a mirror gone back
        monkeypatch.setattr(sb, "_mirror_went_back", lambda *_a, **_k: False)
    half = TURNS // 2
    before = [_write(tenant, i) for i in range(half)]
    sb.sync_state_mirror(conn)
    assert set(before) <= set(_mirror_ids(backend, tenant))
    dump = dump_database(dsn, tmp / "pg_shared.dump", tables=["kazma_episodes", "kazma_beliefs"])

    after = [_write(tenant, i) for i in range(half, TURNS)]
    assert forget.forget_episode(before[1], tenant_id="default", conn=conn)["ok"]
    sb.sync_state_mirror(conn)
    assert set(after) <= set(_mirror_ids(backend, tenant))

    # The shared database rolled back to the dump.
    backend.close()
    restore_database(dump, dsn)
    rolled_back = set(after) - set(_mirror_ids(backend, tenant))

    started = time.monotonic()
    stats = sb.sync_state_mirror(conn)
    rto = time.monotonic() - started
    mirror = _mirror_ids(backend, tenant)
    newest = recall(f"{_word(TURNS - 1)} plan", tenant_id=tenant)
    gone = recall(f"{_word(1)} plan", tenant_id=tenant)
    return {
        "rolled_back": len(rolled_back),
        "missing": sorted(set(before + after) - set(mirror)),
        "forgotten_in_mirror": mirror.get(before[1]),
        "recall_finds_newest": any(_word(TURNS - 1) in e.content for e in newest.episodes),
        "recall_finds_forgotten": any(_word(1) in e.content for e in gone.episodes),
        "mode": stats.get("mode"),
        "rto_s": round(rto, 3),
    }


def test_a_restored_dump_is_caught_up_in_one_pass(drill, monkeypatch, capsys):
    out = _scenario(drill, monkeypatch)
    with capsys.disabled():
        print(f"\n[memory restore drill, postgres primary] turns={TURNS} rolled_back={out['rolled_back']} "
              f"missing={len(out['missing'])} mode={out['mode']} rto_s={out['rto_s']}")
    assert out["rolled_back"] == TURNS - TURNS // 2  # the restore really took them
    assert out["missing"] == [], out["missing"][:5]  # RPO: the local store fills the mirror again
    assert out["mode"] == "full"
    assert out["forgotten_in_mirror"] == "forgotten"
    assert out["recall_finds_newest"]
    assert not out["recall_finds_forgotten"]


def test_without_the_question_the_mirror_stays_behind(drill, monkeypatch):
    """Negative control: an incremental pass alone leaves the rows the restore
    took out of the mirror (until the daily full comparison)."""
    out = _scenario(drill, monkeypatch, probe=False)
    assert out["mode"] == "incremental"
    assert out["missing"] and not out["recall_finds_newest"]
