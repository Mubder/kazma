"""Built-in agent tools.

Was a single 2,833-line module (audit O5). Split by domain behind an
unchanged ``register_builtin_tools`` facade. Registration order across
groups matches the original file, so a tool defined twice still resolves
the same way.
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.agent.tool_builtins.external import register_external_tools
# Module-level helpers that lived beside the old god module's registrar.
# Re-exported so `from kazma_core.agent.tool_builtins import _qnorm` keeps
# working after the audit-O5 package split.
from kazma_core.agent.tool_builtins.external import _qnorm  # noqa: F401
from kazma_core.agent.tool_builtins.filesystem import register_filesystem_tools
from kazma_core.agent.tool_builtins.knowledge import register_knowledge_tools
from kazma_core.agent.tool_builtins.mcp import register_mcp_tools
from kazma_core.agent.tool_builtins.memory import register_memory_tools
from kazma_core.agent.tool_builtins.research import register_research_tools
from kazma_core.agent.tool_builtins.system import register_system_tools

logger = logging.getLogger(__name__)

__all__ = [
    "register_builtin_tools",
    "_qnorm",
    "register_external_tools",
    "register_filesystem_tools",
    "register_memory_tools",
    "register_system_tools",
    "register_knowledge_tools",
    "register_research_tools",
    "register_mcp_tools",
]


def register_builtin_tools(registry: Any) -> None:
    """Register the core built-in tools onto *registry*.

    Order matches the original module: the locally-defined tool groups first,
    then ``external`` — the try/except blocks that pull in tool sets from other
    modules, which sat at the bottom of the old file. Keeping ``external`` last
    preserves which body wins if a name is ever registered twice.
    """
    register_filesystem_tools(registry)
    register_memory_tools(registry)
    register_system_tools(registry)
    register_knowledge_tools(registry)
    register_research_tools(registry)
    register_mcp_tools(registry)
    register_external_tools(registry)
    logger.info("Registered %d built-in tools", len(registry._tools))
