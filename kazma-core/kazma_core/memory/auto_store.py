"""Automatic long-term memory writes for chat turns.

Without this, durable facts only land in the vector store when:
  * the LLM chooses to call ``memory_store``, or
  * context compaction auto-stores a summary.

That left short sessions with empty recall. This module closes the gap by
heuristically capturing **durable** user facts every turn, and optionally
storing a compact user+assistant turn snapshot.

Designed to run fire-and-forget from ``respond_node`` so it never blocks
the user-facing reply path.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

__all__ = ["auto_store_enabled", "auto_store_from_messages", "auto_store_mode", "extract_turn_texts", "looks_durable", "schedule_auto_store", "store_text"]

logger = logging.getLogger(__name__)

# Explicit / strong durable-fact signals (any language-ish patterns we care about).
_DURABLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE | re.UNICODE)
    for p in (
        r"\bremember (that |this |to )?",
        r"\bdon'?t forget\b",
        r"\bmy name is\b",
        r"\bi(?:'m| am) (?:called|named)\b",
        r"\bi (?:live|work|prefer|like|hate|love|use|need|want)\b",
        r"\bmy (?:favorite|favourite|email|phone|timezone|language|role|job|company|team|project)\b",
        r"\bplease note\b",
        r"\bfor (?:future|later) reference\b",
        r"\balways (?:use|reply|respond|write)\b",
        r"\bi speak\b",
        r"\bcall me\b",
        # Arabic durable cues
        r"تذك[رّ]",
        r"اسمي\s",
        r"أنا\s+أ(?:حب|فضل|سكن|عمل)",
        r"لا تنسى",
    )
)

# Skip pure noise / control messages.
_SKIP_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^[\s/!]+",  # slash commands etc. handled separately
        r"^(hi|hello|hey|thanks|thank you|ok|okay|yes|no|yep|nope)\.?$",
        r"^ping$",
        r"^test$",
    )
)

_MIN_DURABLE_LEN = 12
_MAX_STORE_CHARS = 800
_MAX_TURN_CHARS = 600


def _read_memory_cfg() -> dict[str, Any]:
    try:
        from pathlib import Path

        import yaml

        path = Path("kazma.yaml")
        if path.exists():
            with open(path, encoding="utf-8") as f:
                full = yaml.safe_load(f) or {}
            return dict((full.get("memory") or {}))
    except Exception:
        logger.debug("[auto_store] config read failed", exc_info=True)
    return {}


def auto_store_enabled(cfg: dict[str, Any] | None = None) -> bool:
    """Return True when automatic memory writes are on (default True)."""
    c = cfg if cfg is not None else _read_memory_cfg()
    if not bool(c.get("enabled", True)):
        return False
    return bool(c.get("auto_store", True))


def auto_store_mode(cfg: dict[str, Any] | None = None) -> str:
    """``durable`` | ``turns`` | ``both`` (default both)."""
    c = cfg if cfg is not None else _read_memory_cfg()
    mode = str(c.get("auto_store_mode", "both") or "both").strip().lower()
    if mode not in ("durable", "turns", "both"):
        return "both"
    return mode


def looks_durable(text: str) -> bool:
    """Heuristic: does this user utterance look like a fact worth keeping?"""
    t = (text or "").strip()
    if len(t) < _MIN_DURABLE_LEN:
        return False
    if t.startswith("/"):
        return False
    for pat in _SKIP_PATTERNS:
        if pat.search(t):
            return False
    for pat in _DURABLE_PATTERNS:
        if pat.search(t):
            return True
    # Soft signal: first-person + preference-ish verbs without explicit pattern.
    lower = t.lower()
    if re.search(r"\bi (?:prefer|usually|always|never)\b", lower):
        return True
    return False


def _clip(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"


def extract_turn_texts(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (last_user, last_assistant) content from the message list."""
    user = ""
    assistant = ""
    for m in reversed(messages or []):
        role = m.get("role")
        content = m.get("content")
        if not isinstance(content, str):
            # Multimodal: take first text part if present.
            if isinstance(content, list):
                parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") in (None, "text")
                ]
                content = " ".join(p for p in parts if p)
            else:
                content = ""
        content = str(content or "").strip()
        if not content:
            continue
        if role == "assistant" and not assistant:
            assistant = content
        elif role == "user" and not user:
            user = content
        if user and assistant:
            break
    return user, assistant


