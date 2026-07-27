"""Telegram callback_data mapping — re-exports shared platform callbacks.

Kept as a stable import path for telegram.py and tests. Implementation
lives in :mod:`kazma_gateway.adapters.platform_callbacks`.
"""

from __future__ import annotations

from kazma_gateway.adapters.platform_callbacks import (
    CallbackAction,
    parse_callback_data,
)

__all__ = [
    "CallbackAction",
    "parse_callback_data",
]
