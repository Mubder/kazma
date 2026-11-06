"""One answer, for the whole process, to "is Kazma's database answering?"

Live 2026-11-03/04: Docker Desktop stopped and the database went with it for
eleven hours. Every part of Kazma that reaches Postgres found that out for
itself, on its own schedule, and said so in its own words: 6,192 lines of
"error connecting in" from the pool's workers, 3,080 "attempt N/N failed",
594 each of "Postgres init failed", "ConfigStore init attempt" and
"initialization failed", 36 failed passes of each run kind, the document
intake scan, the acquisition monitor (and two of its pages), the lifecycle
record, the X mentions loop. The one fact -- the database is away -- was
buried under ten thousand lines about its consequences.

This module holds that fact once (plan A3):

* every Postgres user that sees an outage-class failure calls
  :func:`note_failure`; a success calls :func:`note_success`;
* the first failure starts one prober thread that signs in every
  :data:`PROBE_EVERY_S` seconds. A refusal confirms the outage: ONE WARNING
  naming the cause. A sign-in that gets in ends it: ONE INFO with how long it
  lasted (a blip the first probe already finds answering is DEBUG);
* after :data:`PAGE_AFTER_S` away, ONE ``database.away`` page; when it comes
  back, ONE ``database.back`` page -- only if the away page reached someone;
* background loops wait through :func:`wait_while_away` (or ask
  :func:`is_away` and skip their pass) quietly
  (``tests/test_database_availability.py`` holds every loop to it);
* while away, the pool's own reconnect chatter is dropped
  (:func:`_install_log_filter`), and the shared pool is not rebuilt per caller
  (``postgres_pool.get_postgres_pool`` refuses at once);
* ``/health/ready`` and the guard read :func:`snapshot`.

Only Postgres failures count: a locked SQLite file or a cancelled statement
says nothing about whether the database server answers (:func:`is_outage`).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: Seconds away before the owner is paged. A database restart (an update, a
#: container recreated) is over well inside it.
PAGE_AFTER_S = 120.0

#: Seconds between sign-in probes while the database is away.
PROBE_EVERY_S = 15.0

#: A prober that has not finished a probe for this long is taken for dead
#: and replaced (a sign-in waits at most 5 s per address). Nothing else ends
#: an outage while background work skips and the shared pool refuses, so a
#: lost prober would hold Kazma "away" with the database long back.
PROBER_STALE_S = 3 * PROBE_EVERY_S + 30

#: SQLSTATEs that mean the server is not there for us, beyond class 08
#: (connection exception) and 28 (invalid authorization): an administrator
#: shutdown, a crash shutdown, the server still starting, too many
#: connections, and a database that does not exist.
_OUTAGE_SQLSTATES = frozenset({"57P01", "57P02", "57P03", "53300", "3D000"})
_OUTAGE_CLASSES = ("08", "28")

#: The pool's own lines while it cannot connect (``psycopg_pool.pool`` and
#: ``pool_async``: the reconnect loop, its give-up, a failed background task
#: and the connections it throws away). Dropped while the database is away.
_POOL_CHATTER = (
    "error connecting in",
    "reconnection attempt in pool",
    "task run ",
    "discarding broken connection",
    "discarding closed connection",
)

_clock: Callable[[], float] = time.monotonic
_wall: Callable[[], float] = time.time
_lock = threading.Lock()


class _State:
    """Everything this module knows, guarded by ``_lock``."""

    def __init__(self) -> None:
        self.away_since: float | None = None
        self.away_wall: float | None = None
        self.cause = ""
        self.reason: str | None = None
        self.confirmed = False
        self.kinds: set[str] = set()
        self.page_requested = False
        self.page_delivered = False
        self.outage_id = 0
        self.last_probe: float | None = None
        self.prober_gen = 0
        self.wake = threading.Event()
        self.last_outage_id = 0
        self.last_outage_s: float | None = None
        self.last_back_wall: float | None = None
        self.last_paged = False


_state = _State()


# ── classification ───────────────────────────────────────────────────────


def _outage_one(exc: BaseException) -> bool:
    cls = type(exc)
    if cls.__name__ == "PostgresUnreachable":
        return True
    if cls.__name__ == "ConfigStoreUnavailableError":
        # The settings store's own word for an outage (a RuntimeError, not
        # psycopg's): its database is away. On SQLite it is a local file's
        # trouble, never the server's.
        return getattr(exc, "backend", "") == "postgres"
    module = cls.__module__ or ""
    if not module.startswith(("psycopg", "psycopg_pool")):
        return False
    if cls.__name__ in ("PoolTimeout", "TooManyRequests"):
        return True
    if cls.__name__ == "PoolClosed":
        return False  # Kazma closed it; the server says nothing by that
    names = {base.__name__ for base in cls.__mro__}
    if not names & {"OperationalError", "InterfaceError"}:
        return False
    sqlstate = getattr(exc, "sqlstate", None)
    if not sqlstate:
        return True  # no answer from a server at all
    return sqlstate[:2] in _OUTAGE_CLASSES or sqlstate in _OUTAGE_SQLSTATES


def is_outage(exc: BaseException | None) -> bool:
    """True when *exc*, or an exception it was raised from, says the
    Postgres server is not answering Kazma.

    A query cancelled at its timeout (57014), a lock not available (55P03), a
    serialization rollback (40xxx) or any SQLite error is not an outage: the
    server answered.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if _outage_one(exc):
            return True
        exc = exc.__cause__
    return False


