"""Which door each checkout of Kazma's own Postgres goes through (Spaces
S4-01, step 1: report mode).

The second lock (S4-01) will ask, at every checkout of a connection, whose
statements these are, and let them reach that space's rows only. Before
anything is armed this module asks the same question and only writes the
answer down, with Spaces off and nothing refused. There are three doors:

* ``space`` -- a space's: the bound scope's (``how="scope"``: a request, a
  turn, ``row_work.work_of``) or a row's own (``how="row"``:
  ``row_work.statements_of``);
* ``install`` -- the install's, by its declared reason (``how="statements"``:
  ``row_work.install_statements``, each use declared in ``INSTALL`` of
  ``tests/test_row_work_gates.py``; ``how="work"``: ``scope.install_work``);
* ``none`` -- nothing said: the finding. ``how`` says what was bound: a
  tenant only (the first lock's variable, which the second refuses) or
  nothing at all.

Every place Kazma hands out a connection to its own database asks
:func:`checkout` (:data:`POOLS`): the shared pool (``PostgresPool.connection``;
``execute`` and ``execute_one`` go through it), the checkpointer's pool
(``checkpoints_pg``), and the memory mirror's and pgvector's connections when
their address is Kazma's own database. The install's own sign-ins (backups,
restore, setup, upgrade, the outage prober: path 5 of the design) are
outside. ``tests/test_space_doors_gates.py`` holds both lists, and every
function that checks a connection out, to the source.

A checkout is counted by its pool, its door and its caller: the nearest
function of Kazma's own code above the door, and for ``none`` the six
nearest (where it came from). No space id, no tenant, no statement and no
row is kept.

Report mode runs for days on a live install, so the counter proves that every
caller says its door before the lock is ever armed (S4-01 step 6). It is
made to be left on:

* nothing changes: no statement is sent and nothing is refused; a failure
  inside the counter is counted and said once, never raised into a checkout;
* cheap: a few microseconds a checkout, no I/O, one short lock
  (``tests/test_space_doors.py`` measures it);
* bounded: at most :data:`MAX_ENTRIES` distinct entries a process, the rest
  counted as ``dropped`` (one WARNING);
* quiet: one INFO line for each caller with no door that the report does
  not hold yet, never one per statement (§58);
* kept where it lasts: :func:`flush` adds what was counted to
  ``<data dir>/space_doors.json`` every 15 minutes (the maintenance runner)
  and when the server stops, so counts add up across restarts.
  ``scripts/space_doors_report.py`` prints it, by door and caller.

``KAZMA_SPACE_DOOR_REPORT=0`` turns the counter off.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_core import tenant_context
from kazma_core.spaces.row_work import Door

__all__ = [
    "DOOR_FUNCTIONS",
    "MAX_ENTRIES",
    "POOLS",
    "REPORT_FILE",
    "checkout",
    "entries",
    "flush",
    "read_report",
    "report_path",
    "reporting",
    "reset",
    "start",
]

logger = logging.getLogger(__name__)

#: Where a connection to Kazma's own database is handed out.
POOLS = ("shared", "checkpoints", "memory mirror", "pgvector")

#: The report, in the data folder (``store_registry.DATA_DIR_ENTRIES``).
REPORT_FILE = "space_doors.json"

#: Distinct entries one process keeps. A live day has a few hundred.
MAX_ENTRIES = 4000
#: Entries the report file keeps.
MAX_FILE_ENTRIES = 8000
#: Callers kept for a checkout through no door (where it came from).
CHAIN = 6
#: Frames walked at most, to find them.
_MAX_WALK = 96
#: Code objects whose kind is remembered.
_MAX_CODES = 50_000

#: The functions that hand a connection out (each calls :func:`checkout`), or
#: only pass a statement on to one: never a caller, so the report names the
#: function that wanted the statement. ``module:qualname``, ``<locals>``
#: left out. ``tests/test_space_doors_gates.py`` holds it to the source.
DOOR_FUNCTIONS: frozenset[str] = frozenset({
    "kazma_core.db.postgres_pool:PostgresPool.connection",
    "kazma_core.db.postgres_pool:PostgresPool.execute",
    "kazma_core.db.postgres_pool:PostgresPool.execute_one",
    "kazma_core.checkpoints_pg:_reported_pool_class.ReportedCheckpointPool.connection",
    "kazma_core.memory.state_backend:PostgresStateBackend._connect",
    "kazma_core.memory.backends:PgvectorBackend._connect",
    "kazma_core.swarm.task_store:_pg_execute",
    "kazma_core.swarm.task_store:_pg_execute_one",
})

#: Kazma's packages, and the folder each lives in.
_PACKAGES = {
    "kazma_core": "kazma-core",
    "kazma_ui": "kazma-ui",
    "kazma_gateway": "kazma-gateway",
    "kazma_cli": "kazma-cli",
    "kazma_tui": "kazma-tui",
    "kazma_skills": "kazma-skills",
}

#: The machinery between a function and its connection: no one's caller.
_PLUMBING = ("contextlib", "asyncio", "psycopg", "psycopg_pool", "threading", "concurrent", "functools",
             __name__)
_PLUMBING_PREFIXES = tuple(f"{name}." for name in _PLUMBING)

_PLUMB, _KAZMA, _FOREIGN = 0, 1, 2

#: What the counter itself may fail with (a frame or a name it cannot read):
#: counted and said once, never raised into a checkout.
_COUNTER_FAILURES = (AttributeError, KeyError, IndexError, TypeError, ValueError, RuntimeError, OSError)

_ENV = "KAZMA_SPACE_DOOR_REPORT"


def _switched_on() -> bool:
    return os.environ.get("KAZMA_SPACE_DOOR_REPORT", "1").strip().lower() not in ("0", "false", "no", "off")


@dataclass
class _Entry:
    count: int
    first: float
    last: float
    flushed: int = 0


#: ``(pool, door, how, reason, callers)`` -> what was counted.
_Key = tuple[str, str, str, str, tuple[str, ...]]

_lock = threading.Lock()
_flush_lock = threading.Lock()
_entries: dict[_Key, _Entry] = {}
#: Checkouts through no door that the report holds, or this process said.
_said: set[_Key] = set()
_codes: dict[Any, tuple[int, str]] = {}
_on = _switched_on()
_dropped = 0
_dropped_flushed = 0
_failures = 0
_failures_flushed = 0
_full_said = False
_failure_said = False


# ── the counter (every checkout) ────────────────────────────────────────


def checkout(pool: str) -> None:
    """Count one checkout from *pool* by the door it goes through now.

    Never raises and reads nothing: a failure here is counted and said once,
    and the checkout goes on as it would have.
    """
    if not _on:
        return
    try:
        _record(pool)
    except _COUNTER_FAILURES:  # the counter never stops a statement
        _count_failure()


def _record(pool: str) -> None:
    global _dropped, _full_said
    door = Door.now()
    if door is None:
        bound = "a tenant only" if tenant_context.get_current_tenant_id() else "nothing"
        door_kind, how, reason, depth = "none", bound, "", CHAIN
    elif door.space:
        door_kind, how, reason, depth = "space", door.how or "scope", "", 1
    else:
        door_kind, how, reason, depth = "install", door.how or "statements", door.reason, 1
    key: _Key = (pool, door_kind, how, reason, _callers(depth))
    now = time.time()
    new = full = False
    with _lock:
        entry = _entries.get(key)
        if entry is not None:
            entry.count += 1
            entry.last = now
            return
        if len(_entries) >= MAX_ENTRIES:
            _dropped += 1
            full, _full_said = not _full_said, True
        else:
            _entries[key] = _Entry(1, now, now)
            if door_kind == "none" and key not in _said:
                _said.add(key)
                new = True
    if new:
        logger.info("[space-doors] a checkout of the %s pool went through no door (%s bound): %s. "
                    "Report mode: nothing is refused (Spaces S4-01 step 1)", pool, how, " <- ".join(key[4]))
    if full:
        logger.warning("[space-doors] the door report holds %d entries; further new ones are counted "
                       "as dropped, not named", MAX_ENTRIES)


def _count_failure() -> None:
    global _failures, _failure_said
    with _lock:
        _failures += 1
        say, _failure_said = not _failure_said, True
    if say:
        logger.warning("[space-doors] the door report could not count a checkout; the checkout went on, "
                       "and further failures are counted in the report", exc_info=True)


def _callers(depth: int) -> tuple[str, ...]:
    """The *depth* nearest functions of Kazma's own code above the door, and
    when fewer, the code that called into them (a test, LangGraph's runner,
    the web server); with none at all (a task LangGraph started), the
    nearest of the code that called the door."""
    frame: Any = sys._getframe(1)
    found: list[str] = []
    outside: list[str] = []
    steps = 0
    while frame is not None and steps < _MAX_WALK:
        kind, name = _kind(frame)
        if kind == _KAZMA:
            found.append(name)
            if len(found) >= depth:
                break
        elif kind == _FOREIGN:
            if found:
                found.append(name)  # where Kazma's own chain was entered from
                break
            if len(outside) < CHAIN:
                outside.append(name)
        frame = frame.f_back
        steps += 1
    return tuple(found or outside) or ("<unknown>",)


def _kind(frame: Any) -> tuple[int, str]:
    code = frame.f_code
    known = _codes.get(code)
    if known is not None:
        return known
    module = str(frame.f_globals.get("__name__") or "")
    qualname = str(getattr(code, "co_qualname", code.co_name)).replace("<locals>.", "")
    top = module.partition(".")[0]
    if module in _PLUMBING or module.startswith(_PLUMBING_PREFIXES) or f"{module}:{qualname}" in DOOR_FUNCTIONS:
        known = (_PLUMB, "")
    elif top in _PACKAGES:
        known = (_KAZMA, f"{_source_of(module, code.co_filename)}::{qualname}")
    else:
        known = (_FOREIGN, f"{module or '?'}::{qualname}")
    if len(_codes) < _MAX_CODES:
        _codes[code] = known
    return known


def _source_of(module: str, filename: str) -> str:
    """``kazma_core.db.postgres_pool`` -> ``kazma-core/kazma_core/db/postgres_pool.py``:
    the name the gates use."""
    parts = module.split(".")
    tail = "/__init__.py" if os.path.basename(filename or "") == "__init__.py" else ".py"
    return f"{_PACKAGES[parts[0]]}/{'/'.join(parts)}{tail}"


# ── reading what was counted ────────────────────────────────────────────


def reporting() -> bool:
    """Whether checkouts are counted (``KAZMA_SPACE_DOOR_REPORT``)."""
    return _on


def entries() -> list[dict[str, Any]]:
    """What this process counted, one dict an entry. No I/O."""
    with _lock:
        items = [(key, entry.count, entry.first, entry.last) for key, entry in _entries.items()]
    return [_as_item(key, count, first, last) for key, count, first, last in items]


def _as_item(key: _Key, count: int, first: float, last: float) -> dict[str, Any]:
    pool, door, how, reason, callers = key
    return {"pool": pool, "door": door, "how": how, "reason": reason, "callers": list(callers),
            "count": count, "first": _when(first), "last": _when(last)}


def _when(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds")


def _ident(item: dict[str, Any]) -> _Key:
    return (str(item.get("pool") or ""), str(item.get("door") or ""), str(item.get("how") or ""),
            str(item.get("reason") or ""), tuple(str(c) for c in item.get("callers") or ()))


def report_path() -> Path:
    """``<data dir>/space_doors.json``. Asking makes no folder."""
    from kazma_core.paths import data_dir

    return Path(data_dir(create=False)) / REPORT_FILE


def read_report(path: Path | str | None = None) -> dict[str, Any]:
    """The report as it is kept; ``{}`` when there is none yet. Blocking.

    ``ValueError`` when the file is there and is not a report."""
    target = Path(path) if path is not None else report_path()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ValueError(f"{target} is not a door report")
    return data


def _kept(target: Path) -> dict[str, Any]:
    """The report to add to: an unreadable one is said and started again."""
    try:
        return read_report(target)
    except (OSError, ValueError):
        logger.warning("[space-doors] the door report at %s cannot be read; a new one is started in its place",
                       target, exc_info=True)
        return {}


# ── keeping it ──────────────────────────────────────────────────────────


def start(path: Path | str | None = None) -> None:
    """At the server's start: read the switch, and the callers with no door
    the report already holds, so they are not said again. Blocking (reads the
    report): from a coroutine, through ``asyncio.to_thread``."""
    global _on
    _on = _switched_on()
    if not _on:
        logger.info("[space-doors] the door report is off (%s=0)", _ENV)
        return
    target = Path(path) if path is not None else report_path()
    known = [_ident(item) for item in _kept(target).get("entries", ()) if isinstance(item, dict)
             and item.get("door") == "none"]
    with _lock:
        _said.update(known)


def flush(path: Path | str | None = None) -> int:
    """Add what this process counted since its last flush to the report.

    How many entries were added or changed; 0 when there was nothing new
    (nothing is written then). ``OSError`` when the report cannot be written:
    nothing is marked as kept, and the next flush tries again. Blocking (a
    small file): from a coroutine, through ``asyncio.to_thread``.
    """
    global _dropped_flushed, _failures_flushed
    target = Path(path) if path is not None else report_path()
    with _flush_lock:
        with _lock:
            deltas = [(key, entry.count - entry.flushed, entry.first, entry.last)
                      for key, entry in _entries.items() if entry.count > entry.flushed]
            dropped, failures = _dropped - _dropped_flushed, _failures - _failures_flushed
        if not deltas and not dropped and not failures:
            return 0
        data = _kept(target)
        items: dict[_Key, dict[str, Any]] = {}
        for item in data.get("entries", ()):
            if isinstance(item, dict):
                items[_ident(item)] = item
        lost = 0
        for key, count, first, last in deltas:
            item = items.get(key)
            if item is None:
                if len(items) >= MAX_FILE_ENTRIES:
                    lost += count
                    continue
                items[key] = _as_item(key, count, first, last)
                continue
            item["count"] = int(item.get("count") or 0) + count
            item["first"] = min(str(item.get("first") or _when(first)), _when(first))
            item["last"] = max(str(item.get("last") or ""), _when(last))
        now = _when(time.time())
        kept = sorted(items.values(), key=lambda i: (("none", "install", "space").index(i["door"])
                                                    if i.get("door") in ("none", "install", "space") else 3,
                                                    -int(i.get("count") or 0), i.get("callers") or []))
        report = {
            "about": ("Spaces S4-01 step 1, report mode: which door each checkout of Kazma's own Postgres "
                      "went through, by pool and caller. Nothing was refused."),
            "version": 1,
            "since": str(data.get("since") or now),
            "updated": now,
            "checkouts": int(data.get("checkouts") or 0) + sum(d[1] for d in deltas) + dropped,
            "dropped": int(data.get("dropped") or 0) + dropped + lost,
            "failures": int(data.get("failures") or 0) + failures,
            "entries": kept,
        }
        from kazma_core.atomic_files import atomic_write_text

        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, json.dumps(report, ensure_ascii=False, indent=1) + "\n")
        with _lock:
            for key, count, _first, _last in deltas:
                entry = _entries.get(key)
                if entry is not None:  # gone only if reset() ran meanwhile
                    entry.flushed += count
            _dropped_flushed += dropped
            _failures_flushed += failures
    return len(deltas)


def reset() -> None:
    """Forget what this process counted, and read the switch again (tests)."""
    global _on, _dropped, _dropped_flushed, _failures, _failures_flushed, _full_said, _failure_said
    with _lock:
        _entries.clear()
        _said.clear()
        _dropped = _dropped_flushed = _failures = _failures_flushed = 0
        _full_said = _failure_said = False
        _on = _switched_on()
