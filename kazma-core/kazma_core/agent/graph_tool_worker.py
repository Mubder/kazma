"""Tool-worker node + commitment gate (extracted from graph_builder)."""

from __future__ import annotations

import logging
import time
from typing import Any

from kazma_core.agent.graph_helpers import (
    _format_hitl_message,
    _resolve_tool_timeout,
    _summarize_args_for_hitl,
    truncate_tool_result,
)
from kazma_core.agent.state import (
    NodeName,
    PendingToolCall,
    SupervisorState,
    ToolResult,
)
from kazma_core.summarizer import _normalize_msg

logger = logging.getLogger(__name__)

def _last_user_text(state: SupervisorState) -> str:
    """Most recent user message text (the commitment gate anchors relative
    phrases to it). Returns '' if none — the gate then degrades to audit-only."""
    msgs = state.get("messages") or []
    for m in reversed(msgs):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):  # multimodal parts
            return " ".join(
                str(p.get("text", "")) for p in c
                if isinstance(p, dict) and p.get("type") == "text"
            )
    return ""


def _commitment_resolve_gate(
    state: SupervisorState,
    pending: list[PendingToolCall],
) -> tuple[list[PendingToolCall], list[ToolResult]]:
    """Phase 2.5 (SRP): the semantic commitment gate, extracted from
    tool_worker_node for independent testing + cleaner separation of policy
    (resolve) from execution. Runs authorize_effect per semantic tool, rewrites
    args, holds clarify/confirm (interrupt()), blocks deny.
    """
    from langgraph.types import interrupt  # not imported at module level
    semantic_blocked: list[ToolResult] = []
    semantic_hold: list[tuple[PendingToolCall, Any]] = []
    try:
        from datetime import datetime as _dt, timezone as _tz
        from kazma_core.safety.commitment import authorize_effect as _authz
        from kazma_core.safety.commitment.constraints import (
            is_commitment_enabled as _cmt_on,
            load_constraint_beliefs as _load_beliefs,
        )
        from kazma_core.safety.side_effects import requires_semantic_check as _needs_sem
        from kazma_core.metrics import record_commitment_terminal
    except Exception:
        _cmt_on = None  # type: ignore[assignment]
        _authz = None; _load_beliefs = None; _needs_sem = None  # type: ignore[assignment]
        record_commitment_terminal = None  # type: ignore[assignment]

    def _is_semantic(tool_name: str) -> bool:
        """Fail-closed semantic probe (deep-audit 2026-08-19, finding #9).

        A classifier/profile explosion must classify the tool as SEMANTIC
        (gated), never let it slip past the gate as if it were a read.
        """
        try:
            return bool(_needs_sem(tool_name))
        except Exception:
            return True

    def _block_unhealthy(tool_name: str, tool_id: Any) -> None:
        """Fail-closed deny card for a per-tool commitment-gate failure."""
        semantic_blocked.append(ToolResult(
            tool_call_id=str(tool_id or ""),
            name=tool_name,
            content=(
                f"Commitment gate unavailable for {tool_name} — "
                "blocked while the policy engine is unhealthy. "
                "Do not retry; tell the user what failed."
            ),
            is_error=True, duration_ms=0, outcome="terminal",
        ))

    if _cmt_on and _cmt_on() and _needs_sem and _authz:
        try:
            _sem: list[PendingToolCall] = []
            for _probe in pending:
                if _is_semantic(str(_probe.get("name") or "")):
                    _sem.append(_probe)
            if _sem:
                _tenant = state.get("tenant_id") or "default"
                _req_at = _dt.now(_tz.utc)
                try:
                    _beliefs = _load_beliefs(_tenant) if _load_beliefs else []
                except Exception:
                    # Transient belief-store failure must not skip the whole
                    # gate (that would free-fire every semantic tool) —
                    # authorize without memory anchors instead
                    # (deep-audit 2026-08-19, finding #9).
                    logger.warning(
                        "[ToolWorker] constraint beliefs unavailable — gating "
                        "without memory anchors",
                        exc_info=True,
                    )
                    _beliefs = []
                _user_text = _last_user_text(state)
                _kept: list[PendingToolCall] = [
                    _tc for _tc in pending
                    if not _is_semantic(str(_tc.get("name") or ""))
                ]
                _enforce_unknown = True
                try:
                    from kazma_core.safety.commitment.config import get_commitment_config

                    _enforce_unknown = bool(
                        get_commitment_config().get("enforce_unknown_mutators")
                    )
                except Exception:
                    _enforce_unknown = True
                for _tc in _sem:
                    try:
                        _dec = _authz(
                            _tc["name"], _tc.get("arguments") or {},
                            user_text=_user_text, request_at=_req_at, memory_beliefs=_beliefs,
                            thread_id=state.get("thread_id"), tenant_id=_tenant,
                            enforce_unknown_mutators=_enforce_unknown,
                            context={"source": "graph"},
                        )
                    except Exception:
                        # Fail CLOSED (deep-audit 2026-08-19, finding #9):
                        # mirror the registry choke's posture so a broken
                        # policy engine cannot free-fire semantic acts (the
                        # remind/CoPilot class). The error tells the model to
                        # surface the failure instead of retrying blind.
                        logger.warning(
                            "[ToolWorker] commitment gate errored for %s — fail-closed",
                            _tc["name"],
                            exc_info=True,
                        )
                        _block_unhealthy(_tc["name"], _tc.get("id"))
                        continue
                    try:
                        if _dec.decision == "allow":
                            if _dec.rewritten_args:
                                _tc["arguments"] = _dec.rewritten_args
                            _kept.append(_tc)
                        elif _dec.decision in ("clarify", "confirm"):
                            semantic_hold.append((_tc, _dec))
                        else:
                            _q = _dec.reason or "denied by commitment gate"
                            semantic_blocked.append(ToolResult(
                                tool_call_id=str(_tc.get("id") or ""),
                                name=_tc["name"],
                            content=(f"Commitment gate denied {_tc['name']}: {_q}. "
                                     "Do not retry; tell the user why and what they can do."),
                            is_error=True, duration_ms=0, outcome="terminal",
                        ))
                        if record_commitment_terminal:
                            try: record_commitment_terminal("denied")
                            except Exception: pass
                    except Exception:
                        # Any exception while processing THIS tool's decision
                        # denies only THAT tool fail-closed — it must never
                        # un-gate the rest of the batch (finding #9).
                        logger.warning(
                            "[ToolWorker] commitment gate errored processing "
                            "%s decision — denying that tool (fail-closed)",
                            _tc.get("name"),
                            exc_info=True,
                        )
                        _block_unhealthy(str(_tc.get("name") or ""), _tc.get("id"))
                pending = _kept
        except Exception:
            # Structural failure in the SHARED gate setup above (tenant
            # stamp, clock, constraint-belief shim) — NOT a per-tool error.
            # Per-tool resolve exceptions are guarded inside the loop below,
            # so one broken tool no longer un-gates the rest of the batch
            # (deep-audit 2026-08-19, finding #9). Anything still unresolved
            # here is DENIED fail-closed (AGENTS.md §20: authorization-engine
            # exceptions on semantic acts fail closed); non-semantic tools
            # continue untouched so reads never stall on this path.
            logger.warning(
                "[ToolWorker] commitment gate structural failure — blocking "
                "remaining semantic tools (fail-closed)",
                exc_info=True,
            )
            _still_open: list[PendingToolCall] = []
            for _tc in pending:
                if _is_semantic(str(_tc.get("name") or "")):
                    _block_unhealthy(_tc["name"], _tc.get("id"))
                else:
                    _still_open.append(_tc)
            pending = _still_open

    if semantic_hold:
        _items = [{
            "tool_call_id": str(_tc.get("id") or ""),
            "tool": _tc["name"],
            "commitment_id": _dec.commitment_id,
            "question": _dec.clarify_question or _dec.reason or "needs clarification",
            "options": list(_dec.options) if _dec.options else [],
        } for _tc, _dec in semantic_hold]
        _sem_kind = ("semantic_confirm" if all(d.decision == "confirm" for _, d in semantic_hold)
                     else "semantic_clarify")
        _sem_payload = {
            "type": "hitl_approval", "kind": _sem_kind, "items": _items,
            "message": (_items[0]["question"] if len(_items) == 1
                        else f"{len(_items)} actions need clarification"),
        }
        _sem_choice = interrupt(_sem_payload)
        _choice_map = (_sem_choice if isinstance(_sem_choice, dict)
                       else {str(semantic_hold[0][0].get("id") or ""): _sem_choice})
        for _tc, _dec in semantic_hold:
            _tcid = str(_tc.get("id") or "")
            _opt_id = _choice_map.get(_tcid) if isinstance(_choice_map, dict) else _sem_choice
            _opt = next((o for o in (_dec.options or []) if o.get("id") == _opt_id), None)
            _patch = (_opt or {}).get("slots_patch")
            if _opt_id == "cancel":
                semantic_blocked.append(ToolResult(
                    tool_call_id=_tcid, name=_tc["name"],
                    content=("Commitment clarify cancelled by the user. Stop this "
                             "scheduling attempt; confirm what the user wants instead."),
                    is_error=True, duration_ms=0, outcome="terminal",
                ))
                if record_commitment_terminal:
                    try: record_commitment_terminal("cancelled")
                    except Exception: pass
            elif _patch is not None:
                # No-late-approve (AGENTS.md §20C): the LangGraph interrupt stays
                # resumable in the checkpointer long after the commitment's TTL
                # has passed, and sweep_expired may have already moved it to
                # 'expired'. The store-level "no late approve" guard only covers
                # resumes that go through update_status — NOT this clarify path,
                # which applies slots_patch directly. Re-check liveness here so a
                # stale fire_at cannot slip through.
                _alive = True
                if _dec.commitment_id:
                    try:
                        from kazma_core.safety.commitment.store import get_commitment
                        _c = get_commitment(_dec.commitment_id)
                        if _c is None or _c.status not in ("needs_clarify", "needs_confirm") or (
                            _c.expires_at is not None and time.time() > _c.expires_at
                        ):
                            _alive = False
                    except Exception:  # noqa: BLE001
                        logger.debug("[ToolWorker] commitment liveness check failed; assuming live")
                if not _alive:
                    semantic_blocked.append(ToolResult(
                        tool_call_id=_tcid, name=_tc["name"],
                        content=("This scheduling clarification has expired — the original "
                                 "timing is no longer valid. Ask the user to confirm the "
                                 "exact date/time again before re-scheduling."),
                        is_error=True, duration_ms=0, outcome="terminal",
                    ))
                    if record_commitment_terminal:
                        try:
                            record_commitment_terminal("clarify_expired")
                        except Exception:
                            pass
                else:
                    _tc["arguments"] = {**(_tc.get("arguments") or {}), **_patch}
                    pending.append(_tc)
            else:
                semantic_blocked.append(ToolResult(
                    tool_call_id=_tcid, name=_tc["name"],
                    content=(f"Commitment clarify unresolved for {_tc['name']}: "
                             f"{_dec.clarify_question or 'a specific time is required'}. "
                             "Ask the user for the exact date/time; do not re-call "
                             "this tool until they answer."),
                    is_error=True, duration_ms=0, outcome="terminal",
                ))
                if record_commitment_terminal:
                    try: record_commitment_terminal("clarify_unresolved")
                    except Exception: pass
    return pending, semantic_blocked


