"""The memory graph's copy in Neo4j converges on SQLite (2026-11-13).

SQLite holds the facts (``beliefs``). Neo4j, when chosen as the graph store,
holds a copy of the current ones as edges, written by the dual-write after
each change (``graph_backend.upsert_belief_edge`` / ``delete_belief_edge``,
best effort, after the commit). Nothing kept that copy whole:

* a fact written while Neo4j was down never reached it;
* an install that had memories before Neo4j was added (``kazma setup
  install`` sets Neo4j up by default since 2026-11-13) had an empty Neo4j
  until someone pressed "Sync beliefs -> Neo4j", a one-shot push of the first
  1,000 current facts that removed nothing;
* a fact invalidated, superseded, forgotten or retired while Neo4j was down
  kept its edge for good: a forgotten value stayed in the copy (§15I).

:func:`run_graph_sync` is the maintenance sweep "memory graph sync" (one entry
of ``worker_bootstrap._MAINTENANCE_SWEEPS``) and what the Settings button runs
(:func:`sync_now`). It follows the Postgres mirror sync (AGENTS.md §15K):

* triggers queue every fact written, changed in a column its edge holds, or
  deleted, in ``memory_graph_outbox`` (``schema_v2._ensure_graph_outbox``, in
  the change's own transaction), and each pass brings those facts' edges to
  what they should be;
* a full comparison -- every current fact against every edge of the tenant --
  runs once a day; at once for a copy never compared or another Neo4j
  (``target``), one that went back (the newest facts it was sent are not
  there: a wiped or restored Neo4j), after a memory restore
  (:func:`mark_full_due`), and while a comparison is still catching up;
* at most :data:`PASS_LIMIT` writes and :data:`TIME_BUDGET_S` seconds a pass,
  removals first (a forgotten value leaves first), then the newest facts.

What the copy should hold is what the dual-write writes: one edge per current
fact (no end of validity, not invalidated, subject and value not empty),
``(:Entity {name: subject, tenant_id})-[:PREDICATE]->(:Entity {name: value,
tenant_id})`` carrying the fact's id, confidence and predicate. Two current
facts on one edge (predicates that sanitize to one type) give it the newest.

What it may change is Kazma's own: an edge carrying a ``belief_id`` and either
this install's tag (``r.install``, on every write since 2026-11-13) or no tag
and a fact this install knows (a row of ``beliefs`` or ``beliefs_archive``).
An edge with no ``belief_id`` (Kazma did not write it), or tagged by another
install sharing the Neo4j, is never written or removed; an untagged edge of a
fact this install never held is counted (``unknown_edges``) and left alone.
Entity nodes of a tenant this install holds facts in, left with no edge, go
(a forgotten value is a node, AUD-063).

A Neo4j that does not answer is waited out: the queue keeps every change (at
most :data:`OUTBOX_MAX_ROWS` rows, then a full comparison is due instead),
one WARNING when it stops answering, one INFO when it is back, and the pass
returns normally (no failed sweep, no incident). No Neo4j chosen: the queue is
emptied. No SQLite write transaction is open while Neo4j is called (§15D2),
and a fact is read again just before its edge is written (a forget in between
must not be pushed back).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "FINDINGS_KEY",
    "graph_copy_status",
    "mark_full_due",
    "run_graph_sync",
    "sync_now",
]

#: Writes (edges written or removed) a pass makes at most.
PASS_LIMIT = 2000
#: Facts or edges per Neo4j statement.
BATCH = 500
#: Seconds a maintenance pass may take; past it no new batch is started.
TIME_BUDGET_S = 120.0
#: The full comparison runs at least this often.
FULL_EVERY_S = 24 * 3600.0
#: A copy that went back forces a full comparison at most this often (a fact
#: Neo4j refuses for good, one of the newest by chance, must not make every
#: pass a full comparison).
_WENT_BACK_EVERY_S = 3600.0
_PROBE_ROWS = 3
#: A queued fact whose batch failed this many times leaves the queue; the next
#: full comparison finds it again.
OUTBOX_MAX_ATTEMPTS = 5
#: Past this many queued changes (Neo4j away for long, or not installed) the
#: queue is emptied and a full comparison is due instead: it covers them all.
OUTBOX_MAX_ROWS = 100_000
#: Isolated Entity nodes removed per tenant per full comparison.
_ISOLATED_PER_PASS = 1000

#: What the last full comparison found, kept in the settings store (runtime).
FINDINGS_KEY = "memory.v2.graph_sync"

#: A fact that should be an edge.
_CURRENT = "valid_until IS NULL AND invalidated_at IS NULL AND subject <> '' AND object <> ''"
_FACT_COLUMNS = "id, subject, predicate, object, confidence, ingested_at"

Key = tuple[str, str, str]  # (subject, relationship type, value)


@dataclass(frozen=True)
class Want:
    """What the edge at a key should carry."""

    bid: str
    pred: str
    conf: float
    at: float


@dataclass(frozen=True)
class Held:
    """An edge Neo4j holds between two Entity nodes of the tenant."""

    eid: str
    bid: str
    key: Key
    conf: float | None
    pred: str | None
    install: str | None


@dataclass
class Plan:
    """What it takes to bring a set of keys in step."""

    deletes: list[Held] = field(default_factory=list)
    upserts: list[tuple[Key, Want]] = field(default_factory=list)
    in_sync: int = 0
    held_elsewhere: int = 0
    unknown: int = 0


@dataclass
class _Done:
    written: int = 0
    removed: int = 0
    failed: int = 0
    #: Actions planned and not attempted (the pass's writes or time ran out);
    #: for the queue, its entries from before the pass still queued after it.
    left: int = 0


_STATE_LOCK = threading.Lock()
#: The last pass's outcome in this process (``graph_copy_status``).
_STATE: dict[str, Any] = {}
#: One pass at a time (the sweep and the Settings button).
_PASS_LOCK = threading.Lock()


# ── what should be there ────────────────────────────────────────────────


def _confidence(value: Any) -> float:
    try:
        return float(value or 0.5)
    except (TypeError, ValueError):
        return 0.5


def _wants(rows: Iterable[Sequence[Any]]) -> dict[Key, Want]:
    """The edge each current fact row makes, the newest fact winning a key."""
    from kazma_core.memory.graph_backend import rel_name

    out: dict[Key, Want] = {}
    for bid, sub, pred, obj, conf, at in rows:
        key = (str(sub), rel_name(str(pred or "")), str(obj))
        want = Want(str(bid), str(pred or ""), _confidence(conf), float(at or 0.0))
        prior = out.get(key)
        if prior is None or (want.at, want.bid) > (prior.at, prior.bid):
            out[key] = want
    return out


def _held(rec: dict[str, Any]) -> Held:
    conf = rec.get("conf")
    install = rec.get("install")
    return Held(
        eid=str(rec.get("eid") or ""),
        bid=str(rec.get("bid") or ""),
        key=(str(rec.get("sub") or ""), str(rec.get("rel") or ""), str(rec.get("obj") or "")),
        conf=float(conf) if isinstance(conf, (int, float)) else None,
        pred=None if rec.get("pred") is None else str(rec.get("pred")),
        install=str(install) if install else None,
    )


def _plan_changes(wants: dict[Key, Want], held: Iterable[Held], *, install: str | None,
                 known: set[str]) -> Plan:
    """What brings the edges *held* at the keys of *wants* in step.

    An edge with no fact id, or another install's, is never planned: at a
    key this install wants it counts as there (``held_elsewhere``) -- the
    write would land on it and change nothing. This install's edges (tagged,
    or untagged with a fact id in *known*) are corrected or removed; an
    untagged edge of an unknown fact is counted (``unknown``) and left.
    """
    plan = Plan()
    matched: set[Key] = set()
    elsewhere: set[Key] = set()
    for h in held:
        want = wants.get(h.key)
        if not h.bid or (h.install is not None and h.install != install):
            if want is not None:
                elsewhere.add(h.key)
            continue
        if want is not None and h.key not in matched:
            matched.add(h.key)
            same = (h.bid, h.pred, h.install) == (want.bid, want.pred, install)
            if same and h.conf is not None and abs(h.conf - want.conf) < 1e-9:
                plan.in_sync += 1
            else:
                plan.upserts.append((h.key, want))
            continue
        if h.install is not None or h.bid in known:
            plan.deletes.append(h)
        else:
            plan.unknown += 1
    for key, want in wants.items():
        if key not in matched and key not in elsewhere:
            plan.upserts.append((key, want))
    plan.held_elsewhere = len(elsewhere - matched)
    plan.upserts.sort(key=lambda kw: (kw[1].at, kw[1].bid), reverse=True)
    return plan


def _known(conn: sqlite3.Connection, bids: Iterable[str]) -> set[str]:
    """The fact ids this install holds or archived."""
    ids = sorted({b for b in bids if b})
    found: set[str] = set()
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        marks = ",".join("?" * len(chunk))
        found.update(str(r[0]) for r in conn.execute(
            f"SELECT id FROM beliefs WHERE id IN ({marks})", chunk))  # nosec B608 - placeholders only
        try:
            found.update(str(r[0]) for r in conn.execute(
                f"SELECT id FROM beliefs_archive WHERE id IN ({marks})", chunk))  # nosec B608 - placeholders only
        except sqlite3.OperationalError:  # a database older than the archive table
            logger.debug("[memory] graph sync: no beliefs_archive to read", exc_info=True)
    return found


def _wants_at(conn: sqlite3.Connection, tenant: str, keys: set[Key]) -> dict[Key, Want]:
    """What the edge at each of *keys* should carry (keys no fact wants are absent)."""
    out: dict[Key, Want] = {}
    for sub, obj in sorted({(k[0], k[2]) for k in keys}):
        rows = conn.execute(
            f"SELECT {_FACT_COLUMNS} FROM beliefs WHERE tenant_id = ? AND subject = ? "  # nosec B608 - constants only
            f"AND object = ? AND {_CURRENT}", (tenant, sub, obj)).fetchall()
        out.update({k: w for k, w in _wants(rows).items() if k in keys})
    return out


def _still_current(conn: sqlite3.Connection, tenant: str, upserts: list[tuple[Key, Want]]) -> list[tuple[Key, Want]]:
    """*upserts* whose fact is still current, at the same key, now: read
    just before the write, so a forget made since the plan is not pushed back."""
    from kazma_core.memory.graph_backend import rel_name

    ids = [w.bid for _k, w in upserts]
    marks = ",".join("?" * len(ids))
    now = {str(r[0]): (str(r[1]), rel_name(str(r[2] or "")), str(r[3])) for r in conn.execute(
        f"SELECT id, subject, predicate, object FROM beliefs WHERE tenant_id = ? AND id IN ({marks}) "  # nosec B608 - placeholders only
        f"AND {_CURRENT}", (tenant, *ids))}
    return [(k, w) for k, w in upserts if now.get(w.bid) == k]


# ── writing it ──────────────────────────────────────────────────────────


def _no_write_open(conn: sqlite3.Connection) -> None:
    """Neo4j is never called inside a memory write transaction (§15D2)."""
    if conn.in_transaction:
        raise RuntimeError("graph sync: a memory write transaction is open across a Neo4j call")


def _in_batches(items: list[Any], write: Callable[[list[Any]], None]) -> int:
    """Write *items* in one statement; refused, one at a time. Returns how
    many were refused. An outage (``GraphUnavailable``) ends the pass."""
    from kazma_core.memory.graph_backend import GraphWriteRefused

    try:
        write(items)
        return 0
    except GraphWriteRefused:
        refused = 0
        for item in items:
            try:
                write([item])
            except GraphWriteRefused:
                refused += 1
                logger.debug("[memory] graph sync: Neo4j refused one write", exc_info=True)
        return refused


def _apply(conn: sqlite3.Connection, be: Any, tenant: str, plan: Plan, *, install: str | None,
           budget: int, deadline: float) -> _Done:
    """Carry *plan* out: removals first, then the newest facts, within *budget*
    writes and *deadline* (monotonic)."""
    from kazma_core.memory.graph_backend import edge_row

    done = _Done()

    def room() -> int:
        if time.monotonic() >= deadline:
            return 0
        return max(0, budget - done.written - done.removed - done.failed)

    deletes = list(plan.deletes)
    while deletes and room():
        size = min(BATCH, room())
        chunk, deletes = deletes[:size], deletes[size:]
        _no_write_open(conn)
        refused = _in_batches(
            [{"eid": h.eid, "bid": h.bid} for h in chunk],
            lambda rows: be.delete_edges(tenant, rows),
        )
        done.removed += len(chunk) - refused
        done.failed += refused
    upserts = list(plan.upserts)
    while upserts and room():
        size = min(BATCH, room())
        chunk, upserts = upserts[:size], upserts[size:]
        fresh = _still_current(conn, tenant, chunk)
        _no_write_open(conn)
        refused = _in_batches(
            [edge_row(k[0], w.pred, k[2], w.bid, w.conf) for k, w in fresh],
            lambda rows: be.upsert_edges(tenant, rows, install=install),
        ) if fresh else 0
        done.written += len(fresh) - refused
        done.failed += refused
    done.left = len(deletes) + len(upserts)
    return done


# ── the queue (memory_graph_outbox) ─────────────────────────────────────


def _outbox_depth(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("SELECT COUNT(*) FROM memory_graph_outbox").fetchone()[0])
    except sqlite3.OperationalError:  # a database older than the queue
        return 0


def _empty_queue(conn: sqlite3.Connection, why: str) -> None:
    """Empty the queue; a copy compared before is due a full comparison then."""
    if not _outbox_depth(conn):
        return
    conn.execute("DELETE FROM memory_graph_outbox")
    conn.commit()
    if _load_findings().get("target"):
        mark_full_due(why)


def _bound_queue(conn: sqlite3.Connection) -> int:
    """The queue's depth, emptied (and a full comparison due) past its bound."""
    depth = _outbox_depth(conn)
    if depth > OUTBOX_MAX_ROWS:
        logger.warning("[memory] graph sync: %d changes waited for Neo4j, more than %d; the queue "
                       "is emptied and the next pass that reaches Neo4j compares every fact",
                       depth, OUTBOX_MAX_ROWS)
        conn.execute("DELETE FROM memory_graph_outbox")
        conn.commit()
        mark_full_due("queue past its bound")
        return 0
    return depth


