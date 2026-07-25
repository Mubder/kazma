"""Slack Block Kit builders — mirrors telegram_keyboards / discord_keyboards."""

from __future__ import annotations

from typing import Any

from kazma_gateway.adapters.platform_keyboards import (
    slack_approval_blocks,
    slack_model_blocks,
    slack_personality_blocks,
    slack_provider_blocks,
)

__all__ = [
    "build_approval_blocks",
    "build_model_blocks",
    "build_personality_blocks",
    "build_provider_blocks",
]


def build_approval_blocks(request_id: str, text: str = "Approval required") -> list[dict[str, Any]]:
    return slack_approval_blocks(request_id, text=text)


def build_personality_blocks(personalities: list[str]) -> list[dict[str, Any]]:
    return slack_personality_blocks(personalities)


def build_provider_blocks(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return slack_provider_blocks(providers)


def build_model_blocks(provider_name: str, models: list[str]) -> list[dict[str, Any]]:
    return slack_model_blocks(provider_name, models)
