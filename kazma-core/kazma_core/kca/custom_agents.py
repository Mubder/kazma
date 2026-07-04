"""KCA Sub-Agents — Specialised institutional analysis agents.

Each agent encapsulates a distinct corporate governance role with a
carefully authored system instruction that shapes its reasoning
behaviour.  All agents share a single GeminiClient instance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from kazma_core.kca.llm import GeminiClient

logger = logging.getLogger(__name__)


@dataclass
class AgentOutput:
    """Structured output from a KCA agent run."""

    agent_name: str
    prompt: str
    raw_response: str


# ── Base agent ──────────────────────────────────────────────────────


class KCABaseAgent:
    """Abstract base for KCA sub-agents.

    Args:
        name: Human-readable agent identifier.
        system_instruction: The system-level prompt that defines the
            agent's persona, tone, and analytical constraints.
        client: A shared :class:`GeminiClient` instance.
    """

    def __init__(
        self,
        name: str,
        system_instruction: str,
        client: GeminiClient,
    ) -> None:
        self.name = name
        self.system_instruction = system_instruction
        self._client = client

    def query(self, prompt: str, temperature: float = 0.3) -> AgentOutput:
        """Execute a single-turn analysis and return structured output.

        Args:
            prompt: The question, incident report, or analysis payload.
            temperature: Sampling temperature forwarded to the LLM.

        Returns:
            An :class:`AgentOutput` containing the agent name, prompt,
            and the model's cleaned text response.
        """
        logger.info("[%s] processing prompt (%d chars)", self.name, len(prompt))
        raw = self._client.generate(
            system_instruction=self.system_instruction,
            prompt=prompt,
            temperature=temperature,
        )
        logger.info("[%s] response received (%d chars)", self.name, len(raw))
        return AgentOutput(
            agent_name=self.name,
            prompt=prompt,
            raw_response=raw,
        )


# ── Concrete KCA agents ─────────────────────────────────────────────


class FounderAnalyst(KCABaseAgent):
    """Institutional-memory guardian and founder-risk analyst.

    Evaluates operational friction, founder dependency, and threats
    to long-term institutional continuity.
    """

    SYSTEM_INSTRUCTION: str = (
        "You are the Founder Analyst for the Kuwait Corporate Atlas (KCA) "
        "framework — an institutional-memory guardian. Your mandate:\n\n"
        "1. PROTECT institutional memory. Identify knowledge that is at "
        "risk of being lost, siloed, or under-documented.\n"
        "2. EVALUATE operational friction. Analyse how the reported event "
        "disrupts workflows, hand-offs, and decision velocity.\n"
        "3. ANALYSE structural founder risks. Flag single points of "
        "failure, bus-factor vulnerabilities, and over-concentration of "
        "critical know-how in one person or team.\n\n"
        "Deliver your analysis in a structured format:\n"
        "- SUMMARY (2–3 sentence executive brief)\n"
        "- INSTITUTIONAL MEMORY RISK (bullets)\n"
        "- OPERATIONAL FRICTION (bullets)\n"
        "- FOUNDER / STRUCTURAL RISK (bullets)\n"
        "- RECOMMENDED ACTIONS (prioritised list)\n\n"
        "Be concise, specific, and actionable.  Do not speculate beyond "
        "the facts provided in the incident report."
    )

    def __init__(self, client: GeminiClient) -> None:
        super().__init__(
            name="FounderAnalyst",
            system_instruction=self.SYSTEM_INSTRUCTION,
            client=client,
        )


class RiskGuardian(KCABaseAgent):
    """Dependency-risk scanner and compliance guardian.

    Evaluates the incident against institutional compliance rules,
    dependency graphs, and knowledge-hoarding patterns.
    """

    SYSTEM_INSTRUCTION: str = (
        "You are the Risk Guardian for the Kuwait Corporate Atlas (KCA) "
        "framework — a dependency-risk scanner and compliance sentinel. "
        "Your mandate:\n\n"
        "1. SCAN for dependency risks. Identify technical, organisational, "
        "and vendor dependencies that are threatened by the reported event.\n"
        "2. DETECT knowledge hoarding. Flag patterns where critical "
        "information or access rights are concentrated in a single "
        "individual or small clique.\n"
        "3. MAP against institutional compliance rules. Cross-reference "
        "the incident against standard continuity, security, and "
        "regulatory requirements.\n\n"
        "Deliver your analysis in a structured format:\n"
        "- SUMMARY (2–3 sentence executive brief)\n"
        "- DEPENDENCY RISK MAP (bullets with severity: HIGH/MEDIUM/LOW)\n"
        "- KNOWLEDGE HOARDING INDICATORS (bullets)\n"
        "- COMPLIANCE GAPS (bullets with regulation references where applicable)\n"
        "- CONTINUITY MATRIX SCORE (1–10 with brief justification)\n\n"
        "You will also receive the Founder Analyst's assessment.  Use it "
        "to enrich your evaluation but do not simply repeat it.  Be "
        "precise and cite specific risks, not generalities."
    )

    def __init__(self, client: GeminiClient) -> None:
        super().__init__(
            name="RiskGuardian",
            system_instruction=self.SYSTEM_INSTRUCTION,
            client=client,
        )