def _sync_facts(conn: sqlite3.Connection, be: Any, tenant: str, bids: list[str], *,
                install: str | None, budget: int, deadline: float) -> _Done:
    """Bring the edges of the facts *bids* (and of every key they touch) in step."""
    from kazma_core.memory.graph_backend import rel_name

    marks = ",".join("?" * len(bids))
    keys: set[Key] = {
        (str(r[0]), rel_name(str(r[1] or "")), str(r[2])) for r in conn.execute(
            f"SELECT subject, predicate, object FROM beliefs WHERE tenant_id = ? AND id IN ({marks}) "  # nosec B608 - placeholders only
            f"AND {_CURRENT}", (tenant, *bids))
    }
    _no_write_open(conn)
    by_bid = [_held(r) for r in be.edges_for_beliefs(tenant, bids)]
    keys |= {h.key for h in by_bid}
    wants = _wants_at(conn, tenant, keys)
    at_keys = [_held(r) for r in be.edges_at_keys(tenant, sorted(keys))] if keys else []
    held = list({h.eid: h for h in [*by_bid, *at_keys]}.values())
    queued = set(bids)
    untagged = {h.bid for h in held if h.bid and h.install is None and h.bid not in queued}
    plan = _plan_changes(wants, held, install=install, known=queued | _known(conn, untagged))
    return _apply(conn, be, tenant, plan, install=install, budget=budget, deadline=deadline)


