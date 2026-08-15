"""Intent handler registry — the only place handlers are registered."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from kazma_core.agent.intent.types import HandlerResult, TurnDecision

__all__ = ["IntentHandler", "IntentRegistry", "get_registry"]

logger = logging.getLogger(__name__)


@dataclass
class IntentHandler:
    name: str
    act: str
    required_slots: tuple[str, ...]
    uses_execute: bool
    mutating: bool
    timeout_seconds: float
    run: Callable[..., Awaitable[HandlerResult]]


class IntentRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, IntentHandler] = {}
        self._composers: dict[frozenset[str], IntentHandler] = {}

    def register(self, handler: IntentHandler) -> None:
        if handler.mutating and not handler.uses_execute:
            raise RuntimeError(
                f"Refusing to register mutating handler '{handler.name}' "
                "that does not use execute()"
            )
        self._handlers[handler.name] = handler
        logger.debug("[intent_registry] registered '%s' (act=%s)", handler.name, handler.act)

    def register_composer(self, kinds: frozenset[str], handler: IntentHandler) -> None:
        if handler.mutating and not handler.uses_execute:
            raise RuntimeError(
                f"Refusing to register mutating composer '{handler.name}' "
                "that does not use execute()"
            )
        self._composers[kinds] = handler

    def get(self, name: str) -> IntentHandler | None:
        return self._handlers.get(name)

    def get_for_act(self, act: str) -> IntentHandler | None:
        for h in self._handlers.values():
            if h.act == act:
                return h
        return None

    def get_composer(self, kinds: frozenset[str]) -> IntentHandler | None:
        return self._composers.get(kinds)


_registry: IntentRegistry | None = None


def get_registry() -> IntentRegistry:
    global _registry
    if _registry is None:
        _registry = IntentRegistry()
        _auto_register()
    return _registry


def _auto_register() -> None:
    """Import and register handlers. Phase 0: no execute handlers."""
    # Phase 1 will add:
    # try:
    #     from kazma_core.agent.intent.handlers.document import register as _reg_doc
    #     _reg_doc()
    # except Exception as exc:
    #     logger.warning("[intent_registry] document handler registration failed: %s", exc)
    pass
