"""Memory's own incidents: paged once when they start, once when they end
(the memory framework audit's F14, part H).

Nothing paged a week of failed extraction or of keyword-only recall: a deep
pass the model could not run was retried and then kept as a dead letter, and
``memory.dead_letter`` paged once an hour while they kept coming (its
cooldown) -- up to 168 pages a week, each about one task, none saying that
extraction as a whole had stopped. Recall that could only search by keyword
paged nobody at all.

Three conditions, each one row of ``memory_incidents`` (``memory_ops.db``)
while it lasts:

* **extraction**: at least :data:`EXTRACTION_FAILURES` model deep passes
  failed over at least :data:`EXTRACTION_SPAN_S` with none answered since
  (``memory_deep_passes``, written by every deep pass the model was asked
  for). It ends at the next deep pass the model answers. While it is open, a
  deep pass that gives up (a dead letter) is recorded and not paged on its
  own (``task_queue._report_dead_letters``): this incident is that outage's
  page.
* **recall_degraded**: of the last :data:`RECALL_WINDOW` recalls within
  :data:`LOOKBACK_S`, at least :data:`RECALL_MIN`, and
  :data:`RECALL_DEGRADED_SHARE` of them could not search by meaning or at
  all. It ends when the last :data:`RECALL_RECOVERED` recalls were whole.
* **recall_slow**: the 95th percentile of the last :data:`SLOW_WINDOW`
  recalls (all of them within :data:`LOOKBACK_S`) took over
  :data:`SLOW_P95_MS`. It ends when the same window is below
  :data:`SLOW_BACK_P95_MS` (one window for both: a longer one to open
  reopened it the moment it closed).

An incident opens by an ``INSERT OR IGNORE`` and closes by a ``DELETE``: only
the statement that changed the row pages, so two processes, or a restart in
the middle of a week, page once. The pages are ops alerts (the alert
catalog's ``memory.*`` entries); nothing here is another notifier.

Install-wide: the model and the embedder are the install's. What is paged
is counts and times, never a memory or a space's words.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import closing
from typing import Any

from kazma_core.observability import ops_alerts  # ops_alerts.alert never raises (AGENTS §33)

logger = logging.getLogger(__name__)

__all__ = [
    "EXTRACTION_FAILURES",
    "EXTRACTION_SPAN_S",
    "EXTRACTION_TASKS",
    "INCIDENTS",
    "deep_pass_outcome",
    "incident_open",
    "open_incidents",
    "recall_seen",
]

#: The queued task types that are model deep passes: their dead letters
#: belong to the extraction incident while it is open.
EXTRACTION_TASKS = frozenset({"micro_consolidation"})

EXTRACTION_FAILURES = 3
EXTRACTION_SPAN_S = 600.0

LOOKBACK_S = 6 * 3600.0
RECALL_WINDOW = 20
RECALL_MIN = 10
RECALL_DEGRADED_SHARE = 0.8
RECALL_RECOVERED = 5
SLOW_WINDOW = 20
SLOW_P95_MS = 8000
SLOW_BACK_P95_MS = 4000

#: How long a deep pass's outcome is kept.
DEEP_PASS_RETENTION_S = 30 * 86400.0

#: name -> (the key it pages when it opens, the key it pages when it ends).
INCIDENTS: dict[str, tuple[str, str]] = {
    "extraction": ("memory.extraction_failing", "memory.extraction_back"),
    "recall_degraded": ("memory.recall_degraded", "memory.recall_back"),
    "recall_slow": ("memory.recall_slow", "memory.recall_speed_back"),
}

_TITLES: dict[str, tuple[str, str]] = {
    "extraction": ("Memory cannot read facts from conversations: the model does not answer",
                   "Memory reads facts from conversations again"),
    "recall_degraded": ("Memory can only search by keyword", "Memory searches by meaning again"),
    "recall_slow": ("Memory search is slow", "Memory search is fast again"),
}

_SCHEMA_READY: set[str] = set()
_SCHEMA_LOCK = threading.Lock()


def ops_conn(*, create: bool = True) -> sqlite3.Connection | None:
    """A short-lived connection to ``memory_ops.db``, its schema made once per
    file and process. *create* False: None when the file is not there (a
    reader never makes the database)."""
    from pathlib import Path

    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import memory_ops_db

    path = str(memory_ops_db(create=create))
    if not create and not Path(path).is_file():
        return None
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    apply_sqlite_pragmas(conn)
    if path not in _SCHEMA_READY:
        with _SCHEMA_LOCK:
            if path not in _SCHEMA_READY:
                from kazma_core.memory.schema_v2 import ensure_ops_schema

                ensure_ops_schema(conn)
                _SCHEMA_READY.add(path)
    return conn


# ── deep passes ────────────────────────────────────────────────────────────


def deep_pass_outcome(ok: bool, *, tenant_id: str, now: float | None = None) -> None:
    """Record one model deep pass: *ok* when the model answered. Opens or
    closes the extraction incident. Blocking (SQLite); never raises."""
    now = time.time() if now is None else float(now)
    try:
        with closing(ops_conn()) as conn:  # type: ignore[arg-type]
            conn.execute("INSERT INTO memory_deep_passes (at, tenant_id, ok) VALUES (?, ?, ?)",
                         (now, str(tenant_id), int(bool(ok))))
            conn.execute("DELETE FROM memory_deep_passes WHERE at < ?", (now - DEEP_PASS_RETENTION_S,))
            conn.commit()
            _check_extraction(conn, now)
    except (sqlite3.Error, OSError):
        logger.warning("[memory_quality] a deep pass's outcome could not be recorded", exc_info=True)


def _check_extraction(conn: sqlite3.Connection, now: float) -> None:
    last_ok = conn.execute("SELECT MAX(at) FROM memory_deep_passes WHERE ok = 1").fetchone()[0]
    fails, first = conn.execute(
        "SELECT COUNT(*), MIN(at) FROM memory_deep_passes WHERE ok = 0 AND at > ?",
        (float(last_ok or 0.0),)).fetchone()
    held = _incident(conn, "extraction")
    if held is None:
        if int(fails or 0) >= EXTRACTION_FAILURES and now - float(first) >= EXTRACTION_SPAN_S:
            detail = (f"{int(fails)} deep passes failed since {_when(float(first))}, none answered since. "
                      "Each is retried and kept on the Memory page's queue; what the turns said is kept.")
            if _open(conn, "extraction", now, detail):
                ops_alerts.alert("memory.extraction_failing", _TITLES["extraction"][0], detail, severity="warn",
                                 cooldown_s=0.0)
    elif last_ok is not None and float(last_ok) > float(held["opened_at"]):
        lasted = _close(conn, "extraction", now)
        if lasted:
            ops_alerts.alert("memory.extraction_back", _TITLES["extraction"][1], f"It lasted {lasted}.",
                  severity="success", cooldown_s=0.0)


# ── recalls ────────────────────────────────────────────────────────────────


def recall_seen(conn: sqlite3.Connection, *, now: float | None = None) -> None:
    """Judge the recall incidents from the newest recall records (*conn* on
    ``memory_ops.db``). Called after each record; never raises."""
    now = time.time() if now is None else float(now)
    try:
        rows = conn.execute(
            "SELECT degraded, failed, latency_ms FROM memory_recall_records WHERE at >= ? "
            "ORDER BY at DESC LIMIT ?", (now - LOOKBACK_S, max(RECALL_WINDOW, SLOW_WINDOW))).fetchall()
        _check_degraded(conn, rows, now)
        _check_slow(conn, rows, now)
    except sqlite3.Error:
        logger.warning("[memory_quality] the recall incidents could not be judged", exc_info=True)


def _check_degraded(conn: sqlite3.Connection, rows: list[Any], now: float) -> None:
    window = rows[:RECALL_WINDOW]
    bad = [bool(r["failed"]) or bool(r["degraded"]) for r in window]
    held = _incident(conn, "recall_degraded")
    if held is None:
        if len(window) >= RECALL_MIN and sum(bad) >= RECALL_DEGRADED_SHARE * len(window):
            detail = (f"{sum(bad)} of the last {len(window)} turns' recalls could not search by meaning "
                      "(the embedding model does not answer, or recall failed). Kazma still answers.")
            if _open(conn, "recall_degraded", now, detail):
                ops_alerts.alert("memory.recall_degraded", _TITLES["recall_degraded"][0], detail, severity="warn",
                      cooldown_s=0.0)
    elif len(window) >= RECALL_RECOVERED and not any(bad[:RECALL_RECOVERED]):
        lasted = _close(conn, "recall_degraded", now)
        if lasted:
            ops_alerts.alert("memory.recall_back", _TITLES["recall_degraded"][1], f"It lasted {lasted}.",
                  severity="success", cooldown_s=0.0)


def _p95(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0


def _check_slow(conn: sqlite3.Connection, rows: list[Any], now: float) -> None:
    window = [int(r["latency_ms"] or 0) for r in rows[:SLOW_WINDOW]]
    held = _incident(conn, "recall_slow")
    if held is None:
        if len(window) >= SLOW_WINDOW and _p95(window) > SLOW_P95_MS:
            detail = (f"Of the last {len(window)} turns, one in twenty waited {_p95(window) / 1000:.1f} s "
                      "or more for memory.")
            if _open(conn, "recall_slow", now, detail):
                ops_alerts.alert("memory.recall_slow", _TITLES["recall_slow"][0], detail, severity="warn",
                                 cooldown_s=0.0)
    elif len(window) >= SLOW_WINDOW and _p95(window) < SLOW_BACK_P95_MS:
        lasted = _close(conn, "recall_slow", now)
        if lasted:
            ops_alerts.alert("memory.recall_speed_back", _TITLES["recall_slow"][1], f"It lasted {lasted}.",
                  severity="success", cooldown_s=0.0)


# ── incidents ──────────────────────────────────────────────────────────────


def _incident(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT name, opened_at, paged, detail FROM memory_incidents WHERE name = ?",
                        (name,)).fetchone()


def _open(conn: sqlite3.Connection, name: str, now: float, detail: str) -> bool:
    """Open incident *name*; True when this call opened it (and pages)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO memory_incidents (name, opened_at, paged, detail) VALUES (?, ?, 1, ?)",
        (name, now, detail))
    conn.commit()
    if cur.rowcount != 1:
        return False  # another process opened it: that one paged
    logger.warning("[memory_quality] %s: %s", name, detail)
    return True


