"""Capability-based routing for the swarm engine.

Matches task requirements to worker capabilities using keyword overlap
scoring. When a task specifies ``workers=["auto"]``, the router selects
the best-suited workers based on their declared expertise, role, and
model specialty.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from kazma_core.swarm.task import SwarmTask, WorkerCapabilities

logger = logging.getLogger(__name__)


class NoCapableWorkersError(ValueError):
    """Raised when no workers match the task requirements."""


def _tokenize(text: str) -> set[str]:
    """Lowercase and split text into word tokens, normalizing underscores/hyphens."""
    normalized = text.lower().replace("_", " ").replace("-", " ")
    return set(re.findall(r"[a-z0-9]+", normalized))


@dataclass
class _ScoredWorker:
    """Internal holder for a worker name and its routing score."""

    name: str
    score: int


class CapabilityRouter:
    """Matches task requirements to worker capabilities.

    Scoring works by counting keyword overlap between the task's textual
    content (prompt, context, metadata requirements) and each worker's
    declared capabilities (expertise list, role, model specialty).

    Usage::

        router = CapabilityRouter()
        selected = router.route(task, available_workers)
    """

    # Default number of workers to return when top_n is not specified.
    DEFAULT_TOP_N: int = 5

    def route(
        self,
        task: SwarmTask,
        available_workers: list[dict[str, Any]],
    ) -> list[str]:
        """Route a task to the best-suited workers.

        Args:
            task: The swarm task to route.
            available_workers: List of worker info dicts, each containing
                at least ``"name"`` and ``"capabilities"`` (a
                :class:`WorkerCapabilities` instance).

        Returns:
            A list of selected worker names, sorted by relevance score
            (highest first).

        Raises:
            NoCapableWorkersError: If ``workers=["auto"]`` and no workers
                match the task requirements.
        """
        # Explicit worker list: return as-is.
        if not self._is_auto_routing(task):
            return list(task.workers)

        if not available_workers:
            raise NoCapableWorkersError("No capable workers available for auto-routing.")

        requirements_tokens = self._extract_requirement_tokens(task)
        top_n = self._resolve_top_n(task)

        scored: list[_ScoredWorker] = []
        for worker_info in available_workers:
            name = worker_info["name"]
            capabilities: WorkerCapabilities = worker_info["capabilities"]
            score = self._score_worker(requirements_tokens, capabilities)
            if score > 0:
                scored.append(_ScoredWorker(name=name, score=score))

        if not scored:
            raise NoCapableWorkersError(
                "No capable workers matched the task requirements. "
                f"Task prompt: '{task.prompt[:100]}'; "
                f"available workers: {[w['name'] for w in available_workers]}"
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        selected = scored[:top_n]
        selected_names = [item.name for item in selected]

        # Record routing metadata on the task.
        task.metadata["routed_workers"] = selected_names
        task.metadata["routing_scores"] = {
            item.name: item.score for item in scored
        }

        logger.info(
            "[CapabilityRouter] auto-routed task '%s' to %s (scores: %s)",
            task.id,
            selected_names,
            {item.name: item.score for item in selected},
        )

        return selected_names

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_auto_routing(task: SwarmTask) -> bool:
        """Return True when the task requests automatic worker routing."""
        return list(task.workers) == ["auto"]

    @staticmethod
    def _extract_requirement_tokens(task: SwarmTask) -> set[str]:
        """Build a set of requirement tokens from the task's textual fields."""
        tokens: set[str] = set()

        if task.prompt:
            tokens |= _tokenize(task.prompt)
        if task.context:
            tokens |= _tokenize(task.context)

        requirements = task.metadata.get("requirements", [])
        if isinstance(requirements, list):
            for req in requirements:
                tokens |= _tokenize(str(req))

        return tokens

    @staticmethod
    def _resolve_top_n(task: SwarmTask) -> int:
        """Return the maximum number of workers to select."""
        raw = task.metadata.get("top_n")
        if raw is not None:
            try:
                return max(1, int(raw))
            except (TypeError, ValueError):
                pass
        return CapabilityRouter.DEFAULT_TOP_N

    @staticmethod
    def _score_worker(
        requirement_tokens: set[str],
        capabilities: WorkerCapabilities,
    ) -> int:
        """Score a worker's capabilities against the requirement tokens.

        Each matching keyword contributes 1 point.  Keywords from the
        expertise list, role, model specialty, and tools are all counted.
        """
        if not requirement_tokens:
            return 0

        capability_tokens: set[str] = set()

        for expertise_item in capabilities.expertise:
            capability_tokens |= _tokenize(expertise_item)

        if capabilities.role:
            capability_tokens |= _tokenize(capabilities.role)

        if capabilities.model_specialty:
            capability_tokens |= _tokenize(capabilities.model_specialty)

        for tool in capabilities.tools:
            capability_tokens |= _tokenize(tool)

        return len(requirement_tokens & capability_tokens)
