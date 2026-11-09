"""Page receipts: what Kazma paged for an incident still open.

Stage 2 of the unified alerts plan (``docs/plans/UNIFIED_ALERTS_PLAN.md``,
"One page per problem"). Three senders can see some problems -- a database
outage, a restart, a full disk -- and each paged on its own: one outage was
two or three messages. Now:

* **Kazma and its guard agree first.** A page whose catalog entry has a
  ``condition`` goes through ``alert_catalog.inside_hold``: the owner pages,
  a sender marked ``owner=False`` records it while an owner can speak, and
  of two inside owners (``SHARED_OWNERS``) the first to page it pages.
  ``held_by`` is that decision for Kazma's own alerts
  (``ops_alerts.alert`` asks it; a held alert writes
  ``[ops_alert_held] <key> | <title> (paged by <who>)``, never the
  ``[ops_alert]`` line the daily digest counts).
* **Kazma Watch knows what was paged.** Every alert Kazma sends that opens
  an incident (a recovery closes it, or another sender can see it) is a
  receipt here: its catalog entry's key (the pattern: no name known only at
  run time leaves the machine), when it was paged and whether a channel took
  it. The recovery settles it. The receipts are written, atomically, to the
  file the guard hands over in ``KAZMA_GUARD_PAGES_FILE`` (beside its state
  file); the guard adds its own and the heartbeat carries them, cut to an
  allowlist on both ends (``heartbeat_body`` in the guard, ``sanitize`` in
  the observer). Kazma Watch stays quiet about a condition the heartbeat
  says was paged and delivered.

Names and times only: no title, no detail, no text. A server no guard
started writes no file (nobody would read it) and holds nothing back.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kazma_core.observability import alert_catalog

logger = logging.getLogger(__name__)

__all__ = [
    "GUARD_FRESH_S",
    "PAGES_ENV",
    "WATCH_FRESH_S",
    "flush",
    "held_by",
    "record_page",
    "settle",
]

#: Set by the guard for the server it spawns: where the receipts are written.
PAGES_ENV = "KAZMA_GUARD_PAGES_FILE"
#: The guard's state file (supervisor_watch): its heartbeat, its own receipts
#: (``pages``) and what the outside watch last heard (``outside_watch``).
STATE_ENV = "KAZMA_GUARD_STATE_FILE"
#: The file's name beside the state file, for a guard that hands over only that.
PAGES_FILE = "kazma_pages.json"
#: Receipts kept at most (one per catalog entry; the heartbeat carries 16).
MAX_RECEIPTS = 16
#: An open receipt older than this is dropped: its recovery never came.
RECEIPT_MAX_AGE_S = 24 * 3600.0
#: The guard beats every 10 s; its receipts are its view only while fresh.
GUARD_FRESH_S = 120.0
#: Kazma Watch is watching when it answered the guard's heartbeat this
#: recently (it sends one a minute).
WATCH_FRESH_S = 180.0

#: When this process started: the guard reads only its current child's file.
_STARTED = time.time()

#: What a receipt can meet: a file that cannot be written, a writer thread
#: that cannot start, a malformed state file or catalog entry. A receipt
#: never stops the page it records (``ops_alerts.alert``'s hot path).
_FAULTS = (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError)


def _pages_path() -> Path | None:
    """Where this server writes its receipts, or None (no guard started it)."""
    env = (os.environ.get(PAGES_ENV) or "").strip()
    if env:
        return Path(env)
    state = (os.environ.get(STATE_ENV) or "").strip()
    return Path(state).with_name(PAGES_FILE) if state else None


def _write_atomic(path: Path, data: dict) -> None:
    """A reader sees the old file or the new one, never half (retried while
    a reader holds it open on Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


class _Book:
    """The open receipts of this process, and the one thread that writes them.

    ``ops_alerts.alert`` is called from exception handlers and on the event
    loop: it only changes the book under a lock and wakes the writer, which
    owns the file.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # One write at a time: the writer thread and an explicit flush.
        self._write_lock = threading.Lock()
        self._open: dict[str, dict[str, Any]] = {}
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._version = 0
        self._written = -1

    def paged(self, key: str, at: float) -> None:
        with self._lock:
            self._open[key] = {"key": key, "paged_at": int(at), "delivered": False}
            if len(self._open) > MAX_RECEIPTS:
                oldest = min(self._open.values(), key=lambda r: r["paged_at"])
                self._open.pop(oldest["key"], None)
            self._changed()

    def delivered(self, key: str, paged_at: int, ok: bool | None) -> None:
        with self._lock:
            rec = self._open.get(key)
            if rec is None or rec["paged_at"] != paged_at:
                return  # settled, or a later page of the same entry
            rec["delivered"] = ok is True
            self._changed()

    def settle(self, keys: list[str]) -> bool:
        with self._lock:
            gone = [k for k in keys if self._open.pop(k, None) is not None]
            if gone:
                self._changed()
            return bool(gone)

    def snapshot(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        with self._lock:
            for key in [k for k, r in self._open.items() if now - r["paged_at"] > RECEIPT_MAX_AGE_S]:
                self._open.pop(key, None)
            return sorted((dict(r) for r in self._open.values()), key=lambda r: r["key"])

    def clear(self) -> None:
        with self._lock:
            self._open.clear()
            self._changed()

    def _changed(self) -> None:
        """Lock held: mark the book changed and wake the writer."""
        self._version += 1
        if _pages_path() is None:
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="kazma-page-receipts", daemon=True)
            self._thread.start()
        self._wake.set()

    def _run(self) -> None:
        while True:
            self._wake.wait(30.0)
            self._wake.clear()
            try:
                self.flush()
            except _FAULTS:  # the writer must outlive a bad write
                logger.warning("[page_receipts] could not write the page receipts", exc_info=True)

    def flush(self) -> bool:
        """Write the book now if it changed. True when the file is current."""
        path = _pages_path()
        if path is None:
            return False
        with self._write_lock:
            with self._lock:
                version = self._version
                if version == self._written and path.exists():
                    return True
            pages = self.snapshot()
            _write_atomic(path, {
                "v": 1,
                "pid": os.getpid(),
                "process_started": _STARTED,
                "updated": time.time(),
                "pages": pages,
            })
            with self._lock:
                self._written = max(self._written, version)
        return True


_BOOK = _Book()


def record_page(key: str, *, now: float | None = None) -> Callable[[bool | None], None] | None:
    """Kazma has just sent the alert *key*: keep its receipt, or settle what
    it closes. Returns the delivery callback of a new receipt, else None.

    Never raises: ``ops_alerts.alert`` calls it on its hot path.
    """
    try:
        if _pages_path() is None:
            return None
        entry = alert_catalog.entry_for(key)
        if entry is None:
            return None
        closes = alert_catalog.closed_by_key(entry.key)
        if closes:
            _BOOK.settle(closes)
        if not alert_catalog.opens_incident(entry):
            return None
        at = int(time.time() if now is None else now)
        _BOOK.paged(entry.key, at)

        def _delivered(ok: bool | None, _key: str = entry.key, _at: int = at) -> None:
            _BOOK.delivered(_key, _at, ok)

        return _delivered
    except _FAULTS:
        logger.warning("[page_receipts] could not record the page of %s", key, exc_info=True)
        return None


def settle(closing_key: str) -> None:
    """Settle what *closing_key* closes without sending it: a sender whose
    page did not reach anyone sends no recovery, and its receipt must not
    outlive the incident. Never raises."""
    try:
        entry = alert_catalog.entry_for(closing_key)
        if entry is not None:
            _BOOK.settle(alert_catalog.closed_by_key(entry.key))
    except _FAULTS:
        logger.warning("[page_receipts] could not settle %s", closing_key, exc_info=True)


def _open_receipts(now: float | None = None) -> list[dict]:
    """This process's open receipts (``{"key", "paged_at", "delivered"}``)."""
    return _BOOK.snapshot(now)


def flush() -> bool:
    """Write the receipts now (tests; the writer thread does it otherwise)."""
    try:
        return _BOOK.flush()
    except OSError:
        logger.warning("[page_receipts] could not write the page receipts", exc_info=True)
        return False


def _guard_view(now: float | None = None) -> dict[str, Any] | None:
    """What the guard that started this server says, or None (no guard).

    ``fresh``: its heartbeat is recent, so it can page; ``pages``: its open
    receipts; ``watch``: Kazma Watch was answering its heartbeats up to now,
    or up to the guard's last beat when the guard is gone (Kazma Watch then
    sees the silence and pages it).
    """
    state_path = (os.environ.get(STATE_ENV) or "").strip()
    if not state_path:
        return None
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    now = time.time() if now is None else now
    beat = data.get("heartbeat")
    fresh = isinstance(beat, (int, float)) and now - float(beat) < GUARD_FRESH_S
    pages = data.get("pages") if fresh else None
    outside = data.get("outside_watch") if isinstance(data.get("outside_watch"), dict) else {}
    ack = outside.get("last_ack")
    reference = now if fresh else (float(beat) if isinstance(beat, (int, float)) else None)
    watch = (outside.get("state") == "reached" and isinstance(ack, (int, float))
             and reference is not None and reference - float(ack) <= WATCH_FRESH_S)
    return {
        "fresh": fresh,
        "pages": [p for p in pages if isinstance(p, dict)] if isinstance(pages, list) else [],
        "watch": bool(watch),
    }


def held_by(key: str, *, now: float | None = None) -> str:
    """Who pages *key*'s condition instead of Kazma; "" when Kazma pages it.

    ``alert_catalog.inside_hold`` over this process's receipts and the
    guard's, with what the guard's state says of who can speak. Never raises:
    a decision that cannot be made pages.
    """
    try:
        entry = alert_catalog.entry_for(key)
        if entry is None or not entry.condition:
            return ""
        view = _guard_view(now)
        receipts = _open_receipts(now) + (view["pages"] if view else [])
        speaks = {"ops": True, "guard": bool(view and view["fresh"]), "watch": bool(view and view["watch"])}
        return alert_catalog.inside_hold(entry.key, receipts, speaks)
    except _FAULTS:
        logger.warning("[page_receipts] could not decide who pages %s", key, exc_info=True)
        return ""


def _reset_for_tests() -> None:
    """Forget every receipt (tests)."""
    _BOOK.clear()
    with _BOOK._lock:
        _BOOK._written = -1