def _drain(conn: sqlite3.Connection, be: Any, *, install: str | None, budget: int,
           deadline: float) -> _Done:
    """The queued facts, oldest change first, a batch at a time. ``left`` is
    the queue entries from before the pass still there after it (a change
    queued during the pass is not work this pass left)."""
    total = _Done()
    try:
        upto = conn.execute("SELECT MAX(seq) FROM memory_graph_outbox").fetchone()[0]
        rows = conn.execute(
            "SELECT seq, belief_id, tenant_id FROM memory_graph_outbox ORDER BY seq LIMIT ?",
            (max(1, budget) * 4,)).fetchall()
    except sqlite3.OperationalError:  # a database older than the queue: the full comparison covers it
        logger.debug("[memory] graph sync: no queue", exc_info=True)
        return total
    if upto is None:
        return total
    _drain_batches(conn, be, rows, total, install=install, budget=budget, deadline=deadline)
    if total.failed:
        gone = conn.execute("DELETE FROM memory_graph_outbox WHERE attempts >= ?", (OUTBOX_MAX_ATTEMPTS,))
        conn.commit()
        if gone.rowcount:
            logger.warning("[memory] graph sync: %d queued facts failed %d passes in a row; the next "
                           "full comparison tries them again", gone.rowcount, OUTBOX_MAX_ATTEMPTS)
            mark_full_due("queued facts failed")
    total.left = int(conn.execute("SELECT COUNT(*) FROM memory_graph_outbox WHERE seq <= ?",
                                  (upto,)).fetchone()[0])
    return total


