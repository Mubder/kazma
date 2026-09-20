"""Dialect-aware routing layer.

Routes requests to appropriate processing pipeline based on detected dialect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from kazma_core.tokenizer import DualEngineTokenizer, TokenResult

__all__ = ["AgentRequest", "AgentResponse", "BasePipeline", "DialectRouter", "KuwaitiPipeline", "MSAPipeline"]

logger = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────────


@dataclass
class AgentRequest:
    """Incoming request to the agent."""

    text: str
    session_id: str = ""
    user_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResponse:
    """Response from the agent."""

    text: str
    dialect: str  # "kw", "msa", etc.
    confidence: float
    pipeline_used: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Pipelines ─────────────────────────────────────────────────────────


class BasePipeline:
    """Base class for dialect-specific pipelines."""

    name: str = "base"

    async def execute(
        self,
        request: AgentRequest,
        token_result: TokenResult,
    ) -> AgentResponse:
        """Execute the pipeline on the request."""
        raise NotImplementedError


class KuwaitiPipeline(BasePipeline):
    """Pipeline for Kuwaiti/Gulf Arabic input.

    Uses dialect-aware prompts that understand Gulf expressions,
    code-switching, and informal address patterns.
    """

    name = "kuwaiti"

    #: Dialect guidance emitted as ROUTING METADATA, not as a system prompt.
    #:
    #: This was ``self.system_prompt`` and nothing read it. That is worse than
    #: unused: a dead prompt sitting next to an ``execute()`` that echoes reads
    #: as an unfinished wiring job, and the obvious "fix" is to feed it to a
    #: provider here — which is exactly the parallel mouth removed on
    #: 2026-09-17, outside the supervisor, HITL, turn_failed and tenant bind.
    #: As metadata it reaches the caller, who owns the LLM call and already has
    #: a fenced, gated path for it.
    DIALECT_GUIDANCE = (
        "Understands Kuwaiti/Gulf Arabic dialect. Respond in the same dialect "
        "the user uses. Understand colloquial expressions, code-switching "
        "between Arabic and English, and Gulf-specific cultural references."
    )

    async def execute(
        self,
        request: AgentRequest,
        token_result: TokenResult,
    ) -> AgentResponse:
        """Execute Kuwaiti pipeline — processes with dialect-aware LLM call."""
        dialect_tokens = token_result.dialect_tokens
        code_switch = token_result.code_switch_tokens

        metadata: dict[str, Any] = {
            "pipeline": self.name,
            "total_tokens": len(token_result.tokens),
            "dialect_markers_found": len(dialect_tokens),
            "code_switch_words": len(code_switch),
            "dialect_meanings": {t.text: t.dialect_meaning for t in dialect_tokens if t.dialect_meaning},
            "dialect_guidance": self.DIALECT_GUIDANCE,
        }

        # Do NOT call the LLM here. Live routing only uses the tokenizer
        # for a dialect boost (routing_engine.py). A provider.chat() on
        # this path was a parallel mouth outside the supervisor, HITL,
        # turn_failed, and tenant bind (audit 2026-09-17). Echo the
        # request; DialectRouter.route() is a classifier, not a brain.
        text = request.text

        return AgentResponse(
            text=text,
            dialect=token_result.dialect.dialect,
            confidence=token_result.dialect.confidence,
            pipeline_used=self.name,
            metadata=metadata,
        )


class MSAPipeline(BasePipeline):
    """Pipeline for Modern Standard Arabic input.

    Uses formal prompts appropriate for MSA text.
    """

    name = "msa"

    #: Routing metadata, not a system prompt — see KuwaitiPipeline.
    DIALECT_GUIDANCE = (
        "Respond in Modern Standard Arabic (MSA) unless the user uses a "
        "dialect, in which case match their register."
    )

    async def execute(
        self,
        request: AgentRequest,
        token_result: TokenResult,
    ) -> AgentResponse:
        """Execute MSA pipeline — processes with LLM call."""
        metadata: dict[str, Any] = {
            "pipeline": self.name,
            "total_tokens": len(token_result.tokens),
            "normalized_words": sum(1 for t in token_result.tokens if t.dialect_meaning is not None),
            "dialect_guidance": self.DIALECT_GUIDANCE,
        }

        # Classifier only — see KuwaitiPipeline.execute.
        text = request.text

        return AgentResponse(
            text=text,
            dialect=token_result.dialect.dialect,
            confidence=token_result.dialect.confidence,
            pipeline_used=self.name,
            metadata=metadata,
        )


# ── Router ────────────────────────────────────────────────────────────


class DialectRouter:
    """Classifies requests into a dialect-specific pipeline.

    1. Tokenize with dialect detection
    2. Route to dialect-specific pipeline
    3. Return the dialect, its confidence, and ``dialect_guidance`` metadata

    **This is a classifier, not a brain.** It makes no LLM call. Step 3 used
    to read "Execute with dialect-aware prompts", which described behaviour
    that was removed on 2026-09-17 — a ``provider.chat()`` inside
    ``Pipeline.execute`` was a second mouth outside the supervisor, HITL,
    ``turn_failed`` and the tenant bind. The guidance strings survive as
    metadata for whoever owns the real call; a docstring that still promised
    execution was an invitation to put the second mouth back.
    """

    def __init__(self) -> None:
        self.tokenizer = DualEngineTokenizer()
        self.pipelines: dict[str, BasePipeline] = {
            "kw": KuwaitiPipeline(),
            "msa": MSAPipeline(),
        }

    async def route(self, request: AgentRequest) -> AgentResponse:
        """Route a request to the appropriate pipeline."""
        token_result = self.tokenizer.tokenize(request.text)

        dialect = token_result.dialect.dialect
        pipeline = self.pipelines.get(dialect, self.pipelines["msa"])

        logger.info(
            "Routing request (dialect=%s, confidence=%.2f) to %s pipeline",
            dialect,
            token_result.dialect.confidence,
            pipeline.name,
        )

        return await pipeline.execute(request, token_result)

    def get_pipeline(self, dialect: str) -> BasePipeline:
        """Get pipeline for a specific dialect."""
        return self.pipelines.get(dialect, self.pipelines["msa"])

    def register_pipeline(self, dialect: str, pipeline: BasePipeline) -> None:
        """Register a new dialect pipeline."""
        self.pipelines[dialect] = pipeline
        logger.info("Registered pipeline for dialect '%s': %s", dialect, pipeline.name)
