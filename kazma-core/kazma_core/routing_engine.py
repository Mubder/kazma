"""Polymorphic routing engine for the Kazma agent framework.

Provides a unified, sequential, and extensible routing pipeline that chains or
falls back across Semantic, Dialect-aware, and Capability-based routers.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from kazma_core.swarm.task import SwarmTask
from kazma_core.swarm.router import CapabilityRouter
from kazma_core.swarm.semantic_router import get_semantic_router
from kazma_core.router import DialectRouter, AgentRequest

logger = logging.getLogger(__name__)


class BaseRouter(ABC):
    """Abstract base class for all swarm routing strategies."""

    @abstractmethod
    async def route(
        self,
        task: SwarmTask,
        available_workers: list[dict[str, Any]],
    ) -> list[str]:
        """Route a task to selected workers.

        Args:
            task: The SwarmTask to route.
            available_workers: List of worker info dictionaries.

        Returns:
            A list of selected worker names, sorted by relevance.
        """
        pass


class CapabilityRouterWrapper(BaseRouter):
    """Wraps the legacy CapabilityRouter for polymorphic routing.

    Scores workers using keyword-overlap between task prompt/context and worker
    declared capabilities (expertise, role, model specialty).
    """

    def __init__(self, router: CapabilityRouter | None = None) -> None:
        self._router = router or CapabilityRouter()

    async def route(
        self,
        task: SwarmTask,
        available_workers: list[dict[str, Any]],
    ) -> list[str]:
        """Execute legacy capability routing."""
        logger.info("[CapabilityRouterWrapper] Scoring workers via keyword-overlap.")
        try:
            return self._router.route(task, available_workers)
        except Exception as exc:
            logger.warning("[CapabilityRouterWrapper] Legacy capability router failed: %s", exc)
            return []


class SemanticRouterWrapper(BaseRouter):
    """Wraps the SemanticRouter using sentence-transformers and ChromaDB.

    Retrieves workers based on semantic cosine similarity of their profile
    embeddings to the task description.
    """

    def __init__(self, persist_dir: str | None = None) -> None:
        self._router = get_semantic_router()
        if persist_dir:
            from kazma_core.swarm.semantic_router import SemanticRouter
            self._router = SemanticRouter(persist_dir=persist_dir)

    async def route(
        self,
        task: SwarmTask,
        available_workers: list[dict[str, Any]],
    ) -> list[str]:
        """Execute semantic similarity routing."""
        if not self._router.available:
            logger.info("[SemanticRouterWrapper] Embedding models or ChromaDB unavailable; skipping.")
            return []

        logger.info("[SemanticRouterWrapper] Routing task '%s' using semantic embedding similarity.", task.id)
        
        # Translate workers array to match SemanticRouter profile builder expectation
        workers_list = []
        for w in available_workers:
            caps = w.get("capabilities")
            expertise = []
            roles = []
            system_prompt = ""
            
            if caps:
                expertise = getattr(caps, "expertise", [])
                roles = [getattr(caps, "role", "")] if getattr(caps, "role", None) else []
                system_prompt = getattr(caps, "system_prompt", "") or getattr(caps, "role", "")
            else:
                expertise = w.get("expertise", [])
                roles = w.get("roles", []) or ([w.get("role")] if w.get("role") else [])
                system_prompt = w.get("system_prompt", "") or w.get("role", "")

            workers_list.append({
                "name": w["name"],
                "expertise": expertise,
                "roles": roles,
                "system_prompt": system_prompt,
            })

        top_n = task.metadata.get("top_n", 5)
        try:
            return self._router.route(
                task_description=task.prompt,
                workers=workers_list,
                top_n=top_n,
            )
        except Exception as exc:
            logger.warning("[SemanticRouterWrapper] Semantic router query failed: %s", exc)
            return []


class DialectRouterWrapper(BaseRouter):
    """Wraps the DialectRouter to prioritize workers matching the input register.

    Checks if the task prompt is written in specific Arabic dialects (e.g. Kuwaiti)
    and prioritizes workers whose expertise or role matches that dialect.
    """

    def __init__(self, router: DialectRouter | None = None) -> None:
        self._router = router or DialectRouter()

    async def route(
        self,
        task: SwarmTask,
        available_workers: list[dict[str, Any]],
    ) -> list[str]:
        """Prioritize workers based on detected Arabic dialect."""
        logger.info("[DialectRouterWrapper] Analyzing dialect for prompt routing.")
        try:
            req = AgentRequest(text=task.prompt)
            token_result = self._router.tokenizer.tokenize(req.text)
            dialect = token_result.dialect.dialect  # e.g., "kw" (Kuwaiti) or "msa"
            confidence = token_result.dialect.confidence

            logger.info("[DialectRouterWrapper] Detected dialect: %s (confidence: %.2f)", dialect, confidence)

            # Prioritize workers with matching dialect capabilities
            scored_workers = []
            for w in available_workers:
                caps = w.get("capabilities")
                expertise_set = set()
                role = ""
                if caps:
                    expertise_set = {e.lower() for e in getattr(caps, "expertise", [])}
                    role = getattr(caps, "role", "").lower()
                else:
                    expertise_set = {e.lower() for e in w.get("expertise", [])}
                    role = w.get("role", "").lower()

                score = 0
                if dialect == "kw":
                    # Look for Kuwaiti or Gulf keywords
                    if any(kw in role for kw in ("kuwait", "gulf", "colloquial", "kw")):
                        score += 15
                    if any("kuwait" in e or "gulf" in e or "kw" in e for e in expertise_set):
                        score += 10
                elif dialect == "msa":
                    # Look for MSA or Standard Arabic keywords
                    if any(kw in role for kw in ("msa", "standard", "formal")):
                        score += 15
                    if any("msa" in e or "standard" in e or "arabic" in e for e in expertise_set):
                        score += 10

                scored_workers.append((w["name"], score))

            scored_workers.sort(key=lambda x: x[1], reverse=True)
            
            # If we found matches with positive scoring, route strictly to them
            matching_workers = [name for name, score in scored_workers if score > 0]
            if matching_workers:
                logger.info("[DialectRouterWrapper] Routed task to matching dialect specialists: %s", matching_workers)
                return matching_workers

            logger.info("[DialectRouterWrapper] No dialect-specific workers matched; passing through.")
            return []
        except Exception as exc:
            logger.warning("[DialectRouterWrapper] Dialect routing failed: %s", exc)
            return []


class RoutingEngine(BaseRouter):
    """Coordinates and cascades multiple routing strategies.

    Executes a chain of routers in sequence. Supports fallback (first router that
    successfully routes) and cooperative chaining (sequentially narrowing down).
    """

    def __init__(self, routers: list[BaseRouter] | None = None, mode: str = "fallback") -> None:
        """Initialize the RoutingEngine.

        Args:
            routers: A list of BaseRouter instances. Defaults to Semantic -> Dialect -> Capability.
            mode: "fallback" (runs until one returns non-empty) or "chain" (successive intersection).
        """
        self.routers = routers if routers is not None else [
            SemanticRouterWrapper(),
            DialectRouterWrapper(),
            CapabilityRouterWrapper(),
        ]
        self.mode = mode

    def add_router(self, router: BaseRouter) -> None:
        """Add a routing strategy to the engine."""
        self.routers.append(router)

    async def route(
        self,
        task: SwarmTask,
        available_workers: list[dict[str, Any]],
    ) -> list[str]:
        """Execute the polymorphic routing chain."""
        if not available_workers:
            return []

        if self.mode == "fallback":
            for router in self.routers:
                try:
                    logger.info("[RoutingEngine] Attempting route via: %s", router.__class__.__name__)
                    routed = await router.route(task, available_workers)
                    if routed:
                        logger.info("[RoutingEngine] Strategy %s succeeded: %s", router.__class__.__name__, routed)
                        return routed
                except Exception as exc:
                    logger.error("[RoutingEngine] Router %s failed: %s", router.__class__.__name__, exc)
                    continue
            
            # Ultimate safety fallback if all failed: return all available workers or run basic CapabilityRouter
            logger.warning("[RoutingEngine] All routing strategies in fallback chain failed/returned empty.")
            return [w["name"] for w in available_workers]

        elif self.mode == "chain":
            current_workers = list(available_workers)
            for router in self.routers:
                if not current_workers:
                    break
                try:
                    logger.info("[RoutingEngine] Chaining route via: %s", router.__class__.__name__)
                    routed = await router.route(task, current_workers)
                    if routed:
                        # Narrow down current_workers to those selected
                        routed_set = set(routed)
                        current_workers = [w for w in current_workers if w["name"] in routed_set]
                except Exception as exc:
                    logger.error("[RoutingEngine] Chaining router %s failed: %s", router.__class__.__name__, exc)
                    continue

            return [w["name"] for w in current_workers]

        return []
