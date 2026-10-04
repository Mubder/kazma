"""Shared thread authority for verified mention authors and authenticated operators."""

from __future__ import annotations

import asyncio
from typing import Any

from kazma_core.x_api.stance import ReplyConfig


async def thread_authority(
    cfg: ReplyConfig, store: Any, *, conversation_id: str,
    parent_text: str, parent_handle: str, summon_text: str, summoner: str,
    operator: bool = False, parent_authorized: bool = False,
) -> tuple[bool, bool, bool]:
    """Return open, closed, trusted-parent; never accept stranger-authored markers."""
    trusted_parent = parent_authorized or cfg.is_trusted_summoner(parent_handle)
    if conversation_id:
        if trusted_parent:
            state = "closed" if cfg.marker_in(cfg.close_thread_marker, parent_text) else (
                "open" if cfg.marker_in(cfg.open_thread_marker, parent_text) else ""
            )
            if state:
                await asyncio.to_thread(store.set_thread_permission, conversation_id, state=state,
                                        actor=parent_handle)
        if operator or cfg.is_trusted_summoner(summoner):
            state = "closed" if cfg.marker_in(cfg.close_thread_marker, summon_text) else (
                "open" if cfg.marker_in(cfg.open_thread_marker, summon_text) else ""
            )
            if state:
                await asyncio.to_thread(store.set_thread_permission, conversation_id, state=state,
                                        actor=summoner, reopen=True)
        permission = await asyncio.to_thread(store.thread_permission, conversation_id)
    else:
        permission = ""
    return permission == "open", permission == "closed", trusted_parent
