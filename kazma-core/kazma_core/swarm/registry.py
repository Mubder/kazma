"""Persistent Swarm Worker Registry.

Single source of truth for all workers. Backed by a JSON file at the
project root so workers survive reboots with no dependency on ChromaDB
or SQLite.  The SwarmEngine uses this as a "phonebook": query by
expertise, fetch the worker's "Soul" (system prompt), apply the
configured model/provider, and instantiate the worker for the task.

Workers are registered once and persist until explicitly removed.
No discovery sweeps are needed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default registry file lives at the project root.
_DEFAULT_PATH = Path("swarm_registry.json")


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
            metadata=dict(data.get("metadata", {})),
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
            "metadata": self.metadata,
        }


# ── Registry ───────────────────────────────────────────────────────────────


class WorkerRegistry:
    """Persistent registry of all swarm workers.

    Backed by a JSON file.  Survives reboots.  The SwarmEngine uses
    this as a phonebook — query by expertise, fetch the Soul, apply
    model/provider, and instantiate.

    Thread-safe: all mutations hold a re-entrant lock.

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

    def __init__(self, path: str | Path = _DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._entries: dict[str, WorkerEntry] = {}
        self._load()

    # ── Persistence ─────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load workers from the JSON file."""
        self._entries.clear()
        if not self._path.exists():
            logger.info("[WorkerRegistry] No registry file at %s — starting empty", self._path)
            self._save()  # create empty file
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

    def _save(self) -> None:
        """Persist all workers to the JSON file."""
        data = [e.to_dict() for e in self._entries.values()]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # ── CRUD ────────────────────────────────────────────────────────────

    def register(self, entry: WorkerEntry) -> WorkerEntry:
        """Register a new worker (or overwrite existing by name)."""
        if not entry.name.strip():
            raise ValueError("Worker name is required")
        self._entries[entry.name] = entry
        self._save()
        logger.info("[WorkerRegistry] Registered worker: %s (expertise=%s)", entry.name, entry.expertise)
        return entry

    def update(self, name: str, **kwargs: Any) -> WorkerEntry | None:
        """Update fields of an existing worker by name.

        Accepted kwargs: expertise, roles, model, provider, worker_type,
        system_prompt, enabled, metadata.
        """
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
        self._save()
        logger.info("[WorkerRegistry] Updated worker: %s", name)
        return entry

    def delete(self, name: str) -> bool:
        """Remove a worker by name. Returns True if deleted."""
        if name in self._entries:
            del self._entries[name]
            self._save()
            logger.info("[WorkerRegistry] Deleted worker: %s", name)
            return True
        return False

    def get(self, name: str) -> WorkerEntry | None:
        """Retrieve a single worker by name."""
        return self._entries.get(name)

    def list_all(self) -> list[WorkerEntry]:
        """Return all registered workers."""
        return list(self._entries.values())

    # ── Query by expertise / role ────────────────────────────────────────

    def find_by_expertise(self, expertise: str) -> list[WorkerEntry]:
        """Find all workers matching a given expertise tag (case-insensitive)."""
        tag = expertise.lower()
        return [
            e for e in self._entries.values()
            if e.enabled and tag in (t.lower() for t in e.expertise)
        ]

    def find_by_role(self, role: str) -> list[WorkerEntry]:
        """Find all workers matching a given role (case-insensitive)."""
        r = role.lower()
        return [
            e for e in self._entries.values()
            if e.enabled and r in (t.lower() for t in e.roles)
        ]

    def find_best(self, task_description: str) -> list[WorkerEntry]:
        """Route a task to the best workers by expertise match.

        1. Try semantic routing via sentence-transformers embeddings.
        2. Fall back to keyword matching if embeddings unavailable.

        Used by SwarmEngine auto-routing when workers=["auto"].
        """
        desc_lower = task_description.lower()

        # 1 — Semantic routing (if available)
        try:
            from kazma_core.swarm.semantic_router import get_semantic_router

            router = get_semantic_router()
            # Build worker dicts for the router
            worker_dicts = [
                {
                    "name": e.name,
                    "expertise": e.expertise,
                    "system_prompt": e.system_prompt,
                    "roles": e.roles,
                }
                for e in self._entries.values()
                if e.enabled
            ]
            if worker_dicts:
                selected = router.route(task_description, worker_dicts, top_n=5)
                result: list[WorkerEntry] = []
                for name in selected:
                    entry = self._entries.get(name)
                    if entry and entry.enabled:
                        result.append(entry)
                if result:
                    logger.info(
                        "[WorkerRegistry] Semantic routing: %s → %s",
                        task_description[:60],
                        [e.name for e in result],
                    )
                    return result
        except Exception:
            pass

        # 2 — Keyword fallback
        logger.info("[WorkerRegistry] Semantic routing unavailable — using keyword fallback")
        scored: list[tuple[int, WorkerEntry]] = []
        for entry in self._entries.values():
            if not entry.enabled:
                continue
            score = 0
            for tag in entry.expertise:
                if tag.lower() in desc_lower:
                    score += 10
            for kw in desc_lower.split():
                for ex in entry.expertise:
                    if kw in ex.lower() or ex.lower() in kw:
                        score += 2
            scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored]

    # ── Utility ──────────────────────────────────────────────────────────

    def expertise_map(self) -> dict[str, list[str]]:
        """Return a map of expertise → list of worker names."""
        result: dict[str, list[str]] = {}
        for entry in self._entries.values():
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
