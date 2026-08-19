"""Auto-scaling for swarm workers.

Monitors demand signals (NoCapableWorkersError, all workers busy) and
transparently spawns worker instances from stored templates so tasks
never fail due to missing capacity.

Templates are stored in ``swarm_templates.json`` alongside the engine.
Each template defines a worker blueprint with min/max instance counts.
The AutoScaler instantiates workers on demand and reaps idle ones after
a configurable TTL.

Lifecycle::

    engine = SwarmEngine(...)
    scaler = AutoScaler(engine)
    scaler.register_template(WorkerTemplate(...))

    # In the dispatch path, on NoCapableWorkersError:
    worker = scaler.maybe_scale(task)
    if worker:
        task.workers = [worker.name]
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kazma_core.swarm.config import WorkerConfig
from kazma_core.swarm.task import WorkerCapabilities

__all__ = ["AutoScaler", "WorkerTemplate"]

logger = logging.getLogger(__name__)

# Default templates file lives alongside kazma.yaml (project root or CWD).
# Resolved to an absolute path at import time — mirrors WorkerRegistry — so
# the autoscaler still finds templates when the app is started from a
# different working directory (deep-audit 2026-08-19, finding #14).
_DEFAULT_TEMPLATES_PATH = Path("swarm_templates.json").resolve()
_DEFAULT_IDLE_TTL = 300  # 5 minutes


@dataclass
class WorkerTemplate:
    """A parameterized worker definition that can be instantiated N times.

    Stored in ``swarm_templates.json``. The AutoScaler spawns instances
    on demand using ``instantiate()`` which generates a unique name.
    """

    name: str
    role: str = ""
    model: str = ""
    provider: str = ""
    worker_type: str = "in_process"
    capabilities: WorkerCapabilities = field(default_factory=WorkerCapabilities)
    min_instances: int = 0
    max_instances: int = 5
    system_prompt: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerTemplate:
        caps_data = data.get("capabilities") or {}
        return cls(
            name=str(data.get("name", "")),
            role=str(data.get("role", "")),
            model=str(data.get("model", "")),
            provider=str(data.get("provider", "")),
            worker_type=str(data.get("worker_type", "in_process")),
            capabilities=WorkerCapabilities.from_dict(caps_data) if caps_data else WorkerCapabilities(),
            min_instances=int(data.get("min_instances", 0)),
            max_instances=int(data.get("max_instances", 5)),
            system_prompt=str(data.get("system_prompt", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "model": self.model,
            "provider": self.provider,
            "worker_type": self.worker_type,
            "capabilities": self.capabilities.to_dict() if hasattr(self.capabilities, "to_dict") else {},
            "min_instances": self.min_instances,
            "max_instances": self.max_instances,
            "system_prompt": self.system_prompt,
        }

    def matches_task(self, task_prompt: str, required_expertise: list[str] | None = None) -> bool:
        """Check if this template's capabilities match a task.

        Matches on role, expertise tags, and model specialty against the prompt
        using **word-boundary token matching** — the prompt is tokenized and a
        tag matches only if it appears as a whole token (or as a contiguous
        multi-word phrase), NOT as a substring. This stops false positives like
        the tag "code" matching "barcode" or "decode".
        """
        import re as _re

        prompt = (task_prompt or "").lower()
        if not prompt:
            return False
        # Tokenize: split on non-alphanumeric. A tag matches if it equals a
        # token, OR appears as a phrase bounded by word boundaries. Multi-word
        # tags (e.g. "pros and cons") use the regex word-boundary form.
        tokens = set(_re.split(r"[^a-z0-9]+", prompt))

        def _tag_hits(tag: str) -> bool:
            t = (tag or "").strip().lower()
            if not t:
                return False
            if " " in t:
                # Multi-word tag: require it as a bounded phrase in the prompt.
                return bool(_re.search(r"\b" + _re.escape(t) + r"\b", prompt))
            return t in tokens

        # Expertise tag match (only when the caller passes required_expertise).
        if required_expertise:
            for tag in required_expertise:
                if _tag_hits(tag):
                    return True

        # Capability expertise match
        for tag in self.capabilities.expertise:
            if _tag_hits(tag):
                return True

        # Role match
        if self.role and _tag_hits(self.role):
            return True

        # Model specialty match
        specialty = getattr(self.capabilities, "model_specialty", "")
        if specialty and _tag_hits(specialty):
            return True

        return False


class AutoScaler:
    """Auto-scaling component for the SwarmEngine.

    Holds worker templates and spawns instances on demand. Tracks
    spawned instances and their last-activity time for reaping.

    Usage::

        scaler = AutoScaler(engine)
        scaler.load_templates()

        # On dispatch failure (NoCapableWorkersError):
        worker = scaler.maybe_scale(task_prompt="write python code")
    """

    def __init__(
        self,
        engine: Any,
        templates_path: str | Path = _DEFAULT_TEMPLATES_PATH,
        idle_ttl: float = _DEFAULT_IDLE_TTL,
    ) -> None:
        self._engine = engine
        self._templates_path = Path(templates_path)
        self._idle_ttl = idle_ttl
        self._templates: dict[str, WorkerTemplate] = {}
        # Track spawned instances: name -> (template_name, spawned_at, last_active)
        self._instances: dict[str, tuple[str, float, float]] = {}
        self._counter: dict[str, int] = {}  # template_name -> next instance number
        # Guards _instances / _counter against concurrent maybe_scale spawns
        # (called from async dispatch) and record_activity/reap (concurrent).
        # Without it, two maybe_scale calls can both pass the capacity check
        # and over-scale beyond max_instances.
        self._lock = threading.Lock()

    # ── Template management ───────────────────────────────────────────

    def register_template(self, template: WorkerTemplate) -> None:
        """Register a worker template."""
        self._templates[template.name] = template
        logger.info("[AutoScaler] Template registered: %s (max %d instances)",
                     template.name, template.max_instances)

    def unregister_template(self, name: str) -> None:
        """Remove a template and reap all its instances."""
        self._templates.pop(name, None)
        # Reap instances of this template
        to_reap = [inst for inst, (tmpl, _, _) in self._instances.items() if tmpl == name]
        for inst in to_reap:
            self._reap_instance(inst)

    def list_templates(self) -> list[WorkerTemplate]:
        """Return all registered templates."""
        return list(self._templates.values())

    def save_templates(self) -> None:
        """Persist templates to JSON file."""
        data = [t.to_dict() for t in self._templates.values()]
        self._templates_path.parent.mkdir(parents=True, exist_ok=True)
        self._templates_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def load_templates(self) -> None:
        """Load templates from JSON file."""
        self._templates.clear()
        if not self._templates_path.exists():
            return
        try:
            raw = json.loads(self._templates_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for item in raw:
                    template = WorkerTemplate.from_dict(item)
                    if template.name:
                        self._templates[template.name] = template
            logger.info("[AutoScaler] Loaded %d templates from %s",
                        len(self._templates), self._templates_path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[AutoScaler] Failed to load templates: %s", exc)

    # ── Scaling logic ─────────────────────────────────────────────────

    def maybe_scale(self, task_prompt: str) -> Any | None:
        """Try to spawn a worker that matches the task prompt.

        Returns the spawned SwarmWorker if a matching template has
        capacity, or ``None`` if no template matches or all are at max.
        """
        # Opportunistically reap idle workers before considering a spawn.
        # This runs on the dispatch hot path so idle pool workers don't
        # accumulate indefinitely (reap_idle is otherwise only invoked by a
        # manual API endpoint). Cheap: a no-op when there's nothing to reap.
        self.reap_idle()
        # Hold the lock across the capacity-check + spawn so two concurrent
        # maybe_scale calls can't both pass the check and over-scale.
        with self._lock:
            for template in self._templates.values():
                if not template.matches_task(task_prompt):
                    continue

                # Check capacity
                active = self._count_active_instances(template.name)
                if active >= template.max_instances:
                    logger.debug("[AutoScaler] Template '%s' at max capacity (%d/%d)",
                                 template.name, active, template.max_instances)
                    continue

                # Spawn an instance
                worker = self._instantiate(template)
                if worker is not None:
                    logger.info("[AutoScaler] Auto-spawned '%s' from template '%s' for task",
                                worker.name, template.name)
                    return worker

        return None

    def spawn_by_name(self, name: str) -> Any | None:
        """Spawn a worker from a template by NAME (not prompt matching).

        Used by the dispatch fallback when a named worker isn't registered
        but a template with that name exists. Returns the spawned worker or
        None if no template matches / at capacity.
        """
        self.reap_idle()
        with self._lock:
            template = self._templates.get(name)
            if template is None:
                return None
            active = self._count_active_instances(template.name)
            if active >= template.max_instances:
                logger.debug("[AutoScaler] Template '%s' at max capacity (%d/%d)",
                             name, active, template.max_instances)
                return None
            worker = self._instantiate(template)
            if worker is not None:
                logger.info("[AutoScaler] Spawned '%s' by name from template '%s'",
                            worker.name, template.name)
            return worker

    def _instantiate(self, template: WorkerTemplate) -> Any | None:
        """Create a worker instance from a template and add it to the engine."""
        # Generate unique name
        count = self._counter.get(template.name, 0) + 1
        self._counter[template.name] = count
        instance_name = f"{template.name}-pool-{count}"

        config = WorkerConfig(
            name=instance_name,
            type=template.worker_type,
            model=template.model,
            provider=template.provider,
            role=template.role,
            capabilities=template.capabilities,
            system_prompt=template.system_prompt,
        )

        try:
            worker = self._engine.add_worker(config)
            now = time.monotonic()
            self._instances[instance_name] = (template.name, now, now)
            # Mark as auto-spawned for UI
            setattr(worker, "auto_spawned", True)
            setattr(worker, "template_name", template.name)
            return worker
        except Exception as exc:
            logger.warning("[AutoScaler] Failed to instantiate '%s': %s",
                           instance_name, exc)
            return None

    def _count_active_instances(self, template_name: str) -> int:
        """Count active instances of a template (caller must hold _lock)."""
        return sum(1 for _, (tmpl, _, _) in self._instances.items() if tmpl == template_name)

    def record_activity(self, worker_name: str) -> None:
        """Update last-activity timestamp for a spawned worker."""
        with self._lock:
            if worker_name in self._instances:
                tmpl, spawned, _ = self._instances[worker_name]
                self._instances[worker_name] = (tmpl, spawned, time.monotonic())

    # ── Reaping ───────────────────────────────────────────────────────

    def reap_idle(self) -> int:
        """Remove workers that have been idle beyond the TTL.

        Returns the number of workers reaped.
        """
        # Collect idle names under lock, then reap WITHOUT the lock held —
        # _reap_instance calls engine.remove_worker (an external call) and we
        # don't want to hold the autoscaler lock across engine mutation.
        with self._lock:
            if not self._instances:
                return 0
            now = time.monotonic()
            to_reap = [
                name for name, (_, _, last_active) in self._instances.items()
                if (now - last_active) > self._idle_ttl
            ]

        for name in to_reap:
            self._reap_instance(name)

        if to_reap:
            logger.info("[AutoScaler] Reaped %d idle workers", len(to_reap))
        return len(to_reap)

    def _reap_instance(self, name: str) -> None:
        """Remove a single spawned worker instance."""
        try:
            self._engine.remove_worker(name)
        except Exception as exc:
            logger.debug("Failed to remove worker %s: %s", name, exc)
        with self._lock:
            self._instances.pop(name, None)
        logger.debug("[AutoScaler] Reaped idle worker: %s", name)

    # ── Queries ───────────────────────────────────────────────────────

    def get_instance_info(self) -> list[dict[str, Any]]:
        """Return info about all spawned instances for UI display."""
        now = time.monotonic()
        result = []
        for name, (template, spawned, last_active) in self._instances.items():
            result.append({
                "name": name,
                "template": template,
                "spawned_ago_s": round(now - spawned, 1),
                "idle_s": round(now - last_active, 1),
                "ttl_s": self._idle_ttl,
            })
        return result