def mark_proposals_posted(
    tool_calls: list[Any],
    results: list[Any],
    tenant_id: str = "default",
) -> int:
    """S1-3 audit trail: mark proposals consumed by SUCCESSFUL posting calls.

    A proposal whose drafts went out is kept with kind='proposal_posted'
    (ages out faster), never deleted — posted-draft provenance must survive
    for audit. Only non-error results of the posting tool class mark; a
    failed post leaves the proposal resolvable for a retry.
    Returns the number of proposals marked (0 when nothing matched or the
    store is unavailable — marking must never break turn delivery).
    """
    try:
        from kazma_core.agent.artifacts import get_artifact_store
        from kazma_core.safety.commitment.authorize import (
            _PROPOSAL_REQUIRED_TOOLS as _POST_TOOLS,
        )

        ok_by_id = {
            str(r.get("tool_call_id")): not bool(r.get("is_error"))
            for r in results
            if isinstance(r, dict)
        }
        store = get_artifact_store()
        marked = 0
        for tc in tool_calls or []:
            if not isinstance(tc, dict) or tc.get("name") not in _POST_TOOLS:
                continue
            if not ok_by_id.get(str(tc.get("id") or ""), False):
                continue
            ref = str((tc.get("arguments") or {}).get("proposal_id") or "").strip()
            if ref:
                store.proposal_posted(ref, tenant_id=tenant_id or "default")
                marked += 1
        return marked
    except Exception:
        logger.debug("[ToolWorker] proposal_posted marking skipped", exc_info=True)
        return 0


