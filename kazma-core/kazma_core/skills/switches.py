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
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["is_skill_enabled", "load_switches", "note_skill_tool", "offered",
           "load_tool_switches", "set_skill_enabled", "set_tool_enabled", "switched_off_reason",
           "tool_key"]

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


# ── Tools (Settings -> Tools) ─────────────────────────────────────────────
#
# Settings -> Tools wrote ``tools.<name>.enabled`` and only its own listing
# read it: a tool switched off was still offered to the model and still ran
# (FA-250, the 2026-11 functional audit). Read like the skill switches: once
# per settings store, then kept in memory and updated by the page's toggle.

_TOOL_CATEGORY = "tools"
_tools_off: set[str] | None = None
_tools_source: weakref.ref | None = None
#: The skill each skill-provided tool belongs to (the native loader names
#: it): a model call's tool list is filtered by name, after the graph was built.
_skill_of: dict[str, str] = {}


def tool_key(name: str) -> str:
    """The settings key of a tool's switch (as Settings -> Tools writes it)."""
    return f"tools.{name}.enabled"


def _load_tools(store) -> set[str]:
    from kazma_core.db.pg_helpers import store_errors

    try:
        saved = store.get_category(_TOOL_CATEGORY)
    except store_errors():
        logger.warning("[tools] could not read the tool switches; all tools on", exc_info=True)
        return set()
    return {
        key[len("tools."):-len(".enabled")]
        for key, value in saved.items()
        if key.startswith("tools.") and key.endswith(".enabled") and value is False
    }


def _tools_switched_off() -> set[str]:
    global _tools_off, _tools_source
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    with _lock:
        if _tools_off is None or _tools_source is None or _tools_source() is not store:
            _tools_off = _load_tools(store)
            try:
                _tools_source = weakref.ref(store)
            except TypeError:
                _tools_source = None
        return _tools_off


def load_tool_switches() -> frozenset[str]:
    """Read the tool switches now (at boot); the names switched off."""
    return frozenset(_tools_switched_off())


def _is_tool_enabled(name: str | None) -> bool:
    """False for a tool switched off on Settings -> Tools."""
    return not name or name not in _tools_switched_off()


def set_tool_enabled(name: str, enabled: bool, *, store: Any = None) -> None:
    """Save a tool's switch in *store* (the settings store by default) and
    apply it to this process at once, when the switches were read from it."""
    from kazma_core.config_store import get_config_store

    store = store if store is not None else get_config_store()
    store.set(tool_key(name), bool(enabled), category=_TOOL_CATEGORY)
    with _lock:
        if _tools_off is None or _tools_source is None or _tools_source() is not store:
            return  # read afresh from the store they belong to
        if enabled:
            _tools_off.discard(name)
        else:
            _tools_off.add(name)


def note_skill_tool(name: str, skill_id: str) -> None:
    """Record which skill provides the tool *name* (the native loader)."""
    with _lock:
        _skill_of[name] = skill_id


def switched_off_reason(name: str, skill_id: str | None = None) -> str | None:
    """Why the tool *name* is neither offered nor run, or None when it is on."""
    skill = skill_id or _skill_of.get(name)
    if skill and not is_skill_enabled(skill):
        return (f"Tool '{name}' belongs to a skill that is switched off ({skill}). "
                "The operator can switch it on in Skills.")
    if not _is_tool_enabled(name):
        return (f"Tool '{name}' is switched off. The operator can switch it on in "
                "Settings > Tools.")
    return None


def offered(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*definitions* without the tools switched off now, by their skill or
    their own switch: the list a model call is given. The graph holds the
    list it was built with, so a switch applies at the next model call
    (blocking on a store's first read: callers on the loop use a thread)."""
    return [d for d in definitions or []
            if switched_off_reason(str(((d or {}).get("function") or {}).get("name") or "")) is None]
