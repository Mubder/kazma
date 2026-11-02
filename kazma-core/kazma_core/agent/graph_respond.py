"""Respond node — turn finalization (extracted from graph_builder)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from kazma_core.agent.graph_helpers import (
    is_unusable_assistant_content,
    sanitize_tool_chains,
)
from kazma_core.agent.plan_fence import (
    is_plan_only,
    normalize_plan_fence,
    rewrite_terminal_assistant_message,
    running_turn,
)
from kazma_core.agent.source_grounding import with_source_grounding
from kazma_core.agent.state import SupervisorState
from kazma_core.llm_stream import invoke_llm_chat
from kazma_core.summarizer import _normalize_msg


async def _ledger_synthesis_call(
    state: SupervisorState, llm: Any, resp: Any, duration_ms: float
) -> None:
    """The final synthesis is one of the turn's LLM calls (llm_calls.db).

    Only the supervisor's calls were ledgered, so a turn's totals missed its
    last and often largest call. Off the loop: the ledger is SQLite, and the
    switch that turns it on a settings read (``arecord_llm_call``).
    """
    from kazma_core.observability.llm_ledger import arecord_llm_call

    usage = getattr(resp, "usage", None) or {}
    try:
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
        cost = float(getattr(resp, "cost_usd", 0.0) or 0.0)
    except (AttributeError, TypeError, ValueError):
        prompt, completion, cost = 0, 0, 0.0
    await arecord_llm_call(
        thread_id=str(state.get("thread_id", "")),
        iteration=int(state.get("iteration", 0) or 0),
        provider=type(llm).__name__,
        model=str(getattr(resp, "model", "") or ""),
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=cost,
        duration_ms=duration_ms,
        status="ok",
    )

logger = logging.getLogger(__name__)


def _what_ran(state: SupervisorState, messages: list[dict[str, Any]]) -> str:
    """One sentence on the tools of the RUNNING turn: which ran, which were
    refused before running (FA-211: the fallback listed every tool call the
    model had ever asked for in the chat, and said "used tools (file_write)"
    over a cut-off call that never ran)."""
    from kazma_core.agent.tool_loop_breaker import ToolOutcome, classify_tool_result

    names: dict[str, str] = {}
    turn = running_turn(messages)
    for m in turn:
        for tc in (m.get("tool_calls") or []) if isinstance(m, dict) else []:
            if isinstance(tc, dict):
                names[str(tc.get("id") or "")] = str(
                    (tc.get("function") or {}).get("name") or tc.get("name") or "tool"
                )
    recorded = state.get("tool_results") or {}
    ran: list[str] = []
    refused: list[str] = []
    for m in turn:
        if not isinstance(m, dict) or m.get("role") not in ("tool", "function"):
            continue
        call_id = str(m.get("tool_call_id") or "")
        result = dict(recorded.get(call_id) or {})
        result.setdefault("content", m.get("content") or "")
        name = str(result.get("name") or m.get("name") or names.get(call_id) or "tool")
        result["name"] = name
        outcome = classify_tool_result(result)
        not_run = (ToolOutcome.POLICY, ToolOutcome.USER_DENY, ToolOutcome.TERMINAL)
        (refused if outcome in not_run else ran).append(name)
    parts = []
    if ran:
        parts.append(f"This turn ran: {', '.join(sorted(set(ran))[:12])}.")
    else:
        parts.append("No tool ran this turn.")
    if refused:
        parts.append(f"Not run (refused or declined): {', '.join(sorted(set(refused))[:12])}.")
    return " ".join(parts)


def _stop_cause(state: SupervisorState, *, max_hit: bool, junk_final: bool) -> tuple[str, str]:
    """Why the turn ended without a finished answer, and what the user can do.

    Said from what happened (FA-211): every ending was "the tool-round limit"
    / "the step limit", and "Send continue" re-ran a provider's failure.
    """
    if max_hit:
        steps = int(state.get("max_iterations", 15) or 15)
        return (
            f"I reached this turn's step limit ({steps} steps)",
            "Send **continue** to carry on, or a narrower request.",
        )
    if state.get("_last_finish_reason") == "length":
        return (
            "the model's answer was cut off by its output limit",
            "Ask for a shorter answer, or for the work in smaller pieces.",
        )
    if junk_final:
        return (
            "the model's last answer was not usable text (tool markup or an unfinished stub)",
            "Send **continue**, or ask again.",
        )
    return (
        "the model ended without a written answer",
        "Send **continue**, or ask again.",
    )


def _partial_notice(
    state: SupervisorState,
    messages: list[dict[str, Any]],
    *,
    max_hit: bool,
    junk_final: bool,
    summary: str,
) -> str:
    """The one wording of a turn that ended without a finished answer."""
    cause, remedy = _stop_cause(state, max_hit=max_hit, junk_final=junk_final)
    summary = f", and {summary}" if summary else ""
    return f"⚠️ Partial result: {cause}{summary}. {_what_ran(state, messages)} {remedy}"


async def respond_node(state: SupervisorState, llm: Any = None) -> dict[str, Any]:
    """Respond node — finalizes the turn.

    Extracts the last assistant message as the response and increments
    the iteration counter. Also schedules automatic long-term memory
    writes (durable facts / turn snapshots) so recall is not tool-only.

    If the last message is a tool result (max-iterations forced respond
    mid-tool-loop), makes a final LLM call to synthesize a text answer
    from the collected tool results so the user gets a response.

    Args:
        state: The current supervisor state.
        llm:   The LLMProvider for synthesizing a final answer when
               max-iterations forces a respond mid-tool-loop. Optional
               for backward compat (the synthesis step is skipped if None).
    """
    messages = [_normalize_msg(m) for m in state.get("messages", [])]
    iteration = state.get("iteration", 0) + 1

    # Clear the per-turn file-read dedup cache (turn boundary — audit 2026-08-15)
    try:
        from kazma_core.tools.file_read import clear_turn_read_cache

        clear_turn_read_cache()
    except Exception:
        pass

    # Sanitize tool chains to remove any unhandled/dangling tool_calls
    # (e.g. when max_iterations forced routing to respond before ToolWorker ran)
    messages = sanitize_tool_chains(messages)
    quality_draft = list(messages)

    logger.info(
        "[Respond] Finalizing turn (iteration=%d, messages=%d)",
        iteration,
        len(messages),
    )

    # If max iterations forced us here mid-tool-loop, there is often no
    # *complete* user-visible answer. Industry rule: ALWAYS run a final
    # synthesis LLM call on max-iter (unless turn_failed). Char-count
    # heuristics failed in production — a 382-char mid-diagnosis ("Let me
    # verify…") looked "substantial" and the UI showed Done with no
    # finished report (2026-08-03 long-horizon cleanup).
    def _final_assistant_text_after_tools(msgs: list[dict[str, Any]]) -> str:
        last_tool_idx = -1
        for i, m in enumerate(msgs):
            if isinstance(m, dict) and m.get("role") in ("tool", "function"):
                last_tool_idx = i
        candidates: list[str] = []
        scan = msgs if last_tool_idx < 0 else msgs[last_tool_idx + 1 :]
        for m in scan:
            if not isinstance(m, dict):
                continue
            if m.get("role") not in ("assistant", "ai"):
                continue
            if m.get("tool_calls"):
                continue
            content = m.get("content") or ""
            if isinstance(content, str) and content.strip():
                candidates.append(content.strip())
        text = candidates[-1] if candidates else ""
        if not text:
            return ""
        # A ```plan is never the user-facing answer on its own — the model
        # planned but never acted (whether or not earlier tools ran).
        # Forcing synthesis with the honesty note prevents fake "done ✅"
        # claims over unexecuted plans (2026-08-26 X-post incident).
        # Plan mode (inspect-only) is the exception: the plan IS the
        # deliverable the user asked for.
        if is_plan_only(text):
            try:
                from kazma_core.agent.plan_mode import is_plan_mode

                if not is_plan_mode(str(state.get("thread_id") or "")):
                    return ""
            except Exception:
                return ""
        return normalize_plan_fence(text)

    _last = messages[-1] if messages else {}
    _last_role = _last.get("role") if isinstance(_last, dict) else None
    _max_hit = iteration >= state.get("max_iterations", 15)
    # A plan-only answer asks the chat's plan mode: a settings read (audit 2026-10-26 AUD-111).
    _final_text = await asyncio.to_thread(_final_assistant_text_after_tools, messages)
    _force_synth = bool(state.get("force_synthesis"))
    _junk_final = bool(_final_text and is_unusable_assistant_content(_final_text))
    _usable_final = bool(_final_text) and not _junk_final
    # Synthesize when: max-iter, supervisor forced it, final is junk/leak,
    # OR there is simply no usable final text (e.g. last msg is tool result
    # after empty/leak was stripped). Never ship "no written answer" without
    # attempting synthesis first (2026-08-03 force_synthesis drop regression).
    _needs_synthesis = bool(
        _max_hit or _force_synth or _junk_final or not _usable_final
    )
    if _junk_final:
        logger.warning(
            "[Respond] Final draft unusable (leak/stub, %d chars) — forcing synthesis",
            len(_final_text or ""),
        )
    elif _force_synth:
        logger.info("[Respond] force_synthesis=True — running final synthesis")
    elif not _usable_final:
        logger.info(
            "[Respond] No usable final text (last_role=%s) — running final synthesis",
            _last_role,
        )
    # If the supervisor's LLM call failed (after retries), the assistant
    # message above is an honest error notice, NOT a real answer. Never
    # synthesize a plausible-looking final answer over a broken turn — that
    # was the root cause of the "model stopped thinking" symptom. Surface
    # the error and end the turn.
    if state.get("turn_failed"):
        logger.info(
            "[Respond] Turn failed (turn_failed=True) — skipping synthesis, "
            "surfacing honest error (iteration=%d messages=%d)",
            iteration,
            len(messages),
        )
        _needs_synthesis = False
    if _needs_synthesis:
        _llm = llm
        if _llm is not None:
            try:
                from kazma_core.runtime.live_llm import resolve_live_client

                _llm, _ = await asyncio.to_thread(resolve_live_client, _llm, state=state)  # the registry (audit 2026-10-26 AUD-111)
            except Exception:
                pass
            try:
                from kazma_core.summarizer import prune_tool_outputs
                pruned_for_synth = prune_tool_outputs(messages, max_tokens=18000)
                _reason = (
                    "tool-round / long-horizon limit"
                    if _max_hit
                    else "unusable draft (leaked tool markup or incomplete stub)"
                    if _junk_final
                    else "forced finalization"
                )
                _wrap_msg = {
                    "role": "user",
                    "content": (
                        f"SYSTEM: Finalization required ({_reason}). "
                        "Write the COMPLETE final answer for the user NOW.\n"
                        "Rules:\n"
                        "- Do not call any more tools.\n"
                        "- Do not emit tool XML/DSML/markup.\n"
                        "- Do not continue mid-thought ('let me check…', 'next I will…').\n"
                        "- NEVER claim an action succeeded (posted, sent, saved, "
                        "deleted) unless its tool RESULT is in the conversation "
                        "above. A plan or intention is NOT a result.\n"
                        "- Summarize what you DID find/complete from tool results.\n"
                        "- Explicitly list what you did NOT finish and the next step "
                        "the user can ask for.\n"
                        "- Start with a one-line status (done / partial / blocked).\n"
                        "- Match the user's language (Arabic if they wrote Arabic)."
                    ),
                }
                _synth_started = time.monotonic()
                _resp = await invoke_llm_chat(
                    _llm, with_source_grounding(pruned_for_synth + [_wrap_msg]), tools=None
                )
                await _ledger_synthesis_call(
                    state, _llm, _resp, (time.monotonic() - _synth_started) * 1000
                )
                _content = getattr(_resp, "content", "") or ""
                if _content.strip() and not is_unusable_assistant_content(_content):
                    # Prefer synthesis as the terminal message; keep prior
                    # drafts in history but surface the complete answer last.
                    messages.append({"role": "assistant", "content": _content})
                    logger.info(
                        "[Respond] Synthesized final answer (%d chars, prior_draft=%d)",
                        len(_content),
                        len(_final_text or ""),
                    )
                elif _content.strip() and is_unusable_assistant_content(_content):
                    logger.warning(
                        "[Respond] Synthesis still unusable (%d chars) — fallback notice",
                        len(_content),
                    )
                    messages.append({"role": "assistant", "content": _partial_notice(
                        state, messages, max_hit=_max_hit, junk_final=_junk_final,
                        summary="the final summary came back as tool markup instead of text",
                    )})
                else:
                    logger.warning(
                        "[Respond] Synthesis returned empty content "
                        "(last_role=%s messages=%d) — partial-result notice",
                        _last_role,
                        len(messages),
                    )
                    messages.append({"role": "assistant", "content": _partial_notice(
                        state, messages, max_hit=_max_hit, junk_final=_junk_final,
                        summary="asking for a final summary got an empty answer",
                    )})
            except Exception as exc:
                logger.warning("[Respond] Could not synthesize final answer: %s", exc)
                from kazma_core.retry import friendly_llm_error

                failed = friendly_llm_error(exc).removeprefix("⚠️").strip()
                messages.append({"role": "assistant", "content": _partial_notice(
                    state, messages, max_hit=_max_hit, junk_final=_junk_final,
                    summary=f"the final summary could not be written ({failed})",
                )})
        else:
            logger.warning(
                "[Respond] No usable answer and no LLM bound "
                "(last_role=%s) — injecting recovery notice",
                _last_role,
            )
            messages.append({"role": "assistant", "content": _partial_notice(
                state, messages, max_hit=_max_hit, junk_final=_junk_final, summary="",
            )})

    # ── Final empty-answer safety net ───────────────────────────────
    # After synthesis (or if synthesis was skipped), ensure the user never
    # sees a blank turn. Prefer tool-aware partial notice over a vague
    # "no written answer" line.
    _final_for_user = await asyncio.to_thread(_final_assistant_text_after_tools, messages)
    if not _final_for_user or is_unusable_assistant_content(_final_for_user):
        logger.warning(
            "[Respond] Still no usable final text after synthesis path "
            "(iteration=%d messages=%d last_role=%s force=%s) — terminal fallback",
            iteration,
            len(messages),
            _last_role,
            _force_synth,
        )
        messages.append({"role": "assistant", "content": _partial_notice(
            state, messages, max_hit=_max_hit, junk_final=_junk_final,
            summary="no clean final answer could be written",
        )})

    # Un-glue ```plan fences on the terminal assistant message so Telegram /
    # SSE / WS / session reload never hide the answer inside an unclosed
    # CommonMark code block (````Saved.`` incident, 2026-08-26).
    messages = rewrite_terminal_assistant_message(messages)

    # One shared terminal answer for SSE, saved history and every adapter.
    # The formatting pass cannot execute tools or synthesize over failure.
    from kazma_core.agent.answer_format import format_terminal_answer

    async def _record_format_call(client: Any, response: Any, duration: float) -> None:
        await _ledger_synthesis_call(state, client, response, duration)

    messages = await format_terminal_answer(
        messages, llm=llm, state=state, on_call=_record_format_call,
    )

    # Metadata only, once at the shared graph terminal boundary. A ledger
    # failure must not prevent a reply, and disk work stays off the loop.
    from kazma_core.observability.answer_quality import record as record_quality

    await asyncio.to_thread(record_quality, quality_draft, messages, state)

    # Post-turn memory: mark this turn as finished so the turn's closer
    # (kazma_ui.turn_runtime.close_turn, which every transport runs) hands
    # it to memory AFTER the graph is terminal -- memory writes never run
    # inside the turn. ``turn`` is the conversation's turn index
    # (consolidator.user_turn_index), not the iteration count: that made a
    # question asked twice in one thread collide into one memory.
    from kazma_core.memory.consolidator import user_turn_index

    return {
        "messages": messages,
        "iteration": iteration,
        "tool_calls_pending": [],
        "tool_calls_done": [],
        "next_node": "end",
        "_post_turn_memory": {
            "session_id": state.get("thread_id"),
            "turn": user_turn_index(messages),
            "tenant_id": state.get("tenant_id", "default"),
        },
    }


