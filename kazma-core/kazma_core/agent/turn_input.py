"""Build agent turn input with checkpointer as sole message source of truth.

Both the gateway and Web SSE paths previously diverged:

* Gateway restored full checkpoint history (including tool chains)
* SSE rebuilt from SessionManager text-only projection and overwrote
  the checkpointer (no ``add_messages`` reducer on SupervisorState)

That caused post-HITL "amnesia" on Web and any path that forgot restore.

Use :func:`build_turn_messages` for every new user turn.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["build_turn_messages", "load_checkpoint_messages"]

logger = logging.getLogger(__name__)


async def load_checkpoint_messages(
    graph: Any,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Load and sanitize messages from the LangGraph checkpointer."""
    if graph is None or getattr(graph, "checkpointer", None) is None:
        return []
    try:
        snap = await graph.aget_state(config)
        prior = list((snap.values or {}).get("messages") or []) if snap else []
    except Exception as exc:
        logger.debug("[turn_input] aget_state failed: %s", exc)
        return []

    if not prior:
        return []

    try:
        from kazma_core.agent.graph_builder import sanitize_tool_chains

        return sanitize_tool_chains(prior)
    except Exception:
        return prior


async def build_turn_messages(
    graph: Any,
    config: dict[str, Any],
    *,
    user_text: str,
    system_messages: list[dict[str, Any]] | None = None,
    fallback_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Assemble the full message list for one new user turn.

    Priority:
      1. Checkpoint history (tool-aware, HITL-complete)
      2. Optional fallback_history (UI projection) if checkpoint empty
      3. Optional system_messages prepended only when not already present
      4. The new user message always last

    SessionManager / UI stores remain a *projection* for display only —
    they must not replace checkpoint tool chains when a checkpoint exists.
    """
    prior = await load_checkpoint_messages(graph, config)

    if not prior and fallback_history:
        # First turn or no checkpointer: use text history for continuity.
        prior = [
            {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "tool_call_id", "name")}
            for m in fallback_history
            if isinstance(m, dict) and m.get("role") in ("system", "user", "assistant", "tool")
        ]
        # Strip incomplete tool tails from fallback
        try:
            from kazma_core.agent.graph_builder import sanitize_tool_chains

            prior = sanitize_tool_chains(prior)
        except Exception:
            pass

    out: list[dict[str, Any]] = list(prior)

    # Prepend system messages that are not already at the head (env refresh etc.)
    if system_messages:
        existing_sys = {
            (m.get("content") or "")[:80]
            for m in out
            if isinstance(m, dict) and m.get("role") == "system"
        }
        to_prepend: list[dict[str, Any]] = []
        for sm in system_messages:
            if not isinstance(sm, dict) or sm.get("role") != "system":
                continue
            key = (sm.get("content") or "")[:80]
            if key and key not in existing_sys:
                to_prepend.append(sm)
                existing_sys.add(key)
        if to_prepend:
            # Keep base system first if present in out
            if out and isinstance(out[0], dict) and out[0].get("role") == "system":
                out = [out[0], *to_prepend, *out[1:]]
            else:
                out = [*to_prepend, *out]

    # Avoid double-appending the same user text if last message is identical
    if out:
        last = out[-1]
        if (
            isinstance(last, dict)
            and last.get("role") == "user"
            and (last.get("content") or "").strip() == (user_text or "").strip()
        ):
            return out

    out.append({"role": "user", "content": user_text})
    return out
