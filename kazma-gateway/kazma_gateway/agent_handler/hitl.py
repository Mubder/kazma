"""HITL submodule — graph interrupt detection and approval resume handlers."""

from __future__ import annotations

import logging
from typing import Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage, SessionStore
from .store import _build_target_id

logger = logging.getLogger(__name__)

__all__: list[str] = []


async def _stale_approval_message(
    graph: Any,
    config: dict[str, Any],
    thread_id: str,
    *,
    action: str,
    approved: bool,
) -> str | None:
    """Human message for a late/duplicate approve, or None to stay silent.

    If we recently completed a resume for this thread, silence the second tap.
    If the graph already finished with an assistant answer, say so honestly —
    never claim "Nothing was executed" when work already completed.
    """
    import time

    try:
        from kazma_core.config_store import get_config_store

        last = get_config_store().get(f"hitl.last_resume.{thread_id}")
        if isinstance(last, dict):
            at = float(last.get("at") or 0)
            if at and (time.time() - at) < 90:
                # Duplicate within 90s of a successful resume — no spam.
                return None
    except Exception:
        pass

    # Graph finished (no next nodes) — likely already approved and completed.
    try:
        snapshot = await graph.aget_state(config)
        next_nodes = getattr(snapshot, "next", None) or ()
        vals = getattr(snapshot, "values", None) or {}
        msgs = vals.get("messages") or []
        has_assistant = any(
            isinstance(m, dict)
            and m.get("role") in ("assistant", "ai")
            and str(m.get("content") or "").strip()
            and not m.get("tool_calls")
            for m in msgs[-6:]
        )
        if not next_nodes and has_assistant and approved:
            try:
                from kazma_core.config_store import get_config_store
                import time as _t

                cs = get_config_store()
                notice_key = f"hitl.last_stale_notice.{thread_id}"
                prev = cs.get(notice_key)
                if isinstance(prev, dict) and (_t.time() - float(prev.get("at") or 0)) < 90:
                    return None
                cs.set(notice_key, {"at": _t.time()}, category="safety")
            except Exception:
                pass
            return (
                "✅ Already handled — this approval was applied earlier and the "
                "turn finished. You can ignore this button (or send a new request)."
            )
        if not next_nodes and not approved:
            return (
                "ℹ️ No pending approval on this chat (already resolved or expired)."
            )
    except Exception:
        logger.debug("[HITL] stale-message state probe failed", exc_info=True)

    return (
        "⚠️ This approval card is no longer active "
        "(already resolved, or a newer turn replaced it).\n"
        "If you still need the action, send your request again."
    )


async def _check_graph_interrupt(graph: Any, config: dict[str, Any]) -> dict[str, Any] | None:
    """Return the hitl_approval interrupt payload if the graph is paused, else None.

    After ``graph.ainvoke()`` returns, an interrupt() in tool_worker_node
    leaves the graph paused at a checkpoint. This inspects the snapshot
    for a pending ``hitl_approval`` task and returns its payload.
    """
    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        logger.debug("[HITL] aget_state unavailable: %s", exc)
        return None
    if not getattr(snapshot, "next", None):
        return None  # graph completed normally
    for task in getattr(snapshot, "tasks", []) or []:
        for intr in getattr(task, "interrupts", []) or []:
            payload = getattr(intr, "value", None)
            if payload is None and isinstance(intr, dict):
                payload = intr.get("value", intr)
            if isinstance(payload, (list, tuple)) and payload:
                payload = payload[0]
            if isinstance(payload, dict) and payload.get("type") == "hitl_approval":
                return payload
            # Fallback: tool/args shape without type tag
            if isinstance(payload, dict) and (
                "tool" in payload or "args" in payload or "tools" in payload
            ):
                return {
                    "type": "hitl_approval",
                    "tool": payload.get("tool", "unknown"),
                    "args": payload.get("args", payload.get("arguments", {})),
                    "tools": payload.get("tools") or [],
                    "message": payload.get("message", ""),
                }
    return None


#: Approval cards sent per thread, newest last:
#: (sent_at, fingerprint, counts_toward_burst).
_recent_cards: dict[str, list[tuple[float, str, bool]]] = {}

#: An identical request inside this window is a duplicate, not a new decision.
_DUPLICATE_WINDOW_S = 180.0

