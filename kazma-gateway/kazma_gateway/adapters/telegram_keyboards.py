"""Telegram inline keyboard builders — extracted from telegram adapter (S5)."""

from __future__ import annotations

from typing import Any

__all__ = [
    "build_approval_keyboard",
    "build_model_keyboard",
    "build_personality_keyboard",
    "build_provider_keyboard",
]


def build_approval_keyboard(request_id: str) -> dict[str, Any]:
    """Build an inline keyboard for HITL approval prompts."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Approve",
                    "callback_data": f"hitl:approve:{request_id}",
                },
                {
                    "text": "❌ Deny",
                    "callback_data": f"hitl:deny:{request_id}",
                },
            ]
        ]
    }


def build_personality_keyboard(personalities: list[str]) -> dict[str, Any]:
    """Build an inline keyboard for personality selection (top 3)."""
    return {
        "inline_keyboard": [
            [{"text": name, "callback_data": f"personality:{name}"}]
            for name in personalities[:3]
        ]
    }


def build_provider_keyboard(providers: list[dict[str, Any]]) -> dict[str, Any]:
    """Build inline keyboard for model provider selection (2 buttons per row)."""
    buttons: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for p in providers:
        name = p.get("name", p.get("display_name", "?"))
        display = p.get("display_name", name)
        row.append({"text": display, "callback_data": f"model_provider:{name}"})
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return {"inline_keyboard": buttons}


def build_model_keyboard(provider_name: str, models: list[str]) -> dict[str, Any]:
    """Build inline keyboard for model selection within a provider.

    *provider_name* is reserved for callback schemes that need it; model
    select callbacks currently use ``model_select:{model_id}`` only.
    """
    del provider_name  # kept for API stability / future callback namespacing
    buttons: list[list[dict[str, str]]] = []
    for model_id in models:
        display = model_id
        if "/" in display:
            display = display.split("/")[-1]
        buttons.append(
            [{"text": display, "callback_data": f"model_select:{model_id}"}]
        )
    return {"inline_keyboard": buttons}
