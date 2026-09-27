"""Kazma Core Tools — Built-in tool implementations for agent capabilities.

Tools in this package follow the LocalToolRegistry pattern:
async functions registered with @registry.register(description=..., category=...).

Each tool returns a string or dict — the registry normalizes results into
{"content": ..., "is_error": ...} for the LangGraph tool_worker node.

A name in this package is its submodule wherever one exists:
``kazma_core.tools.read_url`` is the module, and the tool is
``kazma_core.tools.read_url.read_url``. Until 2026-09-27 this file rebound
nine such names (``read_url``, ``file_read``, ``file_write``, ...) to the
functions, so ``from kazma_core.tools import file_write as fw`` gave the
function and a test's ``monkeypatch.setattr(fw, "check_path_access", ...)``
patched an attribute of the function -- nothing at all -- and passed.
``tests/test_package_namespaces.py`` keeps every package that way.
"""

from kazma_core.tools import (  # noqa: F401 -- the submodules, bound as modules
    computer_use,
    context_cmd,
    export_session,
    file_apply_patch,
    file_read,
    file_write,
    read_url,
    send_message,
    web_search,
)
from kazma_core.tools.code_exec import python_exec
from kazma_core.tools.image_gen import generate_image
from kazma_core.tools.personality_cmd import handle_personality_command, is_personality_command
from kazma_core.tools.read_url import (
    digest_research_file,
    list_research_chunks,
    read_research_chunk,
    read_url_to_file,
    summarize_research_file,
)
from kazma_core.tools.send_message import register_message_backend
from kazma_core.tools.vision_analyze import analyze_image
from kazma_core.tools.web_research import crawl_site

__all__ = [
    "register_message_backend",
    "read_url_to_file",
    "list_research_chunks",
    "read_research_chunk",
    "summarize_research_file",
    "digest_research_file",
    "crawl_site",
    "generate_image",
    "analyze_image",
    "python_exec",
    "is_personality_command",
    "handle_personality_command",
]
