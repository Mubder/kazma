"""Platform adapters for the Kazma Gateway.

Each adapter implements BaseAdapter for a specific messaging platform.

Modular UX (Telegram-parity layout)
-----------------------------------
- ``platform_callbacks`` / ``platform_keyboards`` — shared action IDs
- ``telegram_*`` — keyboards, callbacks, parse, send, stt
- ``discord_*`` — keyboards, callbacks, parse, send
- ``slack_*`` — blocks, callbacks, parse, send
- ``voice_helpers`` — shared STT/TTS for Discord/Slack
"""

from kazma_gateway.adapters.discord import DiscordAdapter
from kazma_gateway.adapters.slack import SlackAdapter
from kazma_gateway.adapters.telegram import TelegramAdapter

__all__ = ["DiscordAdapter", "SlackAdapter", "TelegramAdapter"]
