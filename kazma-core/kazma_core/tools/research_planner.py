"""Adaptive research planner + gap critic (industry R2).

Uses a nested LLM when available; falls back to template queries so offline
and cheap installs still work.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ResearchPlan",
    "GapReport",
    "plan_research_queries",
    "critique_synthesis_gaps",
    "fallback_queries",
]

logger = logging.getLogger(__name__)


@dataclass
class ResearchPlan:
    sub_questions: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    method: str = "fallback"  # llm | fallback
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub_questions": self.sub_questions,
            "search_queries": self.search_queries,
            "success_criteria": self.success_criteria,
            "method": self.method,
        }


@dataclass
class GapReport:
    gaps: list[str] = field(default_factory=list)
    followup_queries: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    method: str = "fallback"
    needs_more: bool = False
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "gaps": self.gaps,
            "followup_queries": self.followup_queries,
            "unsupported_claims": self.unsupported_claims,
            "method": self.method,
            "needs_more": self.needs_more,
        }


def fallback_queries(topic: str, *, language: str = "", max_queries: int = 8) -> list[str]:
    """Deterministic query angles when LLM planner is unavailable."""
    base = topic if not language else f"{topic} {language}"
    qs = [
        base,
        f"{topic} overview analysis",
        f"{topic} benefits risks",
        f"{topic} latest developments",
        f"{topic} comparison alternatives",
        f"{topic} official documentation",
        f"{topic} research paper study",
        f"{topic} criticism limitations",
        f"{topic} best practices",
        f"{topic} case study",
    ]
    return qs[: max(1, min(12, int(max_queries or 8)))]


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _parse_json_blob(text: str) -> dict[str, Any] | None:
    t = (text or "").strip()
    if not t:
        return None
    # strip fences
    if "```" in t:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
        if m:
            t = m.group(1).strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        # try first { ... }
        m = re.search(r"\{[\s\S]*\}", t)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None


async def _llm_json(system: str, user: str, *, max_tokens: int = 1200) -> str:
    from kazma_core.model_registry import get_model_registry

    reg = get_model_registry()
    client = reg.get_client()
    model = None
    try:
        profile = reg.get_active_profile() or {}
        model = profile.get("model")
    except Exception:
        pass
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    # Resilient path (audit): transient retries + failover chain + ledger —
    # previously a bare client.chat with no resilience.
    from kazma_core.agent.resilient_chat import resilient_chat

    result = await resilient_chat(
        client,
        messages=messages,
        tools=None,
        model=model,
        max_attempts=2,
        max_tokens=max_tokens,
        label="research-planner",
    )
    if hasattr(result, "content"):
        return str(result.content or "")
    if isinstance(result, dict):
        return str(result.get("content") or "")
    return str(result or "")


async def plan_research_queries(
    topic: str,
    *,
    language: str = "",
    max_queries: int = 8,
    is_deep: bool = True,
) -> ResearchPlan:
    """Produce search queries + sub-questions for a research topic."""
    topic = (topic or "").strip()
    n = max(3, min(12, int(max_queries or 8)))
    if not topic:
        return ResearchPlan(search_queries=[], method="fallback")

    if not _env_truthy("KAZMA_RESEARCH_LLM_PLANNER", True):
        qs = fallback_queries(topic, language=language, max_queries=n)
        return ResearchPlan(
            sub_questions=qs[: min(5, n)],
            search_queries=qs,
            success_criteria=["multi-source", "cited URLs"],
            method="fallback",
        )

    system = (
        "You are a research planner. Output ONLY valid JSON (no markdown prose) with keys:\n"
        '  "sub_questions": string[]  (3-7 distinct research angles)\n'
        '  "search_queries": string[] (concrete web search strings)\n'
        '  "success_criteria": string[] (what a good report must cover)\n'
        "Rules: diverse angles (definition, evidence, alternatives, risks, official docs); "
        "no duplicates; search_queries must be usable as web search strings."
    )
    user = (
        f"Topic: {topic}\n"
        f"Language hint: {language or 'en'}\n"
        f"Depth: {'deep' if is_deep else 'standard'}\n"
        f"Need up to {n} search_queries.\n"
    )
    try:
        raw = await _llm_json(system, user, max_tokens=900)
        obj = _parse_json_blob(raw) or {}
        sq = [str(x).strip() for x in (obj.get("search_queries") or []) if str(x).strip()]
        sub = [str(x).strip() for x in (obj.get("sub_questions") or []) if str(x).strip()]
        crit = [str(x).strip() for x in (obj.get("success_criteria") or []) if str(x).strip()]
        if len(sq) < 2:
            raise ValueError("planner returned too few queries")
        # pad with fallback if short
        if len(sq) < n:
            for f in fallback_queries(topic, language=language, max_queries=n):
                if f not in sq:
                    sq.append(f)
                if len(sq) >= n:
                    break
        return ResearchPlan(
            sub_questions=sub[:8] or sq[:5],
            search_queries=sq[:n],
            success_criteria=crit[:8] or ["multi-source citations", "risks covered"],
            method="llm",
            raw=raw[:2000],
        )
    except Exception as exc:
        logger.info("[research_planner] LLM plan failed (%s) — fallback", exc)
        qs = fallback_queries(topic, language=language, max_queries=n)
        return ResearchPlan(
            sub_questions=qs[:5],
            search_queries=qs,
            success_criteria=["multi-source", "cited URLs"],
            method="fallback",
            raw=str(exc)[:200],
        )


async def critique_synthesis_gaps(
    topic: str,
    synthesis: str,
    *,
    sources_summary: str = "",
    max_followups: int = 3,
) -> GapReport:
    """Find unsupported claims / missing angles; suggest follow-up searches."""
    if not _env_truthy("KAZMA_RESEARCH_GAP_LOOP", True):
        return GapReport(method="disabled", needs_more=False)

    synth = (synthesis or "").strip()
    if len(synth) < 200:
        return GapReport(
            gaps=["synthesis too short"],
            followup_queries=[f"{topic} official documentation"],
            needs_more=True,
            method="heuristic",
        )

    # Heuristic: no Sources / few URLs
    url_n = len(re.findall(r"https?://", synth))
    has_sources = bool(re.search(r"^##\s+sources\b", synth, re.I | re.M))
    if not has_sources or url_n < 2:
        return GapReport(
            gaps=["missing or thin Sources/citations"],
            followup_queries=[
                f"{topic} official documentation",
                f"{topic} peer reviewed study",
            ][:max_followups],
            needs_more=True,
            method="heuristic",
        )

    if not _env_truthy("KAZMA_RESEARCH_LLM_CRITIC", True):
        return GapReport(method="heuristic", needs_more=False)

    system = (
        "You critique a research draft for gaps. Output ONLY JSON with keys:\n"
        '  "gaps": string[] — missing angles or thin coverage\n'
        '  "unsupported_claims": string[] — claims that lack clear support\n'
        '  "followup_queries": string[] — web searches to fill gaps (0-4)\n'
        '  "needs_more": boolean — true if another acquire pass is warranted\n'
        "Be conservative: needs_more=true only if gaps are material."
    )
    user = (
        f"Topic: {topic}\n\n"
        f"Sources available:\n{sources_summary[:3000]}\n\n"
        f"Draft (truncated):\n{synth[:6000]}\n"
    )
    try:
        raw = await _llm_json(system, user, max_tokens=800)
        obj = _parse_json_blob(raw) or {}
        gaps = [str(x).strip() for x in (obj.get("gaps") or []) if str(x).strip()]
        unsup = [
            str(x).strip()
            for x in (obj.get("unsupported_claims") or [])
            if str(x).strip()
        ]
        fu = [
            str(x).strip()
            for x in (obj.get("followup_queries") or [])
            if str(x).strip()
        ][: max(0, min(6, int(max_followups)))]
        needs = bool(obj.get("needs_more")) and bool(fu)
        return GapReport(
            gaps=gaps[:10],
            unsupported_claims=unsup[:10],
            followup_queries=fu,
            needs_more=needs,
            method="llm",
            raw=raw[:2000],
        )
    except Exception as exc:
        logger.info("[research_planner] critic failed (%s)", exc)
        return GapReport(method="fallback", needs_more=False, raw=str(exc)[:200])