def _drain_batches(conn: sqlite3.Connection, be: Any, rows: list[Any], total: _Done, *,
                   install: str | None, budget: int, deadline: float) -> None:
    """Each tenant's queued facts, :data:`BATCH` at a time, into *total*; a
    batch done leaves the queue, one with a refused write counts an attempt,
    one cut short (writes or time) stays whole for the next pass."""
    groups: dict[str, dict[str, list[int]]] = {}
    for seq, bid, tenant in rows:
        groups.setdefault(str(tenant or "default"), {}).setdefault(str(bid), []).append(int(seq))
    for tenant, by_bid in groups.items():
        bids = list(by_bid)
        for start in range(0, len(bids), BATCH):
            room = budget - total.written - total.removed - total.failed
            if room <= 0 or time.monotonic() >= deadline:
                return
            chunk = bids[start:start + BATCH]
            done = _sync_facts(conn, be, tenant, chunk, install=install, budget=room, deadline=deadline)
            total.written += done.written
            total.removed += done.removed
            total.failed += done.failed
            if done.left:  # cut short: the whole batch again next pass (it is idempotent)
                return
            seqs = [s for b in chunk for s in by_bid[b]]
            marks = ",".join("?" * len(seqs))
            if done.failed:
                conn.execute(f"UPDATE memory_graph_outbox SET attempts = attempts + 1 WHERE seq IN ({marks})",  # nosec B608 - placeholders only
                             seqs)
            else:
                conn.execute(f"DELETE FROM memory_graph_outbox WHERE seq IN ({marks})", seqs)  # nosec B608 - placeholders only
            conn.commit()


