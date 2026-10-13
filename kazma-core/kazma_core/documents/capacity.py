"""Backpressure, capacity, and rate limiting for document intake.

Capacity prechecks and health reporting. Durable ingestion additionally uses
``admission.IntakeAdmissionStore`` for atomic queue/rate/byte reservations:

1. **Storage free-space floor** — refuses intake with HTTP **507** when the
   store's filesystem has less free space than the configured floor.
2. **Global queue ceiling** — refuses with HTTP **503** when the durable
   backlog (non-terminal jobs across all tenants) is at the configured max.
3. **Per-tenant caps** — refuses with HTTP **429** when a tenant already has
   the configured maximum queued or active jobs (fair-share isolation).
4. **Intake rate / byte window** — refuses with HTTP **429** when a tenant
   exceeds the configured files-per-minute or bytes-per-minute sliding
   window (durable for ingestion; a bounded local fallback for standalone use).

Every refusal carries a ``retry_after`` hint. :meth:`DocumentCapacityGuard.snapshot`
returns an alert-compatible health/capacity view with machine-readable
``degraded_reasons`` for dashboards/alerting.

Committed tenant blob references are quota-checked atomically by the metadata
repository at publication. This guard covers queue/rate admission, not a
filesystem quota or aggregate temporary-file reservation.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from kazma_core.db.pg_helpers import store_errors

from .config import DocumentConfig
from .policy import DocumentPolicy, policy_operation

logger = logging.getLogger(__name__)

__all__ = [
    "CapacityError",
    "DocumentCapacityGuard",
]

_WINDOW_SECONDS = 60.0
# Cap how many tenants the in-memory limiter tracks so a flood of distinct
# tenant ids cannot grow memory without bound (LRU eviction).
_MAX_TRACKED_TENANTS = 4096


class CapacityError(RuntimeError):
    """Raised when intake must be refused for capacity/backpressure reasons.

    Carries a truthful HTTP status (409/429/503/507), a stable code, and a
    ``retry_after`` hint in seconds.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int,
        retry_after: int,
        reason: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.status = int(status)
        self.retry_after = int(retry_after)
        self.reason = reason


@dataclass(slots=True)
class _Bucket:
    events: deque[tuple[float, int]]


