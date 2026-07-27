"""Discord interactive components — mirrors telegram_keyboards."""

from __future__ import annotations

from typing import Any

from kazma_gateway.adapters.platform_keyboards import (
    discord_approval_components,
    discord_model_components,
    discord_personality_components,
    discord_provider_components,
)

__all__ = [
    "build_approval_components",
    "build_model_components",
    "build_personality_components",
    "build_provider_components",
]


def build_approval_components(request_id: str) -> list[dict[str, Any]]:
    return discord_approval_components(request_id)


def build_personality_components(personalities: list[str]) -> list[dict[str, Any]]:
    return discord_personality_components(personalities)


def build_provider_components(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return discord_provider_components(providers)


def build_model_components(provider_name: str, models: list[str]) -> list[dict[str, Any]]:
    return discord_model_components(provider_name, models)