def _server_of(dsn: str) -> tuple[str, str] | None:
    """(host, port) a DSN signs in to, or None when it cannot be read."""
    try:
        from psycopg import Error as PsycopgError
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        return None
    try:
        info = conninfo_to_dict(dsn)
    except (PsycopgError, ValueError, TypeError):  # unreadable: not ours
        return None
    host = str(info.get("host") or "localhost").split(",")[0].strip().lower()
    if host in ("127.0.0.1", "::1"):
        host = "localhost"
    return host, str(info.get("port") or "5432").split(",")[0].strip()


def is_kazma_database(dsn: str | None) -> bool:
    """True when *dsn* reaches the server Kazma's own database is on.

    A memory mirror on another server is its own outage, never Kazma's.
    """
    if not dsn:
        return False
    from kazma_core.db.backend import get_database_url

    ours = get_database_url()
    if not ours:
        return False
    for prefix in ("postgres://",):
        if dsn.startswith(prefix):
            dsn = "postgresql://" + dsn[len(prefix):]
        if ours.startswith(prefix):
            ours = "postgresql://" + ours[len(prefix):]
    a, b = _server_of(dsn), _server_of(ours)
    return a is not None and a == b


def _describe(exc: BaseException | None) -> str:
    if exc is None:
        return ""
    from kazma_core.security.url_credentials import mask_urls_in_text

    text = " ".join(str(exc).split())[:200]
    return f"{type(exc).__name__}: {mask_urls_in_text(text)}" if text else type(exc).__name__


# ── what callers report ──────────────────────────────────────────────────


def now() -> float:
    """This module's clock: pass it to :func:`note_success` as ``since``."""
    return _clock()


def note_failure(kind: str, exc: BaseException | None = None, *, reason: str | None = None) -> bool:
    """Report a failure to reach Postgres. True when it counts as an outage.

    *kind* names who noticed (``pool``, ``settings``...), shown in the
    WARNING. *reason* is Postgres's own answer in words
    (``postgres_pool.refusal_reason``), when the caller read one.
    """
    if reason is None and not is_outage(exc):
        return False
    start = 0
    gen = 0
    with _lock:
        s = _state
        s.kinds.add(kind)
        if reason:
            s.reason = reason
            s.cause = reason
        elif not s.cause:
            s.cause = _describe(exc)
        if s.away_since is None:
            s.outage_id += 1
            s.away_since = _clock()
            s.away_wall = _wall()
            s.confirmed = False
            s.page_requested = False
            s.page_delivered = False
            s.wake = threading.Event()
            s.last_probe = s.away_since
            s.prober_gen += 1
            start, gen = s.outage_id, s.prober_gen
    if start:
        _spawn_prober(start, gen)
    return True


def note_success(kind: str, *, since: float | None = None) -> None:
    """Report that Postgres answered.

    *since* is :func:`now` when the operation began: a statement that
    started before the outage and finished during it says nothing about the
    database now, and is ignored.
    """
    if _state.away_since is None:  # the common case, without the lock
        return
    with _lock:
        away_since = _state.away_since
        if away_since is None or (since is not None and since < away_since):
            return
        ended = _mark_back_locked(via=kind)
    _after_back(ended)


