"""Deep-research pipeline tools.

Extracted from the former ``tool_builtins.py`` god module (2,833
lines) — audit O5. Tool bodies are unchanged; registration order
within this group is preserved.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

def _qnorm(q: str) -> str:
    """Normalize a memory q-filter: underscores/hyphens -> single spaces,
    lowercased. Paired with REPLACE(...) in SQL so 'memory system' matches
    user_memory_system (2026-08-27 report — the literal LIKE filter missed
    it while FTS memory_search matched fine)."""
    import re as _re

    return _re.sub(r"[_\-\s]+", " ", str(q or "").strip().lower()).strip()




def register_research_tools(registry: Any) -> None:
    """Register the research tools onto *registry*."""
    from kazma_core.agent.tool_registry import _pending_dispatch_tasks  # noqa: F401

    # ── Research planning / session tools (audit M1: previously defined        # and exported in tools/ but never registered — the model could not
    # call them) ────────────────────────────────────────────────────

    @registry.register(
        description=(
            "Plan a research task: produces sub-questions, concrete web search "
            "queries, and success criteria for a topic. Use before running "
            "run_research_pipeline when you want to inspect or adjust the plan."
        ),
        category="research",
    )
    async def plan_research_queries(
        topic: str, language: str = "", max_queries: int = 8, is_deep: bool = True
    ) -> str:
        try:
            from kazma_core.tools.research_planner import plan_research_queries as _plan

            plan = await _plan(topic, language=language, max_queries=max_queries, is_deep=is_deep)
            return json.dumps(plan.to_dict(), ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Error: research planning failed — {exc}"
    @registry.register(
        description=(
            "Critique a research synthesis for unsupported claims and missing "
            "angles; returns follow-up search suggestions. Use after drafting "
            "an answer from multiple sources to check coverage."
        ),
        category="research",
    )
    async def critique_synthesis_gaps(
        topic: str, synthesis: str, sources_summary: str = "", max_followups: int = 3
    ) -> str:
        try:
            from kazma_core.tools.research_planner import critique_synthesis_gaps as _critique

            report = await _critique(
                topic, synthesis, sources_summary=sources_summary, max_followups=max_followups
            )
            return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Error: gap critique failed — {exc}"
    @registry.register(
        description=(
            "List saved research reports (papers) from past research pipeline runs. "
            "Use to reference or continue earlier research."
        ),
        category="research",
    )
    async def list_research_papers(limit: int = 50) -> str:
        try:
            from kazma_core.tools.research_pipeline import list_research_papers as _list

            papers = _list(limit=max(1, min(200, int(limit))))
            return json.dumps(papers, ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Error: listing research papers failed — {exc}"
    @registry.register(
        description=(
            "Check research readiness: verifies search backends, fetch ladder, and "
            "pipeline prerequisites are operational. Use to diagnose why research "
            "is failing before launching a deep run."
        ),
        category="research",
    )
    async def research_readiness(probe_search: bool = False) -> str:
        try:
            from kazma_core.tools.research_readiness import research_readiness as _ready

            report = _ready(probe_search=bool(probe_search))
            return json.dumps(report, ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            return f"Error: readiness check failed — {exc}"
    @registry.register(
        description=(
            "Start a deep research session in the background: runs the full "
            "research pipeline (plan → search → fetch → digest → synthesize) "
            "and returns a session id to poll for progress. Prefer this over "
            "run_research_pipeline for long tasks."
        ),
        category="research",
    )
    async def start_deep_research(
        topic: str, depth: str = "deep", max_sources: int = 8, export_docx: bool = False
    ) -> str:
        try:
            from kazma_core.tools.research_session import start_deep_research as _start

            sess = await _start(topic, depth=depth, max_sources=max_sources, export_docx=export_docx)
            return json.dumps(sess.to_dict(), ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            return f"Error: starting deep research failed — {exc}"