def _tc_is_git_write(tc: "PendingToolCall") -> bool:
    """A git WRITE (commit/push/reset/checkout --/…) must always require an
    approval card — even under YOLO. Blast-radius rule from the 2026-08-27
    incident: a misread intent ("proceed with next") must cost a
    confirmation dialog, never a repo mutation. Read-only git (status/log/
    diff) is exempt. Also honored by the swarm/IDE registry path via the
    same kazma_core.agent.task_ledger.is_git_write_command predicate."""
    try:
        if str(tc.get("name", "")) not in ("exec", "shell_exec", "run_command"):
            return False
        args = tc.get("arguments") or {}
        cmd = str(args.get("command") or args.get("cmd") or "")
        if not cmd:
            return False
        from kazma_core.agent.task_ledger import is_git_write_command

        return is_git_write_command(cmd)
    except Exception:
        return False


async def tool_worker_node(
    state: SupervisorState,
    *,
    tool_executor: Any,
    tracer: Any,
    hitl_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tool Worker node — executes pending tool calls (parallel fan-out).

    All pending tool calls are dispatched concurrently via asyncio.gather.
    Results are collected and appended as tool-role messages.

    HITL: If hitl_config is provided, danger-tier tools trigger an
    interrupt() before execution, pausing the graph until the user
    approves or denies via the /api/approve endpoint.
    """
    import asyncio

    from langgraph.types import interrupt

    from kazma_core.safety.hitl import requires_approval

    pending = state.get("tool_calls_pending", [])
    if not pending:
        logger.warning("[ToolWorker] No pending tool calls — routing back")
        return {"next_node": NodeName.SUPERVISOR}

    # ── Check Circuit Breaker ──────────────────────────────────────
    breaker_tripped = state.get("circuit_breaker_tripped", False) or (state.get("consecutive_tool_failures", 0) >= 3)
    if breaker_tripped:
        logger.warning("[ToolWorker] Circuit breaker is active! Bypassing all execution.")
        results = [
            ToolResult(
                tool_call_id=tc["id"],
                name=tc["name"],
                content="SYSTEM OVERRIDE: Tool blocked due to consecutive failures. Synthesize final answer now.",
                is_error=True,
                duration_ms=0.0,
            )
            for tc in pending
        ]
        # Build tool-role messages for the conversation
        messages = [_normalize_msg(m) for m in state.get("messages", [])]
        tool_messages = [
            {
                "role": "tool",
                "tool_call_id": tr["tool_call_id"],
                "content": tr["content"],
            }
            for tr in results
        ]
        cumulative = dict(state.get("tool_results", {}))
        for tr in results:
            cumulative[tr["tool_call_id"]] = tr

        return {
            "messages": messages + tool_messages,
            "tool_calls_pending": [],
            "tool_calls_done": list(results),
            "tool_results": cumulative,
            "consecutive_tool_failures": state.get("consecutive_tool_failures", 3),
            "circuit_breaker_tripped": True,
            "next_node": NodeName.RESPOND,
        }

    logger.info("[ToolWorker] Executing %d tool calls", len(pending))

    # ── Structural hard_constraints gate (defense in depth) ────────
    # Schema allowlist at supervisor is primary; YOLO cannot expand it —
    # any non-allowlisted tool is blocked here too.
    constraint_blocked_results: list[ToolResult] = []
    _hc_list = list(state.get("hard_constraints") or [])
    try:
        from kazma_core.agent.turn_input import (
            is_tool_allowed_under_constraints,
            set_active_turn_context,
            reset_active_turn_context,
            bind_scratchpad_thread,
            reset_scratchpad_thread,
            drain_scratchpad_writes,
            should_quarantine_documents_search,
        )

        _turn_tok_tw = set_active_turn_context(
            active_goal=str(state.get("active_goal") or ""),
            active_attachments=list(state.get("active_attachments") or []),
            hard_constraints=_hc_list,
            intent_mode=str(state.get("intent_mode") or ""),
            quarantine_documents_search=should_quarantine_documents_search(
                intent_mode=str(state.get("intent_mode") or ""),
                hard_constraints=_hc_list,
                active_attachments=list(state.get("active_attachments") or []),
            ),
        )
        _sp_tok_tw = bind_scratchpad_thread(str(state.get("thread_id") or ""))
    except Exception:
        _turn_tok_tw = None
        _sp_tok_tw = None
        logger.debug("[ToolWorker] turn context bind skipped", exc_info=True)

    if _hc_list:
        try:
            from kazma_core.agent.turn_input import is_tool_allowed_under_constraints

            allowed: list[PendingToolCall] = []
            for tc in pending:
                name = str(tc.get("name") or "")
                if not is_tool_allowed_under_constraints(name, _hc_list):
                    constraint_blocked_results.append(
                        ToolResult(
                            tool_call_id=tc["id"],
                            name=name,
                            content=(
                                f"BLOCKED by hard_constraints {_hc_list}: "
                                f"tool '{name}' is not on the audit_only allowlist. "
                                "YOLO cannot override this. Use read/inspect tools only."
                            ),
                            is_error=True,
                            duration_ms=0.0,
                        )
                    )
                else:
                    allowed.append(tc)
            if constraint_blocked_results:
                logger.warning(
                    "[ToolWorker] hard_constraints=%s blocked tools: %s",
                    _hc_list,
                    [r.get("name") for r in constraint_blocked_results],
                )
            pending = allowed
        except Exception:
            logger.debug("[ToolWorker] constraint filter skipped", exc_info=True)

    if not pending and constraint_blocked_results:
        messages = [_normalize_msg(m) for m in state.get("messages", [])]
        tool_messages = [
            {
                "role": "tool",
                "tool_call_id": tr["tool_call_id"],
                "content": tr["content"],
            }
            for tr in constraint_blocked_results
        ]
        cumulative = dict(state.get("tool_results", {}))
        for tr in constraint_blocked_results:
            cumulative[tr["tool_call_id"]] = tr
        try:
            if _turn_tok_tw is not None:
                from kazma_core.agent.turn_input import reset_active_turn_context

                reset_active_turn_context(_turn_tok_tw)
            if _sp_tok_tw is not None:
                from kazma_core.agent.turn_input import reset_scratchpad_thread

                reset_scratchpad_thread(_sp_tok_tw)
        except Exception:
            pass
        return {
            "messages": messages + tool_messages,
            "tool_calls_pending": [],
            "tool_calls_done": list(constraint_blocked_results),
            "tool_results": cumulative,
            "next_node": NodeName.SUPERVISOR,
            "scratchpad": dict(state.get("scratchpad") or {}),
        }

    if not pending:
        logger.warning("[ToolWorker] No pending tool calls after filters — routing back")
        return {"next_node": NodeName.SUPERVISOR}

    # ── Bind session messages to the current async context ─────────
    # Tools such as export_session and context_info need access to the
    # current conversation messages, but the LLM does not pass them as
    # arguments.  We publish the state's messages into a ContextVar so
    # each concurrent graph invocation sees its own messages (no shared
    # module-global list).  The token restores the prior value on exit.
    from kazma_core.tools.export_session import (
        reset_current_session_messages,
        set_current_session_messages,
    )

    session_messages = [_normalize_msg(m) for m in state.get("messages", [])]
    _messages_token = set_current_session_messages(session_messages)

    # ── Bind YOLO/HITL thread id from checkpointed state ────────────
    # Transports (SSE/WS) set the ContextVar, but LangGraph node execution
    # (or a missing transport bind) can leave it empty. state.thread_id is
    # the durable identity used when enabling YOLO/tool grants — without
    # this fallback, YOLO Session behaves like Approve once forever.
    from kazma_core.safety.hitl import (
        get_current_tenant_id,
        get_current_thread_id,
        reset_current_thread_id,
        reset_current_tenant_id,
        set_current_thread_id,
        set_current_tenant_id,
    )

    _state_tid_token = None
    if not get_current_thread_id():
        _state_tid = state.get("thread_id")
        if _state_tid:
            _state_tid_token = set_current_thread_id(str(_state_tid))

    # Bind tenant_id into the ContextVar so stateless memory tools
    # (memory_search / memory_store) can read it without a state handle.
    _state_tenant_token = set_current_tenant_id(str(state.get("tenant_id", "default")))

    # ── Bind delivery target from the _gateway routing block ───────
    # Authoritative node-level bind (the reliable layer — see the note above
    # about transport-layer ContextVars not crossing into node execution).
    # `schedule_task` reads this so a reminder can route back to the chat it
    # was booked from, even after the SessionStore row is TTL-evicted.
    from kazma_core.tools.send_message import (
        get_current_delivery_target,
        reset_current_delivery_target,
        set_current_delivery_target,
    )

    _delivery_token = None
    if not get_current_delivery_target():
        _gw = state.get("_gateway") or {}
        _delivery = _gw.get("delivery_target") if isinstance(_gw, dict) else None
        if _delivery:
            _delivery_token = set_current_delivery_target(str(_delivery))

    # Phase 2.5: the semantic commitment gate is extracted to
    # _commitment_resolve_gate for SRP (policy != execution). Runs INSIDE
    # the try below: the gate can raise GraphInterrupt (clarify/confirm
    # card), and the finally must restore the session/thread/tenant/
    # delivery ContextVar binds on that path too — with the call outside
    # the try, every clarify/confirm interrupt leaked the binds.

    try:
        pending, semantic_blocked = _commitment_resolve_gate(state, pending)

        # ── HITL: separate safe and danger tools ──────────────────────
        safe_tools: list[PendingToolCall] = []
        danger_tools: list[PendingToolCall] = []

        # Signal the tool registry that the graph is the HITL authority for
        # this turn, so LocalToolRegistry.execute() skips the redundant
        # SwarmMessageBus safety.check() (mechanism B) — the graph's
        # interrupt() is the sole gate for single-agent chat. Restored in
        # the finally below.
        _graph_gate_token = None
        # Live-read HITL policy when this graph was compiled WITH a gate
        # (audit M-3). A compile-time None still means "this path has no
        # HITL" (child graphs without a checkpointer). Do not invent a gate
        # on those paths. When a snapshot was passed, Settings changes to
        # enabled / require_approval_for apply this turn.
        if hitl_config is not None:
            try:
                from kazma_core.safety.hitl import get_hitl_config as _live_hitl

                _live = _live_hitl({})
                if isinstance(_live, dict) and _live:
                    hitl_config = _live
            except Exception:
                logger.debug("[ToolWorker] live HITL config read failed", exc_info=True)

        # Truthiness fix (audit F9): ``{"enabled": False}`` is a truthy dict
        # but a DISABLED gate. Treating it as active (a) set the gate
        # ContextVar, suppressing the registry-level SwarmMessageBus gate
        # with no graph gate actually backing the turn, and (b) still routed
        # ALWAYS_HITL_TOOLS into interrupt() — which kills checkpointer-less
        # child graphs. A disabled config must behave exactly like None:
        # no ContextVar, ALWAYS_HITL_TOOLS-only danger split.
        _hitl_active = bool(hitl_config) and bool(hitl_config.get("enabled", True))
        if _hitl_active:
            from kazma_core.agent.tool_registry import _graph_hitl_gate_ctx

            _graph_gate_token = _graph_hitl_gate_ctx.set(True)
            for tc in pending:
                if requires_approval(tc["name"], hitl_config) or _tc_is_git_write(tc):
                    danger_tools.append(tc)
                else:
                    safe_tools.append(tc)
        else:
            from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS

            for tc in pending:
                if tc["name"] in ALWAYS_HITL_TOOLS or _tc_is_git_write(tc):
                    danger_tools.append(tc)
                else:
                    safe_tools.append(tc)

        async def _exec_one(tc: PendingToolCall) -> ToolResult:
            start = time.monotonic()
            _args = tc.get("arguments") or {}
            # Truncated-response guard: the provider cut the completion at
            # max_tokens (finish_reason="length"), severing the tool-call
            # JSON mid-string. The args arrive as {"raw": "<partial…"} or
            # empty. Don't execute — tell the model exactly what happened and
            # how to recover (smaller chunks), or it will retry the identical
            # oversized call until the circuit breaker trips.
            _malformed = not _args or set(_args.keys()) <= {"raw", "_malformed"}
            if _malformed and state.get("_last_finish_reason") == "length":
                result = {
                    "content": (
                        f"Error: Your previous response was TRUNCATED by the output token "
                        f"limit (max_tokens), which severed the '{tc['name']}' arguments "
                        f"mid-JSON — the tool was NOT executed. Do NOT retry the same "
                        f"large call. Instead, write the file in SMALLER pieces: call "
                        f"file_write once with the first section, then call file_append "
                        f"for each following section (keep each chunk under ~1500 "
                        f"characters). The provider already auto-retried with a doubled "
                        f"output limit; if you still see this, the content is simply too "
                        f"large for one response."
                    ),
                    "is_error": True,
                }
                duration_ms = (time.monotonic() - start) * 1000
                tracer.trace_tool_execution(
                    tool_name=tc["name"],
                    input_data=tc["arguments"],
                    output_data=result,
                    duration_ms=duration_ms,
                    success=False,
                )
                logger.warning(
                    "[ToolWorker] %s skipped — arguments truncated by max_tokens", tc["name"]
                )
                return ToolResult(
                    tool_call_id=tc["id"],
                    name=tc["name"],
                    content=result["content"],
                    is_error=True,
                    duration_ms=duration_ms,
                )

            # Document-generation guard: the model is trying to build a
            # PDF/DOCX/XLSX via raw Python (reportlab/fpdf/openpyxl) instead
            # of the purpose-built generate_* tools. This burns dozens of
            # iterations on code that produces inferior output — intercept
            # and redirect BEFORE execution (audit 2026-08-14: 100-iteration
            # rabbit hole on a 24-post Arabic calendar).
            _tool_name_low = str(tc["name"]).lower()
            if _tool_name_low in ("python_exec", "code_exec", "shell_exec"):
                _code = str(_args.get("code") or _args.get("command") or "")
                _code_low = _code.lower()
                if any(
                    marker in _code_low
                    for marker in (
                        "reportlab", "fpdf", "from fpdf", "simpledoctemplate",
                        "canvas(", "platypus", "openpyxl.workbook",
                        "docx.document", "from docx",
                    )
                ) and "generate_pdf" not in _code_low and "generate_docx" not in _code_low:
                    _doc_hint = (
                        "SYSTEM OVERRIDE: You are trying to build a document via raw "
                        "Python. STOP — use the dedicated document generator tools instead:\n"
                        "1. Write content to a .md file: file_write(path='output.md', content=...)\n"
                        "2. Generate: generate_pdf(title='...', markdown_path='output.md')\n"
                        "The generate_pdf tool handles Arabic shaping, RTL layout, styling, "
                        "and page numbering automatically — your manual code cannot. "
                        "Do NOT retry with Python."
                    )
                    _dur = (time.monotonic() - start) * 1000
                    return ToolResult(
                        tool_call_id=tc["id"],
                        name=tc["name"],
                        content=_doc_hint,
                        is_error=True,
                        duration_ms=_dur,
                    )
            # Per-tool wall-clock timeout (audit B: fault isolation). A hung
            # tool (deadlocked MCP server, wedged subprocess) previously
            # blocked the whole turn until the outer KAZMA_TURN_TIMEOUT
            # fired. Bound each call individually; a timeout is returned as
            # a HARD tool error so the loop breaker can trip after repeated
            # hangs. Config: ConfigStore agent.tool_timeout_seconds or
            # KAZMA_TOOL_TIMEOUT_SECONDS (default 120s; <=0 disables).
            _tool_timeout = _resolve_tool_timeout()
            # Phase 0 instrumentation (Commitment Layer): log every tool
            # execution with its side-effect tier + an args digest so mutator
            # traffic is observable before authorize_effect lands (Phase 1/2).
            # Interim tier detection reuses the existing TOOL_TIERS taxonomy
            # (safety.hitl.get_tool_tier); the unified side_effects.py registry
            # (plan §5) replaces this lookup in Phase 1.
            try:
                from kazma_core.safety.hitl import get_tool_tier

                _tier = get_tool_tier(tc["name"])
            except Exception:
                _tier = "unknown"
            logger.info(
                "[ToolWorker] exec name=%s tier=%s args=%s",
                tc["name"], _tier, _summarize_args_for_hitl(_args),
            )
            try:
                if _tool_timeout and _tool_timeout > 0:
                    result = await asyncio.wait_for(
                        tool_executor.execute(tc["name"], _args),
                        timeout=_tool_timeout,
                    )
                else:
                    result = await tool_executor.execute(tc["name"], _args)
            except asyncio.TimeoutError:
                duration_ms = (time.monotonic() - start) * 1000
                logger.error(
                    "[ToolWorker] %s timed out after %.0fs — returning tool error",
                    tc["name"],
                    _tool_timeout,
                )
                tracer.trace_tool_execution(
                    tool_name=tc["name"],
                    input_data=tc["arguments"],
                    output_data={"error": "timeout"},
                    duration_ms=duration_ms,
                    success=False,
                )
                return ToolResult(
                    tool_call_id=tc["id"],
                    name=tc["name"],
                    content=(
                        f"Error: Tool '{tc['name']}' timed out after "
                        f"{_tool_timeout:.0f}s and was aborted. Do NOT retry the "
                        "same call unchanged — narrow the request (smaller scope, "
                        "fewer results) or pick a different tool."
                    ),
                    is_error=True,
                    duration_ms=duration_ms,
                )
            duration_ms = (time.monotonic() - start) * 1000

            tracer.trace_tool_execution(
                tool_name=tc["name"],
                input_data=tc["arguments"],
                output_data=result,
                duration_ms=duration_ms,
                success=not result.get("is_error", False),
            )

            logger.info(
                "[ToolWorker] %s → %.0fms (error=%s)",
                tc["name"],
                duration_ms,
                result.get("is_error", False),
            )

            # ── Truncation middleware ──────────────────────────────────
            raw_content = result.get("content", "")
            content = truncate_tool_result(raw_content, tool_name=tc.get("name"))
            if len(content) != len(raw_content):
                logger.info(
                    "[ToolWorker] Truncated result from %s (%d → %d chars)", tc["name"], len(raw_content), len(content)
                )

            return ToolResult(
                tool_call_id=tc["id"],
                name=tc["name"],
                content=content,
                is_error=result.get("is_error", False),
                duration_ms=duration_ms,
            )

        def _denied_result(tc: PendingToolCall) -> ToolResult:
            """Create a ToolResult for a denied tool call."""
            return ToolResult(
                tool_call_id=tc["id"],
                name=tc["name"],
                content=f"Tool '{tc['name']}' denied by user. Operation not executed.",
                is_error=True,
                duration_ms=0,
            )

        # ── HITL: one combined interrupt for the whole danger batch ──
        # (stops N-click floods when the model emits several danger tools
        # in one turn). Scope grants (tool/yolo) are applied by /api/approve
        # *before* resume so later turns skip the gate entirely.
        approved = False
        approved_ids = None
        # Bound here (not at the safe-tool section below) because the
        # auto_deny block appends deny ToolResults before that section runs.
        results: list[ToolResult] = []
        # Sub-agent auto_deny policy (AGENTS.md §7A): spawned child graphs are
        # built with checkpointer=None, so LangGraph interrupt() cannot persist
        # a pause and the external approval-timeout watcher doesn't cover the
        # ephemeral child thread_ids — the documented "1s auto-deny" never
        # fired, and _graph_hitl_gate_ctx skipped the SwarmMessageBus gate,
        # leaving child danger tools ungated/broken (audit finding). Deny them
        # directly here instead of routing through the non-functional
        # interrupt() path.
        if danger_tools and hitl_config and hitl_config.get("auto_deny"):
            for tc in danger_tools:
                results.append(ToolResult(
                    tool_call_id=str(tc.get("id") or ""),
                    name=tc["name"],
                    content=(
                        f"Tool '{tc['name']}' is a danger tool and was auto-denied "
                        "(this graph runs with auto_deny HITL — it cannot pause "
                        "for human approval)."
                    ),
                    is_error=True,
                    duration_ms=0.0,
                    outcome="hard",
                ))
            try:
                from kazma_core.metrics import record_commitment_terminal
            except Exception:
                record_commitment_terminal = None  # type: ignore[assignment]
            if record_commitment_terminal:
                try:
                    record_commitment_terminal("auto_denied")
                except Exception:
                    pass
            danger_tools = []

        # ALWAYS_HITL_TOOLS fail-closed (audit F9): with no ACTIVE gate
        # (hitl_config None or {"enabled": False}) the danger batch above can
        # only contain ALWAYS_HITL_TOOLS. interrupt() is not an option here —
        # the checkpointer is not determinable at this point, and graphs
        # without one (checkpointer-less children, streaming/voice) can
        # never resume the pause. Deny with a clear actionable error
        # instead of minting an unresumable interrupt.
        if danger_tools and not _hitl_active:
            for tc in danger_tools:
                results.append(ToolResult(
                    tool_call_id=str(tc.get("id") or ""),
                    name=tc["name"],
                    content=(
                        f"Tool '{tc['name']}' always requires explicit human "
                        "approval, but the HITL approval gate is disabled on "
                        "this graph — the call was DENIED, not executed. "
                        "Re-enable safety.hitl.enabled to use this tool."
                    ),
                    is_error=True,
                    duration_ms=0.0,
                    outcome="hard",
                ))
            danger_tools = []

        if danger_tools:
            tools_payload = [
                {
                    "id": tc.get("id"),
                    "name": tc["name"],
                    "args": tc.get("arguments") or {},
                }
                for tc in danger_tools
            ]
            if len(danger_tools) == 1:
                tc0 = danger_tools[0]
                message = _format_hitl_message(tc0["name"], tc0.get("arguments") or {})
                primary_tool = tc0["name"]
                primary_args = tc0.get("arguments") or {}
            else:
                names = ", ".join(tc["name"] for tc in danger_tools)
                message = (
                    f"Agent wants to run {len(danger_tools)} danger tools: {names}"
                )
                primary_tool = f"{len(danger_tools)} tools"
                primary_args = {"tools": [t["name"] for t in tools_payload]}

            # S1-3: proposal-backed posts resolve the STORED text onto the
            # card — approve resolves an ID against the durable artifact
            # store, never the model's context memory (2026-08-30 incident).
            try:
                from kazma_core.agent.artifacts import get_artifact_store as _gas

                _tenant = get_current_tenant_id() or "default"
                for _tcd in tools_payload:
                    _ref = str((_tcd.get("args") or {}).get("proposal_id") or "").strip()
                    if not _ref:
                        continue
                    _p = _gas().resolve_proposal(_ref, tenant_id=_tenant)
                    if _p:
                        _tcd["proposal"] = {
                            "proposal_id": _p.get("proposal_id"),
                            "kind": _p.get("kind"),
                            "items": [
                                {"id": i.get("id"), "text": str(i.get("text") or "")[:600]}
                                for i in (_p.get("items") or [])
                            ],
                        }
            except Exception:
                logger.debug("[ToolWorker] proposal card resolution skipped", exc_info=True)

            from kazma_core.safety.yolo import yolo_allowed as _yolo_allowed
            from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS as _ALWAYS_HITL

            _batch_always = any(tc["name"] in _ALWAYS_HITL for tc in danger_tools)
            approval_input = {
                "type": "hitl_approval",
                "kind": "security",  # self-describing (§4.3): every payload carries kind
                "tool": primary_tool,
                "args": primary_args,
                "tools": tools_payload,
                "message": message,
                "yolo_allowed": _yolo_allowed() and not _batch_always,
            }

            # Defense-in-depth: requires_approval() already filtered YOLO/grants
            # when splitting safe/danger above. Re-check here in case grants
            # were applied mid-turn (e.g. YOLO enabled just before resume).
            from kazma_core.safety.yolo import is_yolo_active

            current_thread = get_current_thread_id() or state.get("thread_id") or ""
            from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS

            _always = [
                tc
                for tc in danger_tools
                if tc["name"] in ALWAYS_HITL_TOOLS or _tc_is_git_write(tc)
            ]
            if current_thread and is_yolo_active(str(current_thread)) and not _always:
                logger.warning(
                    "[ToolWorker] YOLO active for thread=%s — auto-approving %d danger tool(s)",
                    current_thread,
                    len(danger_tools),
                )
                approved = True
                approval = {"approved": True, "yolo": True}
            else:
                # interrupt() pauses the graph — resumes when /api/approve
                # calls graph.ainvoke(Command(resume=...), config)
                approval = interrupt(approval_input)
                approved = isinstance(approval, dict) and approval.get("approved", False)
            # Optional selective ids; None/missing → all tools in the batch.
            if isinstance(approval, dict):
                raw_ids = approval.get("approved_ids")
                if isinstance(raw_ids, list):
                    approved_ids = {str(x) for x in raw_ids}

        # ── Execute safe tools in parallel ────────────────────────────
        results.extend(list(constraint_blocked_results) + list(semantic_blocked))
        if safe_tools:
            results.extend(await asyncio.gather(*(_exec_one(tc) for tc in safe_tools)))

        # ── Execute/deny danger tools ─────────────────────────────────
        if danger_tools:
            from kazma_core.agent.tool_registry import _hitl_approved_ctx

            for tc in danger_tools:
                tc_id = str(tc.get("id") or "")
                allow = approved and (approved_ids is None or tc_id in approved_ids)
                if allow:
                    logger.info("[ToolWorker] HITL approved: %s", tc["name"])
                    _token = _hitl_approved_ctx.set(True)
                    try:
                        results.append(await _exec_one(tc))
                    finally:
                        _hitl_approved_ctx.reset(_token)
                else:
                    logger.info("[ToolWorker] HITL denied: %s", tc["name"])
                    results.append(_denied_result(tc))

        # S1-3 audit trail: mark proposals consumed by SUCCESSFUL posts.
        mark_proposals_posted(safe_tools + danger_tools, results, str(state.get("tenant_id") or "default"))

        # ── Tool-loop breaker (typed outcomes, per-round credit) ─────
        # Policy / HITL deny / empty results do not trip. Parallel hard
        # errors in one batch credit +1 only (not +N). See tool_loop_breaker.
        from kazma_core.agent.tool_loop_breaker import update_breaker

        prev_failures = int(state.get("consecutive_tool_failures", 0) or 0)
        breaker_state, results = update_breaker(prev_failures, list(results))
        consecutive_failures = breaker_state.consecutive_hard_rounds
        breaker_tripped_now = breaker_state.tripped
        if breaker_tripped_now:
            logger.warning(
                "[ToolWorker] Circuit breaker tripped! %d consecutive hard tool rounds.",
                consecutive_failures,
            )

        # PR3 (loop kill): a TERMINAL outcome (unresolved / cancelled / denied
        # commitment clarify) ends the turn immediately. It is classified
        # TERMINAL (not HARD) so it doesn't credit the failure counter; here we
        # just force RESPOND so the model is never handed a retryable tool error
        # for a gate outcome. This is the invariant that makes the permission-
        # card loop class unkillable (incident 2026-08-12).
        _terminal_now = any(str(r.get("outcome", "")) == "terminal" for r in results)

        # ── Semantic stagnation detection ───────────────────────────
        # The hard-failure breaker misses loops of *successful-but-useless*
        # calls (same no-op edit, same denied path, same empty search). Track
        # (tool, canonical-args) signatures over a sliding window; a signature
        # repeated >= threshold times trips the same "stop and synthesize"
        # path with a strategy-change hint for the model.
        from kazma_core.agent.tool_loop_breaker import detect_stagnation, tool_signature

        # Only outcomes that represent real model behaviour feed the window:
        # policy denials / user denies / empties are control-plane signals and
        # must NOT count as stagnation (mirrors the hard-breaker credit rules —
        # repeating a denied call is the model being correctly blocked).
        _id_to_outcome = {str(tr.get("tool_call_id")): str(tr.get("outcome", "")) for tr in results}
        sigs = list(state.get("tool_signatures") or [])
        for tc in safe_tools + danger_tools:
            outcome = _id_to_outcome.get(str(tc.get("id")), "")
            if outcome in ("policy", "user_deny", "empty"):
                continue
            sigs.append(tool_signature(tc["name"], tc.get("arguments") or {}))
        sigs = sigs[-24:]  # bounded window
        stagnant_sig = detect_stagnation(sigs)
        if stagnant_sig and not breaker_tripped_now:
            stagnant_names = [
                tc["name"]
                for tc in (safe_tools + danger_tools)
                if tool_signature(tc["name"], tc.get("arguments") or {}) == stagnant_sig
            ]
            logger.warning(
                "[ToolWorker] Semantic stagnation detected (repeated identical "
                "calls: %s) — forcing synthesis with strategy-change hint",
                sorted(set(stagnant_names)),
            )
            try:
                from kazma_core.agent.long_task import record_long_task_event

                record_long_task_event("tool_loop_break")
            except Exception:
                pass
            breaker_tripped_now = True
            # Stamp this round's results with the strategy-change message.
            stamped_results: list[ToolResult] = []
            for tr in results:
                tr2 = dict(tr)
                tr2["content"] = (
                    "SYSTEM OVERRIDE: You have repeated the same tool call(s) "
                    f"({', '.join(sorted(set(stagnant_names))) or 'identical calls'}) "
                    "multiple times without progress. STOP retrying the identical "
                    "call — change strategy (different tool, different arguments, "
                    "or narrower scope) or synthesize your final answer now."
                )
                tr2["is_error"] = True
                tr2["outcome"] = "hard"
                stamped_results.append(tr2)
            results = stamped_results

        # ── Recovery-spiral breaker (S2-3) ─────────────────────────────
        # N≥3 turn-cumulative queries against session/checkpoint/audit
        # stores hunting the assistant's own prior output → force an honest
        # RESPOND. Digging made the 2026-08-30 incident worse than the loss
        # itself: every recovery query added tool output to the history whose
        # size caused the trim.
        from kazma_core.agent.tool_loop_breaker import (
            RECOVERY_SPIRAL_THRESHOLD,
            count_recovery_probes,
            recovery_honest_message,
        )

        _probes = int(state.get("recovery_probes") or 0) + count_recovery_probes(
            list(safe_tools) + list(danger_tools), list(results)
        )
        if _probes >= RECOVERY_SPIRAL_THRESHOLD and not breaker_tripped_now:
            logger.warning(
                "[ToolWorker] Recovery spiral detected (%d store probes hunting "
                "prior output) — forcing honest RESPOND",
                _probes,
            )
            breaker_tripped_now = True
            _honest = recovery_honest_message(_probes)
            stamped_results = []
            for tr in results:
                tr2 = dict(tr)
                tr2["content"] = _honest
                tr2["is_error"] = True
                tr2["outcome"] = "hard"
                stamped_results.append(tr2)
            results = stamped_results
            # The honest turn must not re-enter the tool loop.
            _probes = 0

        # Build tool-role messages for the conversation
        messages = [_normalize_msg(m) for m in state.get("messages", [])]
        tool_messages: list[dict[str, Any]] = []
        for tr in results:
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"],
                }
            )

        # Soft research-depth gate + R4 pipeline prefer (nudge once each)
        try:
            from kazma_core.agent.research_policy import (
                should_nudge_more_sources,
                should_prefer_pipeline,
            )

            already = bool(state.get("_research_depth_nudged"))
            already_pipe = bool(state.get("_research_pipeline_nudged"))
            turn_tools = [str(tc.get("name") or "") for tc in (safe_tools + danger_tools)]
            # Prefer pipeline first when deep intent + manual tools
            pipe_nudge = should_prefer_pipeline(
                messages, turn_tools, already_nudged=already_pipe
            )
            if pipe_nudge:
                tool_messages.append({"role": "system", "content": pipe_nudge})
                state = {**state, "_research_pipeline_nudged": True}
            else:
                nudge = should_nudge_more_sources(
                    messages, turn_tools, already_nudged=already
                )
                if nudge:
                    tool_messages.append({"role": "system", "content": nudge})
                    state = {**state, "_research_depth_nudged": True}
        except Exception:
            pass

        # Merge into cumulative tool_results, bounded to a recent window —
        # the dict is checkpointed every superstep and previously grew for
        # the thread's whole life (memory + checkpoint bloat on long
        # mission threads). dict order ≈ insertion order, so the tail is
        # the newest entries.
        cumulative = dict(state.get("tool_results", {}))
        for tr in results:
            cumulative[tr["tool_call_id"]] = tr
        if len(cumulative) > 200:
            cumulative = dict(list(cumulative.items())[-200:])

        out: dict[str, Any] = {
            "messages": messages + tool_messages,
            "tool_calls_pending": [],  # all consumed
            "tool_calls_done": list(results),
            "tool_results": cumulative,
            "consecutive_tool_failures": consecutive_failures,
            "circuit_breaker_tripped": breaker_tripped_now,
            "tool_signatures": sigs,
            "recovery_probes": _probes,
            # If the breaker just tripped or max consecutive failures hit, force RESPOND
            "next_node": NodeName.RESPOND if (breaker_tripped_now or consecutive_failures >= 3 or _terminal_now) else NodeName.SUPERVISOR,
        }
        if state.get("_research_depth_nudged"):
            out["_research_depth_nudged"] = True
        if state.get("_research_pipeline_nudged"):
            out["_research_pipeline_nudged"] = True
        # Merge typed scratchpad writes from update_scratchpad this hop
        try:
            from kazma_core.agent.turn_input import drain_scratchpad_writes

            _sp = dict(state.get("scratchpad") or {})
            _sp.update(drain_scratchpad_writes(str(state.get("thread_id") or "")))
            out["scratchpad"] = _sp
        except Exception:
            pass
        return out
    finally:
        # Always restore prior ContextVar values, even if a tool raised or
        # the graph was interrupted by HITL.
        reset_current_session_messages(_messages_token)
        try:
            if _graph_gate_token is not None:
                from kazma_core.agent.tool_registry import _graph_hitl_gate_ctx

                _graph_hitl_gate_ctx.reset(_graph_gate_token)
        except NameError:
            pass
        if _state_tid_token is not None:
            reset_current_thread_id(_state_tid_token)
        reset_current_tenant_id(_state_tenant_token)
        if _delivery_token is not None:
            reset_current_delivery_target(_delivery_token)
        try:
            if _turn_tok_tw is not None:
                from kazma_core.agent.turn_input import reset_active_turn_context

                reset_active_turn_context(_turn_tok_tw)
            if _sp_tok_tw is not None:
                from kazma_core.agent.turn_input import reset_scratchpad_thread

                reset_scratchpad_thread(_sp_tok_tw)
        except Exception:
            pass