def _close(conn: sqlite3.Connection, name: str, now: float) -> str:
    """Close incident *name*; how long it lasted when this call closed it
    (and pages), else ""."""
    held = _incident(conn, name)
    cur = conn.execute("DELETE FROM memory_incidents WHERE name = ?", (name,))
    conn.commit()
    if cur.rowcount != 1 or held is None:
        return ""
    lasted = _duration(now - float(held["opened_at"]))
    logger.info("[memory_quality] %s ended after %s", name, lasted)
    return lasted


def incident_open(name: str) -> bool:
    """Whether incident *name* is open. Blocking; False when it cannot be read."""
    try:
        conn = ops_conn(create=False)
        if conn is None:
            return False
        with closing(conn):
            return _incident(conn, name) is not None
    except (sqlite3.Error, OSError):
        logger.warning("[memory_quality] the incidents could not be read", exc_info=True)
        return False


def open_incidents() -> list[dict[str, Any]]:
    """Every open incident (name, since, what it said). Blocking; [] when they
    cannot be read."""
    try:
        conn = ops_conn(create=False)
        if conn is None:
            return []
        with closing(conn):
            return [{"name": r["name"], "since": float(r["opened_at"]), "detail": r["detail"],
                     "key": INCIDENTS.get(r["name"], ("", ""))[0]}
                    for r in conn.execute("SELECT name, opened_at, paged, detail FROM memory_incidents "
                                          "ORDER BY opened_at")]
    except (sqlite3.Error, OSError):
        logger.warning("[memory_quality] the incidents could not be read", exc_info=True)
        return []


def _when(at: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds >= 2 * 86400:
        return f"{seconds / 86400:.1f} days"
    if seconds >= 7200:
        return f"{seconds / 3600:.1f} hours"
    return f"{max(1, round(seconds / 60))} min"
