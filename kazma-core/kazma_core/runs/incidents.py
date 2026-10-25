"""Incidents: a stuck or failed run, what the owner was told, what they decided.

One row per run and condition while it lasts (``run_incidents.db``, a bundle
store). Every change is one compare-and-set in a ``BEGIN IMMEDIATE``
transaction on a short-lived connection, so two surfaces deciding at once
cannot both act: the first decision wins and the second reads it.

States::

    open ──claim──▶ acting ──finish──▶ closed      (an action ran)
      │                     └─refused─▶ resolved   (the run had moved on)
      ├──dismiss──▶ dismissed                       (the owner said "leave it")
      ├──resolve──▶ resolved                        (the run recovered by itself)
      └──expire───▶ expired                         (open for a week)

A decided incident (closed, dismissed, expired) is never opened again for the
same run, condition and run version: the owner decided about THAT failure.
One the run recovered from may open again if it stalls again.

Delivery is an outbox on the row (``notify``): ``pending`` is committed with
the incident, ``sending`` just before the message leaves, then
``delivered``/``refused``. A process that dies with a row ``pending`` sends
it at the next start (once); one ``sending`` may or may not have gone out and
is marked ``uncertain``, never sent again. ``suppressed`` means another
mechanism already tells the owner (``KindSpec.announced_by``).

An ``acting`` row at start belongs to a process that died mid-action: the
action may have run. It is closed as ``interrupted`` and never replayed.

Blocking (SQLite): async callers use ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from kazma_core.runs.contract import INCIDENT_ACTIONS, Run

__all__ = [
    "DecisionConflictError",
    "IncidentStore",
    "get_incident_store",
    "public",
    "reset_incident_store",
]

logger = logging.getLogger(__name__)

#: An open incident nobody decided about expires after this long.
EXPIRE_AFTER_S = 7 * 24 * 3600.0
#: Finished incidents are kept this long (the Dashboard's history), then purged.
RETAIN_S = 30 * 24 * 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_incidents (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    condition TEXT NOT NULL,
    state TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    opened_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    run_state TEXT NOT NULL DEFAULT '',
    run_version TEXT NOT NULL DEFAULT '',
    effects TEXT NOT NULL DEFAULT 'none',
    actions TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL DEFAULT '{}',
    link TEXT NOT NULL DEFAULT '',
    notify TEXT NOT NULL DEFAULT 'pending',
    notify_detail TEXT NOT NULL DEFAULT '',
    decided_by TEXT NOT NULL DEFAULT '',
    decided_platform TEXT NOT NULL DEFAULT '',
    decided_action TEXT NOT NULL DEFAULT '',
    decided_at REAL,
    outcome_code TEXT NOT NULL DEFAULT '',
    outcome_message TEXT NOT NULL DEFAULT '',
    auto_attempts INTEGER NOT NULL DEFAULT 0,
    lineage TEXT NOT NULL DEFAULT '',
    diagnosis TEXT NOT NULL DEFAULT '',
    diagnosis_state TEXT NOT NULL DEFAULT '',
    diagnosed_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_incidents_live
    ON run_incidents(run_id, condition) WHERE state IN ('open', 'acting');
CREATE INDEX IF NOT EXISTS idx_run_incidents_state ON run_incidents(state, updated_at);
"""

_LIVE = ("open", "acting")
_DECIDED = ("closed", "dismissed", "expired")