#: More than this many *retry-loop* cards in _BURST_WINDOW_S is a storm, not
#: a conversation. On 2026-08-30 nine shell_exec variants arrived in three
#: minutes after auto-deny. Distinct X posts / proposal_id cards are
#: separate human decisions and must not share that bucket.
_BURST_LIMIT = 3
_BURST_WINDOW_S = 240.0

#: Official X writes + any proposal-backed outbound. Each distinct text /
#: proposal_id is its own card; the 180s identical-fingerprint check still
#: covers true retry loops.
_BURST_EXEMPT_TOOLS = frozenset({
    "x_post",
    "x_delete_post",
    "x_schedule_post",
    "x_cancel_scheduled_post",
    "book_x_post",
})


def _counts_toward_burst(tool: str, args: Any) -> bool:
    """True for exec-style retry loops; False for distinct operator decisions."""
    if str(tool or "") in _BURST_EXEMPT_TOOLS:
        return False
    if isinstance(args, dict) and str(args.get("proposal_id") or "").strip():
        return False
    return True


def approval_card_suppressed(thread_id: str, tool: str, args: Any) -> str | None:
    """Should this card be withheld? Returns why, or None to send it.

    A short ``approval_timeout_seconds`` with ``auto_deny_on_timeout`` means an
    operator who steps away gets each request auto-denied -- and a model reads
    that denial as "that approach failed", so it tries another one, which
    raises another card. On 2026-08-30 that produced nine approval prompts in
    three minutes for the same underlying intent, at exactly the 60s cadence.

    The reason text in ``hitl_timeout`` now tells the model to stop, but a
    prompt is a request, not a guarantee. This is the mechanical half: after a
    burst of *retry-loop* cards (exec variants), further exec cards for that
    thread are withheld until the operator says something. Distinct X posts
    and proposal-backed cards always notify (identical fingerprints still
    collapse inside ``_DUPLICATE_WINDOW_S``). Withheld, never auto-approved
    -- the tool still does not run.
    """
    import hashlib
    import time

    now = time.time()
    try:
        fingerprint = hashlib.sha256(
            f"{tool}:{args!r}".encode("utf-8", "replace")
        ).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        fingerprint = str(tool)

    counts = _counts_toward_burst(tool, args)
    history: list[tuple[float, str, bool]] = []
    for row in _recent_cards.get(thread_id, []):
        ts = float(row[0])
        fp = str(row[1])
        c = True if len(row) < 3 else bool(row[2])
        if now - ts < _BURST_WINDOW_S:
            history.append((ts, fp, c))

    for ts, fp, _c in history:
        if fp == fingerprint and now - ts < _DUPLICATE_WINDOW_S:
            _recent_cards[thread_id] = history
            return (
                f"identical {tool} approval already sent "
                f"{int(now - ts)}s ago — not repeating it"
            )

    if counts:
        storm = sum(1 for _ts, _fp, c in history if c)
        if storm >= _BURST_LIMIT:
            _recent_cards[thread_id] = history
            return (
                f"{storm} approval requests already sent for this thread in "
                f"the last {int(_BURST_WINDOW_S / 60)} minutes — muting further "
                "cards until you reply. Nothing has been approved or run."
            )

    history.append((now, fingerprint, counts))
    _recent_cards[thread_id] = history
    return None


def clear_approval_throttle(thread_id: str) -> None:
    """Forget a thread's card history — the operator engaged, so the mute lifts."""
    _recent_cards.pop(thread_id, None)


#: Tools where a hidden suffix is the difference between a copy and a wipe.
#: ``cp a b && rm -rf c`` truncated after the ``cp`` reads as harmless.
EXEC_TOOLS = frozenset({
    "shell_exec", "python_exec", "code_exec", "computer_use", "browser_eval_js",
})

#: Telegram caps a message at 4096 characters. Leave room for the header, the
#: reply instructions and the tool name.
_ARGS_BUDGET = 3200
_MULTI_BUDGET = 600


