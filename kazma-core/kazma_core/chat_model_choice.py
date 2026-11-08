"""A chat app's own model (providers stage 3).

``/model`` in Telegram, Discord or Slack switched the model for the whole
install -- every chat, the web included -- and only an admin could use it.
A chat now keeps its own model: ``/model`` sets the model of the chat it is
sent in (any allowed user), ``/model reset`` makes the chat follow the
default again, and ``/model default ...`` changes the install's default
(admin). The gateway pins the chat's model for each of its turns
(``runtime/turn_model``), as the web chat pins the one its picker shows.

Kept in the settings store under :data:`KEY` (``{thread_id: {"ref",
"at"}}``), at most :data:`MAX_CHATS` chats, the oldest choice dropped
first. It is a chat's state, not a setting: never carried by a settings
backup (``settings_restore.KEY_RULES``: runtime).
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["KEY", "MAX_CHATS", "chat_model_for", "clear_chat_model", "set_chat_model"]

KEY = "chat.models"
MAX_CHATS = 2000


def _store(store: Any = None) -> Any:
    if store is not None:
        return store
    from kazma_core.config_store import get_config_store

    return get_config_store()


def _choices(store: Any) -> dict[str, dict[str, Any]]:
    raw = store.get(KEY)
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def chat_model_for(thread_id: str, *, store: Any = None) -> str:
    """The model *thread_id* chose (``provider:model``), or "" for the
    default. A store that cannot answer is the default. Blocking: off the
    loop."""
    if not thread_id:
        return ""
    from kazma_core.db.pg_helpers import store_errors

    try:
        entry = _choices(_store(store)).get(str(thread_id)) or {}
    except store_errors():  # a store that cannot answer: the default answers
        logger.debug("[chat-model] choice unreadable", exc_info=True)
        return ""
    return str(entry.get("ref") or "")


def set_chat_model(thread_id: str, ref: str, *, store: Any = None) -> None:
    """*thread_id* runs with *ref* from its next turn. Raises when the store
    refuses the write (the caller says so)."""
    s = _store(store)

    def update(current: Any) -> dict[str, Any]:
        choices = {str(k): v for k, v in current.items() if isinstance(v, dict)} if isinstance(current, dict) else {}
        choices[str(thread_id)] = {"ref": str(ref), "at": time.time()}
        if len(choices) > MAX_CHATS:
            for old in sorted(choices, key=lambda k: float(choices[k].get("at") or 0))[: len(choices) - MAX_CHATS]:
                choices.pop(old, None)
        return choices

    s.atomic_update(KEY, update, category="registry")


def clear_chat_model(thread_id: str, *, store: Any = None) -> None:
    """*thread_id* follows the default again."""
    s = _store(store)

    def update(current: Any) -> dict[str, Any]:
        choices = {str(k): v for k, v in current.items() if isinstance(v, dict)} if isinstance(current, dict) else {}
        choices.pop(str(thread_id), None)
        return choices

    s.atomic_update(KEY, update, category="registry")
