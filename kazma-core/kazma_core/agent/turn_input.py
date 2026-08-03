"""Build agent turn input with checkpointer + session continuity.

Both the gateway and Web SSE paths previously diverged:

* Gateway restored full checkpoint history (including tool chains)
* SSE rebuilt from SessionManager text-only projection and overwrote
  the checkpointer (no ``add_messages`` reducer on SupervisorState)

That caused post-HITL "amnesia" on Web and any path that forgot restore.

After a **server restart**, the checkpointer can be thinner than the UI
session (or empty) while SessionStore still has the full chat — using only
the checkpoint then makes "Proceed" look like a blank conversation.

Use :func:`build_turn_messages` for every new user turn.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = [
    "build_turn_messages",
    "load_checkpoint_messages",
    "contentful_turn_count",
    "normalize_history_messages",
    "is_short_continuation",
]

logger = logging.getLogger(__name__)

# Short follow-ups that inherit the prior task (same-session continuity).
_CONTINUATION_PHRASES = frozenset(
    {
        "proceed",
        "continue",
        "try now",
        "try again",
        "go",
        "go ahead",
        "do it",
        "yes",
        "yep",
        "ok",
        "okay",
        "same",
        "again",
        "retry",
        "keep going",
        "finish",
        "finish it",
        "pick up",
        "resume",
    }
)


def is_short_continuation(text: str) -> bool:
    """True for short same-session continuations like 'Proceed' / 'try now'."""
    s = " ".join((text or "").strip().lower().split())
    if not s or len(s) > 48:
        return False
    if s in _CONTINUATION_PHRASES:
        return True
    # "proceed with cleanup", "continue please"
    head = s.split()[0] if s.split() else ""
    return head in ("proceed", "continue", "resume", "retry") and len(s) < 40


def contentful_turn_count(msgs: list[dict[str, Any]]) -> int:
    """Count user/assistant turns that carry real text (not empty tool shells)."""
    n = 0
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        if m.get("tool_calls"):
            continue
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            n += 1
        elif isinstance(content, list) and content:
            n += 1
    return n


def normalize_history_messages(raw: list[Any] | None) -> list[dict[str, Any]]:
    """Project UI/session rows into graph-safe message dicts."""
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            continue
        # Skip empty pending assistant placeholders
        content = m.get("content")
        if (
            role == "assistant"
            and not m.get("tool_calls")
            and not (isinstance(content, str) and content.strip())
            and m.get("pending")
        ):
            continue
        row = {
            k: v
            for k, v in m.items()
            if k in ("role", "content", "tool_calls", "tool_call_id", "name")
        }
        out.append(row)
    try:
        from kazma_core.agent.graph_builder import sanitize_tool_chains

        return sanitize_tool_chains(out)
    except Exception:
        return out


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
      1. Checkpoint history when it is the richest source (tool-aware)
      2. Session/UI fallback when checkpoint is empty OR thinner (restart gap)
      3. Optional system_messages prepended only when not already present
      4. The new user message always last

    SessionManager is the UI projection; after restart it often has more
    conversational turns than a thin/empty checkpoint — that continuity
    must win for "Proceed" / same-session work.
    """
    ckpt = await load_checkpoint_messages(graph, config)
    session_hist = normalize_history_messages(fallback_history)

    ckpt_n = contentful_turn_count(ckpt)
    sess_n = contentful_turn_count(session_hist)

    if not ckpt:
        prior = session_hist
        if prior:
            logger.info(
                "[turn_input] checkpoint empty — using session history (%d contentful turns)",
                sess_n,
            )
    elif sess_n > ckpt_n + 1:
        # Session is meaningfully richer (common after restart / failed turns
        # that never wrote full checkpoint state). Prefer session text history
        # so "Proceed" still sees the task the user already gave.
        prior = session_hist
        logger.warning(
            "[turn_input] session history richer than checkpoint "
            "(session=%d contentful, checkpoint=%d) — using session for continuity",
            sess_n,
            ckpt_n,
        )
    else:
        prior = ckpt

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
