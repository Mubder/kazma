"""Respond node — turn finalization (extracted from graph_builder)."""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.agent.graph_helpers import (
    is_unusable_assistant_content,
    sanitize_tool_chains,
)
from kazma_core.agent.state import SupervisorState
from kazma_core.llm_stream import invoke_llm_chat
from kazma_core.summarizer import _normalize_msg

logger = logging.getLogger(__name__)

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
        return candidates[-1] if candidates else ""

    _last = messages[-1] if messages else {}
    _last_role = _last.get("role") if isinstance(_last, dict) else None
    _max_hit = iteration >= state.get("max_iterations", 15)
    _final_text = _final_assistant_text_after_tools(messages)
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
        _llm = llm or state.get("_llm")
        if _llm is not None:
            try:
                from kazma_core.runtime.turn_model import resolve_turn_client

                _llm, _ = resolve_turn_client(_llm)
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
                        "- Summarize what you DID find/complete from tool results.\n"
                        "- Explicitly list what you did NOT finish and the next step "
                        "the user can ask for.\n"
                        "- Start with a one-line status (done / partial / blocked).\n"
                        "- Match the user's language (Arabic if they wrote Arabic)."
                    ),
                }
                _resp = await invoke_llm_chat(
                    _llm, pruned_for_synth + [_wrap_msg], tools=None
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
                    messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                "⚠️ Partial result: I finished tools but could not "
                                "produce a clean final report (model returned tool "
                                "markup instead of text). Send **continue** or a "
                                "narrower request (e.g. list entities / invalidate X)."
                            ),
                        }
                    )
                else:
                    logger.warning(
                        "[Respond] Synthesis returned empty content "
                        "(last_role=%s messages=%d) — generating action summary fallback",
                        _last_role,
                        len(messages),
                    )
                    tools_used = [
                        tc.get("function", {}).get("name") or tc.get("name", "tool")
                        for m in messages if isinstance(m, dict) and m.get("tool_calls")
                        for tc in (m.get("tool_calls") or []) if isinstance(tc, dict)
                    ]
                    tool_summary_str = (
                        f" used tools ({', '.join(sorted(set(tools_used)))})"
                        if tools_used
                        else " hit the tool-round limit"
                    )
                    messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                f"⚠️ Partial result:{tool_summary_str}. "
                                "I could not finish a full report before the step limit. "
                                "Send **continue** or a narrower request to finish."
                            ),
                        }
                    )
            except Exception as exc:
                logger.warning("[Respond] Could not synthesize final answer: %s", exc)
                messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "⚠️ Turn stopped at the tool-round limit. "
                            "Send **continue** with a shorter goal so I can finish."
                        ),
                    }
                )
        else:
            logger.warning(
                "[Respond] Max iterations with no LLM bound "
                "(last_role=%s) — injecting recovery notice",
                _last_role,
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": (
                        "⚠️ Turn stopped at the tool-round limit without a written "
                        "answer. Please send another message to continue."
                    ),
                }
            )

    # ── Final empty-answer safety net ───────────────────────────────
    # After synthesis (or if synthesis was skipped), ensure the user never
    # sees a blank turn. Prefer tool-aware partial notice over a vague
    # "no written answer" line.
    _final_for_user = _final_assistant_text_after_tools(messages)
    if not _final_for_user or is_unusable_assistant_content(_final_for_user):
        tools_used = [
            tc.get("function", {}).get("name") or tc.get("name", "tool")
            for m in messages
            if isinstance(m, dict) and m.get("tool_calls")
            for tc in (m.get("tool_calls") or [])
            if isinstance(tc, dict)
        ]
        tool_note = (
            f" Tools used: {', '.join(sorted(set(tools_used))[:12])}."
            if tools_used
            else ""
        )
        logger.warning(
            "[Respond] Still no usable final text after synthesis path "
            "(iteration=%d messages=%d last_role=%s force=%s) — terminal fallback",
            iteration,
            len(messages),
            _last_role,
            _force_synth,
        )
        messages.append(
            {
                "role": "assistant",
                "content": (
                    "⚠️ Partial result: I could not produce a clean final answer "
                    f"this turn.{tool_note} "
                    "Send **continue** or a narrower request "
                    "(e.g. `list memory entities` / `invalidate belief …`)."
                ),
            }
        )

    # Post-turn memory: signal that memory work is pending so the gateway
    # handler can fire it AFTER the graph reaches terminal state (preventing
    # the CoT "active again" flicker — the memory thread's SQLite writes
    # would otherwise re-trigger the CoT panel while it's showing "Done").
    return {
        "messages": messages,
        "iteration": iteration,
        "tool_calls_pending": [],
        "tool_calls_done": [],
        "next_node": "end",
        "_post_turn_memory": {
            "session_id": state.get("thread_id"),
            "turn": iteration,
            "tenant_id": state.get("tenant_id", "default"),
        },
    }


