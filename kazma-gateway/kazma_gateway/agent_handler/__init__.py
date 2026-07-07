"""Agent handler package.

Decomposed from the original god-module agent_handler.py.
Conforms perfectly to the original API and test specifications.
"""

from __future__ import annotations

from .store import (
    _resolve_thread,
    _InMemoryStore,
    _build_initial_state,
    _build_target_id,
    _MAX_DICT_ENTRIES,
    _PLATFORM_KEYS,
)
from .hitl import (
    _check_graph_interrupt,
    _build_approval_prompt,
    _handle_hitl_resume,
)
from .swarm_dispatch import (
    _extract_swarm_task,
    _dispatch_auto_route,
    _find_worker_prompt_split,
    _get_output_target_config,
    _parse_output_target_suffix,
    _maybe_send_to_output_target,
    _dispatch_swarm_from_chat,
    _send_swarm_reply,
)
from .commands import (
    _try_swarm_command,
    _handle_swarm_config_command,
    _get_visible_providers,
    _try_model_command,
    _get_provider_models,
    _is_active_model,
    _send_model_reply,
    _build_slash_ctx,
)
from .graph import (
    create_graph_handler,
)

__all__ = [
    "create_graph_handler",
    "_resolve_thread",
    "_InMemoryStore",
    "_build_initial_state",
    "_build_target_id",
    "_MAX_DICT_ENTRIES",
    "_PLATFORM_KEYS",
    "_check_graph_interrupt",
    "_build_approval_prompt",
    "_handle_hitl_resume",
    "_extract_swarm_task",
    "_dispatch_auto_route",
    "_find_worker_prompt_split",
    "_get_output_target_config",
    "_parse_output_target_suffix",
    "_maybe_send_to_output_target",
    "_dispatch_swarm_from_chat",
    "_send_swarm_reply",
    "_try_swarm_command",
    "_handle_swarm_config_command",
    "_get_visible_providers",
    "_try_model_command",
    "_get_provider_models",
    "_is_active_model",
    "_send_model_reply",
    "_build_slash_ctx",
]
