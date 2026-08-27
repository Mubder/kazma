"""Supervisor node — the ReAct brain (extracted from graph_builder)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from kazma_core.agent.graph_helpers import (
    _ensure_personality,
    _memory_explain_cv,
    _rag_top_k,
    is_unusable_assistant_content,
    prune_messages_if_exceeding_cap,
    sanitize_tool_chains,
)
from kazma_core.agent.plan_fence import (
    PLAN_EXECUTE_CONTINUE,
    PLAN_EXECUTE_FINAL,
    normalize_plan_fence,
    should_execute_plan_only_hop,
)
from kazma_core.agent.state import NodeName, PendingToolCall, SupervisorState
from kazma_core.llm_provider import LLMProvider
from kazma_core.llm_stream import invoke_llm_chat
from kazma_core.summarizer import _normalize_msg

logger = logging.getLogger(__name__)

# ── Failover state (module-level, process-wide) ──────────────────────
# One-off failover clients cached by model id (created only on primary
# failure; never mutate the active profile). Cooldowns give a failing
# provider time to recover before it is tried again.
_failover_clients: dict[str, Any] = {}
_failover_cooldowns: dict[str, float] = {}

async def supervisor_node(
    state: SupervisorState,
    *,
    llm: LLMProvider,
    system_prompt: str,
    tool_definitions: list[dict[str, Any]],
    tool_executor: Any,  # LocalToolRegistry or ToolRegistry
    cost_breaker: Any,  # CostCircuitBreaker
    authority: Any,  # ContextAuthority
    tracer: Any,  # KazmaTracer
    model_router: Any | None = None,  # ModelRouter for multi-model routing
    personality_prompt: str | None = None,  # Active personality system prompt
) -> dict[str, Any]:
    """Supervisor node — the brain of the ReAct loop.

    Responsibilities:
      1. Enforce cost circuit breaker.
      2. Check & trigger 80% context compaction.
      3. Call the LLM with conversation + tool schemas.
      4. Route: tool_calls → TOOL_WORKER, text → RESPOND.
    """
    iteration = state.get("iteration", 0)
    messages = [_normalize_msg(m) for m in state.get("messages", [])]

    logger.info("[Supervisor] iteration=%d messages=%d", iteration, len(messages))

    # ── Iteration-efficiency nudges (audit 2026-08-15) ──────────────
    # Long tasks burn iterations on one-tool-per-turn patterns (71 of 117
    # calls were python_exec on a calendar reproduction task). Inject a
    # strategy-change hint at milestones so the model course-corrects
    # BEFORE hitting the hard iteration wall.
    _ITER_NUDGE_MARKERS = (20, 40, 60, 80)
    _budget_nudge: dict[str, Any] | None = None
    if iteration in _ITER_NUDGE_MARKERS:
        _max_iter_now = int(state.get("max_iterations") or 15)
        _remaining = max(0, _max_iter_now - iteration)
        _budget_nudge = {
            "role": "system",
            "content": (
                f"SYSTEM BUDGET CHECK: You have used {iteration} iterations "
                f"({_remaining} remaining of {_max_iter_now}). If you are making "
                "steady progress, continue. If you are LOOPING (re-reading files "
                "you already read, writing debug scripts, retrying similar code):\n"
                "- BATCH tool calls: issue MULTIPLE reads/writes in ONE response\n"
                "- Use structured tools (generate_pdf, file_write) not python_exec\n"
                "- Summarize what you have so far and produce the final output NOW"
            ),
        }
        logger.info(
            "[Supervisor] iteration-efficiency nudge injected at iteration=%d "
            "(ephemeral — not checkpointed)",
            iteration,
        )

    # ── Mission mode: auto-extend past soft max_iterations ─────────
    # Budget /long still force-stops. Mission mode resets the wave counter
    # and continues until mission_hard_rounds (safety wall), without asking
    # the user to "Proceed" after every 40 rounds.
    _mission_patch: dict[str, Any] = {}
    try:
        from kazma_core.agent.long_task import (
            is_mission_mode,
            mission_hard_rounds,
            record_long_task_event,
        )

        _tid = str(state.get("thread_id") or "") or None
        _max_iter = int(state.get("max_iterations") or 15)
        _rounds_used = int(state.get("mission_rounds_used") or 0)
        if (
            _tid
            and is_mission_mode(_tid)
            and iteration >= _max_iter
        ):
            _hard = int(state.get("mission_hard_rounds") or mission_hard_rounds())
            _next_used = _rounds_used + max(iteration, _max_iter)
            if _next_used < _hard:
                logger.warning(
                    "[Supervisor] MISSION wave extend: used≈%d hard=%d "
                    "(reset iteration %d→0)",
                    _next_used,
                    _hard,
                    iteration,
                )
                record_long_task_event("mission_wave")
                messages = list(messages) + [
                    {
                        "role": "system",
                        "content": (
                            f"[MISSION AUTO-CONTINUE — wave complete, "
                            f"~{_next_used}/{_hard} rounds used]\n"
                            "Do **not** stop for user confirmation. "
                            "Continue remaining work only; avoid re-doing "
                            "completed steps. When the full user goal is "
                            "satisfied, write the final report with "
                            "no further tool calls."
                        ),
                    }
                ]
                iteration = 0
                _mission_patch = {
                    "mission_rounds_used": _next_used,
                    "mission_hard_rounds": _hard,
                    "messages": messages,
                    "auto_continue": True,
                }
            else:
                logger.warning(
                    "[Supervisor] MISSION hard wall reached used≈%d hard=%d — "
                    "forcing final synthesis",
                    _next_used,
                    _hard,
                )
                record_long_task_event("mission_hard_wall")
                _mission_patch = {
                    "mission_rounds_used": _next_used,
                    "force_synthesis": True,
                    "next_node": NodeName.RESPOND,
                }
    except Exception:
        logger.debug("[Supervisor] mission extend skipped", exc_info=True)

    if _mission_patch.get("next_node") == NodeName.RESPOND:
        return {
            **_mission_patch,
            "iteration": iteration,
            "messages": messages,
        }
    if _mission_patch.get("messages") is not None:
        messages = _mission_patch["messages"]

    # Carry mission counters on every later return from this supervisor visit
    _mission_carry: dict[str, Any] = {}
    for _mk in ("mission_rounds_used", "mission_hard_rounds", "auto_continue"):
        if _mk in _mission_patch:
            _mission_carry[_mk] = _mission_patch[_mk]
    if not _mission_carry and int(state.get("mission_hard_rounds") or 0) > 0:
        _mission_carry = {
            "mission_rounds_used": int(state.get("mission_rounds_used") or 0),
            "mission_hard_rounds": int(state.get("mission_hard_rounds") or 0),
        }

    # Long-task progress heartbeat (Telegram/gateway when progress sender set)
    try:
        from kazma_core.agent.long_task import maybe_heartbeat

        _recent_tools = [
            str(t.get("name") or "")
            for t in (state.get("tool_calls_done") or [])[:6]
            if isinstance(t, dict)
        ]
        _wave = int(state.get("mission_rounds_used") or 0) + int(iteration or 0)
        await maybe_heartbeat(
            thread_id=str(state.get("thread_id") or "") or None,
            iteration=int(iteration or 0) if not _mission_patch else max(1, int(iteration or 0) or 5),
            max_iterations=int(state.get("max_iterations") or 15),
            last_tools=_recent_tools,
        )
        # Extra heartbeat on mission wave boundaries
        if _mission_patch.get("mission_rounds_used"):
            await maybe_heartbeat(
                thread_id=str(state.get("thread_id") or "") or None,
                iteration=5,  # force send
                max_iterations=int(state.get("max_iterations") or 15),
                last_tools=[f"mission_wave≈{_mission_patch['mission_rounds_used']}"],
            )
    except Exception:
        logger.debug("[Supervisor] long-task heartbeat skipped", exc_info=True)

    # ── Reset tool circuit breaker and cost breaker timer on new user turn ──
    # The breaker trips after 2 consecutive empty/failed tool results.
    # Without this reset, the breaker stays tripped permanently across
    # all subsequent turns (state persists in the checkpointer).
    breaker_reset = {}
    if iteration == 0:
        if state.get("circuit_breaker_tripped", False) or state.get("consecutive_tool_failures", 0) > 0:
            logger.info("[Supervisor] Resetting tool circuit breaker for new turn")
        breaker_reset = {"circuit_breaker_tripped": False, "consecutive_tool_failures": 0}
        if cost_breaker and hasattr(cost_breaker, "record_user_interaction"):
            cost_breaker.record_user_interaction()

    # ── Cost breaker gate ──────────────────────────────────────────
    if cost_breaker.should_halt():
        logger.warning("[Supervisor] Cost breaker tripped — forcing respond")
        return {
            **breaker_reset,
            **_mission_carry,
            "next_node": NodeName.RESPOND,
            "messages": messages
            + [
                {
                    "role": "assistant",
                    "content": "⚠️ ميزانية الجلسة انتهت. أعد التشغيل أو اتصل بالمسؤول.",
                }
            ],
        }

    # Mid-turn LLM summarization removed. Oversized contexts are handled
    # later via deterministic trim_messages (after Working Memory is set).
    # ContextAuthority may still run for observability / needs_compaction
    # flag, but we never replace messages with an LLM summary mid-loop.
    try:
        state_for_check = {**state, "messages": messages}
        if authority is not None and hasattr(authority, "counter"):
            if authority.counter.should_compact(messages) or state.get("needs_compaction"):
                breaker_reset = {
                    **breaker_reset,
                    "needs_compaction": True,
                }
                logger.info(
                    "[Supervisor] Context over budget — will apply deterministic trim "
                    "(no LLM summarize)"
                )
    except Exception:
        logger.debug("[Supervisor] budget probe skipped", exc_info=True)

    # ── Ensure system prompt and personality are present ───────────
    # The personality prompt is injected at position 0, replacing any
    # stale personality message from a previous personality setting.
    # The base system_prompt goes at position 0 if no system message
    # exists yet. Personality goes right after the base system prompt.
    if personality_prompt:
        messages = _ensure_personality(messages, system_prompt, personality_prompt)
    elif not any(m.get("role") == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": system_prompt})

    # ── LLM call ──────────────────────────────────────────────────
    # Extract the latest user message (used by both the model router and
    # per-turn memory retrieval below).
    last_user_content = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_content = str(m.get("content", ""))
            break

    # Same-session short continuations ("Proceed", "try now") must inherit
    # the prior task — expand recall query + pin a continuity system note.
    # Opposite case: bulk "add this to ShipX memory" after a reminder thread
    # must NOT inherit ZCode/session topics via recall session_boost.
    # Topic shifts drop session boost and supersede the open task focus.
    _recall_query = last_user_content
    _store_intent = False
    _graph_cleanup = False
    _multi_part = False
    _store_focus = ""
    _recall_session_id = state.get("thread_id")
    _intent_mode = "normal"
    # Merged into every supervisor return after classification (focus lifecycle).
    intent_patch: dict[str, Any] = {}
    try:
        from kazma_core.agent.state import TaskStatus
        from kazma_core.agent.turn_input import (
            classify_turn_intent,
            extract_store_focus_query,
            latest_turn_priority_note,
            prior_substantive_user_texts,
        )

        _prev_status = str(state.get("task_status") or TaskStatus.IDLE)
        _prev_goal = str(state.get("task_goal_summary") or "")
        _intent_mode = classify_turn_intent(
            last_user_content,
            messages=messages,
            task_status=_prev_status,
            task_goal_summary=_prev_goal,
            use_embedding_drift=(iteration == 0),
        )
        _graph_cleanup = _intent_mode == "cleanup"
        _multi_part = _intent_mode == "multi_part"
        _store_intent = _intent_mode in ("store", "multi_part")
        _is_continue = _intent_mode == "continue"
        _is_shift = _intent_mode == "shift"

        intent_patch["intent_mode"] = _intent_mode

        # ── Intent Engine (§14 of KAZMA_INTENT_ENGINE.md) ─────────────
        # Classify every turn (focus + acts + entities) and write the
        # decision onto SupervisorState. Route is execute/constrain/loop.
        # Phase 2+: execute allowlist = {document_generate, research_deep};
        # multi-act research+document dispatches the composer.
        _decision = None
        if iteration == 0:
            try:
                from kazma_core.agent.intent.classify import classify_turn
                from kazma_core.agent.intent.config import intent_engine_enabled

                if intent_engine_enabled():
                    _atts = list(state.get("active_attachments") or [])
                    if not _atts:
                        try:
                            from kazma_core.agent.turn_input import extract_active_attachments

                            _atts = extract_active_attachments(
                                messages, user_text=last_user_content
                            )
                        except Exception:
                            _atts = []
                    _decision = await classify_turn(
                        last_user_content,
                        messages=messages,
                        attachments=_atts,
                        task_status=_prev_status,
                        task_goal_summary=_prev_goal,
                        llm=llm,
                        use_embedding_drift=(iteration == 0),
                        focus=_intent_mode,
                    )
                    _intent_mode = _decision.focus
                    intent_patch["intent_mode"] = _decision.focus
                    intent_patch["intent_route"] = str(_decision.route)
                    intent_patch["intent_acts"] = [
                        {"kind": a.kind, "confidence": a.confidence, "slots": a.slots, "source": a.source}
                        for a in _decision.acts
                    ]
                    intent_patch["intent_reason"] = _decision.reason
                    logger.info(
                        "[Supervisor] Intent Engine: focus=%s route=%s acts=%s reason=%s source=%s",
                        _decision.focus,
                        _decision.route,
                        [(a.kind, round(a.confidence, 2)) for a in _decision.acts],
                        _decision.reason,
                        _decision.source,
                    )
            except Exception:
                logger.debug("[Supervisor] Intent engine failed (non-fatal)", exc_info=True)
                _decision = None

        # Execute (Phase 2+: document_generate / research_deep / composer)
        if _decision is not None and _decision.route.value == "execute" and _decision.handler:
            try:
                from kazma_core.agent.intent.registry import get_registry as _get_intent_registry

                _h = _get_intent_registry().resolve(_decision.handler)
                if _h is not None:
                    _res = await asyncio.wait_for(
                        _h.run(_decision, {**state, "messages": messages}, llm=llm, tool_executor=tool_executor),
                        timeout=_h.timeout_seconds,
                    )
                else:
                    _res = None
            except Exception as exc:
                logger.warning("[Supervisor] handler %s failed: %s — loop", _decision.handler, exc)
                _res = None
            if _res is not None and _res.ok and not _res.escalate:
                return {
                    **intent_patch,
                    "messages": messages + [{"role": "assistant", "content": _res.message}],
                    "next_node": NodeName.RESPOND,
                    "iteration": iteration + 1,
                }
            # else fall through to loop

        # Constrain: inject plan_note once
        if (
            _decision is not None
            and _decision.route.value == "constrain"
            and _decision.plan_note
            and not any(
                m.get("role") == "system" and "INTENT ENGINE" in str(m.get("content") or "")
                for m in messages
            )
        ):
            messages = list(messages) + [{"role": "system", "content": _decision.plan_note}]

        # Collapse prior multi-step tool payloads when focus is done/shifted
        # so attention is not dominated by stale tool chains (PR5).
        if iteration == 0:
            try:
                from kazma_core.agent.topic_drift import (
                    should_stub_prior_tools,
                    stub_prior_tool_chains,
                )

                if should_stub_prior_tools(
                    intent_mode=_intent_mode,
                    prev_task_status=_prev_status,
                ):
                    messages = stub_prior_tool_chains(
                        messages, keep_last_n_user_turns=1
                    )
            except Exception:
                logger.debug("[Supervisor] tool stub skipped", exc_info=True)

        if _graph_cleanup:
            # Focus list/merge tools on named projects in the message
            _store_focus = extract_store_focus_query(last_user_content) or "kazma entities graph"
            _recall_query = _store_focus
            _recall_session_id = None
            intent_patch["task_status"] = TaskStatus.IN_PROGRESS
            intent_patch["task_goal_summary"] = (_store_focus or last_user_content)[:240]
            logger.info(
                "[Supervisor] intent_mode=cleanup recall=%r (no session_boost)",
                (_recall_query or "")[:80],
            )
        elif _store_intent or _multi_part:
            _store_focus = extract_store_focus_query(last_user_content)
            if _multi_part and not _store_focus:
                # Prefer project names from the user line for multi-part PAT/read work
                _store_focus = (last_user_content or "")[:200]
            if _store_focus:
                _recall_query = _store_focus
            # Drop same-thread session boost so prior reminder turns do not
            # drown a document-store request in ZCode/Grok quota facts.
            _recall_session_id = None
            intent_patch["task_status"] = TaskStatus.IN_PROGRESS
            intent_patch["task_goal_summary"] = (
                _store_focus or last_user_content
            )[:240]
            logger.info(
                "[Supervisor] intent_mode=%s recall=%r (no session_boost)",
                "multi_part" if _multi_part else "store",
                (_recall_query or "")[:80],
            )
        elif _is_continue:
            prev_users = prior_substantive_user_texts(
                messages, exclude=last_user_content, min_chars=8, limit=3
            )
            if prev_users:
                # Prefer last 3 substantive user turns as the real goal.
                _recall_query = " | ".join(prev_users[-3:])
                intent_patch["task_goal_summary"] = _recall_query[:240]
                logger.info(
                    "[Supervisor] intent_mode=continue phrase=%r expanded_recall from %d prior user turns",
                    last_user_content[:40],
                    len(prev_users[-3:]),
                )
            # Keep open focus; only re-open if we were idle/completed.
            if _prev_status in ("", TaskStatus.IDLE, TaskStatus.COMPLETED, TaskStatus.SUPERSEDED):
                intent_patch["task_status"] = TaskStatus.IN_PROGRESS
            else:
                intent_patch["task_status"] = _prev_status or TaskStatus.IN_PROGRESS
            # Always inject continuity instruction when history has more than
            # this one short line (industry: do not re-ask "what should I do?").
            _user_turns = sum(
                1
                for m in messages
                if isinstance(m, dict)
                and m.get("role") == "user"
                and str(m.get("content") or "").strip()
            )
            if iteration == 0 and _user_turns >= 2:
                _cont_note = (
                    "CONTINUITY: The user sent a short follow-up "
                    f"({last_user_content!r}). The open task is in the conversation "
                    "history above (and prior user messages). Continue that work "
                    "— all unfinished steps (GitHub read, memory store, analysis), "
                    "not only graph cleanup if that was only part of the goal. "
                    "Do NOT claim you forgot the task or ask what to do unless the "
                    "history truly has no prior goal."
                )
                messages.insert(
                    1 if messages and messages[0].get("role") == "system" else 0,
                    {"role": "system", "content": _cont_note},
                )
        elif _is_shift:
            # Soft-reset focus: do not session-boost old-thread episodes and
            # never expand recall to prior goals on a pivot.
            _recall_session_id = None
            _recall_query = last_user_content
            intent_patch["task_status"] = TaskStatus.SUPERSEDED
            intent_patch["auto_continue"] = False
            intent_patch["task_goal_summary"] = (last_user_content or "")[:240]
            logger.info(
                "[Supervisor] intent_mode=shift — session_boost off, auto_continue cleared, "
                "prior task superseded",
            )
        else:
            # normal chat — keep session boost; do not expand query
            if last_user_content.strip() and len(last_user_content.strip()) >= 40:
                intent_patch["task_status"] = TaskStatus.IN_PROGRESS
                intent_patch["task_goal_summary"] = last_user_content.strip()[:240]
            logger.info(
                "[Supervisor] intent_mode=normal session_boost=%s",
                bool(_recall_session_id),
            )

        # Priority pin: every non-continuation turn (drop the old len>=80 gate).
        # Continuations get CONTINUITY above instead of fighting priority text.
        # Only on iteration 0 so ReAct tool rounds do not re-stack system notes.
        if iteration == 0 and last_user_content.strip() and not _is_continue:
            _prio = latest_turn_priority_note(
                store_intent=_store_intent,
                graph_cleanup=_graph_cleanup,
                multi_part=_multi_part,
                topic_shift=_is_shift,
                focus=_store_focus,
            )
            _ins = 1 if messages and messages[0].get("role") == "system" else 0
            messages.insert(_ins, {"role": "system", "content": _prio})

        if iteration == 0:
            logger.info(
                "[Supervisor] intent_mode=%s task_status=%s session_boost=%s recall_q=%r",
                _intent_mode,
                intent_patch.get("task_status", _prev_status),
                _recall_session_id is not None,
                (_recall_query or "")[:80],
            )
        # Mid-turn ReAct: keep prior focus fields; do not re-supersede from the
        # same user message re-read as "latest" after tool messages.
        if iteration > 0:
            intent_patch = {
                "intent_mode": state.get("intent_mode") or _intent_mode,
                "task_status": state.get("task_status")
                or intent_patch.get("task_status")
                or _prev_status,
                "task_goal_summary": state.get("task_goal_summary")
                or intent_patch.get("task_goal_summary")
                or "",
            }
            if state.get("auto_continue") is False or _is_shift:
                # Preserve soft-reset once shift cleared auto_continue
                if state.get("task_status") == "superseded" or _is_shift:
                    intent_patch["auto_continue"] = False
                    intent_patch["task_status"] = "superseded"
    except Exception:
        logger.debug("[Supervisor] continuation/store intent expand skipped", exc_info=True)
        intent_patch = {}

    # ── Explicit Working Memory (immutable turn anchors) ───────────
    # Parse once at iteration 0; re-inject the system anchor every iteration.
    from kazma_core.agent.turn_input import (
        extract_active_attachments,
        extract_latest_user_text,
        filter_tools_for_constraints,
        format_working_memory_anchor,
        parse_hard_constraints,
        resolve_trim_token_budget,
        trim_messages_deterministic,
        WORKING_MEMORY_MARKER,
    )

    from kazma_core.agent.turn_input import (
        should_suppress_memory_recall,
        should_quarantine_documents_search,
        set_active_turn_context,
        reset_active_turn_context,
        bind_scratchpad_thread,
        reset_scratchpad_thread,
        drain_scratchpad_writes,
    )

    # Merge any tool-side scratchpad writes from the previous tool_worker hop.
    _scratch = dict(state.get("scratchpad") or {})
    try:
        _delta = drain_scratchpad_writes(str(state.get("thread_id") or ""))
        if _delta:
            _scratch.update(_delta)
    except Exception:
        pass

    working_memory_patch: dict[str, Any] = {}
    if iteration == 0:
        # Prefer transport-pinned fields when already set on input_state.
        _goal = str(state.get("active_goal") or "").strip()
        if not _goal:
            _goal = (last_user_content or extract_latest_user_text(messages) or "").strip()
        _atts = list(state.get("active_attachments") or [])
        if not _atts:
            _atts = extract_active_attachments(messages, user_text=_goal)
        _constraints = list(state.get("hard_constraints") or [])
        if not _constraints:
            _constraints = parse_hard_constraints(_goal)
        if state.get("scratchpad"):
            _scratch = dict(state.get("scratchpad") or {})
        working_memory_patch = {
            "active_goal": _goal[:4000],
            "active_attachments": _atts,
            "hard_constraints": _constraints,
            "scratchpad": _scratch,
        }
        if _constraints:
            logger.info(
                "[Supervisor] hard_constraints=%s attachments=%d goal_chars=%d",
                _constraints,
                len(_atts),
                len(_goal),
            )
    else:
        working_memory_patch = {
            "active_goal": str(state.get("active_goal") or ""),
            "active_attachments": list(state.get("active_attachments") or []),
            "hard_constraints": list(state.get("hard_constraints") or []),
            "scratchpad": _scratch,
        }
        # If iter>0 but goal empty (old checkpoint), backfill from messages once.
        if not working_memory_patch["active_goal"] and last_user_content:
            working_memory_patch["active_goal"] = last_user_content.strip()[:4000]
            if not working_memory_patch["hard_constraints"]:
                working_memory_patch["hard_constraints"] = parse_hard_constraints(
                    last_user_content
                )
            if not working_memory_patch["active_attachments"]:
                working_memory_patch["active_attachments"] = extract_active_attachments(
                    messages, user_text=last_user_content
                )

    # First-class plan mode: union read_only constraints + system note.
    # Proceed / `/plan go` exits and injects the execute note instead.
    _plan_kind = "off"
    try:
        from kazma_core.agent.plan_mode import apply_plan_mode_to_turn

        _hc, messages, _plan_kind = apply_plan_mode_to_turn(
            str(state.get("thread_id") or ""),
            hard_constraints=list(working_memory_patch.get("hard_constraints") or []),
            messages=messages,
            user_text=last_user_content or "",
        )
        working_memory_patch["hard_constraints"] = _hc
        if _plan_kind != "off":
            logger.info("[Supervisor] plan_mode=%s thread=%s", _plan_kind, str(state.get("thread_id") or "")[:12])
    except Exception:
        logger.debug("[Supervisor] plan mode skipped", exc_info=True)
        _plan_kind = "off"

    # Merge working memory into intent_patch so every return path persists it.
    intent_patch = {**working_memory_patch, **intent_patch}

    _intent_for_anchor = str(
        intent_patch.get("intent_mode") or state.get("intent_mode") or ""
    )
    _suppress_recall = should_suppress_memory_recall(
        intent_mode=_intent_for_anchor,
        hard_constraints=list(intent_patch.get("hard_constraints") or []),
    )
    _quarantine_docs = should_quarantine_documents_search(
        intent_mode=_intent_for_anchor,
        hard_constraints=list(intent_patch.get("hard_constraints") or []),
        active_attachments=list(intent_patch.get("active_attachments") or []),
    )

    # Bind tool-side ContextVars for this supervisor hop (file_search quarantine, etc.)
    _turn_tok = set_active_turn_context(
        active_goal=str(intent_patch.get("active_goal") or ""),
        active_attachments=list(intent_patch.get("active_attachments") or []),
        hard_constraints=list(intent_patch.get("hard_constraints") or []),
        intent_mode=_intent_for_anchor,
        suppress_memory_recall=_suppress_recall,
        quarantine_documents_search=_quarantine_docs,
    )
    _sp_tok = bind_scratchpad_thread(str(state.get("thread_id") or ""))

    _wm_block = format_working_memory_anchor(
        active_goal=str(intent_patch.get("active_goal") or ""),
        active_attachments=list(intent_patch.get("active_attachments") or []),
        hard_constraints=list(intent_patch.get("hard_constraints") or []),
        intent_mode=_intent_for_anchor,
        scratchpad=dict(intent_patch.get("scratchpad") or {}),
    )
    # Replace any prior working-memory system message, then pin at index 0/1.
    messages = [
        m
        for m in messages
        if not (
            m.get("role") == "system"
            and WORKING_MEMORY_MARKER in str(m.get("content") or "")
        )
    ]
    _wm_ins = 1 if messages and messages[0].get("role") == "system" else 0
    messages.insert(_wm_ins, {"role": "system", "content": _wm_block})

    # Structural tool filter for the whole turn (audit_only / read_only / …).
    effective_tool_definitions = filter_tools_for_constraints(
        tool_definitions,
        list(intent_patch.get("hard_constraints") or []),
    )

    # Classify as a hint; models.defaults.<kind> wins over keywords / YAML.
    routed_model = None
    routed_client = None
    if last_user_content:
        try:
            from kazma_core.models.selection import resolve_supervisor_route

            routed_model, routed_client, _profile = resolve_supervisor_route(
                _recall_query or last_user_content,
                model_router=model_router,
            )
            if routed_model:
                logger.info(
                    "[Supervisor] Routed profile=%s model=%s (defaults win over keywords)",
                    _profile,
                    routed_model,
                )
        except Exception:
            logger.debug("[Supervisor] route resolve failed", exc_info=True)
            if model_router is not None:
                from kazma_core.models.router import ModelRouter

                profile = ModelRouter.classify(_recall_query or last_user_content)
                model_spec = model_router.route(profile)
                routed_model = model_spec.model

    # Per-turn pin from the mouth (SSE/WS body.model) wins over the router
    # and does NOT mutate the process-wide active profile.
    turn_llm = llm
    try:
        from kazma_core.runtime.turn_model import resolve_turn_client

        turn_llm, _pinned = resolve_turn_client(llm)
        if _pinned:
            routed_model = _pinned
            routed_client = None
            logger.info("[Supervisor] turn-model pin=%s", _pinned)
        elif routed_client is not None:
            turn_llm = routed_client
    except Exception:
        turn_llm = llm
        if routed_client is not None:
            turn_llm = routed_client

    # ── Per-turn memory retrieval (RAG) ──────────────────────────
    # Retrieve relevant memories for the current user message and inject
    # them as a system message before the LLM call. Gated on iteration==0
    # so it fires once per user turn (not per ReAct iteration). This is
    # the key difference from compaction-only retrieval — the agent now
    # has recall on EVERY turn, not just when the context window is full.
    # Honours memory.per_turn_retrieval via ConfigStore ← yaml (default true).
    _per_turn_on = True
    try:
        from kazma_core.memory.config import memory_per_turn_enabled

        _per_turn_on = memory_per_turn_enabled()
    except Exception:
        pass

    if _suppress_recall and iteration == 0:
        logger.info(
            "[Supervisor] V2 recall suppressed (intent=%s constraints=%s)",
            _intent_for_anchor,
            list(intent_patch.get("hard_constraints") or []),
        )

    if (
        _per_turn_on
        and iteration == 0
        and last_user_content
        and not _suppress_recall
    ):
        # ── V2 cognitive recall (single memory stack) ─────────────────
        # Suppressed on topic-shift / audit_only so prior radiology/ZCode
        # beliefs cannot hijack the active attachment turn.
        # When memory.v2.use_new_stack is False, skip injection entirely
        # (V1 RRF was removed — there is no legacy rollback path).
        _use_v2 = False
        try:
            from kazma_core.memory.config import memory_v2_enabled

            _use_v2 = memory_v2_enabled()
        except Exception:
            pass

        if _use_v2:
            try:
                _top_k = _rag_top_k()
                from kazma_core.memory.recall import format_recall_block, recall

                _explain = None
                try:
                    from kazma_core.memory.config import read_memory_cfg

                    _explain = bool(
                        ((read_memory_cfg() or {}).get("v2") or {}).get(
                            "explain_recall", False
                        )
                    )
                except Exception:
                    _explain = False
                # to_thread: recall() is synchronous SQLite + embedding work
                # — running it on the loop stalled concurrent SSE/WS turns.
                result = await asyncio.to_thread(
                    recall,
                    _recall_query or last_user_content,
                    limit=_top_k,
                    session_id=_recall_session_id,
                    tenant_id=state.get("tenant_id", "default"),
                    explain=_explain,
                )
                if not result.empty:
                    mem_block = format_recall_block(result, explain=_explain)
                    if mem_block:
                        messages.insert(1, {"role": "system", "content": mem_block})
                        logger.info(
                            "[Supervisor] V2 recall: %d beliefs, %d episodes for turn",
                            len(result.beliefs), len(result.episodes),
                        )
                elif not _suppress_recall:
                    # Transcript fallback (2026-08-27 "green names" incident):
                    # V2 memory had NOTHING for the query, yet the answer often
                    # lives in a past chat transcript. Search past web sessions
                    # (title + message text) and inject a fenced excerpt block —
                    # instead of the supervisor hand-writing SQL against
                    # chat_sessions.db for 21 iterations (plus a YOLO gate).
                    try:
                        from kazma_core.memory.transcript_recall import (
                            format_transcript_block,
                            search_transcripts,
                            transcript_fallback_enabled,
                        )

                        if transcript_fallback_enabled():
                            _tx_hits = await asyncio.to_thread(
                                search_transcripts,
                                _recall_query or last_user_content,
                                exclude_session_id=_recall_session_id,
                                tenant_id=str(state.get("tenant_id", "default")),
                            )
                            if _tx_hits:
                                _tx_block = format_transcript_block(_tx_hits)
                                if _tx_block:
                                    messages.insert(
                                        1, {"role": "system", "content": _tx_block}
                                    )
                                    logger.info(
                                        "[Supervisor] transcript fallback: %d past-session hit(s) for turn",
                                        len(_tx_hits),
                                    )
                    except Exception:
                        logger.debug(
                            "[Supervisor] transcript fallback failed", exc_info=True
                        )
                # Merge Knowledge Library into chat path (labeled, fenced).
                # Product merge: inject KB next to memory; optional promote to episodes.
                _kb_hits_for_explain: list[dict[str, Any]] = []
                try:
                    from kazma_core.memory.config import read_memory_cfg
                    from kazma_core.safety.prompt_fence import format_untrusted_block

                    _v2m = (read_memory_cfg() or {}).get("v2") or {}
                    if _v2m.get("merge_knowledge_into_chat", True):
                        from kazma_core.memory.federated_search import (
                            federated_search,
                            format_kb_hits_for_prompt,
                            promote_kb_hits_to_episodes,
                        )

                        # Industry path: hybrid RRF over inject-scoped libs first
                        # (auto_inject + optional smart search), same stack as
                        # get_knowledge_auto_inject_block — not a parallel FTS path.
                        fed = federated_search(
                            last_user_content,
                            tenant_id=state.get("tenant_id", "default"),
                            session_id=state.get("thread_id"),
                            limit_memory=0,
                            limit_kb=3,
                            include_memory=False,
                            include_knowledge=True,
                            kb_mode="inject",
                        )
                        _kb_hits_for_explain = list(fed.get("hits") or [])
                        kb_md = format_kb_hits_for_prompt(
                            _kb_hits_for_explain, max_hits=3
                        )
                        if not kb_md:
                            # Fallback: explicit inject helper (same RRF; handles
                            # footer wording if federated returned empty edge case)
                            try:
                                from kazma_core.stores.knowledge_index import (
                                    get_knowledge_auto_inject_block,
                                )

                                kb_md = await get_knowledge_auto_inject_block(
                                    last_user_content
                                )
                            except Exception:
                                kb_md = ""
                        if kb_md:
                            messages.insert(
                                1,
                                {
                                    "role": "system",
                                    "content": format_untrusted_block(
                                        kb_md, source="knowledge"
                                    ),
                                },
                            )
                            logger.info("[Supervisor] Knowledge Library merged into chat inject")
                            if _v2m.get("promote_kb_to_episodes", True):
                                try:
                                    promote_kb_hits_to_episodes(
                                        _kb_hits_for_explain,
                                        session_id=str(state.get("thread_id") or "kb"),
                                        tenant_id=state.get("tenant_id", "default"),
                                        max_promote=2,
                                    )
                                except Exception:
                                    logger.debug(
                                        "[Supervisor] kb promote skipped",
                                        exc_info=True,
                                    )
                except Exception:
                    logger.debug(
                        "[Supervisor] knowledge merge inject skipped", exc_info=True
                    )
                # Chat-turn Memory context (always when inject ran; full chips
                # when explain_recall is on — industry observability default).
                try:
                    from kazma_core.memory.recall import build_memory_explain_payload

                    _had_inject = (not result.empty) or bool(_kb_hits_for_explain)
                    if _explain or _had_inject:
                        _payload = build_memory_explain_payload(
                            query=_recall_query or last_user_content,
                            result=result if not result.empty else None,
                            kb_hits=_kb_hits_for_explain,
                            explain=True if _explain else "summary",
                        )
                        if _payload is not None:
                            if not _explain:
                                _payload["detail"] = "summary"
                                _payload["hint"] = (
                                    "Enable Settings → Memory → Explain recall "
                                    "for full channel chips on every hit."
                                )
                            else:
                                _payload["detail"] = "full"
                            _memory_explain_cv.set(_payload)
                except Exception:
                    logger.debug(
                        "[Supervisor] memory explain payload skipped",
                        exc_info=True,
                    )
                # Phase C: procedural skill hints (fenced, untrusted)
                try:
                    import sqlite3

                    from kazma_core.memory.procedural import (
                        format_procedural_hints,
                        match_procedural_dags,
                    )
                    from kazma_core.memory.schema_v2 import ensure_primary_schema
                    from kazma_core.paths import primary_memory_db

                    def _fetch_procedural_dags() -> list[Any]:
                        # to_thread: sync SQLite + DAG matching must stay off
                        # the event loop (same rule as recall() above).
                        pconn = sqlite3.connect(
                            primary_memory_db(), check_same_thread=False
                        )
                        try:
                            ensure_primary_schema(pconn)
                            return list(match_procedural_dags(
                                pconn,
                                last_user_content,
                                tenant_id=state.get("tenant_id", "default"),
                                limit=3,
                            ))
                        finally:
                            pconn.close()

                    dags = await asyncio.to_thread(_fetch_procedural_dags)
                    if dags:
                        hint = format_procedural_hints(dags)
                        if hint:
                            messages.insert(1, {"role": "system", "content": hint})
                except Exception:
                    logger.debug(
                        "[Supervisor] procedural inject skipped", exc_info=True
                    )
            except Exception:
                logger.warning(
                    "[Supervisor] V2 recall failed — skipping memory injection",
                    exc_info=True,
                )

    # Per-turn language lock (graph level so Telegram/Discord/Web share one path).
    # Always replace any prior LANGUAGE LOCK messages so a session that started
    # in Arabic does not keep an old lock (or skip injecting English) after the
    # user flips language mid-thread.
    if iteration == 0 and last_user_content:
        try:
            from kazma_core.language_lock import language_lock_message

            lock = language_lock_message(last_user_content)
            if lock:
                messages = [
                    m
                    for m in messages
                    if not (
                        m.get("role") == "system"
                        and "LANGUAGE LOCK" in str(m.get("content", ""))
                    )
                ]
                # Place just before the last user message so it is the nearest
                # instruction to the model.
                insert_at = len(messages)
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "user":
                        insert_at = i
                        break
                messages.insert(insert_at, {"role": "system", "content": lock})
        except Exception:
            logger.debug("[Supervisor] language lock skipped", exc_info=True)

    # ── Repair broken tool chains before the LLM call ──────────────
    # A checkpoint poisoned by a paused HITL turn (assistant tool_calls
    # with no tool responses, possibly mid-history) would 400 on every
    # provider call forever. Sanitizing here also heals the thread: the
    # repaired list is what gets persisted by the return paths below.
    messages = sanitize_tool_chains(messages)
    messages = prune_messages_if_exceeding_cap(messages)
    from kazma_core.summarizer import prune_tool_outputs
    messages = prune_tool_outputs(messages, max_tokens=24000)

    # Deterministic trim (PATH B replacement): never LLM-summarize mid-loop.
    _lm_for_trim = state.get("last_model") or routed_model
    _trim_budget = resolve_trim_token_budget(
        last_model=_lm_for_trim if isinstance(_lm_for_trim, str) else None
    )
    _before_trim_msgs = list(messages)
    _before_trim = len(messages)
    messages = trim_messages_deterministic(
        messages,
        max_tokens=_trim_budget,
        keep_last_tool_rounds=8,
        active_goal=str(intent_patch.get("active_goal") or ""),
        working_memory_block=_wm_block,
    )
    messages = sanitize_tool_chains(messages)
    if len(messages) < _before_trim:
        breaker_reset = {
            **breaker_reset,
            "needs_compaction": False,
            "circuit_breaker_tripped": False,
            "consecutive_tool_failures": 0,
        }
        logger.info(
            "[Supervisor] Deterministic trim applied: %d → %d messages (budget=%d)",
            _before_trim,
            len(messages),
            _trim_budget,
        )
        # /compact or 80% budget: summarize what trim dropped (don't just forget).
        _want_summary = bool(state.get("needs_compaction"))
        try:
            if (
                not _want_summary
                and authority is not None
                and hasattr(authority, "counter")
                and authority.counter.should_compact(_before_trim_msgs)
            ):
                _want_summary = True
        except Exception:
            pass
        if _want_summary:
            try:
                from kazma_core.agent.semantic_compact import inject_summary_of_dropped

                messages = await inject_summary_of_dropped(
                    _before_trim_msgs, messages, llm=turn_llm
                )
                messages = sanitize_tool_chains(messages)
            except Exception:
                logger.warning(
                    "[Supervisor] semantic compact of dropped turns failed",
                    exc_info=True,
                )

    # Soft force-plan: on the first supervisor hop of a tool-capable turn,
    # remind the model to open with a ```plan fence so the UI workbench
    # can pin a checklist (providers rarely expose true chain-of-thought).
    if iteration == 0 and effective_tool_definitions:
        _plan_nudge = (
            "UI WORKBENCH: If you will call any tools this turn, put a short "
            "```plan fence (3–7 bullets) in your content field before or "
            "alongside tool_calls so the user sees your plan. Then use tools. "
            "Close the fence with ``` alone on its own line, then a blank line, "
            "then any user-facing text — never glue the answer onto the ticks "
            "(wrong: ```Saved.). A plan with no tool_calls is not a finished turn."
        )
        if not any(
            m.get("role") == "system" and "UI WORKBENCH" in str(m.get("content", ""))
            for m in messages
        ):
            messages.append({"role": "system", "content": _plan_nudge})

    # R4: soft-route deep research intent toward run_research_pipeline
    # §18 Phase 2: skip when the intent engine's constrain note already
    # covers research_deep (avoid double-nudging)
    if iteration == 0 and effective_tool_definitions and last_user_content:
        _intent_covers_research = (
            _decision is not None
            and any(a.kind == "research_deep" for a in _decision.acts)
            and any(
                m.get("role") == "system" and "INTENT ENGINE" in str(m.get("content", ""))
                for m in messages
            )
        )
        if not _intent_covers_research:
            try:
                from kazma_core.agent.research_policy import deep_research_route_hint

                route = deep_research_route_hint(last_user_content)
                if route and not any(
                    m.get("role") == "system"
                    and "DEEP RESEARCH ROUTE" in str(m.get("content", ""))
                    for m in messages
                ):
                    messages.append({"role": "system", "content": route})
            except Exception:
                logger.debug("[Supervisor] deep research route hint skipped", exc_info=True)

    # ── Steer: hard pause + soft drain, right before the LLM call ────
    # HARD steer: if one is pending, fire a LangGraph interrupt so the
    # running turn pauses cleanly. The /api/chat/steer (mode=hard) / gateway
    # /steer! poller detects the pause and resumes via
    # ainvoke(Command(resume=...)). On resume LangGraph re-runs this node;
    # peek-then-pop keeps the text present across that re-run (interrupt()
    # returns the resume value instead of pausing the second time), so we
    # pop+apply exactly once. Mirrors the commitment-gate conditional
    # interrupt (~line 1909).
    #
    # SOFT steer drain runs AFTER the hard gate (unconditionally). This
    # placement matters: LangGraph re-runs the node on resume, so draining
    # before the interrupt would pop soft steers on the pre-pause pass and
    # then discard them. Draining after the gate guarantees a single drain
    # on the path that actually reaches the LLM.
    try:
        from kazma_core.agent.steer import (
            drain_soft_steers,
            hard_steer_note,
            hard_steer_payload,
            peek_hard_steer,
            pop_hard_steer,
            soft_steer_note,
        )

        _steer_tid = str(state.get("thread_id") or "")
        if not _steer_tid:
            try:
                from kazma_core.safety.hitl import get_current_thread_id

                _steer_tid = str(get_current_thread_id() or "")
            except Exception:
                _steer_tid = ""
        _hard_text = peek_hard_steer(_steer_tid)
        if _hard_text:
            from langgraph.types import interrupt  # local import (cf. commitment gate)

            logger.info("[Supervisor] hard steer interrupt thread=%s", _steer_tid[:12])
            interrupt(hard_steer_payload(_hard_text))  # pauses; returns on resume
            _applied = pop_hard_steer(_steer_tid) or _hard_text
            messages.append({"role": "user", "content": hard_steer_note(_applied)})

        # Soft steer: append queued user notes for this LLM call. Persisted
        # into the checkpoint (messages), so they apply for the rest of the
        # turn + future turns. Zero disruption — no cancel, no pause.
        for _s in drain_soft_steers(_steer_tid):
            messages.append({"role": "user", "content": soft_steer_note(_s["text"])})
    except Exception:
        logger.exception("[Supervisor] steer gate/drain failed")

    start = time.monotonic()
    try:
        from kazma_core.retry import friendly_llm_error, load_retry_config
        from kazma_core.llm_provider import LLMError

        cfg = load_retry_config()
        # Guard: max_attempts <= 0 would make range(1, 1) empty and raise
        # last_exc=None — the LLM would never be called. Clamp to >= 1.
        cfg["max_attempts"] = max(1, int(cfg.get("max_attempts", 1) or 1))
        retryable_exc: tuple[type[Exception], ...] = (ConnectionError, TimeoutError)
        try:
            import httpx

            retryable_exc = retryable_exc + (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                httpx.ReadError,
            )
        except ImportError:
            pass

        _llm_attempts = 0
        _served_by: list[str] = []  # failover bookkeeping: [model] when a chain model answered
        _llm_messages = list(messages) + ([_budget_nudge] if _budget_nudge else [])
        _did_overflow_compact = False

        async def _call_llm_with_retry() -> Any:
            nonlocal _llm_attempts, _llm_messages, messages, _did_overflow_compact
            last_exc: Exception | None = None
            # Per-call max_tokens: tool-call iterations need less output
            # (tool JSON is bounded) — use 8192 to save cost. Content-only
            # turns (no tools) may produce long answers — use the full
            # configured limit (16384 default). This prevents the wasteful
            # truncation-retry loop on content-generation tasks.
            _call_max_tokens = 8192 if effective_tool_definitions else None
            for attempt in range(1, cfg["max_attempts"] + 1):
                try:
                    return await invoke_llm_chat(
                        turn_llm,
                        messages=_llm_messages,
                        tools=effective_tool_definitions if effective_tool_definitions else None,
                        model=routed_model,
                        max_tokens=_call_max_tokens,
                    )
                except retryable_exc as exc:
                    last_exc = exc
                    _llm_attempts = attempt
                    if attempt < cfg["max_attempts"]:
                        wait_time = min(cfg["min_wait"] * (2 ** (attempt - 1)), cfg["max_wait"])
                        logger.warning(
                            "[Supervisor] LLM call attempt %d/%d failed: %s (retrying in %ds)",
                            attempt,
                            cfg["max_attempts"],
                            exc,
                            wait_time,
                        )
                        import asyncio

                        await asyncio.sleep(wait_time)
                    else:
                        raise
                except LLMError as exc:
                    # ``llm.chat`` wraps every failure in LLMError. Only
                    # transient errors (network blips, 429) are worth
                    # retrying; permanent 4xx content/schema errors fail
                    # fast so we don't waste attempts that cannot succeed.
                    is_transient = bool(getattr(exc, "transient", False))
                    last_exc = exc
                    _llm_attempts = attempt
                    if (
                        getattr(exc, "kind", "") == "context_overflow"
                        and not _did_overflow_compact
                    ):
                        _did_overflow_compact = True
                        logger.warning(
                            "[Supervisor] context overflow — semantic compact and retry"
                        )
                        try:
                            from kazma_core.agent.semantic_compact import (
                                semantic_compact_messages,
                            )

                            _llm_messages = await semantic_compact_messages(
                                _llm_messages, llm=turn_llm
                            )
                            messages = list(_llm_messages)
                        except Exception:
                            logger.warning(
                                "[Supervisor] overflow compact failed",
                                exc_info=True,
                            )
                            raise
                        continue
                    # A rate-limit-exhausted 429 is transient (so failover
                    # fires) but the provider already did bounded backoff, so
                    # don't re-retry the SAME provider here.
                    skip_retry = getattr(exc, "kind", "") == "rate_limit_exhausted"
                    if is_transient and not skip_retry and attempt < cfg["max_attempts"]:
                        wait_time = min(cfg["min_wait"] * (2 ** (attempt - 1)), cfg["max_wait"])
                        logger.warning(
                            "[Supervisor] LLM call attempt %d/%d failed (transient): %s "
                            "(retrying in %ds)",
                            attempt,
                            cfg["max_attempts"],
                            exc,
                            wait_time,
                        )
                        import asyncio

                        await asyncio.sleep(wait_time)
                    else:
                        raise
            raise last_exc  # type: ignore[misc]

        # ── Model failover chain (agent.nonstop.failover) ───────────
        # After the primary model exhausts its retries on a TRANSIENT
        # failure (network/429/outage), try each model in the configured
        # chain via a one-off registry client — the active profile is NOT
        # mutated. Permanent 4xx errors never trigger failover (they would
        # fail identically on every model). Per-model cooldown prevents
        # hammering a provider that just failed.
        async def _try_failover_models(last_exc: Exception) -> Any:
            is_transient = isinstance(last_exc, retryable_exc) or bool(
                getattr(last_exc, "transient", False)
            )
            if not is_transient:
                return None
            try:
                from kazma_core.agent.nonstop import get_nonstop_config

                ns = get_nonstop_config()
            except Exception:
                return None
            if not ns.failover.enabled or not ns.failover.chain:
                return None
            try:
                from kazma_core.model_registry import get_model_registry

                registry = get_model_registry()
            except Exception:
                return None
            now = time.monotonic()
            for fb_model in ns.failover.chain:
                if not fb_model or fb_model == routed_model:
                    continue
                cooled_until = _failover_cooldowns.get(fb_model, 0.0)
                if now < cooled_until:
                    logger.info(
                        "[Failover] %s in cooldown (%.0fs left) — skipping",
                        fb_model,
                        cooled_until - now,
                    )
                    continue
                try:
                    client = _failover_clients.get(fb_model)
                    if client is None:
                        client = registry.get_client(fb_model)
                        _failover_clients[fb_model] = client
                    logger.warning(
                        "[Failover] Primary model '%s' failed transiently — "
                        "trying failover model '%s'",
                        routed_model,
                        fb_model,
                    )
                    response = await invoke_llm_chat(
                        client,
                        messages=_llm_messages,
                        tools=effective_tool_definitions if effective_tool_definitions else None,
                        model=fb_model,
                    )
                    logger.warning(
                        "[Failover] model '%s' answered after primary failure",
                        fb_model,
                    )
                    _served_by.append(fb_model)
                    return response
                except Exception as fb_exc:
                    _failover_cooldowns[fb_model] = now + ns.failover.cooldown_seconds
                    logger.warning(
                        "[Failover] model '%s' also failed: %s (cooldown %.0fs)",
                        fb_model,
                        fb_exc,
                        ns.failover.cooldown_seconds,
                    )
            return None

        async def _call_llm_resilient() -> Any:
            try:
                return await _call_llm_with_retry()
            except Exception as primary_exc:
                fb = await _try_failover_models(primary_exc)
                if fb is not None:
                    return fb
                raise

        response = await _call_llm_resilient()
    except Exception as exc:
        logger.error("[Supervisor] LLM call failed after retries: %s", exc)
        from kazma_core.retry import friendly_llm_error

        # Per-call ledger (observability §A): durable record of the failure.
        try:
            from kazma_core.agent.nonstop import get_nonstop_config

            if get_nonstop_config().ledger_enabled:
                from kazma_core.observability.llm_ledger import record_llm_call

                record_llm_call(
                    thread_id=str(state.get("thread_id", "")),
                    iteration=int(state.get("iteration", 0) or 0),
                    provider=type(llm).__name__,
                    model=str(routed_model or ""),
                    duration_ms=(time.monotonic() - start) * 1000,
                    status="error",
                    error_kind=str(getattr(exc, "kind", "") or type(exc).__name__),
                )
        except Exception:
            pass

        error_content = friendly_llm_error(exc)
        # Surface an HONEST failure rather than disguising it as a normal
        # assistant reply. ``turn_failed`` tells respond_node to skip
        # synthesis (no fabricated final answer over the broken turn) — the
        # user gets a clear error they can act on (the "model stopped
        # thinking" symptom's real cause).
        return {
            **breaker_reset,
            **intent_patch,
            **_mission_carry,
            "next_node": NodeName.RESPOND,
            "turn_failed": True,
            "messages": messages
            + [
                {
                    "role": "assistant",
                    "content": error_content,
                }
            ],
        }

    duration_ms = (time.monotonic() - start) * 1000
    cost_breaker.record_cost(response.cost_usd)

    # Per-call ledger (observability §A): durable record of the success,
    # including failover attribution (failover_from = primary model).
    try:
        from kazma_core.agent.nonstop import get_nonstop_config

        if get_nonstop_config().ledger_enabled:
            from kazma_core.observability.llm_ledger import record_llm_call

            _served_model = _served_by[-1] if _served_by else ""
            record_llm_call(
                thread_id=str(state.get("thread_id", "")),
                iteration=int(state.get("iteration", 0) or 0),
                provider=type(llm).__name__,
                model=str(response.model or _served_model or routed_model or ""),
                prompt_tokens=int(response.usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(response.usage.get("completion_tokens", 0) or 0),
                cost_usd=float(response.cost_usd or 0.0),
                duration_ms=duration_ms,
                status="ok",
                failover_from=str(routed_model or "") if _served_model else "",
            )
    except Exception:
        pass

    # Trace
    tracer.trace_llm_call(
        model=response.model,
        prompt=str(messages[-1].get("content", ""))[:500],
        response=response.content[:500],
        tokens=response.usage.get("total_tokens", 0),
        cost=response.cost_usd,
        duration_ms=duration_ms,
    )

    logger.info(
        "[Supervisor] LLM responded: model=%s tokens=%d cost=$%.4f duration=%.0fms tool_calls=%d",
        response.model,
        response.usage.get("total_tokens", 0),
        response.cost_usd,
        duration_ms,
        len(response.tool_calls),
    )

    # ── Route decision ─────────────────────────────────────────────
    if not response.tool_calls:
        content = response.content.strip() if response.content else ""

        # ── Empty-response recovery ────────────────────────────────
        # Some providers (Groq compound-mini, certain Ollama models, and
        # deepseek-v4-flash on long/summarised contexts) return content=""
        # — either on the final turn after a tool call (large tool result
        # like memory_search JSON) OR on a first-pass reply with no tool
        # calls at all. Without this guard the user sees "Done" with no
        # bubble text. Retry once with an explicit nudge on ANY iteration.
        # (Previously gated on `iteration > 0`, which let iteration=0 empty
        # replies reach respond_node and stream as empty — 2026-07-31.)
        if not content:
            logger.warning(
                "[Supervisor] LLM returned empty content "
                "(iteration=%d) — retrying with pruned context nudge", iteration,
            )
            # Prune context specifically for nudge call to prevent sending bloated prompt
            _nudge_tail = (
                "Answer only the latest user request; do not resume a superseded or abandoned prior task."
                if intent_patch.get("intent_mode") == "shift"
                or intent_patch.get("task_status") in ("superseded", "abandoned")
                else (
                    "Based on the conversation and tool results above, tell the "
                    "user what you found and what remains unfinished."
                )
            )
            pruned_nudge_msgs = prune_tool_outputs(messages, max_tokens=14000, keep_recent_tool_outputs=2) + [
                {"role": "system", "content": (
                    "Your previous response was empty. Provide a clear, helpful "
                    "TEXT answer only (no tools, no XML, no DSML, no tool_calls). "
                    + _nudge_tail
                )},
            ]
            try:
                nudge_response = await invoke_llm_chat(
                    turn_llm,
                    messages=pruned_nudge_msgs,
                    tools=[],
                    model=routed_model,
                )
                if nudge_response.content and nudge_response.content.strip():
                    content = nudge_response.content.strip()
                    response = nudge_response  # update for tracing/cost
                    logger.info("[Supervisor] Nudge retry succeeded — content recovered (%d chars)", len(content))
                else:
                    logger.warning("[Supervisor] Nudge retry returned empty content — routing to synthesis fallback")
            except Exception as nudge_exc:
                logger.warning("[Supervisor] Nudge retry failed: %s — routing to synthesis fallback", nudge_exc)

        # Reject leaked tool-call markup / mid-thought stubs (not a real answer)
        if content and is_unusable_assistant_content(content):
            logger.warning(
                "[Supervisor] Unusable assistant content (%d chars) — "
                "forcing synthesis (leak/stub), iteration=%d",
                len(content),
                iteration,
            )
            content = ""

        # Un-glue ```plan fences before we decide (````Saved.`` is not a
        # finished answer until the closer is on its own line).
        if content:
            content = normalize_plan_fence(content)

        max_iter = int(state.get("max_iterations") or 15)

        # Workbench plan with no tool_calls is not a finished turn — the UI
        # pins the checklist and the user sees silence. One auto-continue
        # (not in /plan mode). See plan_fence.should_execute_plan_only_hop.
        if should_execute_plan_only_hop(
            content=content,
            has_tool_calls=False,
            tools_available=bool(effective_tool_definitions),
            plan_mode_kind=_plan_kind,
            plan_only_continues=int(state.get("plan_only_continues") or 0),
            iteration=int(iteration or 0),
            max_iterations=max_iter,
        ):
            logger.info(
                "[Supervisor] plan-only hop with no tools (iteration=%d) — "
                "auto-continue to execute",
                iteration,
            )
            assistant_msg = {"role": "assistant", "content": content}
            # Second nudge is sharper — the model already ignored one
            # execute instruction (repeat-planner loop, 2026-08-26).
            _prior = int(state.get("plan_only_continues") or 0)
            continuation_msg = {
                "role": "user",
                "content": PLAN_EXECUTE_FINAL if _prior >= 1 else PLAN_EXECUTE_CONTINUE,
            }
            return {
                **breaker_reset,
                **intent_patch,
                **_mission_carry,
                "messages": messages + [assistant_msg, continuation_msg],
                "next_node": NodeName.SUPERVISOR,
                "iteration": iteration + 1,
                "plan_only_continues": _prior + 1,
                "last_model": response.model,
                "last_tokens": response.usage.get("total_tokens", 0),
                "last_cost_usd": response.cost_usd,
            }

        # Auto-continuation guard for multi-step goals/tasks.
        # Topic shifts / superseded focus never auto-continue the old goal.
        is_auto = bool(state.get("auto_continue", False))
        if "auto_continue" in intent_patch:
            is_auto = bool(intent_patch["auto_continue"])
        if intent_patch.get("task_status") in ("superseded", "abandoned") or intent_patch.get("intent_mode") == "shift":
            is_auto = False
        if not is_auto and content:
            _content_lower = content.lower()
            if any(marker in _content_lower for marker in ["now section", "proceeding to section", "next section", "proceeding with section"]):
                # Only section-auto when not on a soft-reset pivot
                if intent_patch.get("intent_mode") != "shift":
                    is_auto = True

        if is_auto and iteration + 1 < max_iter and content:
            logger.info("[Supervisor] Auto-continue active (iteration=%d/%d) — looping back to supervisor", iteration + 1, max_iter)
            assistant_msg = {"role": "assistant", "content": content}
            continuation_msg = {"role": "user", "content": "Please proceed automatically with the remaining steps and complete the task."}
            return {
                **breaker_reset,
                **intent_patch,
                **_mission_carry,
                "messages": messages + [assistant_msg, continuation_msg],
                "next_node": NodeName.SUPERVISOR,
                "iteration": iteration + 1,
                "last_model": response.model,
                "last_tokens": response.usage.get("total_tokens", 0),
                "last_cost_usd": response.cost_usd,
            }

        # If content is still empty or unusable after nudge, force synthesis
        if not content:
            logger.warning(
                "[Supervisor] Turn finished with no usable text (iteration=%d) — "
                "forcing respond_node synthesis",
                iteration,
            )
            return {
                **breaker_reset,
                **intent_patch,
                **_mission_carry,
                "messages": messages,
                "next_node": NodeName.RESPOND,
                "force_synthesis": True,
                "last_model": response.model,
                "last_tokens": response.usage.get("total_tokens", 0),
                "last_cost_usd": response.cost_usd,
            }

        # Pure text response → RESPOND. Mark completed when focus was open
        # and this turn is not mid multi-step tool work.
        _done_patch = dict(intent_patch)
        if _done_patch.get("intent_mode") in ("normal", "shift") and _done_patch.get(
            "task_status"
        ) not in ("superseded", "abandoned"):
            # Final answer with no tools — focus can rest as completed for
            # short Q&A; multi-part/store stay in_progress until tools finish.
            if _done_patch.get("intent_mode") == "normal" and len(
                (last_user_content or "").strip()
            ) < 120:
                _done_patch.setdefault("task_status", "completed")
        assistant_msg = {"role": "assistant", "content": content}
        return {
            **breaker_reset,
            **_done_patch,
            **_mission_carry,
            "messages": messages + [assistant_msg],
            "next_node": NodeName.RESPOND,
            "last_model": response.model,
            "last_tokens": response.usage.get("total_tokens", 0),
            "last_cost_usd": response.cost_usd,
        }

    # Tool calls → build pending list and route to TOOL_WORKER.
    # NOTE: Do NOT convert content to None when it's an empty string.
    # Some providers (Groq compound-mini, certain Ollama models) return
    # content="" alongside tool_calls. Converting to None breaks the
    # message history on the next LLM call (API rejects null content).
    # Keep the original value — empty string is valid per OpenAI spec.
    _tool_hop_content = response.content if response.content is not None else ""
    if _tool_hop_content:
        _tool_hop_content = normalize_plan_fence(_tool_hop_content)
    assistant_msg: dict[str, Any] = {
        "role": "assistant",
        "content": _tool_hop_content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            for tc in response.tool_calls
        ],
    }

    pending = [PendingToolCall(id=tc.id, name=tc.name, arguments=tc.arguments) for tc in response.tool_calls]

    # Budget-exhausted divert (audit H5): iteration was already advanced for
    # this hop, so when THIS increment reaches the cap the router force-
    # diverts to RESPOND and the sanitizer silently strips the dangling
    # tool_calls — the model lost its final tool round with no explanation.
    # Instead, answer every pending call here with an explicit synthetic
    # tool result and route to RESPOND ourselves, so the chain stays valid
    # AND the model is told its calls were discarded by budget, not lost.
    # Mission-mode extension mirrors the router's own hard-wall predicate so
    # legit mission hops past a soft cap are untouched.
    _next_iter = int(iteration or 0) + 1
    _soft_max = int(state.get("max_iterations") or 15)
    _budget_divert = _next_iter >= _soft_max
    if _budget_divert:
        try:
            from kazma_core.agent.long_task import is_mission_mode, mission_hard_rounds

            _tid = str(state.get("thread_id") or "") or None
            if _tid and is_mission_mode(_tid):
                _used = int(state.get("mission_rounds_used") or 0) + int(iteration or 0)
                _hard = int(state.get("mission_hard_rounds") or mission_hard_rounds())
                if _used < _hard:
                    _budget_divert = False
        except Exception:
            logger.debug("[Supervisor] mission check failed", exc_info=True)

    if _budget_divert:
        logger.warning(
            "[Supervisor] Iteration %d == max_iterations — answering %d tool "
            "call(s) with budget-exhausted notices and routing to respond",
            _next_iter, len(pending),
        )
        _synth = [
            {
                "role": "tool",
                "tool_call_id": p.id,
                "content": (
                    f"Iteration budget exhausted ({_next_iter}/{_soft_max}) — "
                    f"'{p.name}' was NOT executed. Work with what you have."
                ),
            }
            for p in pending
        ]
        return {
            **breaker_reset,
            **_tool_patch,
            **_mission_carry,
            "messages": messages + [assistant_msg] + _synth,
            "tool_calls_pending": [],
            "tool_calls_done": [],
            "next_node": NodeName.RESPOND,
            "iteration": _next_iter,
            "last_model": response.model,
            "last_tokens": response.usage.get("total_tokens", 0),
            "last_cost_usd": response.cost_usd,
            "_last_finish_reason": _finish_reason,
        }

    # Truncation detection: finish_reason="length" means the provider cut the
    # response at max_tokens — tool-call JSON is likely severed mid-string.
    # The tool worker reads this to give a truncation-specific corrective
    # error ("write in smaller chunks") instead of a generic schema complaint.
    _finish_reason = getattr(response, "finish_reason", "") or ""
    if _finish_reason == "length":
        logger.warning(
            "[Supervisor] Response TRUNCATED at max_tokens (finish_reason=length); "
            "tool-call arguments may be incomplete"
        )

    # Tools imply an open multi-step focus (unless user already superseded).
    _tool_patch = dict(intent_patch)
    if _tool_patch.get("task_status") != "superseded":
        _tool_patch["task_status"] = "in_progress"

    return {
        **breaker_reset,
        **_tool_patch,
        **_mission_carry,
        "messages": messages + [assistant_msg],
        "tool_calls_pending": pending,
        "tool_calls_done": [],  # reset for this iteration
        "next_node": NodeName.TOOL_WORKER,
        "iteration": iteration + 1,
        "last_model": response.model,
        "last_tokens": response.usage.get("total_tokens", 0),
        "last_cost_usd": response.cost_usd,
        "_last_finish_reason": _finish_reason,
    }
