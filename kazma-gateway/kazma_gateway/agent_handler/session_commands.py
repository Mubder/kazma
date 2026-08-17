"""Gateway /sessions /session /switch — pick a season on any mouth."""

from __future__ import annotations

import logging
from typing import Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage

from .store import _build_target_id

logger = logging.getLogger(__name__)

__all__ = ["try_session_command"]


def _parse(text: str) -> tuple[str, str] | None:
    """Return (verb, rest) or None if this is not a session command."""
    raw = (text or "").strip()
    if not raw:
        return None
    parts = raw.split(None, 1)
    cmd = parts[0].lower().lstrip("/")
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]
    rest = parts[1].strip() if len(parts) > 1 else ""
    if cmd in {"sessions", "seasons"}:
        return ("list", rest)
    if cmd == "new":
        return ("new", rest)
    if cmd in {"session", "season", "switch"}:
        if not rest or rest.lower() in {"list", "ls"}:
            return ("list", "")
        if rest.lower() in {"all", "archived"}:
            return ("list_all", "")
        low = rest.split(None, 1)
        if low[0].lower() in {"new", "start"}:
            return ("new", low[1].strip() if len(low) > 1 else "")
        if low[0].lower() in {"here", "current", "this"}:
            return ("here", "")
        return ("switch", rest)
    return None


async def try_session_command(
    msg: IncomingMessage,
    *,
    thread_id: str,
    sender: str,
    store: Any,
    manager: Any,
    sessions_map: dict[str, str],
    prepare_outbound: Any,
) -> bool:
    """Handle /sessions /session /switch. True = consumed (skip graph)."""
    parsed = _parse(msg.text or "")
    if parsed is None:
        return False
    verb, rest = parsed

    async def _reply(text: str) -> None:
        ctx = msg.context_metadata
        out_text, out_ctx = prepare_outbound(msg, text, ctx)
        await manager.send(
            OutboundMessage(
                target_id=_build_target_id(msg.platform, ctx),
                text=out_text,
                context_metadata=out_ctx,
            )
        )

    try:
        from kazma_core.sessions.directory import (
            bind_sender_to_thread,
            create_named_session,
            format_session_list,
            list_directory,
            resolve_session,
        )
    except Exception:
        logger.exception("[session-cmd] directory import failed")
        await _reply("⚠️ Session directory unavailable.")
        return True

    if verb in {"list", "list_all"}:
        entries = list_directory(include_archived=(verb == "list_all"), limit=40)
        await _reply(format_session_list(entries, current_thread_id=thread_id))
        return True

    if verb == "here":
        hit = resolve_session(thread_id, include_archived=True)
        title = hit.title if hit else thread_id
        short = hit.short_id if hit else thread_id[-8:]
        await _reply(
            f"This mouth is on **{title}**\n"
            f"id `{short}` · `/chat?s={hit.session_id if hit else thread_id}` on Web\n"
            f"`/sessions` lists every season you can take over."
        )
        return True

    if verb == "new":
        entry = create_named_session(
            platform=msg.platform,
            sender_id=sender,
            title=rest,
        )
        await bind_sender_to_thread(
            sender,
            entry.thread_id,
            platform=msg.platform,
            delivery_ctx=dict(msg.context_metadata or {}),
            session_store=store,
        )
        sessions_map[sender] = entry.thread_id
        try:
            from kazma_core.memory.consolidator import clear_working_memory

            clear_working_memory(thread_id)
        except Exception:
            logger.debug("[session-cmd] clear_working_memory on new failed", exc_info=True)
        name = entry.title
        await _reply(
            f"🆕 New season: **{name}**\n"
            f"This chat continues it. Same list on Web (`/chat?s={entry.session_id}`).\n"
            f"`/sessions` to pick another later."
        )
        logger.info(
            "[session-cmd] /session new thread=%s platform=%s",
            entry.thread_id,
            msg.platform,
        )
        return True

    # switch
    hit = resolve_session(rest, current_thread_id=thread_id, include_archived=False)
    if hit is None:
        hit = resolve_session(rest, current_thread_id=thread_id, include_archived=True)
    if hit is None:
        await _reply(
            f"No season matches `{rest}`.\n"
            f"`/sessions` to see the numbered list."
        )
        return True

    await bind_sender_to_thread(
        sender,
        hit.thread_id,
        platform=msg.platform,
        delivery_ctx=dict(msg.context_metadata or {}),
        session_store=store,
    )
    sessions_map[sender] = hit.thread_id
    await _reply(
        f"▶️ Taken over: **{hit.title}**\n"
        f"This {msg.platform.capitalize()} chat now continues that season "
        f"({hit.message_count} msgs).\n"
        f"Web: `/chat?s={hit.session_id}`"
    )
    logger.info(
        "[session-cmd] take-over sender=%s → thread=%s via %s",
        sender,
        hit.thread_id,
        msg.platform,
    )
    return True