# ── the full comparison ─────────────────────────────────────────────────


def _compare_tenant(conn: sqlite3.Connection, be: Any, tenant: str, *, install: str | None,
                    budget: int, deadline: float, holds_facts: bool) -> dict[str, int]:
    """Every edge of *tenant* against every current fact of it. Neo4j is
    read first: a fact written meanwhile is pushed again (harmless), never
    taken for stale."""
    _no_write_open(conn)
    held = [_held(r) for r in be.iter_tenant_edges(tenant)]
    wants = _wants(conn.execute(
        f"SELECT {_FACT_COLUMNS} FROM beliefs WHERE tenant_id = ? AND {_CURRENT}", (tenant,)))  # nosec B608 - constants only
    untagged = {h.bid for h in held if h.bid and h.install is None}
    plan = _plan_changes(wants, held, install=install, known=_known(conn, untagged))
    done = _apply(conn, be, tenant, plan, install=install, budget=budget, deadline=deadline)
    isolated = 0
    if holds_facts and not done.left and time.monotonic() < deadline:
        _no_write_open(conn)
        isolated = be.delete_isolated_nodes(tenant, _ISOLATED_PER_PASS)
    return {
        "facts": len(wants), "edges": sum(1 for h in held if h.bid),
        "in_sync": plan.in_sync, "held_elsewhere": plan.held_elsewhere, "unknown_edges": plan.unknown,
        "written": done.written, "removed": done.removed, "failed": done.failed,
        "behind": done.left, "isolated_nodes": isolated,
    }


