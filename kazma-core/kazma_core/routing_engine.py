"""Unified routing engine for the Kazma agent framework.

Provides a unified router that chains Semantic, Dialect-aware, and Capability-based
fallback routing in a single cohesive execution path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from kazma_core.swarm.task import SwarmTask, WorkerCapabilities
from kazma_core.swarm.semantic_router import get_semantic_router
from kazma_core.router import DialectRouter, AgentRequest

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


class UnifiedRouter:
    """Matches task requirements to worker capabilities using multiple strategies.

    Strategies applied sequentially (with dialect boosting):
    1. Semantic Routing (if chromadb is available and task provides enough context)
    2. Dialect Boosting (if Arabic dialects detected, boost matching workers)
    3. Keyword Fallback (if no semantic matches, overlap task text and capabilities)

    Usage::

        router = UnifiedRouter()
        selected = await router.route(task, available_workers)
    """

    DEFAULT_TOP_N: int = 5

    def __init__(self) -> None:
        self._semantic_router = get_semantic_router()
        self._dialect_router = DialectRouter()

    async def route(
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
        if not self._is_auto_routing(task):
            return list(task.workers)

        if not available_workers:
            raise NoCapableWorkersError("No capable workers available for auto-routing.")

        top_n = self._resolve_top_n(task)

        # Build consistent profiles for semantic & dialect routing
        worker_profiles = self._build_worker_profiles(available_workers)

        scored_names: list[str] = []
        semantic_scores: dict[str, float] = {}
        dialect_detected = None
        dialect_confidence = 0.0

        # ── 1. Dialect Detection ──
        try:
            req = AgentRequest(text=task.prompt)
            token_result = self._dialect_router.tokenizer.tokenize(req.text)
            dialect_detected = token_result.dialect.dialect
            dialect_confidence = float(token_result.dialect.confidence)
        except Exception as exc:
            logger.debug("[UnifiedRouter] Dialect tokenization for diagnostics failed: %s", exc)

        dialect_boosts = self._calculate_dialect_boosts(task.prompt, worker_profiles)

        # ── 2. Semantic Routing ──
        if self._semantic_router.available:
            logger.info("[UnifiedRouter] Attempting Semantic routing.")
            try:
                self._semantic_router.build_profiles(worker_profiles)
                scored = self._semantic_router.query(task.prompt, top_n=top_n * 2)
                if scored:
                    logger.info("[UnifiedRouter] Semantic query yielded: %s", scored)
                    scored_names = [name for name, _score in scored]
                    for name, sim in scored:
                        semantic_scores[name] = float(sim)
            except Exception as exc:
                logger.warning("[UnifiedRouter] Semantic router failed, falling back to keyword: %s", exc)

        # Calculate keyword overlaps for ALL workers for routing diagnostics
        keyword_overlaps: dict[str, int] = {}
        requirements_tokens = self._extract_requirement_tokens(task)
        for worker_info in available_workers:
            name = worker_info["name"]
            capabilities = worker_info["capabilities"]
            overlap = self._score_worker(requirements_tokens, capabilities)
            keyword_overlaps[name] = int(overlap)

        # ── 3. Keyword Fallback Routing ──
        if not scored_names:
            logger.info("[UnifiedRouter] Falling back to Keyword capability routing.")
            scored_workers = [_ScoredWorker(name=name, score=score) for name, score in keyword_overlaps.items() if score > 0]
            scored_workers.sort(key=lambda item: item.score, reverse=True)
            scored_names = [item.name for item in scored_workers]

        # Add any workers that got a dialect boost to the scored_names so they aren't lost
        if dialect_boosts:
            for name in dialect_boosts:
                if name not in scored_names:
                    scored_names.append(name)

        if not scored_names:
            logger.warning(
                "[RoutingEngine] No capable workers matched. Task prompt: '%s'; available workers: %s",
                task.prompt[:100],
                [w['name'] for w in available_workers],
            )
            raise NoCapableWorkersError(
                "No capable workers matched the task requirements. "
                "Please check worker capabilities and try again."
            )

        # ── 4. Apply Dialect Boosts & Final Sort ──
        base_score_map = {name: (len(scored_names) - i) * 10 for i, name in enumerate(scored_names)}
        
        if dialect_boosts:
            logger.info("[UnifiedRouter] Applying dialect boosts: %s", dialect_boosts)
            
            final_scored = []
            for name in scored_names:
                boost = dialect_boosts.get(name, 0)
                final_scored.append(_ScoredWorker(name=name, score=base_score_map[name] + boost))
            
            final_scored.sort(key=lambda x: x.score, reverse=True)
            scored_names = [item.name for item in final_scored]

        selected_names = scored_names[:top_n]

        # ── 5. Record Routing Diagnostics ──
        diagnostics = {
            "strategy_used": "semantic" if (self._semantic_router.available and semantic_scores) else "keyword",
            "dialect_detected": dialect_detected if (dialect_detected in ("kw", "msa") and dialect_confidence >= 0.3) else None,
            "dialect_confidence": dialect_confidence if (dialect_detected in ("kw", "msa") and dialect_confidence >= 0.3) else 0.0,
            "scores": {}
        }
        for worker_info in available_workers:
            name = worker_info["name"]
            diagnostics["scores"][name] = {
                "semantic_similarity": float(semantic_scores.get(name, 0.0)),
                "keyword_overlap": int(keyword_overlaps.get(name, 0)),
                "dialect_boost": int(dialect_boosts.get(name, 0)),
                "final_score": int(base_score_map.get(name, 0) + dialect_boosts.get(name, 0) if dialect_boosts else (semantic_scores.get(name, 0.0) * 100 if semantic_scores else keyword_overlaps.get(name, 0)))
            }
        
        task.metadata["routed_workers"] = selected_names
        task.metadata["routing_diagnostics"] = diagnostics
        
        logger.info(
            "[UnifiedRouter] auto-routed task '%s' to %s",
            task.id,
            selected_names,
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
    def _resolve_top_n(task: SwarmTask) -> int:
        """Return the maximum number of workers to select."""
        raw = task.metadata.get("top_n")
        if raw is not None:
            try:
                return max(1, int(raw))
            except (TypeError, ValueError):
                pass
        return UnifiedRouter.DEFAULT_TOP_N

    @staticmethod
    def _build_worker_profiles(available_workers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate worker info dicts into profiles for Semantic/Dialect logic."""
        profiles = []
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

            profiles.append({
                "name": w["name"],
                "expertise": expertise,
                "roles": roles,
                "system_prompt": system_prompt,
            })
        return profiles

    def _calculate_dialect_boosts(self, prompt: str, worker_profiles: list[dict[str, Any]]) -> dict[str, int]:
        """Determine if dialect boosts should be applied, and return worker score boosts."""
        boosts = {}
        try:
            req = AgentRequest(text=prompt)
            token_result = self._dialect_router.tokenizer.tokenize(req.text)
            dialect = token_result.dialect.dialect  # e.g., "kw" (Kuwaiti) or "msa"
            confidence = token_result.dialect.confidence

            if dialect not in ("kw", "msa") or confidence < 0.3:
                return boosts

            logger.debug("[UnifiedRouter] Detected dialect: %s (confidence: %.2f)", dialect, confidence)

            for p in worker_profiles:
                score = 0
                expertise_set = {e.lower() for e in p["expertise"]}
                role = " ".join(p["roles"]).lower()

                if dialect == "kw":
                    if any(kw in role for kw in ("kuwait", "gulf", "colloquial", "kw")):
                        score += 150
                    if any("kuwait" in e or "gulf" in e or "kw" in e for e in expertise_set):
                        score += 100
                elif dialect == "msa":
                    if any(kw in role for kw in ("msa", "standard", "formal")):
                        score += 150
                    if any("msa" in e or "standard" in e or "arabic" in e for e in expertise_set):
                        score += 100
                
                if score > 0:
                    boosts[p["name"]] = score

        except Exception as exc:
            logger.warning("[UnifiedRouter] Dialect analysis failed: %s", exc)

        return boosts

    def _keyword_route(self, task: SwarmTask, available_workers: list[dict[str, Any]]) -> list[str]:
        """Legacy capability-based keyword overlap routing."""
        requirements_tokens = self._extract_requirement_tokens(task)

        scored: list[_ScoredWorker] = []
        for worker_info in available_workers:
            name = worker_info["name"]
            capabilities: WorkerCapabilities = worker_info["capabilities"]
            score = self._score_worker(requirements_tokens, capabilities)
            if score > 0:
                scored.append(_ScoredWorker(name=name, score=score))

        scored.sort(key=lambda item: item.score, reverse=True)
        return [item.name for item in scored]

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
    def _score_worker(
        requirement_tokens: set[str],
        capabilities: WorkerCapabilities,
    ) -> int:
        """Score a worker's capabilities against the requirement tokens."""
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
