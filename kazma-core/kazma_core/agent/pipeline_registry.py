"""Pipeline Registry — extensible structured workflows.

Each pipeline is a deterministic step sequence for a class of tasks.
Pipelines self-register on import; the intent router matches user intents
to pipelines. Structured tasks never enter the free-form agent loop.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

__all__ = ["Pipeline", "PipelineBudget", "PipelineRegistry", "get_registry"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineBudget:
    """Per-pipeline resource limits — exceeded returns partial, not crash."""

    max_tokens: int = 15_000
    max_steps: int = 10
    max_llm_calls: int = 5
    timeout_seconds: float = 300.0

    #: Allow the pipeline to escalate to the free-form agent loop
    #: when its structured steps can't complete the task.
    allow_escalation: bool = True


@dataclass
class IntentPattern:
    """A pattern that maps user text to a pipeline."""

    keywords: list[str]
    regex: str | None = None
    negations: list[str] = field(default_factory=list)
    weight: float = 1.0


# Type for pipeline handlers — receives the TaskIntent + graph state
PipelineHandler = Callable[..., Awaitable[str]]


@dataclass
class Pipeline:
    """A structured workflow for a class of tasks."""

    name: str
    description: str
    handler: PipelineHandler
    budget: PipelineBudget = field(default_factory=PipelineBudget)
    intent_patterns: list[IntentPattern] = field(default_factory=list)
    #: The intent category that triggers this pipeline
    category: str = ""

    def matches(self, intent_category: str) -> bool:
        """True when this pipeline handles the given intent category."""
        return self.category == intent_category


class PipelineRegistry:
    """Central registry — pipelines self-register on import."""

    def __init__(self) -> None:
        self._pipelines: dict[str, Pipeline] = {}

    def register(self, pipeline: Pipeline) -> None:
        """Register a pipeline. Re-registering replaces the old one."""
        self._pipelines[pipeline.name] = pipeline
        logger.debug(
            "[pipeline_registry] registered '%s' (category=%s)",
            pipeline.name,
            pipeline.category,
        )

    def unregister(self, name: str) -> None:
        self._pipelines.pop(name, None)

    def get(self, name: str) -> Pipeline | None:
        return self._pipelines.get(name)

    def match(self, intent_category: str) -> Pipeline | None:
        """Find the pipeline that handles the given intent category."""
        for p in self._pipelines.values():
            if p.matches(intent_category):
                return p
        return None

    def list(self) -> list[Pipeline]:
        return list(self._pipelines.values())

    def categories(self) -> dict[str, str]:
        """category → pipeline name mapping."""
        return {p.category: p.name for p in self._pipelines.values() if p.category}


# ─── Singleton ───────────────────────────────────────────────────────────

_registry: PipelineRegistry | None = None


def get_registry() -> PipelineRegistry:
    """Process-wide pipeline registry singleton."""
    global _registry
    if _registry is None:
        _registry = PipelineRegistry()
        _auto_register()
    return _registry


def _auto_register() -> None:
    """Import and register all built-in pipelines."""
    try:
        from kazma_core.agent.pipelines.document import register as _reg_doc

        _reg_doc()
    except Exception as exc:
        logger.debug("[pipeline_registry] document pipeline registration failed: %s", exc)