def _format_args_for_approval(
    tool: str, args: Any, *, budget: int = _ARGS_BUDGET
) -> str:
    """Render args for a card that a human is about to authorise.

    This used to be ``str(args)[:300] + "…"``. On 2026-08-30 that produced a
    card asking approval for::

        shell_exec: 'cd ... && cp "a.jpg" "b.jpg" && cp "c.jpg" "d.jpg" && cp "phot…

    -- a chained shell command whose tail was invisible. The dangerous half of
    a command is usually at the end, so an approval prompt that elides it is
    asking for consent to something unread.

    Nothing is hidden silently now. Commands are shown whole when they fit,
    and when they genuinely cannot fit the card SAYS SO, in the imperative,
    rather than trailing off in an ellipsis that reads like formatting.
    """
    import json

    try:
        text = json.dumps(args, ensure_ascii=False, indent=2, default=str)
    except Exception:  # noqa: BLE001
        text = str(args)

    if len(text) <= budget:
        return text

    hidden = len(text) - budget
    warning = (
        f"\n\n⚠️ {hidden} MORE CHARACTERS ARE NOT SHOWN."
    )
    if tool in EXEC_TOOLS:
        warning += (
            "\nDo NOT approve this from chat — you would be authorising a "
            "command you cannot read. Open the web UI, which shows all of it."
        )
    else:
        warning += "\nOpen the web UI to see the rest before approving."
    return text[:budget] + warning


