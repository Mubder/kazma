"""Persistent Swarm Worker Registry.

Single source of truth for all workers. Backed by a JSON file in the data
dir (``<data dir>/swarm_registry.json``) so workers survive reboots with no
dependency on ChromaDB or SQLite.  The SwarmEngine uses this as a "phonebook": query by
expertise, fetch the worker's "Soul" (system prompt), apply the
configured model/provider, and instantiate the worker for the task.

Workers are registered once and persist until explicitly removed.
No discovery sweeps are needed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["WorkerEntry", "WorkerRegistry", "default_registry_path", "get_worker_registry"]

logger = logging.getLogger(__name__)

_FILE = "swarm_registry.json"

#: The registry file when set (tests pin it); None means the data dir's.
_DEFAULT_PATH: Path | None = None


def default_registry_path() -> Path:
    """``<data dir>/swarm_registry.json``: where the worker registry lives.

    It was ``Path("swarm_registry.json").resolve()`` at import -- the process's
    working directory, outside the data dir: no backup copied it, and a test
    run inside an install wrote the install's (2026-10-01).
    """
    if _DEFAULT_PATH is not None:
        return Path(_DEFAULT_PATH)
    from kazma_core.paths import data_dir

    return data_dir() / _FILE


def _legacy_registry_paths() -> list[Path]:
    """Where older builds kept the registry: the working directory, and the
    install root (the same folder for a server the guard starts)."""
    out = [Path.cwd() / _FILE]
    try:
        from kazma_core.paths import get_project_root, installed_project_root

        out.append((installed_project_root() or get_project_root()) / _FILE)
    except Exception:  # noqa: BLE001 -- no install root: the CWD is all there is
        logger.debug("[WorkerRegistry] no install root for the legacy lookup", exc_info=True)
    seen: set[str] = set()
    unique: list[Path] = []
    for p in out:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _write_atomic(path: Path, data: list[dict[str, Any]]) -> None:
    """Write *data* as the registry file whole, or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _adopt_legacy_registry(target: Path) -> None:
    """Move an older build's registry into the data dir, once.

    The old file is renamed ``.migrated`` (as the Soul's JSON file was) so
    nothing reads two registries.
    """
    if target.exists():
        return
    from kazma_core import paths as _paths

    for legacy in _legacy_registry_paths():
        try:
            if not legacy.is_file() or legacy.resolve() == target.resolve():
                continue
            if _paths._is_throwaway(target) and not _paths._is_throwaway(legacy):
                # A one-way move of an install's only copy into a folder
                # that is deleted later -- a test's data dir, or a scratch
                # copy's. A subprocess a test starts has no conftest pin, and
                # its install root is the checkout the tests run in. The home
                # folder's migration refuses the same (paths.migrate_legacy_user_home).
                logger.warning(
                    "[WorkerRegistry] not moving %s into the temporary data dir %s",
                    legacy, target.parent,
                )
                continue
            raw = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("[WorkerRegistry] could not read the old registry %s: %s", legacy, exc)
            continue
        if not isinstance(raw, list):
            logger.warning("[WorkerRegistry] the old registry %s is not a list; left as it is", legacy)
            continue
        _write_atomic(target, raw)
        try:
            legacy.replace(legacy.with_name(legacy.name + ".migrated"))
        except OSError as exc:
            logger.warning(
                "[WorkerRegistry] moved %s but could not rename it (%s); it is no longer read",
                legacy, exc,
            )
        logger.info("[WorkerRegistry] moved %d worker(s) from %s to %s", len(raw), legacy, target)
        return

# Module-level singleton cache
_REGISTRY_SINGLETON: WorkerRegistry | None = None
_REGISTRY_SINGLETON_LOCK = threading.Lock()


# ── Data model ────────────────────────────────────────────────────────────


@dataclass
class WorkerEntry:
    """A single worker record in the registry."""

    name: str
    expertise: list[str] = field(default_factory=lambda: ["general"])
    roles: list[str] = field(default_factory=lambda: ["leaf"])
    model: str = ""
    provider: str = ""
    worker_type: str = "in_process"  # "in_process" | "telegram_bot"
    system_prompt: str = ""          # the worker's "Soul" — instructions
    enabled: bool = True
    tools: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerEntry:
        return cls(
            name=str(data.get("name", "")),
            expertise=list(data.get("expertise", ["general"])),
            roles=list(data.get("roles", ["leaf"])),
            model=str(data.get("model", "")),
            provider=str(data.get("provider", "")),
            worker_type=str(data.get("worker_type", "in_process")),
            system_prompt=str(data.get("system_prompt", "")),
            enabled=bool(data.get("enabled", True)),
            tools=list(data.get("tools", [])),
            metadata=dict(data.get("metadata", {})),
        )

    @property
    def is_generalist(self) -> bool:
        """A worker is a generalist if it has no expertise tags or no Soul."""
        return (
            not self.expertise
            or self.expertise == ["general"]
            or not self.system_prompt
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "expertise": self.expertise,
            "roles": self.roles,
            "model": self.model,
            "provider": self.provider,
            "worker_type": self.worker_type,
            "system_prompt": self.system_prompt,
            "enabled": self.enabled,
            "tools": self.tools,
            "metadata": self.metadata,
        }


