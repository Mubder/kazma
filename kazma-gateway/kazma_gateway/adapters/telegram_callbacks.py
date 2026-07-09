"""Telegram callback_data → synthetic command mapping (S5 extract)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CallbackAction:
    """Parsed result of a Telegram inline keyboard callback."""

    kind: str  # hitl | personality | model_provider | model_select | swarm | sys_install | install_dep | unknown
    text: str = ""  # synthetic message text for agent path (if any)
    package_name: str = ""
    swarm_data: str = ""  # raw callback data for swarm bus
    handled_in_process: bool = False  # True if no IncomingMessage needed


def parse_callback_data(data: str) -> CallbackAction:
    """Parse Telegram callback_data into a structured action."""
    if not data:
        return CallbackAction(kind="unknown")

    if data.startswith("hitl:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            action, request_id = parts[1], parts[2]
            return CallbackAction(
                kind="hitl",
                text=f"/hitl {action} {request_id}",
            )
        return CallbackAction(kind="unknown")

    if data.startswith(("swarm_approve_", "swarm_reject_")):
        return CallbackAction(
            kind="swarm",
            swarm_data=data,
            handled_in_process=True,
        )

    if data.startswith("personality:"):
        name = data.split(":", 1)[1]
        return CallbackAction(kind="personality", text=f"/personality {name}")

    if data.startswith("model_provider:"):
        provider_name = data.split(":", 1)[1]
        return CallbackAction(
            kind="model_provider",
            text=f"/_models_provider {provider_name}",
        )

    if data.startswith("model_select:"):
        model_id = data.split(":", 1)[1]
        return CallbackAction(
            kind="model_select",
            text=f"/_models_select {model_id}",
        )

    if data.startswith("sys_install:"):
        return CallbackAction(
            kind="sys_install",
            package_name=data.split(":", 1)[1],
            handled_in_process=True,
        )

    if data.startswith("install_dependency:"):
        return CallbackAction(
            kind="install_dep",
            package_name=data.split(":", 1)[1],
            handled_in_process=True,
        )

    return CallbackAction(kind="unknown", text=data)
