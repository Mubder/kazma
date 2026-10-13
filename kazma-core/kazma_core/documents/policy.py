"""One immutable policy per operation, shared across document service layers."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar, cast

from .config import DocumentConfig, get_document_config

# These values are bound to constructed stores, queues and worker objects.
# Changing them requires reconstructing the coordinator, never retargeting it.
_RESTART_FIELDS = (
    "storage_root", "quota_tenant_bytes", "worker_concurrency",
    "worker_lease_seconds", "worker_heartbeat_seconds",
    "worker_retry_base_seconds", "worker_retry_max_seconds",
)
_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True, slots=True)
class _Snapshot:
    configured: DocumentConfig
    effective: DocumentConfig


class DocumentPolicy:
    def __init__(self, config: DocumentConfig | None = None) -> None:
        self.live = config is None
        self.initial = config if config is not None else get_document_config(required=True)
        self._active: ContextVar[_Snapshot | None] = ContextVar("document_policy", default=None)

    def capture(self) -> _Snapshot:
        active = self._active.get()
        if active is not None:
            return active
        configured = get_document_config(required=True) if self.live else self.initial
        effective = replace(configured, **{key: getattr(self.initial, key) for key in _RESTART_FIELDS})
        return _Snapshot(configured, effective)

    @property
    def current(self) -> DocumentConfig:
        return self.capture().effective

    @contextmanager
    def scope(self, snapshot: _Snapshot | None = None) -> Iterator[DocumentConfig]:
        selected = snapshot or self.capture()
        token = self._active.set(selected)
        try:
            yield selected.effective
        finally:
            self._active.reset(token)

    @contextmanager
    def detached(self) -> Iterator[None]:
        """Long-lived tasks must capture policy per job, never inherit startup's."""
        token = self._active.set(None)
        try:
            yield
        finally:
            self._active.reset(token)

    @staticmethod
    def _revision(config: DocumentConfig) -> str:
        payload = json.dumps(asdict(config), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def revision(self) -> str:
        return self._revision(self.current)

    def status(self) -> dict[str, Any]:
        snapshot = self.capture()
        changes = {}
        for key in _RESTART_FIELDS:
            configured = getattr(snapshot.configured, key)
            effective = getattr(snapshot.effective, key)
            if configured != effective:
                changes[key] = {
                    "configured": str(configured) if isinstance(configured, Path) else configured,
                    "effective": str(effective) if isinstance(effective, Path) else effective,
                }
        return {"mode": "live" if self.live else "explicit", "restart_required": bool(changes),
                "configured_revision": self._revision(snapshot.configured),
                "effective_revision": self._revision(snapshot.effective),
                "pending_restart": changes}


def policy_operation(method: _F) -> _F:
    """Bind once; nested calls and asyncio.to_thread inherit that exact policy.

    Async entries read settings off the event loop. The ContextVar belongs to a
    policy instance, so concurrent operations and other service instances cannot
    replace each other's captured values. An explicit config stays fixed.
    """
    if inspect.iscoroutinefunction(method):
        @wraps(method)
        async def async_call(self: Any, *args: Any, **kwargs: Any) -> Any:
            snapshot = await asyncio.to_thread(self._policy.capture)
            with self._policy.scope(snapshot):
                return await method(self, *args, **kwargs)
        return cast(_F, async_call)

    @wraps(method)
    def sync_call(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self._policy.scope():
            return method(self, *args, **kwargs)
    return cast(_F, sync_call)