async def _get_store() -> Any | None:
    """Resolve the same store used by tools / per-turn RAG."""
    try:
        from kazma_core.swarm.memory.adapter import get_adapter

        adapter = get_adapter()
        if adapter is not None:
            return adapter
    except Exception:
        logger.debug("[auto_store] adapter unavailable", exc_info=True)
    try:
        from kazma_core.agent.tool_registry import get_vector_memory
        from kazma_core.memory.async_adapter import wrap_vector_memory

        vm = get_vector_memory()
        if vm is not None:
            return wrap_vector_memory(vm)
    except Exception:
        logger.debug("[auto_store] vector memory unavailable", exc_info=True)
    return None


async def store_text(text: str, metadata: dict[str, Any] | None = None) -> str:
    """Store one memory fragment. Returns doc id or empty string."""
    body = _clip(text, _MAX_STORE_CHARS)
    if not body:
        return ""
    store = await _get_store()
    if store is None:
        return ""
    meta = {"source": "auto_store", "ts": time.time(), **(metadata or {})}
    try:
        result = store.store(body, metadata=meta)
        if hasattr(result, "__await__"):
            return str(await result or "")
        return str(result or "")
    except Exception:
        logger.warning("[auto_store] store failed", exc_info=True)
        return ""


async def auto_store_from_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Inspect the finished turn and write durable / turn memories.

    Returns a small stats dict for logging/tests.
    """
    cfg = _read_memory_cfg()
    stats: dict[str, Any] = {"enabled": False, "durable": 0, "turn": 0, "ids": []}
    if not auto_store_enabled(cfg):
        return stats
    stats["enabled"] = True
    mode = auto_store_mode(cfg)
    user, assistant = extract_turn_texts(messages)
    if not user:
        return stats

    if mode in ("durable", "both") and looks_durable(user):
        doc = await store_text(
            user,
            metadata={"type": "durable_fact", "kind": "user_statement"},
        )
        if doc:
            stats["durable"] = 1
            stats["ids"].append(doc)

    if mode in ("turns", "both") and assistant and len(user) >= _MIN_DURABLE_LEN:
        # Compact turn snapshot so every real exchange is searchable later.
        # Skip pure durable-only double-write when we already stored the user
        # fact and the assistant reply is a short ack.
        turn_blob = (
            f"User: {_clip(user, _MAX_TURN_CHARS // 2)}\n"
            f"Assistant: {_clip(assistant, _MAX_TURN_CHARS // 2)}"
        )
        if mode == "turns" or not stats["durable"]:
            doc = await store_text(
                turn_blob,
                metadata={"type": "turn_snapshot", "kind": "user_assistant"},
            )
            if doc:
                stats["turn"] = 1
                stats["ids"].append(doc)
        elif mode == "both" and stats["durable"] and len(assistant) >= 40:
            doc = await store_text(
                f"Confirmed: {_clip(assistant, _MAX_TURN_CHARS)}",
                metadata={"type": "turn_snapshot", "kind": "assistant_confirm"},
            )
            if doc:
                stats["turn"] = 1
                stats["ids"].append(doc)

    if stats["durable"] or stats["turn"]:
        logger.info(
            "[auto_store] wrote durable=%d turn=%d ids=%s",
            stats["durable"],
            stats["turn"],
            stats["ids"][:3],
        )
    return stats


def schedule_auto_store(messages: list[dict[str, Any]]) -> None:
    """Fire-and-forget auto-store from a sync or async context.

    Safe to call from ``respond_node``: creates a task if a loop is running,
    otherwise no-ops (tests without a loop can await ``auto_store_from_messages``).
    """
    if not auto_store_enabled():
        return
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run() -> None:
        try:
            await auto_store_from_messages(messages)
        except Exception:
            logger.warning("[auto_store] background task failed", exc_info=True)

    try:
        loop.create_task(_run())
    except Exception:
        logger.debug("[auto_store] could not schedule task", exc_info=True)
