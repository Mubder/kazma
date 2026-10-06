"""Active/passive ownership: local volume lock plus a dedicated Postgres session.

The database serializes owners and binds the deployment to its state volume.
Storage fencing is still required: a session lock cannot fence a partitioned
host's SQLite writes or an external tool already in flight.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from kazma_core.runtime_writer import RuntimeWriterBusy, RuntimeWriterLease

logger = logging.getLogger(__name__)
_LOCK_NAMESPACE = 1262570829
_LOCK_SLOT = 1
_OWNERSHIP_HEALTH_TIMEOUT_SECONDS = 8.0


def _stop_on_ownership_loss() -> None:
    """Stop every worker, even if the asyncio loop cannot make progress."""
    logger.critical("Runtime database ownership lost; exiting for fenced recovery")
    os._exit(75)


def _volume_identity(root: Path) -> str:
    """Create once under the volume lock; incomplete/corrupt identities refuse boot."""
    path = root / ".runtime-state-id"
    if not path.exists():
        with path.open("x", encoding="ascii", newline="") as stream:
            stream.write(str(uuid.uuid4()))
            stream.flush()
            os.fsync(stream.fileno())
    try:
        return str(uuid.UUID(path.read_text(encoding="ascii").strip()))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeWriterBusy("Invalid runtime state identity; restore the authoritative state volume") from exc


class _PostgresRuntimeLease:
    """One runtime per database, with no TTL takeover or automatic reacquisition.

    Use a direct PostgreSQL endpoint or session pooling. Transaction-pooling
    proxies cannot preserve the advisory lock's session ownership.
    """

    def __init__(self, dsn: str, root: Path, *, on_loss: Callable[[], None] = _stop_on_ownership_loss) -> None:
        self._dsn = dsn
        self.root = root
        self._on_loss = on_loss
        self._conn: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None
        self._lost = threading.Event()
        self._health_lock = threading.Lock()
        self._health_deadline = 0.0

    def acquire(self) -> None:
        """Acquire before constructing any runtime services or opening their stores."""
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeWriterBusy("HA ownership requires the Postgres driver; install kazma[postgres]") from exc

        if self._conn is not None:
            raise RuntimeWriterBusy("Runtime ownership is already acquired")
        conn = None
        admitted = False
        try:
            conn = psycopg.connect(
                self._dsn, autocommit=True, connect_timeout=5,
                options="-c statement_timeout=3000 -c lock_timeout=3000",
                keepalives=1, keepalives_idle=2, keepalives_interval=1,
                keepalives_count=3, tcp_user_timeout=5000,
            )
            owned = conn.execute("SELECT pg_try_advisory_lock(%s, %s)", (_LOCK_NAMESPACE, _LOCK_SLOT)).fetchone()
            if not owned or not owned[0]:
                raise RuntimeWriterBusy("Another Kazma runtime owns this Postgres database; wait for fenced failover")
            identity = _volume_identity(self.root)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS kazma_runtime_volume "
                "(singleton boolean PRIMARY KEY CHECK (singleton), state_id text NOT NULL)"
            )
            conn.execute(
                "INSERT INTO kazma_runtime_volume (singleton, state_id) VALUES (true, %s) "
                "ON CONFLICT (singleton) DO NOTHING", (identity,),
            )
            row = conn.execute("SELECT state_id FROM kazma_runtime_volume WHERE singleton = true").fetchone()
            if not row or row[0] != identity:
                raise RuntimeWriterBusy("This database belongs to a different state volume; restore its complete paired volume")
            admitted = True
        except (psycopg.Error, OSError, ValueError) as exc:
            # Driver exceptions can include credentials or connection URLs.
            raise RuntimeWriterBusy(f"Cannot establish runtime database ownership ({type(exc).__name__})") from None
        finally:
            if not admitted and conn is not None:
                conn.close()
        self._conn = conn
        self._stop.clear()
        self._lost.clear()
        with self._health_lock:
            self._health_deadline = time.monotonic() + _OWNERSHIP_HEALTH_TIMEOUT_SECONDS
        self._thread = threading.Thread(target=self._monitor, name="kazma-runtime-owner", daemon=True)
        self._watchdog = threading.Thread(target=self._watch_health, name="kazma-runtime-owner-deadline", daemon=True)
        try:
            # Start the independent deadline first. A local proxy can ACK TCP
            # while libpq's query blocks forever on its upstream connection.
            self._watchdog.start()
            self._thread.start()
        except RuntimeError:
            self._stop.set()
            if self._watchdog.is_alive():
                self._watchdog.join(timeout=10)
            self._watchdog = None
            self._thread = None
            conn.close()
            self._conn = None
            raise

    def _notify_loss(self, *, expired_only: bool = False) -> None:
        """Only one monitor may fail-stop; shutdown does not authorize reacquisition."""
        with self._health_lock:
            if self._stop.is_set() or self._lost.is_set():
                return
            if expired_only and time.monotonic() < self._health_deadline:
                return
            self._lost.set()
        self._on_loss()

    def _watch_health(self) -> None:
        """Bound a stalled ownership query without touching its native connection."""
        while not self._stop.wait(min(1.0, _OWNERSHIP_HEALTH_TIMEOUT_SECONDS / 4)):
            with self._health_lock:
                expired = time.monotonic() >= self._health_deadline
            if expired:
                self._notify_loss(expired_only=True)
                if self._lost.is_set():
                    return

    def _monitor(self) -> None:
        """Never reconnect: a new connection cannot prove uninterrupted ownership."""
        import psycopg

        while not self._stop.wait(1):
            try:
                self._conn.execute("SELECT 1").fetchone()
            except psycopg.Error:
                self._notify_loss()
                return
            with self._health_lock:
                # Once proof expired, a late answer must never revive ownership.
                if self._lost.is_set() or self._stop.is_set():
                    return
                if time.monotonic() >= self._health_deadline:
                    expired = True
                else:
                    self._health_deadline = time.monotonic() + _OWNERSHIP_HEALTH_TIMEOUT_SECONDS
                    expired = False
            if expired:
                self._notify_loss(expired_only=True)
                return

    def release(self) -> None:
        """Called only after all runtime services and stores have stopped."""
        self._stop.set()
        if self._watchdog is not None:
            self._watchdog.join(timeout=10)
            if self._watchdog.is_alive():
                raise RuntimeWriterBusy("Ownership deadline did not stop; retain the volume fence until process exit")
            self._watchdog = None
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                raise RuntimeWriterBusy("Ownership monitor did not stop; retain the volume fence until process exit")
            self._thread = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class RuntimeOwnership:
    """Hold the local fence through database acquisition and complete teardown."""

    def __init__(self, root: Path) -> None:
        self._local = RuntimeWriterLease(root)
        self._distributed: _PostgresRuntimeLease | None = None
        self._held = False

    def acquire(self) -> None:
        if self._held:
            return
        self._local.acquire()
        admitted = False
        try:
            if os.environ.get("KAZMA_RUNTIME_HA", "0") == "1":
                from kazma_core.db.backend import get_database_url, is_postgres

                dsn = get_database_url()
                if not is_postgres() or not dsn:
                    raise RuntimeWriterBusy("KAZMA_RUNTIME_HA=1 requires a direct Postgres database and a fenced state volume")
                self._distributed = _PostgresRuntimeLease(dsn, self._local.root)
                self._distributed.acquire()
            admitted = True
            self._held = True
        finally:
            if not admitted:
                self._local.release()

    def release(self) -> None:
        if self._distributed is not None:
            self._distributed.release()
            self._distributed = None
        self._local.release()
        self._held = False