def _build_approval_prompt(
    payload: dict[str, Any],
    thread_id: str,
    *,
    platform: str = "telegram",
) -> dict[str, Any]:
    """Build the approval prompt text + platform-specific interactive controls.

    All platforms share the ``hitl:approve|deny:<id>`` action vocabulary
    (Telegram keyboards, Discord components, Slack Block Kit).
    """
    tool = payload.get("tool", "unknown")
    args = payload.get("args", {})
    # Redact known-sensitive keys before stringifying into the chat-visible
    # approval card — args can carry credentials (http_request Authorization
    # header, MCP env API keys, git token URLs) (audit finding).
    _SENSITIVE = {
        "authorization", "token", "api_key", "apikey", "password", "secret",
        "header", "headers", "env", "cookie", "private_key",
    }

    def _redact(obj):
        if isinstance(obj, dict):
            return {
                k: ("***" if k.lower() in _SENSITIVE else _redact(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_redact(x) for x in obj]
        return obj

    args_str = _format_args_for_approval(tool, _redact(args))
    tools = payload.get("tools") or []
    # S1-3: proposal-backed posts — show the STORED drafts this approval
    # publishes, resolved server-side from the durable artifact store. The
    # user approves the stored text, not the model's memory.
    proposal_lines: list[str] = []
    try:
        _prop_sources: list[dict[str, Any]] = []
        if isinstance(payload.get("proposal"), dict):
            _prop_sources.append(payload["proposal"])
        if isinstance(tools, list):
            for _t in tools:
                if isinstance(_t, dict) and isinstance(_t.get("proposal"), dict):
                    _prop_sources.append(_t["proposal"])
        for _p in _prop_sources:
            _items = _p.get("items") or []
            if not isinstance(_items, list) or not _items:
                continue
            proposal_lines.append(
                f"📋 Content to publish — stored proposal "
                f"{_p.get('proposal_id', '')} (verified against your approval):"
            )
            for _item in _items[:8]:
                if isinstance(_item, dict):
                    proposal_lines.append(
                        f"  • {_item.get('id', '')}: "
                        f"{str(_item.get('text') or '')[:240]}"
                    )
        if proposal_lines:
            proposal_lines.append("")
    except Exception:
        proposal_lines = []
    if isinstance(tools, list) and len(tools) > 1:
        lines = [
            "⚠️ Approval required",
            f"{len(tools)} actions in this turn:",
            "",
        ]
        for i, item in enumerate(tools, 1):
            if not isinstance(item, dict):
                continue
            tname = str(item.get("name") or item.get("tool") or "tool")
            targs = _redact(item.get("args") or item.get("arguments") or {})
            tstr = _format_args_for_approval(tname, targs, budget=_MULTI_BUDGET)
            lines.append(f"{i}. {tname}: {tstr}")
        lines.extend(proposal_lines)
        lines.extend(
            [
                "",
                f"Reply: hitl approve {thread_id}",
                f"   or: hitl deny {thread_id}",
            ]
        )
        text_multi = "\n".join(lines)
        markup_multi = None
        plat_multi = (platform or "telegram").lower()
        try:
            if plat_multi == "telegram":
                from kazma_gateway.adapters.telegram import TelegramAdapter

                markup_multi = TelegramAdapter.build_approval_keyboard(thread_id)
            elif plat_multi == "discord":
                from kazma_gateway.adapters.discord import DiscordAdapter

                markup_multi = DiscordAdapter.build_approval_keyboard(thread_id)
            elif plat_multi == "slack":
                from kazma_gateway.adapters.slack import SlackAdapter

                markup_multi = SlackAdapter.build_approval_keyboard(thread_id)
        except Exception as exc:
            logger.debug(
                "Approval keyboard build failed for platform=%s: %s",
                plat_multi,
                exc,
                exc_info=True,
            )
        return {"text": text_multi, "markup": markup_multi, "platform": plat_multi}
    # Phase 3: semantic clarify/confirm → render question + per-option keyboard
    kind = payload.get("kind", "security")
    if kind in ("semantic_clarify", "semantic_confirm"):
        items = payload.get("items") or []
        question = ((items[0].get("question") if items else "")
                    or payload.get("message", "")) or "Needs clarification"
        options = (items[0].get("options") if items else []) or []
        text = f"❓ {question}\n\nChoose an option below."
        markup = None
        plat = (platform or "telegram").lower()
        if plat == "telegram":
            try:
                from kazma_gateway.adapters.telegram_keyboards import build_semantic_keyboard
                markup = build_semantic_keyboard(thread_id, options)
            except Exception:
                pass
        elif plat == "discord":
            try:
                from kazma_gateway.adapters.platform_keyboards import discord_semantic_components
                markup = discord_semantic_components(thread_id, options)
            except Exception:
                pass
        elif plat == "slack":
            try:
                from kazma_gateway.adapters.platform_keyboards import slack_semantic_blocks
                markup = slack_semantic_blocks(thread_id, question, options)
            except Exception:
                pass
        return {"text": text, "markup": markup, "platform": plat}
    text = (
        f"⚠️ Approval required\n"
        f"Tool: {tool}\n"
        f"Args: {args_str}\n\n"
        + ("\n".join(proposal_lines) + "\n" if proposal_lines else "")
        + f"Reply: hitl approve {thread_id}\n"
        f"   or: hitl deny {thread_id}"
    )
    markup = None
    plat = (platform or "telegram").lower()
    try:
        if plat == "telegram":
            from kazma_gateway.adapters.telegram import TelegramAdapter

            markup = TelegramAdapter.build_approval_keyboard(thread_id)
        elif plat == "discord":
            from kazma_gateway.adapters.discord import DiscordAdapter

            markup = DiscordAdapter.build_approval_keyboard(thread_id)
        elif plat == "slack":
            from kazma_gateway.adapters.slack import SlackAdapter

            markup = SlackAdapter.build_approval_keyboard(thread_id)
    except Exception as exc:
        logger.debug(
            "Approval keyboard build failed for platform=%s: %s",
            plat,
            exc,
            exc_info=True,
        )
    return {"text": text, "markup": markup, "platform": plat}


def apply_hitl_approval_markup(
    ctx: dict[str, Any],
    *,
    platform: str,
    hitl_approval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach platform-native HITL buttons to a ``send_message`` context.

    ``send_approval_request`` used to emit ASCII ``[ APPROVE ]`` / ``[ DENY ]``
    because the text backend has no keyboard. The tool now passes
    ``hitl_approval={"request_id": ...}`` and this helper copies the same
    Approve / Deny / Approve-for-task markup used by graph interrupts.
    """
    out = dict(ctx or {})
    if not isinstance(hitl_approval, dict):
        return out
    request_id = str(hitl_approval.get("request_id") or "").strip()
    if not request_id:
        return out
    prompt = _build_approval_prompt(
        {
            "type": "hitl_approval",
            "tool": str(hitl_approval.get("tool") or "approval"),
            "args": hitl_approval.get("args") if isinstance(hitl_approval.get("args"), dict) else {},
            "message": str(hitl_approval.get("title") or hitl_approval.get("message") or ""),
        },
        request_id,
        platform=platform,
    )
    markup = prompt.get("markup")
    if not markup:
        return out
    plat = (platform or "telegram").lower()
    if plat == "telegram":
        out["reply_markup"] = markup
    elif plat == "discord":
        out["components"] = markup
    elif plat == "slack":
        out["blocks"] = markup
    return out


async def _handle_hitl_resume(
    msg: IncomingMessage,
    graph: Any,
    config: dict[str, Any],
    thread_id: str,
    store: SessionStore,
    manager: Any,
    lock_getter: Any = None,
) -> bool:
    """Process a ``hitl approve|deny <thread_id>`` message.

    The leading ``/`` is optional — platforms that block slash-commands
    (Slack) use the bare ``hitl`` prefix.

    Resumes the paused graph with ``Command(resume=...)`` and sends the
    resulting assistant reply back to the platform.

    ``lock_getter`` is the handler's per-thread lock factory. The resume
    MUST serialize against regular turns on the same thread: a concurrent
    ``graph.ainvoke()`` would interleave checkpoint writes and corrupt the
    message history.

    Stale cards: on langgraph >= 1.x, a new user message on a paused thread
    silently discards the pending interrupt, and a later ``Command(resume=)``
    is a silent no-op that replays old state. We therefore verify a pending
    ``hitl_approval`` interrupt actually exists before resuming, and tell
    the user when their card has expired instead of pretending to approve.

    Returns True if the message was handled (always, for hitl commands).
    """
    parts = msg.text.strip().split()
    # Expected: [hitl|/hitl] <action> <thread_id>
    if len(parts) < 2:
        return False

    cmd = parts[0].lower().lstrip("/")
    if cmd != "hitl":
        return False

    action = parts[1].lower()
    approved = action in ("approve", "yes", "y", "allow", "approve_task")
    is_task_grant = action == "approve_task"
    # Phase 3: semantic option — /hitl opt <thread> <option_id>
    semantic_option = None
    if action == "opt" and len(parts) >= 4:
        semantic_option = parts[3]
        target_thread = parts[2]
    else:
        # The target thread_id defaults to the current sender's thread but can
        # be overridden by the third argument (for cross-thread approvals).
        target_thread = parts[2] if len(parts) >= 3 else thread_id
    resume_config = {"configurable": {"thread_id": target_thread, "checkpoint_ns": ""}}

    # Delivery MUST come from the inbound message. SessionStore TTL is 5 min
    # (AGENTS.md §16 / sessions.ttl) — a paused HITL card outlives that row.
    ctx = dict(msg.context_metadata or {})
    stored = await store.get(thread_id)
    if stored:
        for _k, _v in stored.items():
            ctx.setdefault(_k, _v)

    # Authorization: verify that the requester is the same user who
    # initiated the paused task. Look up the target thread's context
    # and compare sender_id. This prevents any user from approving
    # another user's paused danger-tool execution.
    # Fail-closed: if the target session is missing, deny cross-thread
    # approvals rather than skipping the check.
    if target_thread != thread_id:
        from kazma_core.sessions.ttl import refuse_session_lookup_for_durable_job

        target_ctx = await store.get(target_thread)
        if not target_ctx:
            refuse_session_lookup_for_durable_job(
                job_kind="hitl_cross_thread",
                thread_id=str(target_thread),
            )
            logger.warning(
                "[HITL] Authz denied: cross-thread approve for missing session %s by %s",
                target_thread, msg.sender_id,
            )
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=(
                        "⚠️ Cannot approve: target session expired "
                        "(SessionStore TTL is 5 minutes). Approve from the "
                        "original chat, or send a new request."
                    ),
                    context_metadata=ctx,
                )
            )
            return True
        original_sender = (target_ctx.get("sender_id") or "").strip()
        current_sender = (msg.sender_id or "").strip()
        # Fail-closed (audit M6): empty owner or mismatch → deny
        if not original_sender or not current_sender or original_sender != current_sender:
            logger.warning(
                "[HITL] Authz denied: %s tried to approve thread %s owned by %s",
                current_sender or "(empty)",
                target_thread,
                original_sender or "(empty)",
            )
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text="⚠️ You are not authorized to approve this task.",
                    context_metadata=ctx,
                )
            )
            return True

    try:
        import contextlib

        from langgraph.types import Command

        lock = await lock_getter(target_thread) if lock_getter is not None else None
        async with (lock if lock is not None else contextlib.AsyncExitStack()):
            # ── Stale-card guard ────────────────────────────────────
            # Verify the graph is actually paused on a hitl_approval
            # interrupt. If not (a newer message superseded the card, or
            # it was already resolved), Command(resume=...) would be a
            # silent no-op replaying old state — the "approve did nothing"
            # bug. Fail loudly instead.
            pending = await _check_graph_interrupt(graph, resume_config)
            if pending is None:
                # Stale/duplicate callback after a successful resume is common
                # (double-tap, Telegram retries). Prefer a calm message — and
                # suppress spam within a short debounce window.
                soft = await _stale_approval_message(
                    graph, resume_config, target_thread, action=action, approved=approved
                )
                logger.info(
                    "[HITL] Stale approval ignored: thread=%s action=%s "
                    "(no pending interrupt) soft=%s",
                    target_thread,
                    action,
                    soft is not None,
                )
                if soft:
                    await manager.send(
                        OutboundMessage(
                            target_id=_build_target_id(msg.platform, ctx),
                            text=soft,
                            context_metadata=ctx,
                        )
                    )
                return True

            logger.info(
                "[HITL] Resume: thread=%s approved=%s action=%s",
                target_thread, approved, action,
            )
            # Mark successful resume so a late second callback stays quiet.
            try:
                from kazma_core.config_store import get_config_store
                import time as _time

                get_config_store().set(
                    f"hitl.last_resume.{target_thread}",
                    {
                        "at": _time.time(),
                        "action": action,
                        "approved": approved,
                    },
                    category="safety",
                )
            except Exception:
                pass

            # ── Task grant: auto-approve all danger tools until next message ──
            # When the user clicks "Approve for task", grant ALL danger tools
            # for this thread so subsequent turns skip the approval card
            # entirely. The grant auto-clears on the next user message (not
            # a callback) or after the TTL (default 10 min).
            if is_task_grant and approved:
                try:
                    from kazma_core.safety.task_grants import grant_task

                    # IncomingMessage has no user_id attribute — the `or
                    # msg.user_id` would AttributeError if sender_id was ever
                    # empty (caught by the surrounding try, silently dropping
                    # the actor from the grant audit) (audit finding).
                    actor = msg.sender_id or "unknown"
                    grant_task(target_thread, actor=actor)
                    logger.info(
                        "[HITL] Task grant activated for thread=%s actor=%s",
                        target_thread, actor,
                    )
                except Exception as exc:
                    logger.warning("[HITL] Task grant failed: %s — continuing with single approve", exc)

            # Phase 3/§4.3: semantic interrupts need {tcid: option_id}; security
            # needs {approved: bool}. Routed through the single chokepoint
            # (build_resume_command) so transports cannot drift again.
            from kazma_core.safety.commitment.resume import build_resume_command

            _resume_cmd = build_resume_command(
                pending,
                approved=approved,
                semantic_option=semantic_option,
                scope="task" if is_task_grant else "once",
                reason=action,
            )
            if _resume_cmd is None:
                # Stale (pending cleared between the check above and here) —
                # treat as a security deny so the turn ends deterministically.
                _resume_cmd = build_resume_command(
                    {"type": "hitl_approval", "kind": "security"},
                    approved=approved, scope="task" if is_task_grant else "once", reason=action,
                )
            from kazma_ui.turn_runtime import invoke_turn as _invoke_hitl

            result_state = await _invoke_hitl(
                graph,
                _resume_cmd,
                resume_config,
                thread_id=target_thread,
            )

            # Re-surface a chained interrupt (a second danger tool paused in
            # the same resumed turn). Without this the user sees "Approved —
            # continuing." while the graph is actually still paused on
            # another tool, so the agent appears to "do nothing".
            chained = await _check_graph_interrupt(graph, resume_config)

        from .graph import _prepare_tg_outbound

        # Extract the assistant's response from the resumed turn.
        assistant_text = ""
        messages = result_state.get("messages", []) if isinstance(result_state, dict) else []
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content"):
                assistant_text = m["content"]
                break

        # If there is assistant text, send it first
        if assistant_text:
            tg_text, tg_ctx = _prepare_tg_outbound(msg, assistant_text, ctx)
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=tg_text,
                    context_metadata=tg_ctx,
                )
            )

        if chained is not None:
            prompt = _build_approval_prompt(
                chained, target_thread, platform=msg.platform
            )
            prompt_text, send_ctx = _prepare_tg_outbound(msg, prompt["text"], ctx)
            if prompt.get("markup"):
                if msg.platform == "telegram":
                    send_ctx["reply_markup"] = prompt["markup"]
                elif msg.platform == "discord":
                    send_ctx["components"] = prompt["markup"]
                elif msg.platform == "slack":
                    send_ctx["blocks"] = prompt["markup"]
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=prompt_text,
                    context_metadata=send_ctx,
                )
            )
            return True

        if not assistant_text:
            fallback_text = "✅ Approved — continuing." if approved else "🚫 Denied."
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=fallback_text,
                    context_metadata=ctx,
                )
            )
    except Exception:
        logger.exception("[HITL] Resume failed for thread=%s", target_thread)
        await manager.send(
            OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text="⚠️ HITL resume failed — no paused task found for that session.",
                context_metadata=ctx,
            )
        )

    return True