# ── Registry ───────────────────────────────────────────────────────────────


class WorkerRegistry:
    """Persistent registry of all swarm workers.

    Backed by a JSON file.  Survives reboots.  The SwarmEngine uses
    this as a phonebook — query by expertise, fetch the Soul, apply
    model/provider, and instantiate.

    Thread-safe: every change and every read of the entries holds a
    threading.Lock (route handlers run in FastAPI's threadpool while the
    swarm reads on the loop). The registry is a module-level singleton —
    all callers share the same instance.

    Usage::

        registry = WorkerRegistry()
        registry.register(WorkerEntry(
            name="core", expertise=["code", "security"], roles=["orchestrator"],
            model="deepseek-v4-pro", provider="deepseek",
            system_prompt="You are the core engineer. You write code and review PRs.",
        ))
        entry = registry.get("core")
        workers = registry.find_by_expertise("code")
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else default_registry_path()
        self._entries: dict[str, WorkerEntry] = {}
        self._lock = threading.Lock()
        if path is None and _DEFAULT_PATH is None:
            _adopt_legacy_registry(self._path)
        self._load()

    def _snapshot(self) -> list[WorkerEntry]:
        """The entries now, read under the lock."""
        with self._lock:
            return list(self._entries.values())

    # ── Persistence ─────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load workers from the JSON file."""
        with self._lock:
            self._entries.clear()
            if not self._path.exists():
                logger.info("[WorkerRegistry] No registry file at %s — starting empty", self._path)
                self._save_unlocked()
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(raw, list):
                    logger.warning("[WorkerRegistry] Invalid format — expected JSON array")
                    return
                for item in raw:
                    entry = WorkerEntry.from_dict(item)
                    self._entries[entry.name] = entry
                logger.info("[WorkerRegistry] Loaded %d workers from %s", len(self._entries), self._path)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("[WorkerRegistry] Failed to load: %s — starting empty", exc)

    def _save_unlocked(self) -> None:
        """Persist workers (caller must hold lock), atomically: a crash
        mid-write used to leave a truncated file that loaded as empty."""
        _write_atomic(self._path, [e.to_dict() for e in self._entries.values()])

    def _save(self) -> None:
        """Persist all workers to the JSON file."""
        with self._lock:
            self._save_unlocked()

    # ── CRUD ────────────────────────────────────────────────────────────

    def register(self, entry: WorkerEntry) -> WorkerEntry:
        """Register a new worker (or overwrite existing by name)."""
        if not entry.name.strip():
            raise ValueError("Worker name is required")
        with self._lock:
            self._entries[entry.name] = entry
            self._save_unlocked()
        logger.info("[WorkerRegistry] Registered worker: %s (expertise=%s)", entry.name, entry.expertise)
        return entry

    def update(self, name: str, **kwargs: Any) -> WorkerEntry | None:
        """Update fields of an existing worker by name.

        Accepted kwargs: expertise, roles, model, provider, worker_type,
        system_prompt, enabled, metadata.
        """
        with self._lock:
            entry = self._entries.get(name)
            if entry is None:
                logger.warning("[WorkerRegistry] Update failed — no worker named '%s'", name)
                return None
            for field_name in (
                "expertise", "roles", "model", "provider",
                "worker_type", "system_prompt", "enabled", "metadata",
            ):
                if field_name in kwargs:
                    setattr(entry, field_name, kwargs[field_name])
            self._save_unlocked()
        logger.info("[WorkerRegistry] Updated worker: %s", name)
        return entry

    def delete(self, name: str) -> bool:
        """Remove a worker by name. Returns True if deleted."""
        with self._lock:
            if name not in self._entries:
                return False
            del self._entries[name]
            self._save_unlocked()
        logger.info("[WorkerRegistry] Deleted worker: %s", name)
        return True

    def get(self, name: str) -> WorkerEntry | None:
        """Retrieve a single worker by name."""
        return self._entries.get(name)

    def list_all(self) -> list[WorkerEntry]:
        """Return all registered workers."""
        return self._snapshot()

    # ── Query by expertise / role ────────────────────────────────────────

    def find_by_expertise(self, expertise: str) -> list[WorkerEntry]:
        """Find all workers matching a given expertise tag (case-insensitive)."""
        tag = expertise.lower()
        return [
            e for e in self._snapshot()
            if e.enabled and tag in (t.lower() for t in e.expertise)
        ]

    def find_by_role(self, role: str) -> list[WorkerEntry]:
        """Find all workers matching a given role (case-insensitive)."""
        r = role.lower()
        return [
            e for e in self._snapshot()
            if e.enabled and r in (t.lower() for t in e.roles)
        ]

    def find_generalists(self) -> list[WorkerEntry]:
        """Return all enabled generalist workers (no expertise / no Soul)."""
        return [e for e in self._snapshot() if e.enabled and e.is_generalist]

    def find_best(self, task_description: str) -> list[WorkerEntry]:
        """Route a task to the best workers by expertise match.

        1. Try unified routing via UnifiedRouter (semantic, dialect-aware, and keyword).
        2. If no specialist found, fall back to any generalist worker.
        3. If no generalist, return ALL enabled workers as last resort.

        This guarantees zero dispatch failures.
        """
        try:
            from kazma_core.routing_engine import UnifiedRouter
            from kazma_core.swarm.task import SwarmTask, WorkerCapabilities

            router = UnifiedRouter()
            task = SwarmTask(prompt=task_description, workers=["auto"])

            available_workers = []
            for e in self._snapshot():
                if e.enabled:
                    caps = WorkerCapabilities(
                        role=e.roles[0] if e.roles else "leaf",
                        expertise=e.expertise,
                        tools=e.tools,
                        model_specialty=e.model
                    )
                    # Support system_prompt check in UnifiedRouter._build_worker_profiles
                    caps.system_prompt = e.system_prompt
                    available_workers.append({
                        "name": e.name,
                        "capabilities": caps
                    })

            if available_workers:
                from concurrent.futures import ThreadPoolExecutor
                import asyncio

                def run_sync(coro):
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        def _run():
                            loop = asyncio.new_event_loop()
                            try:
                                asyncio.set_event_loop(loop)
                                return loop.run_until_complete(coro)
                            finally:
                                loop.close()
                        return executor.submit(_run).result()

                selected_names = run_sync(router.route(task, available_workers))
                result = []
                for name in selected_names:
                    entry = self._entries.get(name)
                    if entry and entry.enabled:
                        result.append(entry)
                if result:
                    logger.info(
                        "[WorkerRegistry] Unified routing: %s → %s",
                        task_description[:60],
                        [e.name for e in result],
                    )
                    return result
        except Exception as exc:
            logger.debug("[WorkerRegistry] Unified routing failed: %s", exc)

        # Fallback to generalists
        generalists = self.find_generalists()
        if generalists:
            logger.info(
                "[WorkerRegistry] No specialist via unified routing — falling back to generalists for %r: %s",
                task_description[:60],
                [g.name for g in generalists],
            )
            return generalists

        # Last resort fallback: return ALL enabled workers
        all_enabled = [e for e in self._snapshot() if e.enabled]
        if all_enabled:
            logger.info("[WorkerRegistry] No generalist — returning all %d workers as last resort", len(all_enabled))
            return all_enabled

        return []

    # ── Utility ──────────────────────────────────────────────────────────

    def expertise_map(self) -> dict[str, list[str]]:
        """Return a map of expertise → list of worker names."""
        result: dict[str, list[str]] = {}
        for entry in self._snapshot():
            if not entry.enabled:
                continue
            for tag in entry.expertise:
                result.setdefault(tag, []).append(entry.name)
        return result

    def count(self) -> int:
        """Number of registered workers."""
        return len(self._entries)

    def __len__(self) -> int:
        return self.count()

    def __contains__(self, name: str) -> bool:
        return name in self._entries


def get_worker_registry(path: str | Path | None = None) -> WorkerRegistry:
    """Return the shared WorkerRegistry singleton.

    All callers in the same process share one registry instance,
    avoiding repeated file I/O and ensuring consistency.
    """
    global _REGISTRY_SINGLETON
    with _REGISTRY_SINGLETON_LOCK:
        if _REGISTRY_SINGLETON is None:
            _REGISTRY_SINGLETON = WorkerRegistry(path)
        return _REGISTRY_SINGLETON
