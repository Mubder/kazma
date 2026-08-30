"""Registration of tool sets that live in other modules.

Extracted from the former ``tool_builtins.py`` god module (2,833
lines) — audit O5. Tool bodies are unchanged; registration order
within this group is preserved.
"""

from __future__ import annotations

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




def register_external_tools(registry: Any) -> None:
    """Register the external tools onto *registry*."""
    from kazma_core.agent.tool_registry import _pending_dispatch_tasks  # noqa: F401

    # ── Register tools from kazma_core/tools/ ──────────────────────
    try:
        from kazma_core.tools.web_search import web_search
        registry.register_function(
            "web_search",
            web_search,
            description=(
                "Search the public web (SearXNG if configured, else DuckDuckGo, "
                "Bing HTML last). Returns markdown titles/URLs/**snippets only**. "
                "For thorough research: run ≥2 queries, then fetch full pages with "
                "read_url_to_file / read_url — do not answer from snippets alone. "
                "Prefer KAZMA_SEARXNG_URL. Args: query, max_results=8."
            ),
            category="search",
        )
    except ImportError:
        logger.debug("web_search not available (missing duckduckgo-search)")
    try:
        from kazma_core.tools.read_url import (
            digest_research_file,
            list_research_chunks,
            read_research_chunk,
            read_url,
            read_url_to_file,
            summarize_research_file,
        )

        registry.register_function(
            "read_url",
            read_url,
            description=(
                "Fetch one public URL; text window (default ~16k, KAZMA_READ_URL_MAX_CHARS). "
                "Args: url, offset=0, max_chars=None. Hard sites: Firecrawl/Jina recovery. "
                "For research: prefer read_url_to_file then digest_research_file; multi-page: crawl_site."
            ),
            category="search",
        )
        registry.register_function(
            "read_url_to_file",
            read_url_to_file,
            description=(
                "Fetch URL and save FULL extract under the workspace "
                "(default research/). Preferred for multi-source research so you can "
                "digest later. Args: url, path=workspace-relative."
            ),
            category="search",
        )
        registry.register_function(
            "list_research_chunks",
            list_research_chunks,
            description=(
                "List chunk indices and previews for a saved research file. "
                "Args: path, chunk_size=4000."
            ),
            category="search",
        )
        registry.register_function(
            "read_research_chunk",
            read_research_chunk,
            description=(
                "Read one chunk of a saved research file. "
                "Args: path, chunk_index=0, chunk_size=4000."
            ),
            category="search",
        )
        registry.register_function(
            "summarize_research_file",
            summarize_research_file,
            description=(
                "Light extractive outline (per-chunk previews). "
                "Args: path, chunk_size=4000, max_chunks=40."
            ),
            category="search",
        )
        registry.register_function(
            "digest_research_file",
            digest_research_file,
            description=(
                "Walk ALL chunks in-tool and return one bounded extractive digest "
                "(default ~12k). Not LLM analysis — use synthesize_from_digests for "
                "cross-source analysis. Args: path, chunk_size=4000, max_output_chars=12000."
            ),
            category="search",
        )
    except ImportError:
        logger.debug("read_url / research tools not available (missing trafilatura)")
    try:
        from kazma_core.tools.research_synthesize import synthesize_from_digests

        registry.register_function(
            "synthesize_from_digests",
            synthesize_from_digests,
            description=(
                "LLM multi-source synthesis from saved research files/digests. "
                "Args: paths (list or comma-separated), question, outline='', max_chars=20000. "
                "Use after acquiring ≥2 sources via read_url_to_file."
            ),
            category="search",
        )
    except ImportError:
        logger.debug("synthesize_from_digests not available")
    try:
        from kazma_core.tools.research_pipeline import run_research_pipeline

        registry.register_function(
            "run_research_pipeline",
            run_research_pipeline,
            description=(
                "Deep research paper mode: multi-query search → parallel acquire → "
                "digest → LLM synthesis → research/reports/.../report.md (+ optional DOCX). "
                "Args: topic, depth='deep'|'standard', max_sources=8, language=''. "
                "Use for comprehensive/thorough research or a full report."
            ),
            category="search",
        )
    except ImportError:
        logger.debug("run_research_pipeline not available")
    try:
        from kazma_core.tools.web_research import crawl_site

        registry.register_function(
            "crawl_site",
            crawl_site,
            description=(
                "Bounded multi-page crawl (same-domain by default). "
                "Args: start_url, profile=research_brief|research_deep|"
                "kb_site|single_page (named cap preset), max_pages "
                "(default from profile; hard max 50; use 12–20 for deep "
                "docs), max_depth, same_domain_only=True, delay_ms, "
                "save=True. Saves pages under workspace; returns markdown "
                "index. SSRF-safe."
            ),
            category="search",
        )
    except ImportError:
        logger.debug("crawl_site not available")
    try:
        from kazma_core.tools.image_gen import generate_image
        registry.register_function("generate_image", generate_image,
            description="Generate an image from a text prompt. provider can be 'auto' (first available), 'pollinations' (free, no key), 'dall-e' (OpenAI), 'stability' (SDXL), or 'flux' (FAL). Returns the saved file path.",
            category="media")
    except ImportError:
        logger.debug("generate_image not available")
    try:
        from kazma_core.tools.vision_analyze import analyze_image
        registry.register_function("analyze_image", analyze_image,
            description="Analyze an image using LLM vision. Provide a local path or URL and an optional question.",
            category="media")
    except ImportError:
        logger.debug("analyze_image not available")
    try:
        from kazma_core.tools.export_session import export_session
        registry.register_function("export_session", export_session,
            description="Export the current conversation session to a file (JSON or Markdown format).",
            category="utility")
    except ImportError:
        logger.debug("export_session not available")
    # ── Agent Skills (agentskills.io / SKILL.md) ───────────────────
    try:
        from kazma_core.agent_skills.tools import (
            activate_skill,
            install_agent_skill,
            list_agent_skills,
            search_agent_skills,
            uninstall_agent_skill,
        )
        registry.register_function(
            "search_agent_skills",
            search_agent_skills,
            description=(
                "Search the open Agent Skills marketplace (GitHub topic:agent-skills) "
                "for installable skills matching a query. Returns repos with stars, "
                "descriptions, and the install_agent_skill command for each."
            ),
            category="skills",
        )
        registry.register_function(
            "list_agent_skills",
            list_agent_skills,
            description=(
                "List installed Agent Skills (SKILL.md / agentskills.io format). "
                "Shows name, description, and location for each skill."
            ),
            category="skills",
        )
        registry.register_function(
            "activate_skill",
            activate_skill,
            description=(
                "Load full instructions for an installed Agent Skill into context. "
                "Call this when a task matches a skill's description before proceeding. "
                "Pass the skill name from list_agent_skills / the available_skills catalog."
            ),
            category="skills",
        )
        registry.register_function(
            "install_agent_skill",
            install_agent_skill,
            description=(
                "Install an Agent Skill from GitHub or a local path. "
                "Preferred over npx/npm (node is not in the shell allowlist). "
                "Accepts owner/repo (e.g. 'shadcn/improve'), a GitHub URL, "
                "or a local path with SKILL.md. One approval covers the whole install. "
                "Hub: https://agentskills.io/"
            ),
            category="skills",
        )
        registry.register_function(
            "uninstall_agent_skill",
            uninstall_agent_skill,
            description="Uninstall a user-level Agent Skill by name.",
            category="skills",
        )
    except Exception as e:
        logger.error("Failed to register agent skills tools: %s", e, exc_info=True)
    # Typed findings scratchpad — survives deterministic context trim
    try:
        from kazma_core.agent.turn_input import apply_scratchpad_write

        async def update_scratchpad(key: str, finding: str) -> str:
            """Save a durable finding for this turn (survives history trim)."""
            return apply_scratchpad_write(key, finding)

        registry.register_function(
            "update_scratchpad",
            update_scratchpad,
            description=(
                "Save a durable intermediate finding/conclusion for THIS turn into the "
                "typed scratchpad (key → finding). Scratchpad entries are re-injected "
                "into the system working-memory block every iteration and SURVIVE "
                "deterministic context trim (unlike raw tool output). "
                "Use for audit facts, bidi counts, root-cause notes. "
                "Args: key (short label), finding (text ≤4000 chars)."
            ),
            category="memory",
        )
    except Exception as e:
        logger.error("Failed to register update_scratchpad: %s", e, exc_info=True)
    # Durable outbound-draft proposals (context-integrity S1-3). Posting tools
    # (x_post / x_schedule_post) REFUSE without a resolvable proposal_id —
    # approval must never depend on the drafts still being in context.
    try:
        async def save_proposal(kind: str, items: list) -> str:
            """Persist outbound drafts durably; returns stable proposal IDs."""
            from kazma_core.agent.artifacts import get_artifact_store as _gas
            from kazma_core.safety.hitl import (
                get_current_tenant_id as _tenant,
                get_current_thread_id as _thread,
            )

            try:
                payload = _gas().save_proposal(
                    _tenant() or "default",
                    _thread() or "",
                    kind,
                    items,
                )
            except ValueError as ve:
                return f"Error: {ve}"
            lines = [f"Proposal saved: {payload['proposal_id']} ({payload['kind']}, {len(payload['items'])} items)."]
            for i in payload["items"]:
                lines.append(f"  - {i['id']}: {str(i['text'])[:80]}")
            lines.append(
                "IDs survive context trim, restarts, and thread switches. Reference "
                "proposal_id when posting — posting tools refuse without it."
            )
            return "\n".join(lines)

        registry.register_function(
            "save_proposal",
            save_proposal,
            description=(
                "Persist an enumerated set of outbound drafts (posts/tweets/"
                "messages) as a durable proposal BEFORE asking for approval or "
                "posting. Returns stable proposal/item IDs that survive context "
                "trim, turn boundaries, and restarts. ALWAYS call this before "
                "posting an enumerated draft set; then post with proposal_id=<id>. "
                "Args: kind (e.g. 'tweets'), items (list of draft texts)."
            ),
            category="memory",
        )
    except Exception as e:
        logger.error("Failed to register save_proposal: %s", e, exc_info=True)
    # Task Ledger — the durable task-state object the user's short
    # continuations ("proceed"/"next") resolve against. The deterministic
    # extractor maintains plan/next_action automatically; THIS tool lets the
    # model maintain goal/findings/steps deliberately (2026-08-27 design).
    try:
        from kazma_core.agent.task_ledger import get_ledger_store

        async def task_ledger_update(
            goal: str = "",
            next_action: str = "",
            add_finding: str = "",
            add_open_question: str = "",
            mark_step_done: int = -1,
            complete: bool = False,
        ) -> str:
            """Update the durable task ledger (goal / next step / findings).

            Maintains the structured task state that binds the user's short
            continuation replies ("proceed", "next") to the RIGHT step and
            survives refreshes and restarts. Set ``goal`` when the mission
            is (re)defined, ``next_action`` to declare what comes next,
            ``add_finding`` to record a durable result, ``mark_step_done``
            (0-based plan index) as steps complete, ``complete`` when the
            task is finished.
            """
            from kazma_core.shutdown import is_shutting_down

            if is_shutting_down():
                return "Error: shutting down"
            tid = ""
            try:
                from kazma_core.safety.hitl import get_current_thread_id

                tid = get_current_thread_id() or ""
            except Exception:
                tid = ""
            if not tid:
                return "Error: no active thread for the task ledger"
            store = get_ledger_store()
            led = store.get_or_create(tid)
            changed: list[str] = []
            if goal.strip():
                led.goal = goal.strip()[:240]
                changed.append("goal")
            if next_action.strip():
                led.declare_next(next_action)
                changed.append("next_action")
            if add_finding.strip():
                led.add_finding(add_finding)
                changed.append("finding")
            if add_open_question.strip():
                led.add_open_question(add_open_question)
                changed.append("open_question")
            if mark_step_done >= 0:
                led.mark_step(mark_step_done, "done")
                changed.append(f"step[{mark_step_done}]=done")
            if complete:
                led.complete()
                changed.append("complete")
            if not changed:
                return "No changes requested — ledger unchanged."
            store.save(led)
            return (
                "Task ledger updated: "
                + ", ".join(changed)
                + f". Current: goal={led.goal[:120]!r} next={led.next_action[:120]!r} "
                f"steps={sum(1 for s in led.steps if s.status == 'done')}/{len(led.steps)} done."
            )

        registry.register_function(
            "task_ledger_update",
            task_ledger_update,
            description=(
                "Update the DURABLE TASK LEDGER — the structured task state "
                "(goal, plan steps, declared next action, findings) that the "
                "user's short continuation replies ('proceed', 'next', "
                "'continue') resolve against. Maintaining it is how you make "
                "'next' unambiguous. Set goal when the mission is defined, "
                "next_action whenever you announce the next step, add_finding "
                "for durable results, mark_step_done (0-based) as steps "
                "complete, complete=True when finished. Args are all optional."
            ),
            category="memory",
        )
    except Exception as e:
        logger.error("Failed to register task_ledger_update: %s", e, exc_info=True)
    # Load and register Top 10 Native Skills
    try:
        from kazma_skills.native_loader import NativeSkillLoader
        loader = NativeSkillLoader(registry)
        loader.register_all()
    except Exception as e:
        logger.error("Failed to load native skills: %s", e, exc_info=True)
    logger.info("Registered %d built-in tools", len(registry._tools))
