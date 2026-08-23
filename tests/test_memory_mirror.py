"""M-04 — mirror tombstone propagation tests.

The PG mirror previously only ever received LIVE rows (the dual-write
hardcoded valid_until=None/invalidated_at=None) and no death path ever
touched it: superseded/invalidated/archived facts stayed live in
kazma_beliefs forever (35 confirmed on prod). These tests lock the
propagation contract using a recording fake backend.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from kazma_core.memory import state_backend as sb


class _RecordingBackend:
    """Stands in for PostgresStateBackend; records what reaches the mirror."""

    name = "postgres"
    write_ready = True
    available = True

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.deleted: list[str] = []

    def mirror_belief(self, row: dict) -> bool:
        self.rows[str(row["id"])] = {
            "valid_until": row.get("valid_until"),
            "invalidated_at": row.get("invalidated_at"),
            "object": row.get("object"),
            "subject": row.get("subject"),
        }
        return True

    def mirror_belief_snapshot(self) -> dict[str, bool]:
        return {
            bid: (r.get("invalidated_at") is None and r.get("valid_until") is None)
            for bid, r in self.rows.items()
        }

    def delete_belief(self, row_id: str) -> bool:
        self.deleted.append(str(row_id))
        self.rows.pop(str(row_id), None)
        return True


@pytest.fixture()
def mem_db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    ensure_primary_schema(conn)

    backend = _RecordingBackend()
    monkeypatch.setattr(sb, "get_state_backend", lambda: backend)
    yield conn, backend
    conn.close()


def _add_belief(conn, subject, predicate, obj, bid):
    now = time.time()
    conn.execute(
        """INSERT INTO beliefs (id, subject, predicate, object, predicate_type,
                                valid_from, ingested_at, confidence)
           VALUES (?, ?, ?, ?, 'functional', ?, ?, 0.9)""",
        (bid, subject, predicate, obj, now, now),
    )
    conn.commit()


def _invalidate(conn, bid):
    ts = time.time()
    conn.execute(
        "UPDATE beliefs SET valid_until=?, invalidated_at=? WHERE id=?",
        (ts, ts, bid),
    )
    conn.commit()


class TestTombstonePropagation:
    def test_invalidate_pushes_death_flags_to_mirror(self, mem_db, monkeypatch):
        conn, backend = mem_db
        _add_belief(conn, "sakhrfit", "availability_status", "fully_clean", "b_t1")

        from kazma_core.memory.hygiene import invalidate_belief

        invalidate_belief("b_t1", conn=conn)

        assert "b_t1" in backend.rows, "dead belief must still exist in mirror"
        row = backend.rows["b_t1"]
        assert row["valid_until"] is not None
        assert row["invalidated_at"] is not None

    def test_supersede_marks_old_row_dead_in_mirror(self, mem_db, monkeypatch):
        conn, backend = mem_db
        _add_belief(conn, "qudrafit_ai", "is_available", "offline", "b_old")

        from kazma_core.memory.belief_mutation import mutate_belief

        mutate_belief(
            conn,
            "qudrafit_ai",
            "is_available",
            "available",
            extraction_method="user_explicit",
            predicate_type="functional",
        )

        old = backend.rows.get("b_old")
        assert old is not None, "superseded row must be tombstoned, not vanish"
        assert old["invalidated_at"] is not None

    def test_restore_repushes_live_state(self, mem_db, monkeypatch):
        """Undo paths flip invalidated_at back to NULL — mirror must follow."""
        conn, backend = mem_db
        _add_belief(conn, "thravor", "brand_name_status", "backup", "b_r1")
        _invalidate(conn, "b_r1")
        sb.remirror_belief_by_id(conn, "b_r1")
        assert backend.rows["b_r1"]["invalidated_at"] is not None

        # Operator undo: flip back to live locally, re-mirror.
        conn.execute(
            "UPDATE beliefs SET valid_until=NULL, invalidated_at=NULL WHERE id='b_r1'"
        )
        conn.commit()
        sb.remirror_belief_by_id(conn, "b_r1")
        assert backend.rows["b_r1"]["valid_until"] is None
        assert backend.rows["b_r1"]["invalidated_at"] is None


class TestArchiveUnmirror:
    def test_macro_sleep_archive_deletes_from_mirror(self, mem_db, monkeypatch):
        conn, backend = mem_db
        now = time.time()
        _add_belief(conn, "old_brand", "status", "dead", "b_arch")
        # Age past archive window.
        conn.execute(
            "UPDATE beliefs SET valid_until=?, invalidated_at=? WHERE id='b_arch'",
            (now - 40 * 86400, now - 39 * 86400),
        )
        conn.commit()

        from kazma_core.memory.macro_sleep import run_macro_sleep

        run_macro_sleep(
            conn,
            cfg={"v2": {"archive_after_days": 30}},
            tenant_id="default",
        )

        # Row left the SQLite SoT...
        local = conn.execute("SELECT COUNT(*) FROM beliefs WHERE id='b_arch'").fetchone()[0]
        assert local == 0
        # ...and left the mirror with it.
        assert "b_arch" in backend.deleted


class TestReconcile:
    def test_reconcile_heals_all_three_drift_classes(self, mem_db, monkeypatch):
        conn, backend = mem_db
        # live in both, in sync
        _add_belief(conn, "ok_subject", "status", "fine", "r_ok")
        backend.rows["r_ok"] = {"valid_until": None, "invalidated_at": None}
        # dead locally but live in mirror (the prod finding)
        _add_belief(conn, "dead_subject", "status", "whatever", "r_dead")
        _invalidate(conn, "r_dead")
        backend.rows["r_dead"] = {"valid_until": None, "invalidated_at": None}
        # exists ONLY in mirror (ghost)
        backend.rows["r_ghost"] = {"valid_until": None, "invalidated_at": None}

        stats = sb.reconcile_state_beliefs(conn, dry_run=True)
        assert stats["deleted_mirror_only"] == 1
        assert stats["tombstoned"] == 1
        assert stats["inserted"] == 0

        stats = sb.reconcile_state_beliefs(conn)
        assert stats["deleted_mirror_only"] == 1
        assert stats["tombstoned"] == 1
        assert backend.rows["r_dead"]["invalidated_at"] is not None
        assert "r_ghost" not in backend.rows

        # Fully healed: second pass finds nothing to do.
        again = sb.reconcile_state_beliefs(conn)
        assert again["deleted_mirror_only"] == 0
        assert again["tombstoned"] == 0
        assert again["inserted"] == 0

    def test_drift_summary_counts_poison(self, mem_db, monkeypatch):
        conn, backend = mem_db
        _add_belief(conn, "live_ok", "x", "y", "d_live")
        backend.rows["d_live"] = {"valid_until": None, "invalidated_at": None}
        _add_belief(conn, "poison", "x", "y", "d_poison")
        _invalidate(conn, "d_poison")
        backend.rows["d_poison"] = {"valid_until": None, "invalidated_at": None}

        summary = sb.mirror_drift_summary(conn)
        assert summary["dead_mismatch"] == 1
        assert summary["only_in_mirror"] == 0