def _compare(conn: sqlite3.Connection, be: Any, *, install: str | None, budget: int,
             deadline: float) -> dict[str, Any]:
    """The full comparison, tenant by tenant: every tenant this install holds
    facts of (current or not), and every tenant Neo4j holds fact edges of."""
    mine = {str(r[0] or "default") for r in conn.execute("SELECT DISTINCT tenant_id FROM beliefs")}
    _no_write_open(conn)
    tenants = sorted(mine | set(be.edge_tenants()))
    totals: dict[str, Any] = {"facts": 0, "edges": 0, "in_sync": 0, "held_elsewhere": 0, "unknown_edges": 0,
                              "written": 0, "removed": 0, "failed": 0, "behind": 0, "isolated_nodes": 0}
    complete = True
    for tenant in tenants:
        room = budget - totals["written"] - totals["removed"] - totals["failed"]
        if time.monotonic() >= deadline:
            complete = False
            break
        part = _compare_tenant(conn, be, tenant, install=install, budget=room, deadline=deadline,
                               holds_facts=tenant in mine)
        for name in totals:
            totals[name] += part[name]
    totals["complete"] = complete
    return totals


# ── the pass ────────────────────────────────────────────────────────────


def _load_findings() -> dict[str, Any]:
    from kazma_core.config_store import get_config_store
    from kazma_core.db.pg_helpers import store_errors

    try:
        kept = get_config_store().get(FINDINGS_KEY)
    except store_errors():
        logger.debug("[memory] graph sync: findings unreadable", exc_info=True)
        return {}
    return dict(kept) if isinstance(kept, dict) else {}


def _save_findings(findings: dict[str, Any]) -> None:
    from kazma_core.config_store import get_config_store
    from kazma_core.db.pg_helpers import store_errors

    try:
        get_config_store().set(FINDINGS_KEY, findings, category="memory")
    except store_errors():  # the next pass compares in full again
        logger.warning("[memory] graph sync: could not keep the comparison's findings", exc_info=True)


def mark_full_due(why: str) -> None:
    """Make the next pass that reaches Neo4j compare every fact (a memory
    restore, a queue emptied with changes in it)."""
    findings = _load_findings()
    if findings.get("full_due"):
        return
    findings["full_due"] = why
    _save_findings(findings)


def _full_reason(findings: dict[str, Any], target: str, now: float, forced: bool | None) -> str:
    if forced:
        return "requested"
    if findings.get("target") != target:
        return "a new copy"
    if findings.get("full_due"):
        return str(findings["full_due"])
    if int(findings.get("behind") or 0) > 0:
        return "catching up"
    if now - float(findings.get("verified_at") or 0) >= FULL_EVERY_S:
        return "daily"
    return ""


def _went_back(conn: sqlite3.Connection, be: Any, now: float) -> bool:
    """Whether Neo4j lost edges it was sent: none of the newest current facts
    (none waiting in the queue) has an edge at its key. A Neo4j recreated
    empty or restored from an older backup does that; nothing queued would
    bring those back until the daily comparison. Every one of them, not one:
    a value Neo4j refuses (counted by the comparison) is missing for good."""
    from kazma_core.memory.graph_backend import rel_name

    with _STATE_LOCK:
        if now - float(_STATE.get("went_back_at") or 0) < _WENT_BACK_EVERY_S:
            return False
    try:
        rows = conn.execute(
            f"SELECT tenant_id, subject, predicate, object FROM beliefs WHERE {_CURRENT} "  # nosec B608 - constants only
            "AND id NOT IN (SELECT belief_id FROM memory_graph_outbox) ORDER BY rowid DESC LIMIT ?",
            (_PROBE_ROWS,)).fetchall()
    except sqlite3.OperationalError:  # a database older than the queue
        return False
    if not rows:
        return False
    for tenant, sub, pred, obj in rows:
        _no_write_open(conn)
        if be.edges_at_keys(str(tenant or "default"), [(str(sub), rel_name(str(pred or "")), str(obj))]):
            return False
    with _STATE_LOCK:
        _STATE["went_back_at"] = now
    logger.warning("[memory] graph sync: Neo4j lacks the newest facts it was sent (recreated "
                   "or restored?); comparing every fact now")
    return True


