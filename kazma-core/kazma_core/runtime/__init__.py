"""Runtime orchestration helpers (model rebind, process-wide live state)."""

from __future__ import annotations

from kazma_core.runtime.model_switch import (
    SwitchResult,
    ensure_active_model,
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
    "current_turn_model",
    "ensure_active_model",
    "pin_turn_model",
    "register_rebind_hook",
    "reset_turn_model",
    "resolve_turn_client",
    "switch_active_model",
    "switch_active_provider",
    "unregister_rebind_hook",
]