def is_away() -> bool:
    """True while Kazma's database is not answering. Background work skips
    its pass quietly; the next pass after it is back runs as usual.

    A prober that stopped probing is replaced here: the outage it was
    watching would otherwise never end.
    """
    if _state.away_since is None:
        return False
    respawn = None
    with _lock:
        s = _state
        if s.away_since is None:
            return False
        if _clock() - (s.last_probe or s.away_since) > PROBER_STALE_S:
            s.last_probe = _clock()  # one replacement per stale period
            s.prober_gen += 1
            respawn = (s.outage_id, s.prober_gen)
    if respawn is not None:
        logger.debug("[database] availability prober was stale; starting another")
        _spawn_prober(*respawn)
    return True


async def wait_while_away(*, step_s: float = PROBE_EVERY_S) -> bool:
    """Wait while Kazma's database is away. False the moment it answers (at
    once when it already does); True when the process began to stop
    meanwhile -- the caller's loop then ends.

    A background loop calls it after its sleep and before its work, so a pass
    that would only fail (and log) waits instead, and the work it was for --
    a backup, a sweep -- runs whole once the database is back.
    """
    if not is_away():
        return False
    from kazma_core.shutdown import sleep_or_shutdown  # lazy: no import cycle

    while is_away():
        if await sleep_or_shutdown(step_s):
            return True
    return False


def cached_reason() -> str | None:
    """Postgres's own answer, in words, while it is away (or None)."""
    with _lock:
        return _state.reason if _state.away_since is not None else None


def snapshot() -> dict[str, Any]:
    """The state, for ``/health/ready`` and the guard."""
    with _lock:
        s = _state
        if s.away_since is None:
            out: dict[str, Any] = {"state": "answering"}
            if s.last_outage_s is not None:
                out["last_outage_s"] = round(s.last_outage_s, 1)
                out["back_at"] = s.last_back_wall
            return out
        now_ = _clock()
        return {
            "state": "away",
            "away_s": round(max(0.0, now_ - s.away_since), 1),
            "probe_age_s": round(max(0.0, now_ - (s.last_probe or s.away_since)), 1),
            "probe_stale_after_s": PROBER_STALE_S,
            "since": s.away_wall,
            "cause": s.cause,
            "confirmed": s.confirmed,
            "noticed_by": sorted(s.kinds),
            "page_at_s": PAGE_AFTER_S,
            "paged": s.page_delivered,
            "page_requested": s.page_requested,
        }


def reset() -> None:
    """Forget everything (tests; a fresh process starts here anyway)."""
    global _state
    with _lock:
        old = _state
        _state = _State()
        _state.outage_id = old.outage_id + 1
        _state.last_outage_id = old.outage_id + 1
    old.wake.set()


# ── the prober ───────────────────────────────────────────────────────────


def _sign_in() -> tuple[bool | None, str | None]:
    """One direct sign-in: (True, None) in, (False, reason) refused,
    (None, None) when there is nothing to sign in to."""
    from kazma_core.db.backend import get_database_url
    from kazma_core.db.postgres_pool import refusal_reason

    dsn = get_database_url()
    if not dsn:
        return None, None
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]
    try:
        from kazma_core.db.backend import require_postgres_driver

        require_postgres_driver()
    except ImportError:
        return None, None
    reason = refusal_reason(dsn)
    return (reason is None), reason


#: Replaced in tests: a real sign-in reaches the network.
_probe: Callable[[], tuple[bool | None, str | None]] = _sign_in


def _spawn_prober(outage_id: int, gen: int) -> None:
    thread = threading.Thread(
        target=_probe_loop, args=(outage_id, gen), name="kazma-db-availability", daemon=True,
    )
    thread.start()


def _probe_loop(outage_id: int, gen: int) -> None:
    while True:
        with _lock:
            if (_state.outage_id != outage_id or _state.away_since is None
                    or _state.prober_gen != gen):
                return
            wake = _state.wake
        try:
            _probe_once(outage_id)
        except Exception:  # noqa: BLE001 -- the prober must outlive any one probe
            logger.debug("[database] availability probe failed", exc_info=True)
        if wake.wait(PROBE_EVERY_S):
            return
        try:
            from kazma_core.shutdown import is_shutting_down

            if is_shutting_down():
                return
        except ImportError:
            pass


