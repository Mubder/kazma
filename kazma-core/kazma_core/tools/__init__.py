"""Kazma Core Tools — Built-in tool implementations for agent capabilities.

Tools in this package follow the LocalToolRegistry pattern:
async functions registered with @registry.register(description=..., category=...).

Each tool returns a string or dict — the registry normalizes results into
{"content": ..., "is_error": ...} for the LangGraph tool_worker node.
"""

from kazma_core.tools.code_exec import python_exec
from kazma_core.tools.context_cmd import context_cmd
from kazma_core.tools.export_session import export_session
from kazma_core.tools.file_read import file_read
from kazma_core.tools.file_write import file_write
from kazma_core.tools.image_gen import generate_image
from kazma_core.tools.personality_cmd import handle_personality_command, is_personality_command
from kazma_core.tools.read_url import read_url
from kazma_core.tools.send_message import register_message_backend, send_message
from kazma_core.tools.vision_analyze import analyze_image
from kazma_core.tools.web_search import web_search

__all__ = [
    "send_message",
    "register_message_backend",
    "web_search",
    "read_url",
    "export_session",
    "file_read",
    "file_write",
    "generate_image",
    "analyze_image",
    "python_exec",
    "context_cmd",
    "is_personality_command",
    "handle_personality_command",
]