def _say(prior: str | None, status: str, reason: str) -> None:
    """One line per change of state, never one per pass."""
    if status == prior:
        return
    if status == "unreachable":
        logger.warning("[memory] graph sync: Neo4j does not answer (%s); the memory graph's copy "
                       "there is brought up to date when it does", reason or "no answer")
    elif status == "driver_missing":
        from kazma_core.memory.graph_backend import report_graph_driver

        report_graph_driver()  # one WARNING per process, naming the extra
    elif status == "needs_url":
        logger.warning("[memory] graph sync: the graph store is Neo4j but has no address "
                       "(memory.backends.graph.url): nothing reaches it")
    elif status == "not_configured":
        if prior is not None:
            logger.info("[memory] graph sync: Neo4j is no longer the graph store; nothing to keep in step")
    elif prior in ("unreachable", "driver_missing", "needs_url"):
        logger.info("[memory] graph sync: Neo4j answers again; the copy is being brought up to date")


def _record(status: str, report: dict[str, Any], *, reason: str = "") -> dict[str, Any]:
    report.update(status=status, reason=reason)
    with _STATE_LOCK:
        prior = _STATE.get("status")
        _STATE.update(status=status, reason=reason, at=time.time(), last=dict(report))
    _say(prior, status, reason)
    return report


def _open() -> sqlite3.Connection:
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db(), timeout=30, check_same_thread=False)
    apply_sqlite_pragmas(conn)
    return conn


def run_graph_sync(conn: sqlite3.Connection | None = None, *, full: bool | None = None,
                   limit: int = PASS_LIMIT, time_budget_s: float = TIME_BUDGET_S,
                   wait_s: float = 0.0) -> dict[str, Any]:
    """One pass (see the module docstring). Returns its report; never raises
    for a Neo4j that does not answer. ``status`` is ``in_sync``, ``behind``,
    ``unreachable``, ``not_configured``, ``needs_url``, ``driver_missing`` or
    ``busy`` (another pass is running)."""
    got = _PASS_LOCK.acquire(timeout=wait_s) if wait_s > 0 else _PASS_LOCK.acquire(blocking=False)
    if not got:
        return {"status": "busy"}
    try:
        own = conn is None
        db = _open() if own else conn
        try:
            return _pass(db, full=full, limit=max(0, int(limit)),
                         deadline=time.monotonic() + max(0.0, float(time_budget_s)))
        finally:
            if own:
                db.close()
    finally:
        _PASS_LOCK.release()


