"""How much the model reasons before a chat answer: the owner's choice.

The weak-spot roadmap of 2026-11, theme 7 (FA-277). The first token of a
chat answer took 5.3 s at the median and 20.1 s at p95 against a budget of 3
and 8: Kazma's own share is 0.05 s, the rest is the model, and
``deepseek-flash`` thinks at DeepSeek's default effort, "high", before every
answer. The owner chose a setting:

``agent.chat_reasoning`` -- ``default`` (send nothing: the provider's own
choice), ``off``, ``low``, ``high`` or ``max``.

It reaches a model only where it is a switch Kazma knows
(``LLMProvider.can_turn_off_reasoning``: DeepSeek's own API, as
``thinking`` / ``reasoning_effort``); any other provider gets nothing, so a
failover to a provider with a ``chat`` of its own never receives an argument
it does not take. A model that refuses the field is asked again without it
(``LLMProvider._chat_inner``'s reasoning-switch fallback).

It applies to the chat turn's own model calls: the supervisor's, its
failover, the empty-answer nudge and the respond node's synthesis. X
drafting and research keep their own choice (``reasoning="off"``); the
one-paragraph formatter (``answer_format``) and the swarm's workers keep the
provider's default.
Gate: ``tests/test_chat_reasoning.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

CHAT_REASONING_KEY = "agent.chat_reasoning"
#: The levels, in the Settings page's order. ``default`` sends nothing.
LEVELS = ("default", "off", "low", "high", "max")

_warned: set[str] = set()


def parse_level(raw: Any) -> str | None:
    """The level *raw* names, or None when it names none."""
    if not isinstance(raw, str):
        return None
    level = raw.strip().lower()
    return level if level in LEVELS else None


def _chat_reasoning_level() -> str:
    """The level the owner chose for chat turns (a settings read: off the loop).

    A value the page could not have saved is said once and read as
    ``default``: the provider's own choice, never a guess.
    """
    from kazma_core.config_store import get_config_store

    raw = get_config_store().get(CHAT_REASONING_KEY, "default")
    level = parse_level(raw)
    if level is None:
        key = repr(raw)
        if key not in _warned:
            _warned.add(key)
            logger.warning("[chat_reasoning] %s = %s is not one of %s; the provider's "
                           "default is used", CHAT_REASONING_KEY, key, ", ".join(LEVELS))
        return "default"
    return level


def provider_takes_level(base_url: str) -> bool:
    """Whether a provider at *base_url* takes the level, for the Settings page
    (the model call itself asks the client, ``can_turn_off_reasoning``)."""
    from kazma_core.llm_provider import is_deepseek_api

    return is_deepseek_api(base_url)


def _reasoning_kwargs_for(client: Any, level: str) -> dict[str, Any]:
    """``{"reasoning": level}`` where *client* takes it, else ``{}``."""
    if level == "default":
        return {}
    can_switch = getattr(client, "can_turn_off_reasoning", None)
    if callable(can_switch) and can_switch() is True:
        return {"reasoning": level}
    return {}


async def chat_reasoning_kwargs(client: Any) -> dict[str, Any]:
    """The chat level's keyword for a model call on *client*, read off the loop."""
    level = await asyncio.to_thread(_chat_reasoning_level)
    return _reasoning_kwargs_for(client, level)
