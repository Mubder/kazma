"""Runtime orchestration helpers (model rebind, process-wide live state)."""

from __future__ import annotations

from kazma_core.runtime.live_llm import (
    coerce_api_key,
    key_is_usable,
    resolve_live_client,
    url_is_cloud,
    url_is_local,
)
from kazma_core.runtime.model_switch import (
    SwitchResult,
    bind_live_agent,
    ensure_active_model,
    maybe_activate_provider_for_chat,
    notify_credentials_changed,
    register_rebind_hook,
    switch_active_model,
    switch_active_provider,
    unregister_rebind_hook,
)
from kazma_core.runtime.turn_model import (
    current_turn_model,
    pin_turn_model,
    reset_turn_model,
    resolve_turn_client,
)

__all__ = [
    "SwitchResult",
    "bind_live_agent",
    "coerce_api_key",
    "current_turn_model",
    "ensure_active_model",
    "key_is_usable",
    "maybe_activate_provider_for_chat",
    "notify_credentials_changed",
    "pin_turn_model",
    "register_rebind_hook",
    "reset_turn_model",
    "resolve_live_client",
    "resolve_turn_client",
    "switch_active_model",
    "switch_active_provider",
    "unregister_rebind_hook",
    "url_is_cloud",
    "url_is_local",
]
