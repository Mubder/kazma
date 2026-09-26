"""Who else boots against this Postgres settings store.

A Postgres-backed ConfigStore is shared by every process whose
``KAZMA_DATABASE_URL`` names the same database. ``ConfigStore`` already warns
when ``KAZMA_DATA_DIR`` is relocated on Postgres, but that check reads one
install's environment, and the incident behind it was a SECOND CHECKOUT that
relocated nothing: the dev clone shared the operator's live store for days,
``kazma doctor`` answered differently on the two boxes, and a ``vault://``
pointer written by one machine resolved to nothing on the other — the
2026-09-16 outage. A heuristic on this process cannot see another process.
The store can: every server boot records itself here and reads who else did.

Identity is a random id kept in the install's OWN data dir
(``<data_dir>/install_id``), so a restart or a redeploy with a persisted data
dir keeps it, and a second checkout, a second box or a scratch data dir gets
its own. Records live under ``system.installs.<id>`` (category
``system.installs``). Installs booted within :data:`PEER_WINDOW_DAYS` are named
in one line per boot; records older than :data:`PRUNE_AFTER_DAYS` are deleted
so a one-off script stops being mentioned. Replicas that share the store on
purpose are listed by id under :data:`ACK_KEY`; they are then reported at INFO
and only an unknown install is a WARNING — a new sharer still gets named.

SQLite installs never run this: their settings live in their own data dir.
Expected store and filesystem errors are logged and absorbed; nothing here
may fail a boot.
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ACK_KEY",
    "CATEGORY",
    "PEER_WINDOW_DAYS",
    "PRUNE_AFTER_DAYS",
    "InstallRecord",
    "check_shared_store_peers",
    "install_id",
    "recent_peers",
    "split_acknowledged",
    "store_errors",
]

PEER_WINDOW_DAYS = 14
PRUNE_AFTER_DAYS = 90
CATEGORY = "system.installs"
ACK_KEY = "database.shared_store.acknowledged_peers"

_KEY_PREFIX = CATEGORY + "."
_ID_FILE = "install_id"
_DAY = 86400.0
_process_fallback_id: str | None = None


@dataclass(frozen=True)
class InstallRecord:
    install_id: str
    host: str
    data_dir: str
    booted_at: float

    def describe(self, now: float | None = None) -> str:
        age = max(0.0, ((time.time() if now is None else now) - self.booted_at) / 3600)
        when = f"{age:.0f}h ago" if age < 48 else f"{age / 24:.0f}d ago"
        return f"{self.install_id[:8]} on {self.host} (data dir {self.data_dir}, booted {when})"


def store_errors() -> tuple[type[BaseException], ...]:
    """The failures a settings round trip can legitimately raise
    (:func:`kazma_core.db.pg_helpers.store_errors`, the one list)."""
    from kazma_core.db.pg_helpers import store_errors as _store_errors

    return _store_errors()


def _is_id(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def _fallback_id() -> str:
    """An id for this process only, when the data dir cannot hold one."""
    global _process_fallback_id
    if _process_fallback_id is None:
        _process_fallback_id = uuid.uuid4().hex
    return _process_fallback_id


def install_id(data_dir: Path | str | None = None, *, create: bool = True) -> str | None:
    """This install's stable id, kept in its own data dir.

    With ``create=False`` (``kazma doctor``, which writes nothing) a missing
    file returns ``None`` instead of minting one.
    """
    if data_dir is None:
        from kazma_core.paths import data_dir as _data_dir

        data_dir = _data_dir()
    path = Path(data_dir) / _ID_FILE
    try:
        current = path.read_text(encoding="utf-8").strip()
        exists = True
    except FileNotFoundError:
        current, exists = "", False
    except OSError:
        logger.debug("[SharedStore] install id unreadable at %s", path, exc_info=True)
        return _fallback_id() if create else None
    if _is_id(current):
        return current
    if not create:
        return None
    new = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not exists:
            try:
                # Exclusive create: two first boots converge on one id.
                with open(path, "x", encoding="utf-8") as fh:
                    fh.write(new + "\n")
                return new
            except FileExistsError:
                theirs = path.read_text(encoding="utf-8").strip()
                if _is_id(theirs):
                    return theirs
        # Present but not an id (truncated, hand-edited): replace atomically.
        tmp = path.with_name(f".{_ID_FILE}.{new}.tmp")
        tmp.write_text(new + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return new
    except OSError:
        logger.warning("[SharedStore] cannot persist an install id under %s", path.parent, exc_info=True)
        return _fallback_id()


def _hostname() -> str:
    try:
        return socket.gethostname() or "?"
    except OSError:
        return "?"


def _records(store: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, rec in (store.get_category(CATEGORY) or {}).items():
        if isinstance(key, str) and key.startswith(_KEY_PREFIX) and isinstance(rec, dict):
            out[key[len(_KEY_PREFIX):]] = rec
    return out


def _booted_at(rec: dict[str, Any]) -> float:
    try:
        return float(rec.get("booted_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def recent_peers(store: Any, me: str | None, *, now: float | None = None) -> list[InstallRecord]:
    """Other installs booted against *store* within the window. Read-only."""
    now = time.time() if now is None else now
    peers = [
        InstallRecord(iid, str(rec.get("host") or "?"), str(rec.get("data_dir") or "?"), _booted_at(rec))
        for iid, rec in _records(store).items()
        if iid != me and (now - _booted_at(rec)) <= PEER_WINDOW_DAYS * _DAY
    ]
    return sorted(peers, key=lambda r: r.booted_at, reverse=True)


def _announce_and_find_peers(
    store: Any, *, me: str, data_dir: str, now: float | None = None,
) -> list[InstallRecord]:
    """Record this install in *store*, prune dead records, return recent peers."""
    now = time.time() if now is None else now
    store.set(
        _KEY_PREFIX + me,
        {"host": _hostname(), "data_dir": data_dir, "booted_at": now},
        category=CATEGORY,
    )
    for iid, rec in _records(store).items():
        if iid != me and (now - _booted_at(rec)) > PRUNE_AFTER_DAYS * _DAY:
            store.delete(_KEY_PREFIX + iid)
    return recent_peers(store, me, now=now)


def _acknowledged(store: Any) -> list[str]:
    raw = store.get(ACK_KEY) or []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(",")]
    # An 8-character prefix is what the boot line prints; shorter is too
    # ambiguous to silence anything.
    return [str(p).strip().lower() for p in raw if len(str(p).strip()) >= 8]


def split_acknowledged(
    store: Any, peers: list[InstallRecord],
) -> tuple[list[InstallRecord], list[InstallRecord]]:
    """``(acknowledged, unknown)`` — the operator lists replicas under ACK_KEY."""
    acks = _acknowledged(store)
    known = [p for p in peers if any(p.install_id.startswith(a) for a in acks)]
    return known, [p for p in peers if p not in known]


def _report(store: Any, me: str, peers: list[InstallRecord], now: float) -> None:
    if not peers:
        logger.info(
            "[SharedStore] install %s is the only one booted against this Postgres "
            "settings store in the last %d days", me[:8], PEER_WINDOW_DAYS,
        )
        return
    known, unknown = split_acknowledged(store, peers)
    if known:
        logger.info(
            "[SharedStore] shares the Postgres settings store with %d acknowledged "
            "install(s): %s", len(known), "; ".join(p.describe(now) for p in known),
        )
    if unknown:
        logger.warning(
            "[SharedStore] this Postgres settings store is ALSO used by %d other "
            "install(s) booted in the last %d days: %s. Every setting and every "
            "vault:// pointer one of them writes is read by all of them — a key "
            "saved on one machine resolved to nothing on another this way "
            "(2026-09-16). If that is not intended, give this install its own "
            "KAZMA_DATABASE_URL, or KAZMA_DB_BACKEND=sqlite. If it is (replicas), "
            "add their ids to the setting %s. This install is %s.",
            len(unknown), PEER_WINDOW_DAYS,
            "; ".join(p.describe(now) for p in unknown), ACK_KEY, me[:8],
        )


def _backend_is_postgres() -> bool:
    from kazma_core.db.backend import is_postgres

    return is_postgres()


def check_shared_store_peers(store: Any | None = None) -> list[InstallRecord] | None:
    """Boot hook: on a Postgres ConfigStore, record this install and name peers.

    Returns the recent peers (``[]`` when alone), or ``None`` when the check
    did not run — SQLite backend, or an expected store/filesystem error.
    Blocking I/O: call it through ``asyncio.to_thread`` from async code.
    """
    if not _backend_is_postgres():
        return None
    try:
        if store is None:
            from kazma_core.config_store import get_config_store

            store = get_config_store()
        from kazma_core.paths import data_dir

        root = data_dir()
        me = install_id(root) or _fallback_id()
        now = time.time()
        peers = _announce_and_find_peers(store, me=me, data_dir=str(root), now=now)
        _report(store, me, peers, now)
        return peers
    except store_errors():
        logger.warning("[SharedStore] could not check who shares the settings store", exc_info=True)
        return None
