"""Kazma Comms — Omnichannel communication layer for Kazma.

This package provides real-time message bridges for external platforms.
Each bridge is a self-contained module that:

1. Exposes a secure FastAPI webhook router for message ingestion.
2. Maps platform-specific session IDs (e.g. Telegram chat_id) to
   Kazma thread_ids for checkpointed conversation history.
3. Hands messages to the LangGraph agent loop in a non-blocking fashion.
4. Delivers agent responses back through platform-native APIs.

Bridges
───────

* ``telegram_bridge`` — Telegram webhook bridge (replaces long-polling).
  Router factory: ``create_telegram_webhook_router(graph=..., system_prompt=...)``

* ``setup_telegram`` — Utility to register webhook URLs with Telegram.
  ``setup_webhook("https://kazma.example.com")``

Usage
─────

    from kazma_comms.telegram_bridge import create_telegram_webhook_router
    from fastapi import FastAPI

    app = FastAPI()
    router = create_telegram_webhook_router(graph=my_graph)
    app.include_router(router)
"""

from kazma_comms.setup_telegram import (
    delete_webhook,
    get_webhook_info,
    setup_webhook,
)
from kazma_comms.telegram_bridge import (
    create_telegram_webhook_router,
    get_session_by_chat_id,
    get_chat_id_for_thread,
    list_telegram_sessions,
)

__all__ = [
    # Router factory
    "create_telegram_webhook_router",
    # Session management
    "list_telegram_sessions",
    "get_session_by_chat_id",
    "get_chat_id_for_thread",
    # Setup utilities
    "setup_webhook",
    "delete_webhook",
    "get_webhook_info",
]
