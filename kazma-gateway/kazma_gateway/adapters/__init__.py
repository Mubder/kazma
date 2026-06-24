"""Platform adapters for the Kazma Gateway.

Each adapter implements BaseAdapter for a specific messaging platform.
"""

from kazma_gateway.adapters.telegram import TelegramAdapter

__all__ = ["TelegramAdapter"]