class DecisionConflictError(RuntimeError):
    """The incident was already decided (or is being): ``row`` says how."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        super().__init__("incident already decided" if row else "no such incident")
        self.row = row


def _row(cur_row: sqlite3.Row | None) -> dict[str, Any] | None:
    if cur_row is None:
        return None
    row = dict(cur_row)
    row["actions"] = json.loads(row.get("actions") or "[]")
    row["origin"] = json.loads(row.get("origin") or "{}")
    return row


def public(row: dict[str, Any]) -> dict[str, Any]:
    """An incident as the pages see it: never the delivery address."""
    out = {k: v for k, v in row.items() if k not in ("origin", "decided_by")}
    out["origin"] = {"platform": (row.get("origin") or {}).get("platform", "")}
    out["decided"] = bool(row.get("decided_action"))
    return out


class IncidentStore:
    """The incidents database. Each call opens, uses and closes a connection."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    # ── plumbing ─────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        from kazma_core.config_store import apply_sqlite_pragmas

        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        apply_sqlite_pragmas(conn)
        if not self._schema_ready:
            with self._schema_lock:
                if not self._schema_ready:
                    conn.executescript(_SCHEMA)
                    self._schema_ready = True
        return conn

    def _write(self, fn: Any) -> Any:
        """Run ``fn(conn)`` inside one ``BEGIN IMMEDIATE`` transaction."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = fn(conn)
            conn.execute("COMMIT")
            return result
        finally:
            # A failure (or a cancel) inside the block rolls it back: the
            # write lock is never left held, half a change never lands.
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()

    def _read(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [_row(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    # ── reads ────────────────────────────────────────────────────────────
    def get(self, incident_id: str) -> dict[str, Any] | None:
        rows = self._read("SELECT * FROM run_incidents WHERE id = ?", (incident_id,))
        return rows[0] if rows else None

    def open_for_run(self, run_id: str) -> dict[str, Any] | None:
        """The run's live incident (open or being acted on), newest first."""
        rows = self._read("SELECT * FROM run_incidents WHERE run_id = ? AND state IN ('open', 'acting') "
                          "ORDER BY opened_at DESC LIMIT 1", (run_id,))
        return rows[0] if rows else None

    def live(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM run_incidents WHERE state IN ('open', 'acting') ORDER BY opened_at DESC")

    def recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Live incidents first, then the newest finished ones."""
        return self._read(
            "SELECT * FROM run_incidents ORDER BY (state IN ('open', 'acting')) DESC, updated_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),))

    def decided_keys(self) -> set[tuple[str, str, str]]:
        """``(run_id, condition, run_version)`` of every decided incident: a
        pass skips these without a write transaction."""
        conn = self._connect()
        try:
            return {(r[0], r[1], r[2]) for r in conn.execute(
                f"SELECT run_id, condition, run_version FROM run_incidents WHERE state IN {_DECIDED}")}
        finally:
            conn.close()

    def pending_notifications(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM run_incidents WHERE notify = 'pending' ORDER BY opened_at")

    # ── opening and settling ─────────────────────────────────────────────
    def open_incident(self, run: Run, condition: str, *, origin: dict[str, str],
                      notify: str = "pending", notify_detail: str = "",
                      now: float | None = None) -> tuple[dict[str, Any], bool] | None:
        """Open an incident for *run* and *condition*, or return the live one.

        ``(row, True)`` when this call opened it, ``(row, False)`` when one was
        already open, None when the owner already decided about this run,
        condition and run version (it is never opened again).
        """
        stamp = time.time() if now is None else now
        actions = [*run.actions, *INCIDENT_ACTIONS]

        def change(conn: sqlite3.Connection) -> tuple[dict[str, Any], bool] | None:
            live = conn.execute("SELECT * FROM run_incidents WHERE run_id = ? AND condition = ? "
                                "AND state IN ('open', 'acting')", (run.run_id, condition)).fetchone()
            if live is not None:
                return _row(live), False
            decided = conn.execute(
                "SELECT 1 FROM run_incidents WHERE run_id = ? AND condition = ? AND run_version = ? "
                f"AND state IN {_DECIDED}", (run.run_id, condition, run.version)).fetchone()
            if decided is not None:
                return None
            ident = "inc_" + secrets.token_hex(6)
            conn.execute(
                "INSERT INTO run_incidents (id, run_id, kind, condition, state, opened_at, updated_at, title, "
                "detail, run_state, run_version, effects, actions, origin, link, notify, notify_detail, lineage) "
                "VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ident, run.run_id, run.kind, condition, stamp, stamp, run.title[:200], run.detail[:500],
                 str(run.state), run.version, run.effects, json.dumps(actions), json.dumps(origin),
                 run.link, notify, notify_detail[:300], run.lineage or run.run_id))
            return _row(conn.execute("SELECT * FROM run_incidents WHERE id = ?", (ident,)).fetchone()), True

        return self._write(change)

    def refresh(self, incident_id: str, run: Run, *, now: float | None = None) -> None:
        """Keep a live incident's view of its run current (title, actions, state)."""
        stamp = time.time() if now is None else now
        actions = [*run.actions, *INCIDENT_ACTIONS]
        self._write(lambda conn: conn.execute(
            "UPDATE run_incidents SET title = ?, detail = ?, run_state = ?, effects = ?, actions = ?, link = ?, "
            "updated_at = ? WHERE id = ? AND state = 'open'",
            (run.title[:200], run.detail[:500], str(run.state), run.effects, json.dumps(actions),
             run.link, stamp, incident_id)))

    def resolve(self, incident_id: str, *, reason: str, now: float | None = None) -> bool:
        """The run recovered (or is gone): an open incident is resolved by itself."""
        stamp = time.time() if now is None else now
        return bool(self._write(lambda conn: conn.execute(
            "UPDATE run_incidents SET state = 'resolved', outcome_code = 'resolved', outcome_message = ?, "
            "version = version + 1, updated_at = ? WHERE id = ? AND state = 'open'",
            (reason[:300], stamp, incident_id)).rowcount))

    def expire_old(self, *, now: float | None = None) -> int:
        stamp = time.time() if now is None else now
        return int(self._write(lambda conn: conn.execute(
            "UPDATE run_incidents SET state = 'expired', outcome_code = 'expired', version = version + 1, "
            "updated_at = ? WHERE state = 'open' AND opened_at < ?", (stamp, stamp - EXPIRE_AFTER_S)).rowcount))

    def purge(self, *, now: float | None = None) -> int:
        """Delete finished incidents older than :data:`RETAIN_S`."""
        stamp = time.time() if now is None else now
        return int(self._write(lambda conn: conn.execute(
            "DELETE FROM run_incidents WHERE state NOT IN ('open', 'acting') AND updated_at < ?",
            (stamp - RETAIN_S,)).rowcount))

    # ── delivery outbox ──────────────────────────────────────────────────
    def claim_notification(self, incident_id: str) -> bool:
        """``pending`` -> ``sending``, just before the message leaves."""
        return bool(self._write(lambda conn: conn.execute(
            "UPDATE run_incidents SET notify = 'sending' WHERE id = ? AND notify = 'pending'",
            (incident_id,)).rowcount))

    def finish_notification(self, incident_id: str, *, outcome: str, detail: str = "") -> None:
        """``sending`` -> ``delivered`` (a platform took it), ``refused`` or
        ``uncertain`` (delivery was interrupted: never sent again)."""
        if outcome not in ("delivered", "refused", "uncertain"):
            raise ValueError(f"not a delivery outcome: {outcome!r}")
        self._write(lambda conn: conn.execute(
            "UPDATE run_incidents SET notify = ?, notify_detail = ? WHERE id = ? AND notify = 'sending'",
            (outcome, detail[:300], incident_id)))

    def recover(self, *, now: float | None = None) -> dict[str, int]:
        """At start: messages caught mid-send are uncertain (never resent); an
        action caught mid-run is closed as interrupted (never replayed)."""
        stamp = time.time() if now is None else now

        def change(conn: sqlite3.Connection) -> dict[str, int]:
            sending = conn.execute(
                "UPDATE run_incidents SET notify = 'uncertain', notify_detail = 'the process stopped while "
                "sending; it may or may not have arrived' WHERE notify = 'sending'").rowcount
            acting = conn.execute(
                "UPDATE run_incidents SET state = 'closed', outcome_code = 'interrupted', outcome_message = "
                "'Kazma stopped while the action ran; check the run before acting again.', "
                "version = version + 1, updated_at = ? WHERE state = 'acting'", (stamp,)).rowcount
            diagnosing = conn.execute(
                "UPDATE run_incidents SET diagnosis_state = 'failed', diagnosis = 'Kazma stopped while "
                "diagnosing; ask again.' WHERE diagnosis_state = 'running'").rowcount
            return {"uncertain_messages": int(sending), "interrupted_actions": int(acting),
                    "interrupted_diagnoses": int(diagnosing)}

        result = self._write(change)
        if any(result.values()):
            logger.warning("[runs] incident recovery at start: %s", result)
        return result

    # ── decisions ────────────────────────────────────────────────────────
    def claim_decision(self, incident_id: str, action: str, *, actor: str, platform: str,
                       now: float | None = None) -> dict[str, Any]:
        """``open`` -> ``acting`` (or ``dismissed`` for Dismiss). The first
        decision wins: a second raises :class:`DecisionConflictError` with the row."""
        stamp = time.time() if now is None else now

        def change(conn: sqlite3.Connection) -> dict[str, Any]:
            row = _row(conn.execute("SELECT * FROM run_incidents WHERE id = ?", (incident_id,)).fetchone())
            if row is None or row["state"] != "open":
                raise DecisionConflictError(row)
            if action not in row["actions"]:
                raise ValueError(f"the incident does not offer {action}")
            state = "dismissed" if action == "dismiss" else "acting"
            conn.execute(
                "UPDATE run_incidents SET state = ?, decided_by = ?, decided_platform = ?, decided_action = ?, "
                "decided_at = ?, version = version + 1, updated_at = ? WHERE id = ? AND state = 'open'",
                (state, actor[:120], platform[:40], action, stamp, stamp, incident_id))
            if state == "dismissed":
                conn.execute("UPDATE run_incidents SET outcome_code = 'dismissed' WHERE id = ?", (incident_id,))
            return _row(conn.execute("SELECT * FROM run_incidents WHERE id = ?", (incident_id,)).fetchone())

        return self._write(change)

    def finish_decision(self, incident_id: str, *, ok: bool, code: str, message: str,
                        now: float | None = None) -> dict[str, Any] | None:
        """End an ``acting`` row: ``closed`` when the action ran, ``resolved``
        when the run had moved on (nothing to do), else ``open`` again with
        the refusal recorded, so the owner can choose another way."""
        stamp = time.time() if now is None else now
        if ok:
            state = "closed"
        elif code in ("gone", "not_offered", "not_found", "not_active"):
            state = "resolved"
        else:
            state = "open"

        def change(conn: sqlite3.Connection) -> dict[str, Any] | None:
            extra = ", decided_action = '', decided_by = '', decided_platform = '', decided_at = NULL" \
                if state == "open" else ""
            conn.execute(
                f"UPDATE run_incidents SET state = ?, outcome_code = ?, outcome_message = ?{extra}, "
                "version = version + 1, updated_at = ? WHERE id = ? AND state = 'acting'",
                (state, code[:60], message[:300], stamp, incident_id))
            return _row(conn.execute("SELECT * FROM run_incidents WHERE id = ?", (incident_id,)).fetchone())

        return self._write(change)

    def claim_diagnosis(self, incident_id: str, *, now: float | None = None) -> bool:
        """Start a diagnosis unless one is running (one at a time per incident)."""
        stamp = time.time() if now is None else now
        return bool(self._write(lambda conn: conn.execute(
            "UPDATE run_incidents SET diagnosis_state = 'running', diagnosed_at = ? "
            "WHERE id = ? AND diagnosis_state != 'running'", (stamp, incident_id)).rowcount))

    def finish_diagnosis(self, incident_id: str, *, text: str, ok: bool, now: float | None = None) -> None:
        stamp = time.time() if now is None else now
        self._write(lambda conn: conn.execute(
            "UPDATE run_incidents SET diagnosis = ?, diagnosis_state = ?, diagnosed_at = ? WHERE id = ?",
            (text[:4000], "done" if ok else "failed", stamp, incident_id)))

    def claim_auto_step(self, incident_id: str, lineage: str, *, limit: int, since: float) -> bool:
        """Count one automatic step on *incident_id*, unless *lineage* already
        had *limit* of them since *since* (one transaction: two passes cannot
        both take the last one)."""
        def change(conn: sqlite3.Connection) -> bool:
            used = conn.execute("SELECT COALESCE(SUM(auto_attempts), 0) FROM run_incidents "
                                "WHERE lineage = ? AND opened_at >= ?", (lineage, since)).fetchone()[0]
            if int(used) >= limit:
                return False
            conn.execute("UPDATE run_incidents SET auto_attempts = auto_attempts + 1 WHERE id = ?", (incident_id,))
            return True

        return bool(self._write(change))


_store: IncidentStore | None = None
_store_lock = threading.Lock()


def get_incident_store() -> IncidentStore:
    """The install's incident store (``<data dir>/run_incidents.db``)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from kazma_core.paths import data_dir

                _store = IncidentStore(data_dir() / "run_incidents.db")
    return _store


def reset_incident_store(store: IncidentStore | None = None) -> None:
    """Replace the process's store (tests: a private file)."""
    global _store
    _store = store
