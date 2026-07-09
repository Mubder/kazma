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

    @staticmethod
    def get_adapter(platform: str) -> "SwarmOutputTarget | None":
        """Factory method to get the appropriate adapter for a platform."""
        platform = platform.lower()
        if platform == "telegram":
            return TelegramSwarmOutputTarget()
        elif platform == "discord":
            return DiscordSwarmOutputTarget()
        elif platform == "slack":
            return SlackSwarmOutputTarget()
        return None


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
                                "[swarm-output] Telegram Markdown send failed: %s. Retrying in plain text...",
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
                        logger.info(
                            "[swarm-output] Swarm output routed via direct bot to %s",
                            chat_id,
                        )
                        return True
                    return False
            except Exception:
                logger.warning(
                    "[swarm-output] Failed routing swarm output via direct bot to %s",
                    chat_id, exc_info=True,
                )
                return False

        # Helper: Mode 2 - Gateway adapter send
        async def try_mode2_gateway_send() -> bool:
            if manager is None:
                return False
            try:
                await manager.send(OutboundMessage(
                    target_id=f"{platform}:{chat_id}",
                    text=text,
                    context_metadata={"chat_id": chat_id},
                ))
                logger.info(
                    "[swarm-output] Swarm output routed to %s:%s via gateway manager",
                    platform, chat_id,
                )
                return True
            except Exception:
                logger.warning(
                    "[swarm-output] Failed routing swarm output to %s:%s via gateway manager",
                    platform, chat_id, exc_info=True,
                )
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

        # Execute resolution logic:
        if explicit_bot_token:
            # Scenario A: User explicitly configured a separate dedicated swarm bot token
            if await try_mode1_direct_send(explicit_bot_token):
                return True
            # If dedicated direct send fails, fall back to gateway manager if available
            if await try_mode2_gateway_send():
                return True
            return False
        else:
            # Scenario B: User wants standard gateway adapter routing
            if await try_mode2_gateway_send():
                return True
            # If manager is None or failed, resolve the primary token and fallback to direct send
            primary_token = resolve_primary_token()
            if primary_token:
                if await try_mode1_direct_send(primary_token):
                    return True
            return False

    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate Telegram output target config."""
        platform = config.get("platform", "telegram")
        chat_id = config.get("chat_id")
        if platform != "telegram":
            return False
        if chat_id is None:
            return False
        return True


class DiscordSwarmOutputTarget(SwarmOutputTarget):
    """Discord-specific swarm output target."""

    async def send(
        self,
        manager: Any,
        text: str,
        target_config: dict[str, Any],
    ) -> bool:
        platform = target_config.get("platform", "discord")
        chat_id = target_config.get("chat_id")

        if not chat_id:
            return False

        if manager is None:
            logger.warning("[swarm-output] No gateway manager for Discord output")
            return False

        try:
            await manager.send(OutboundMessage(
                target_id=f"{platform}:{chat_id}",
                text=text,
                context_metadata={"chat_id": chat_id},
            ))
            logger.info(
                "[swarm-output] Swarm output routed to %s:%s via gateway manager",
                platform, chat_id,
            )
            return True
        except Exception:
            logger.warning(
                "[swarm-output] Failed routing swarm output to %s:%s via gateway manager",
                platform, chat_id, exc_info=True,
            )
            return False

    def validate_config(self, config: dict[str, Any]) -> bool:
        platform = config.get("platform", "discord")
        chat_id = config.get("chat_id")
        if platform != "discord":
            return False
        if chat_id is None:
            return False
        return True


class SlackSwarmOutputTarget(SwarmOutputTarget):
    """Slack-specific swarm output target."""

    async def send(
        self,
        manager: Any,
        text: str,
        target_config: dict[str, Any],
    ) -> bool:
        platform = target_config.get("platform", "slack")
        chat_id = target_config.get("chat_id")

        if not chat_id:
            return False

        if manager is None:
            logger.warning("[swarm-output] No gateway manager for Slack output")
            return False

        try:
            await manager.send(OutboundMessage(
                target_id=f"{platform}:{chat_id}",
                text=text,
                context_metadata={"chat_id": chat_id},
            ))
            logger.info(
                "[swarm-output] Swarm output routed to %s:%s via gateway manager",
                platform, chat_id,
            )
            return True
        except Exception:
            logger.warning(
                "[swarm-output] Failed routing swarm output to %s:%s via gateway manager",
                platform, chat_id, exc_info=True,
            )
            return False

    def validate_config(self, config: dict[str, Any]) -> bool:
        platform = config.get("platform", "slack")
        chat_id = config.get("chat_id")
        if platform != "slack":
            return False
        if chat_id is None:
            return False
        return True


async def send_swarm_output(
    manager: Any,
    text: str,
    target_config: dict[str, Any] | None = None,
) -> bool:
    """High-level function to send swarm output using the appropriate adapter.

    Resolution order: per-dispatch target_config override → ConfigStore entry.

    Args:
        manager: GatewayManager instance.
        text: Message text to send.
        target_config: Optional per-dispatch override (platform, chat_id, bot_token, enabled).

    Returns:
        True if a message was sent (or attempted), False if no target configured.
    """
    if not target_config:
        # Try to get from ConfigStore
        try:
            from kazma_core.config_store import get_config_store
            cs = get_config_store()
            target_config = cs.get("swarm.output_target", None)
            if not isinstance(target_config, dict):
                return False
            if not target_config.get("enabled", False):
                return False
            if not target_config.get("chat_id"):
                return False
            target_config.setdefault("platform", "telegram")
        except Exception:
            logger.debug("[swarm-output] Failed reading swarm.output_target", exc_info=True)
            return False

    platform = target_config.get("platform", "telegram")
    adapter = SwarmOutputTarget.get_adapter(platform)
    if adapter is None:
        logger.warning("[swarm-output] No adapter for platform %r", platform)
        return False

    if not adapter.validate_config(target_config):
        logger.warning("[swarm-output] Invalid config for platform %r: %s", platform, target_config)
        return False

    return await adapter.send(manager, text, target_config)