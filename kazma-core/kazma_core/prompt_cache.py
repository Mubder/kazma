"""Stable system-prefix packing for provider prompt caches.

OpenAI/Gemini automatically cache the longest *identical* prefix. Anthropic
caches explicit ``cache_control`` breakpoints. Kazma used to hoist every
mid-turn system note (working memory, recall, budget, language lock) as its
own message at the head, which reshuffled the prefix every iteration and
made those caches miss.

This module packs messages into:

    [0] STABLE system  — identity + personality + env (cacheable)
    [1] DYNAMIC system — working memory, recall, locks, nudges (one blob)
    [2+] conversation  — user / assistant / tool (order preserved)

``hoist_system_messages`` calls :func:`pack_system_messages` so local
templates (LM Studio / llama.cpp) still see system at the head.

Kill-switch: ``KAZMA_PROMPT_CACHE=0`` restores legacy hoist (no merge,
no Anthropic cache_control).
"""

from __future__ import annotations

import logging
import os
from typing import Any

__all__ = [
    "build_anthropic_system",
    "pack_system_messages",
    "prompt_cache_enabled",
    "stamp_anthropic_tool_cache",
]

logger = logging.getLogger(__name__)

# Markers that mean "this system note changes per turn / per iteration".
_DYNAMIC_MARKERS: tuple[str, ...] = (
    "[KAZMA_WORKING_MEMORY]",
    "LANGUAGE LOCK",
    "UI WORKBENCH",
    "SYSTEM BUDGET CHECK",
    "[CONTEXT SUMMARY]",
    "<kazma:data",
    "INTENT ENGINE",
    "Please proceed automatically",
    "SYSTEM: Finalization required",
    "MISCELLANEOUS WORKING MEMORY",
)


def prompt_cache_enabled() -> bool:
    raw = (os.environ.get("KAZMA_PROMPT_CACHE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _text_of(msg: dict[str, Any]) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for p in c:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                parts.append(str(p.get("text") or p.get("content") or ""))
        return "\n".join(parts)
    return "" if c is None else str(c)


def is_dynamic_system(msg: dict[str, Any]) -> bool:
    """True when *msg* is a per-turn system note (must not enter the cache prefix)."""
    if not isinstance(msg, dict):
        return False
    if msg.get("role") not in ("system", "developer"):
        return False
    blob = _text_of(msg)
    return any(m in blob for m in _DYNAMIC_MARKERS)


def pack_system_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hoist + merge system notes into a stable prefix + one dynamic blob.

    No-op on non-dicts. Conversation roles keep their relative order (so
    assistant/tool adjacency stays intact).
    """
    if not messages:
        return messages

    first_user = next(
        (
            i
            for i, m in enumerate(messages)
            if isinstance(m, dict) and m.get("role") == "user"
        ),
        len(messages),
    )
    stable: list[str] = []
    dynamic: list[str] = []
    rest: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            rest.append(m)
            continue
        if m.get("role") in ("system", "developer"):
            text = _text_of(m).strip()
            if not text:
                continue
            # After the first user turn, any system note is mid-stream
            # (INTENT / budget / language lock) even without a marker.
            if is_dynamic_system(m) or i > first_user:
                dynamic.append(text)
            else:
                stable.append(text)
        else:
            rest.append(m)

    out: list[dict[str, Any]] = []
    if stable:
        out.append({"role": "system", "content": "\n\n".join(stable)})
    if dynamic:
        out.append({"role": "system", "content": "\n\n".join(dynamic)})
    out.extend(rest)
    return out


def build_anthropic_system(messages: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Anthropic ``system`` payload with a cache breakpoint on the stable prefix.

    Returns ``""`` when there is no system text (Anthropic accepts omitted
    system). Returns a string when caching is off. Returns a list of text
    blocks with ``cache_control`` on the first block when caching is on.
    """
    packed = pack_system_messages(messages)
    systems = [
        m for m in packed
        if isinstance(m, dict) and m.get("role") in ("system", "developer")
    ]
    texts = [_text_of(m).strip() for m in systems]
    texts = [t for t in texts if t]
    if not texts:
        return ""
    if not prompt_cache_enabled():
        return "\n\n".join(texts)
    blocks: list[dict[str, Any]] = []
    for i, text in enumerate(texts):
        block: dict[str, Any] = {"type": "text", "text": text}
        if i == 0:
            block["cache_control"] = {"type": "ephemeral"}
        blocks.append(block)
    return blocks


def stamp_anthropic_tool_cache(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the last tool schema as a cache breakpoint (Anthropic docs)."""
    if not tools or not prompt_cache_enabled():
        return tools
    out = [dict(t) for t in tools]
    last = dict(out[-1])
    last["cache_control"] = {"type": "ephemeral"}
    out[-1] = last
    return out