def _pass(conn: sqlite3.Connection, *, full: bool | None, limit: int, deadline: float) -> dict[str, Any]:
    from kazma_core.memory.graph_backend import GraphUnavailable, neo4j_target, this_install

    # The pass commits its own small writes (the queue); started inside a
    # caller's transaction it would commit that too, and call Neo4j under it.
    _no_write_open(conn)
    be, state, target = neo4j_target()
    report: dict[str, Any] = {"mode": "", "written": 0, "removed": 0, "failed": 0, "at": time.time()}
    if be is None:
        if state == "not_configured":
            _empty_queue(conn, "the queue was emptied while Neo4j was not the graph store")
        report["pending"] = _bound_queue(conn) if state != "not_configured" else 0
        return _record(state, report)
    answers, why = be.reachable()
    if not answers:
        report["pending"] = _bound_queue(conn)
        return _record("driver_missing" if why == "driver_missing" else "unreachable", report, reason=why)
    install = this_install()
    now = time.time()
    findings = _load_findings()
    try:
        drained = _drain(conn, be, install=install, budget=limit, deadline=deadline)
        report.update(mode="incremental", written=drained.written, removed=drained.removed,
                      failed=drained.failed)
        reason = _full_reason(findings, target, now, full)
        if not reason and _went_back(conn, be, now):
            reason = "went back"
        # A comparison due and not run (the queue took the pass's time) is
        # work left: the next pass runs it.
        compare_skipped = bool(reason) and time.monotonic() >= deadline
        if reason and not compare_skipped:
            used = drained.written + drained.removed + drained.failed
            compared = _compare(conn, be, install=install, budget=max(0, limit - used), deadline=deadline)
            for name in ("written", "removed", "failed"):
                report[name] += compared[name]
            report.update(mode="full", why=reason, **{k: compared[k] for k in (
                "facts", "edges", "in_sync", "held_elsewhere", "unknown_edges", "isolated_nodes")})
            # Work cut short makes the next pass compare again ("catching up");
            # a write Neo4j refused waits for the next daily comparison (it
            # would be refused every pass), counted apart.
            behind = int(compared["behind"]) + (0 if compared["complete"] else 1)
            findings = {"target": target, "verified_at": now, "behind": behind, "refused": compared["failed"],
                        **{k: compared[k] for k in ("facts", "edges", "held_elsewhere", "unknown_edges")}}
            _save_findings(findings)
            if compared["written"] or compared["removed"] or compared["unknown_edges"]:
                logger.info(
                    "[memory] graph sync (%s): facts %d, edges in Neo4j %d: written %d, removed %d, "
                    "left for the next pass %d; edges of facts this install does not know, left alone: %d",
                    reason, compared["facts"], compared["edges"], compared["written"], compared["removed"],
                    behind, compared["unknown_edges"])
    except GraphUnavailable as exc:
        report["pending"] = _bound_queue(conn)
        return _record("unreachable", report, reason=str(exc))
    report["pending"] = _outbox_depth(conn)
    # What is known to be left: what the last full comparison could not do
    # (unknown -- at least one -- for a copy never compared), a comparison
    # due and not run, and the queue when this pass did not drain it all.
    compared_left = (int(findings.get("behind") or 0) + int(findings.get("refused") or 0)
                     if findings.get("target") == target else 1)
    report["behind"] = compared_left + int(compare_skipped) + drained.left
    if report["mode"] == "incremental" and (drained.written or drained.removed):
        logger.debug("[memory] graph sync: %d edges written, %d removed for changed facts",
                     drained.written, drained.removed)
    if report["failed"]:
        logger.warning("[memory] graph sync: Neo4j refused %d writes; the next pass tries them again",
                       report["failed"])
    return _record("in_sync" if not report["behind"] else "behind", report)


# ── what the pages read ─────────────────────────────────────────────────


#: What the Settings button answers for a state that is not a sync.
_NOT_SYNCED = {
    "busy": "A sync is already running; it finishes on its own.",
    "not_configured": "Neo4j is not the graph store: choose it, Save, then Test.",
    "needs_url": "Set the Neo4j address (bolt://...), Save, then Test.",
    "driver_missing": 'The neo4j driver is not installed: uv pip install -e ".[neo4j]" in the Kazma folder, then restart.',
    "unreachable": "Neo4j does not answer. Test the connection; the copy is brought up to date when it answers.",
}


def sync_now(*, time_budget_s: float = 60.0) -> dict[str, Any]:
    """The Settings button: one pass with the full comparison, now."""
    report = run_graph_sync(full=True, time_budget_s=time_budget_s, wait_s=5.0)
    status = str(report.get("status") or "")
    if status in _NOT_SYNCED:
        return {"ok": False, "synced": 0, "status": status, "error": _NOT_SYNCED[status]}
    return {"ok": True, "status": status, "synced": int(report.get("written") or 0),
            "removed": int(report.get("removed") or 0), "failed": int(report.get("failed") or 0),
            "behind": int(report.get("behind") or 0), "facts": int(report.get("facts") or 0)}


def graph_copy_status(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """The copy's state for memory health, from the last pass (never asks
    Neo4j): ``status`` (``unknown`` before the first pass of this process),
    what it left ``behind``, the changes waiting in the queue, and what the
    last full comparison found."""
    with _STATE_LOCK:
        state = dict(_STATE)
    findings = _load_findings()
    last = state.get("last") or {}
    out: dict[str, Any] = {
        "status": state.get("status") or "unknown",
        "reason": state.get("reason") or "",
        "checked_at": state.get("at"),
        "behind": int(last.get("behind") or 0),
        "verified_at": findings.get("verified_at"),
        "facts": findings.get("facts"),
        "edges": findings.get("edges"),
        "held_elsewhere": findings.get("held_elsewhere"),
        "unknown_edges": findings.get("unknown_edges"),
        "refused": findings.get("refused"),
    }
    if conn is not None:
        out["pending_changes"] = _outbox_depth(conn)
    return out