class _SlidingWindowLimiter:
    """Per-tenant sliding-window rate + byte limiter (in-memory, bounded)."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def _prune(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - _WINDOW_SECONDS
        events = bucket.events
        while events and events[0][0] < cutoff:
            events.popleft()

    def try_acquire(
        self, tenant_id: str, byte_size: int, *, max_files: int, max_bytes: int
    ) -> tuple[bool, int]:
        """Atomically check and, if allowed, record one intake event.

        Returns ``(allowed, retry_after_seconds)``. When denied the event is
        NOT recorded, so a rejected upload does not consume the window.
        """
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(tenant_id)
            if bucket is None:
                bucket = _Bucket(events=deque())
                self._buckets[tenant_id] = bucket
                if len(self._buckets) > _MAX_TRACKED_TENANTS:
                    self._buckets.popitem(last=False)
            else:
                self._buckets.move_to_end(tenant_id)
            self._prune(bucket, now)
            current_files = len(bucket.events)
            current_bytes = sum(size for _, size in bucket.events)
            if current_files + 1 > max_files:
                retry = self._retry_after(bucket, now)
                return False, retry
            if max_bytes > 0 and current_bytes + max(0, int(byte_size)) > max_bytes:
                retry = self._retry_after(bucket, now)
                return False, retry
            bucket.events.append((now, max(0, int(byte_size))))
            return True, 0

    def _retry_after(self, bucket: _Bucket, now: float) -> int:
        if not bucket.events:
            return 1
        oldest = bucket.events[0][0]
        # Seconds until the oldest event leaves the window.
        return max(1, int(_WINDOW_SECONDS - (now - oldest)) + 1)

    def window_usage(self, tenant_id: str) -> tuple[int, int]:
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(tenant_id)
            if bucket is None:
                return 0, 0
            self._prune(bucket, now)
            return len(bucket.events), sum(size for _, size in bucket.events)


class DocumentCapacityGuard:
    """Enforces intake backpressure and exposes a capacity/health snapshot."""

    def __init__(
        self,
        *,
        config: DocumentConfig,
        jobs: Any,
        storage_root: str | Path,
        clock: Callable[[], float] = time.monotonic,
        admissions: Any = None,
        policy: DocumentPolicy | None = None,
    ) -> None:
        self._policy = policy or DocumentPolicy(config)
        self._jobs = jobs
        self._storage_root = Path(storage_root)
        self._limiter = _SlidingWindowLimiter(clock=clock)
        self._admissions = admissions

    @property
    def _config(self) -> DocumentConfig:
        return self._policy.current

    # ── Enforcement ─────────────────────────────────────────────────────

    @policy_operation
    def check_intake(self, *, tenant_id: str, byte_size: int = 0) -> None:
        """Raise :class:`CapacityError` if this intake must be refused.

        Order: storage floor (507) → global queue (503) → per-tenant caps
        (429) → in-memory rate/byte window (429). The durable queue is
        authoritative; the rate window is the last, additional guard so a
        rejected upload never consumes a rate token.
        """
        cfg = self._config

        # 1. Storage free-space floor → 507.
        free = self._free_bytes()
        if free is None and cfg.capacity_storage_free_floor_bytes > 0:
            raise CapacityError(
                "capacity_unavailable", "Document storage capacity could not be measured; retry shortly.",
                status=503, retry_after=30, reason="storage_capacity_unavailable",
            )
        if free is not None and cfg.capacity_storage_free_floor_bytes > 0:
            # The incoming file counts: a 50 MiB upload must leave the floor
            # intact, not merely start above it (2026-10-13, F08).
            if free - max(0, int(byte_size)) < cfg.capacity_storage_free_floor_bytes:
                raise CapacityError(
                    "storage_full",
                    "Document storage is below its free-space floor; try again "
                    "after garbage collection frees space.",
                    status=507,
                    retry_after=300,
                    reason="storage_low",
                )

        # 2. Global durable queue ceiling → 503.
        try:
            stats = self._jobs.queue_stats()
            if stats.non_terminal >= cfg.capacity_max_queued_jobs:
                raise CapacityError(
                    "queue_full",
                    "The document processing queue is at capacity; retry shortly.",
                    status=503,
                    retry_after=30,
                    reason="queue_backpressure",
                )
        except CapacityError:
            raise
        except store_errors() as exc:  # noqa: BLE001 - unavailable authority refuses intake
            logger.debug("[documents.capacity] global queue stats failed", exc_info=True)
            raise CapacityError(
                "capacity_unavailable", "Document queue capacity is unavailable; retry shortly.",
                status=503, retry_after=30, reason="queue_capacity_unavailable",
            ) from exc

        # 3. Per-tenant queued/active caps → 429.
        try:
            load = self._jobs.tenant_load(tenant_id=tenant_id)
            if load.queued >= cfg.capacity_max_tenant_queued_jobs:
                raise CapacityError(
                    "tenant_queue_full",
                    "This tenant has too many queued documents; retry shortly.",
                    status=429,
                    retry_after=30,
                    reason="tenant_queue_backpressure",
                )
            if load.active >= cfg.capacity_max_tenant_active_jobs:
                raise CapacityError(
                    "tenant_active_full",
                    "This tenant has too many documents processing; retry shortly.",
                    status=429,
                    retry_after=15,
                    reason="tenant_active_backpressure",
                )
        except CapacityError:
            raise
        except store_errors() as exc:  # noqa: BLE001
            logger.debug("[documents.capacity] tenant load failed", exc_info=True)
            raise CapacityError(
                "capacity_unavailable", "Document tenant capacity is unavailable; retry shortly.",
                status=503, retry_after=30, reason="tenant_capacity_unavailable",
            ) from exc

        # 4. In-memory rate / byte window → 429 (additional guard, records last).
        # Durable ingestion reserves and reconciles these budgets in the DB.
        # The local limiter remains for standalone callers of this guard.
        if self._admissions is not None:
            return
        allowed, retry_after = self._limiter.try_acquire(
            tenant_id,
            byte_size,
            max_files=cfg.capacity_intake_rate_per_minute,
            max_bytes=cfg.capacity_intake_bytes_per_minute,
        )
        if not allowed:
            raise CapacityError(
                "rate_limited",
                "Intake rate limit exceeded for this tenant; retry shortly.",
                status=429,
                retry_after=retry_after,
                reason="rate_limited",
            )

    # ── Snapshot / health ────────────────────────────────────────────────

    @policy_operation
    def snapshot(self, *, tenant_id: str | None = None) -> dict[str, Any]:
        """Return an alert-compatible capacity + health view.

        ``degraded_reasons`` is a list of machine-readable codes suitable for
        alerting; ``status`` is ``ok`` / ``degraded`` / ``unavailable``.
        """
        cfg = self._config
        degraded: list[str] = []
        data: dict[str, Any] = {"limits": self._limits()}
        if self._admissions is not None:
            try:
                data["admission"] = self._admissions.snapshot(tenant_id=tenant_id)
                if data["admission"]["pending"]:
                    degraded.append("intake_reservations_pending")
            except store_errors():  # noqa: BLE001 - unavailable admission is visible
                data["admission"] = {"error": "unavailable"}
                degraded.append("admission_unavailable")

        try:
            stats = self._jobs.queue_stats()
            data["queue"] = {
                "depth": stats.depth,
                "active_leases": stats.active_leases,
                "retry_waiting": stats.retry_waiting,
                "dead_letter": stats.dead_letter,
                "non_terminal": stats.non_terminal,
                "oldest_age_seconds": round(stats.oldest_age_seconds, 1),
            }
            if stats.non_terminal >= cfg.capacity_max_queued_jobs:
                degraded.append("queue_backpressure")
            elif stats.non_terminal >= cfg.capacity_max_queued_jobs * 0.9:
                degraded.append("queue_near_capacity")
            if stats.dead_letter > 0:
                degraded.append("dead_letter_present")
        except store_errors():  # noqa: BLE001
            data["queue"] = {"error": "unavailable"}
            degraded.append("queue_stats_unavailable")

        free = self._free_bytes()
        total = self._total_bytes()
        storage: dict[str, Any] = {
            "free_bytes": free,
            "total_bytes": total,
            "floor_bytes": cfg.capacity_storage_free_floor_bytes,
        }
        if free is not None and cfg.capacity_storage_free_floor_bytes > 0:
            storage["below_floor"] = free < cfg.capacity_storage_free_floor_bytes
            if storage["below_floor"]:
                degraded.append("storage_low")
        data["storage"] = storage

        if tenant_id is not None:
            try:
                load = self._jobs.tenant_load(tenant_id=tenant_id)
                files, bytes_used = self._limiter.window_usage(tenant_id)
                if self._admissions is not None:
                    usage = data["admission"]
                    files, bytes_used = usage["window_files"], usage["window_bytes"]
                data["tenant"] = {
                    "tenant_id": tenant_id,
                    "queued": load.queued,
                    "active": load.active,
                    "window_files": files,
                    "window_bytes": bytes_used,
                }
            except store_errors():  # noqa: BLE001
                data["tenant"] = {"tenant_id": tenant_id, "error": "unavailable"}

        if "queue_stats_unavailable" in degraded or "admission_unavailable" in degraded:
            status = "unavailable"
        elif degraded:
            status = "degraded"
        else:
            status = "ok"
        data["status"] = status
        data["degraded_reasons"] = degraded
        return data

    # ── Helpers ──────────────────────────────────────────────────────────

    def _limits(self) -> dict[str, int]:
        cfg = self._config
        return {
            "max_queued_jobs": cfg.capacity_max_queued_jobs,
            "max_tenant_queued_jobs": cfg.capacity_max_tenant_queued_jobs,
            "max_tenant_active_jobs": cfg.capacity_max_tenant_active_jobs,
            "intake_rate_per_minute": cfg.capacity_intake_rate_per_minute,
            "intake_bytes_per_minute": cfg.capacity_intake_bytes_per_minute,
            "storage_free_floor_bytes": cfg.capacity_storage_free_floor_bytes,
        }

    def _disk_target(self) -> Path:
        # disk_usage needs an existing path — walk up to the first that exists.
        candidate = self._storage_root
        for path in (candidate, *candidate.parents):
            if path.exists():
                return path
        return Path.cwd()

    def below_floor(self, extra_bytes: int = 0) -> bool:
        """True when writing ``extra_bytes`` would cross the free-space floor
        (or free space cannot be measured while a floor is configured)."""
        floor = self._config.capacity_storage_free_floor_bytes
        if floor <= 0:
            return False
        free = self._free_bytes()
        return free is None or free - max(0, int(extra_bytes)) < floor

    def _free_bytes(self) -> int | None:
        try:
            return int(shutil.disk_usage(self._disk_target()).free)
        except store_errors():  # noqa: BLE001 - measurement failure is unavailable
            logger.debug("[documents.capacity] disk_usage failed", exc_info=True)
            return None

    def _total_bytes(self) -> int | None:
        try:
            return int(shutil.disk_usage(self._disk_target()).total)
        except store_errors():  # noqa: BLE001
            return None
