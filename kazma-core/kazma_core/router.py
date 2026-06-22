"""Dialect-aware routing layer.

Routes requests to appropriate processing pipeline based on detected dialect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from kazma_core.tokenizer import DualEngineTokenizer, TokenResult

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

    def __init__(self) -> None:
        # Kuwaiti-specific system prompt that acknowledges dialect
        self.system_prompt = (
            "You are a helpful assistant that understands Kuwaiti/Gulf Arabic dialect. "
            "Respond in the same dialect the user uses. "
            "Understand colloquial expressions, code-switching between Arabic and English, "
            "and Gulf-specific cultural references."
        )

    async def execute(
        self,
        request: AgentRequest,
        token_result: TokenResult,
    ) -> AgentResponse:
        """Execute Kuwaiti pipeline."""
        dialect_tokens = token_result.dialect_tokens
        code_switch = token_result.code_switch_tokens

        metadata: dict[str, Any] = {
            "pipeline": self.name,
            "total_tokens": len(token_result.tokens),
            "dialect_markers_found": len(dialect_tokens),
            "code_switch_words": len(code_switch),
            "dialect_meanings": {t.text: t.dialect_meaning for t in dialect_tokens if t.dialect_meaning},
        }

        return AgentResponse(
            text=request.text,
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

    def __init__(self) -> None:
        self.system_prompt = (
            "You are a helpful assistant. Respond in Modern Standard Arabic (MSA) "
            "unless the user uses a dialect, in which case match their register."
        )

    async def execute(
        self,
        request: AgentRequest,
        token_result: TokenResult,
    ) -> AgentResponse:
        """Execute MSA pipeline."""
        metadata: dict[str, Any] = {
            "pipeline": self.name,
            "total_tokens": len(token_result.tokens),
            "normalized_words": sum(1 for t in token_result.tokens if t.dialect_meaning is not None),
        }

        return AgentResponse(
            text=request.text,
            dialect=token_result.dialect.dialect,
            confidence=token_result.dialect.confidence,
            pipeline_used=self.name,
            metadata=metadata,
        )


# ── Router ────────────────────────────────────────────────────────────


class DialectRouter:
    """Routes requests to appropriate processing pipeline.

    1. Tokenize with dialect detection
    2. Route to dialect-specific pipeline
    3. Execute with dialect-aware prompts
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
