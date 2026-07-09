"""Swarm output target adapters — platform-specific routing for swarm results.

This module provides a unified interface for sending swarm task results
to configured output targets (Telegram groups, Discord channels, Slack channels).

Each platform adapter implements the SwarmOutputTarget interface.
"""

from __future__ import annotations

import abc
import logging
from typing import Any

from kazma_gateway.gateway import OutboundMessage

logger = logging.getLogger(__name__)


class SwarmOutputTarget(abc.ABC):
    """Abstract base class for swarm output target adapters."""

    @abc.abstractmethod
    async def send(
        self,
        manager: Any,
        text: str,
        target_config: dict[str, Any],
    ) -> bool:
        """Send swarm output to the target.

        Args:
            manager: GatewayManager instance for platform-agnostic sending.
            text: The message text to send.
            target_config: Configuration dict with platform, chat_id, etc.

        Returns:
            True if sent (or attempted), False if no target available.
        """
        pass

    @abc.abstractmethod
    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate that the config has required fields for this platform."""
        pass


class TelegramSwarmOutputTarget(SwarmOutputTarget):
    """Telegram-specific swarm output target.

    Supports two delivery modes:
    1. Dedicated bot token (direct Bot API call) — "separate swarm bot" mode
    2. Gateway adapter (via manager.send) — standard group routing mode
    """

    async def send(
        self,
        manager: Any,
        text: str,
        target_config: dict[str, Any],
    ) -> bool:
        platform = target_config.get("platform", "telegram")
        chat_id = target_config.get("chat_id")
        explicit_bot_token = target_config.get("bot_token", "")

        if not chat_id:
            return False

        # Helper: Mode 1 - Direct Bot API send
        async def try_mode1_direct_send(token: str) -> bool:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                    all_chunks_ok = True
                    for i in range(0, len(text), 4096):
                        chunk = text[i:i + 4096]
                        chunk_ok = False
                        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}
                        resp = await client.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            json=payload,
                        )
                        resp_json = resp.json()
                        if resp_json.get("ok"):
                            chunk_ok = True
                        else:
                            desc = resp_json.get("description", "")
                            logger.warning(
                                "[swarm-output] Telegram Markdown send failed: %s. Retrying plain...",
                                desc or "unknown",
                            )
                            payload_plain = {"chat_id": chat_id, "text": chunk}
                            resp_plain = await client.post(
                                f"https://api.telegram.org/bot{token}/sendMessage",
                                json=payload_plain,
                            )
                            resp_plain_json = resp_plain.json()
                            if resp_plain_json.get("ok"):
                                chunk_ok = True
                            else:
                                logger.warning(
                                    "[swarm-output] Telegram fallback plain send failed: %s",
                                    resp_plain_json.get("description", "unknown"),
                                )
                        if not chunk_ok:
                            all_chunks_ok = False
                            break
                    if all_chunks_ok:
                        logger.info("[swarm-output] Swarm output routed via direct bot to %s", chat_id)
                        return True
                    return False
            except Exception:
                logger.warning("[swarm-output] Failed routing via direct bot to %s", chat_id, exc_info=True)
                return False

        # Helper: Mode 2 - Gateway manager send
        async def try_mode2_gateway_send() -> bool:
            if manager is None:
                return False
            try:
                await manager.send(OutboundMessage(
                    target_id=f"{platform}:{chat_id}",
                    text=text,
                    context_metadata={"chat_id": chat_id},
                ))
                logger.info("[swarm-output] Swarm output routed to %s:%s via gateway manager", platform, chat_id)
                return True
            except Exception:
                logger.warning("[swarm-output] Failed routing to %s:%s via gateway manager", platform, chat_id, exc_info=True)
                return False

        # Helper: Resolve primary bot token as fallback
        def resolve_primary_token() -> str:
            if platform != "telegram":
                return ""
            try:
                from kazma_core.config_store import get_config_store
                import os
                cs = get_config_store()
                token = ""
                if cs:
                    token = cs.get("connectors.telegram.token", "")
                if not token:
                    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                return token
            except Exception:
                return ""

        # Execute resolution logic
        if explicit_bot_token:
            # Scenario A: Explicit dedicated swarm bot token
            if await try_mode1_direct_send(explicit_bot_token):
                return True
            # Fallback to gateway manager
            if await try_mode2_gateway_send():
                return True
            return False
        else:
            # Scenario B: Standard gateway adapter routing
            if await try_mode2_gateway_send():
                return True
            # Fallback: resolve primary token and try direct send
            primary_token = resolve_primary_token()
            if primary_token:
                if await try_mode1_direct_send(primary_token):
                    return True
            return False

    def validate_config(self, config: dict[str, Any]) -> bool:
        return config.get("platform") == "telegram" and config.get("chat_id") is not None


class DiscordSwarmOutputTarget(SwarmOutputTarget):
    """Discord-specific swarm output target."""

    async def send(
        self,
        manager: Any,
        text: str,
        target_config: dict[str, Any],
    ) -> bool:
        if manager is None:
            return False
        chat_id = target_config.get("chat_id")
        if not chat_id:
            return False
        try:
            await manager.send(OutboundMessage(
                target_id=f"discord:{chat_id}",
                text=text,
                context_metadata={"channel_id": chat_id},
            ))
            logger.info("[swarm-output] Swarm output routed to Discord channel %s", chat_id)
            return True
        except Exception:
            logger.warning("[swarm-output] Failed routing to Discord channel %s", chat_id, exc_info=True)
            return False

    def validate_config(self, config: dict[str, Any]) -> bool:
        return config.get("platform") == "discord" and config.get("chat_id") is not None


class SlackSwarmOutputTarget(SwarmOutputTarget):
    """Slack-specific swarm output target."""

    async def send(
        self,
        manager: Any,
        text: str,
        target_config: dict[str, Any],
    ) -> bool:
        if manager is None:
            return False
        chat_id = target_config.get("chat_id")
        if not chat_id:
            return False
        try:
            await manager.send(OutboundMessage(
                target_id=f"slack:{chat_id}",
                text=text,
                context_metadata={"channel_id": chat_id},
            ))
            logger.info("[swarm-output] Swarm output routed to Slack channel %s", chat_id)
            return True
        except Exception:
            logger.warning("[swarm-output] Failed routing to Slack channel %s", chat_id, exc_info=True)
            return False

    def validate_config(self, config: dict[str, Any]) -> bool:
        return config.get("platform") == "slack" and config.get("chat_id") is not None


# Registry of available adapters
_OUTPUT_TARGET_ADAPTERS: dict[str, SwarmOutputTarget] = {
    "telegram": TelegramSwarmOutputTarget(),
    "discord": DiscordSwarmOutputTarget(),
    "slack": SlackSwarmOutputTarget(),
}


def get_output_target_adapter(platform: str) -> SwarmOutputTarget | None:
    """Get the output target adapter for a platform."""
    return _OUTPUT_TARGET_ADAPTERS.get(platform.lower())


async def send_swarm_output(
    manager: Any,
    text: str,
    target_config: dict[str, Any] | None,
) -> bool:
    """Send swarm output to the configured target.

    Args:
        manager: GatewayManager instance.
        text: Message text to send.
        target_config: Target configuration dict (platform, chat_id, etc.) or None.

    Returns:
        True if sent (or attempted), False if no target or invalid config.
    """
    if not target_config:
        return False

    platform = target_config.get("platform", "telegram").lower()
    adapter = get_output_target_adapter(platform)
    if not adapter:
        logger.warning("[swarm-output] No adapter for platform %s", platform)
        return False

    if not adapter.validate_config(target_config):
        logger.warning("[swarm-output] Invalid config for platform %s: %s", platform, target_config)
        return False

    return await adapter.send(manager, text, target_config)