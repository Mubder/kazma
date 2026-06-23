"""Kazma Core Tools — Built-in tool implementations for agent capabilities.

Tools in this package follow the LocalToolRegistry pattern:
async functions registered with @registry.register(description=..., category=...).

Each tool returns a string or dict — the registry normalizes results into
{"content": ..., "is_error": ...} for the LangGraph tool_worker node.
"""

from kazma_core.tools.telegram_tools import send_telegram_message

__all__ = ["send_telegram_message"]