def _probe_once(outage_id: int | None = None) -> None:
    """One probe of the current outage (the prober's step; tests call it)."""
    with _lock:
        if _state.away_since is None:
            return
        if outage_id is not None and outage_id != _state.outage_id:
            return
        outage_id = _state.outage_id
    answers, reason = _probe()
    if answers or answers is None:
        # In, or nothing to sign in to (no Postgres configured): either way
        # there is nothing to wait for.
        with _lock:
            if _state.outage_id != outage_id or _state.away_since is None:
                return
            ended = _mark_back_locked(via="probe" if answers else "no database configured")
        _after_back(ended)
        return
    announce: tuple[str, list[str]] | None = None
    with _lock:
        s = _state
        if s.outage_id != outage_id or s.away_since is None:
            return
        s.last_probe = _clock()
        if reason:
            s.reason = reason
            s.cause = reason
        if not s.confirmed:
            s.confirmed = True
            announce = (s.cause or "no answer", sorted(s.kinds))
    if announce is not None:
        logger.warning(
            "[database] Postgres is not answering: %s. Background work waits for it; "
            "Kazma keeps checking every %d s (noticed by: %s)",
            announce[0], int(PROBE_EVERY_S), ", ".join(announce[1]) or "a probe",
        )
    _maybe_page(outage_id)


def _maybe_page(outage_id: int) -> None:
    with _lock:
        s = _state
        if (s.outage_id != outage_id or s.away_since is None or not s.confirmed
                or s.page_requested or _clock() - s.away_since < PAGE_AFTER_S):
            return
        s.page_requested = True
        cause = s.cause or "no answer"
        since = s.away_wall
    when = time.strftime("%H:%M UTC", time.gmtime(since)) if since else "a while"
    detail = (
        f"Not answering since {when}: {cause}. Chats that need history, settings saves, "
        "backups and background work wait for it; Kazma retries every "
        f"{int(PROBE_EVERY_S)} s and will say when it is back. If Docker Desktop is "
        "stopped, start it."
    )
    _alert("database.away", "Kazma's database is not answering", detail,
           severity="error", on_delivery=lambda ok: _away_delivered(outage_id, ok))


def _away_delivered(outage_id: int, ok: bool | None) -> None:
    if not ok:
        return
    send_back = None
    with _lock:
        s = _state
        if s.outage_id == outage_id and s.away_since is not None:
            s.page_delivered = True
        elif s.last_outage_id == outage_id and not s.last_paged:
            # Back before the away page was confirmed: say so now.
            s.last_paged = True
            send_back = s.last_outage_s
    if send_back is not None:
        _page_back(send_back)


def _mark_back_locked(*, via: str) -> tuple[int, float, bool, bool] | None:
    """Close the current outage (``_lock`` held). Returns what to announce."""
    s = _state
    if s.away_since is None:
        return None
    duration = max(0.0, _clock() - s.away_since)
    ended = (s.outage_id, duration, s.confirmed, s.page_delivered)
    s.last_outage_id = s.outage_id
    s.last_outage_s = duration
    s.last_back_wall = _wall()
    s.last_paged = s.page_delivered
    s.away_since = None
    s.away_wall = None
    s.last_probe = None
    s.cause = ""
    s.reason = None
    s.confirmed = False
    s.kinds = set()
    s.page_requested = False
    s.page_delivered = False
    s.wake.set()
    logger.debug("[database] back (noticed by %s)", via)
    return ended


def _after_back(ended: tuple[int, float, bool, bool] | None) -> None:
    if ended is None:
        return
    _outage_id, duration, confirmed, paged = ended
    if confirmed:
        logger.info("[database] Postgres answers again after %s", _duration(duration))
    else:
        logger.debug("[database] Postgres answered again after %s", _duration(duration))
    if paged:
        _page_back(duration)


def _page_back(duration: float) -> None:
    _alert("database.back", "Kazma's database answers again",
           f"Back after {_duration(duration)}. Waiting work runs on its next pass.",
           severity="success")


def _alert(key: str, title: str, detail: str, **kw: Any) -> None:
    from kazma_core.observability import ops_alerts

    ops_alerts.alert(key, title, detail, **kw)  # never raises


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {sec} s" if sec else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


# ── the pool's own log lines ─────────────────────────────────────────────


class _PoolChatterFilter(logging.Filter):
    """Drops the pool's reconnect lines while the database is away."""

    def filter(self, record: logging.LogRecord) -> bool:
        if _state.away_since is None:
            return True
        msg = record.msg if isinstance(record.msg, str) else ""
        return not msg.startswith(_POOL_CHATTER)


_FILTER = _PoolChatterFilter()


def _install_log_filter() -> None:
    """Attach the filter to psycopg's pool logger (idempotent)."""
    pool_logger = logging.getLogger("psycopg.pool")
    if _FILTER not in pool_logger.filters:
        pool_logger.addFilter(_FILTER)


_install_log_filter()
