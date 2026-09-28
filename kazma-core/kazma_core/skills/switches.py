"""Which skills are switched off on the Skills page.

The page wrote ``skills.enabled.<skill id>`` and nothing read it: a built-in
skill switched off kept every one of its tools, and the page -- which listed
every built-in skill as enabled whatever was saved -- showed it switched on
again after a reload (found on the live install 2026-09-28). The switch is
now real: a switched-off skill's tools are not offered to the model
(``LocalToolRegistry.get_tool_definitions``) and are refused if called
(``_execute_inner``).

Read once per process, then kept in memory and updated by
:func:`set_skill_enabled` (the page's toggle): the tool list is built for
every model call, and a settings read there would be one per call.
Agent Skills (``agent-skill:<name>``) keep their own switch in
``agent_skills.enabled.<name>``, read by their discovery.
"""

from __future__ import annotations

import logging
import threading
import weakref

logger = logging.getLogger(__name__)

__all__ = ["is_skill_enabled", "load_switches", "set_skill_enabled"]

_PREFIX = "skills.enabled."
_CATEGORY = "skills"
_disabled: set[str] | None = None
#: The settings store the cache was read from: a new store (a test's, a
#: reconfigured one) is read afresh. A weak reference, not id(): a new object
#: can reuse a freed one's id.
_source: weakref.ref | None = None
_lock = threading.Lock()


def _skill_key(skill_id: str) -> str:
    """The settings key of a skill's switch (as the Skills page writes it)."""
    return f"{_PREFIX}{skill_id}"


def _load(store) -> set[str]:
    from kazma_core.db.pg_helpers import store_errors

    try:
        saved = store.get_category(_CATEGORY)
    except store_errors():
        logger.warning("[skills] could not read the skill switches; all skills on", exc_info=True)
        return set()
    return {
        key[len(_PREFIX):]
        for key, value in saved.items()
        if key.startswith(_PREFIX) and value is False
    }


def _switched_off() -> set[str]:
    global _disabled, _source
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    with _lock:
        if _disabled is None or _source is None or _source() is not store:
            _disabled = _load(store)
            try:
                _source = weakref.ref(store)
            except TypeError:  # an object that takes no weak reference
                _source = None
        return _disabled


def load_switches() -> frozenset[str]:
    """Read the switches now (at boot, off any model call); the ids switched off."""
    return frozenset(_switched_off())


def is_skill_enabled(skill_id: str | None) -> bool:
    """False only for a skill the operator switched off."""
    return not skill_id or skill_id not in _switched_off()


def set_skill_enabled(skill_id: str, enabled: bool) -> None:
    """Save the switch and apply it to this process at once."""
    from kazma_core.config_store import get_config_store

    get_config_store().set(_skill_key(skill_id), bool(enabled), category=_CATEGORY)
    off = _switched_off()
    with _lock:
        if enabled:
            off.discard(skill_id)
        else:
            off.add(skill_id)
