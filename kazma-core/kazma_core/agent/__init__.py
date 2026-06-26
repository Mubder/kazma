"""Kazma Agent — LangGraph-based agentic orchestration layer.

Exposes the compiled Supervisor graph, enhanced state schema,
and the lightweight local + MCP tool registry.

Also re-exports backward-compatible names from the legacy agent module
(AgentConfig, KazmaAgent, load_config, build_graph, run_agent, main).
"""

# ── New Supervisor components ───────────────────────────────────────
from kazma_core.agent.graph_builder import build_supervisor_graph, create_supervisor_app
from kazma_core.agent.state import NodeName, SupervisorState, initial_supervisor_state
from kazma_core.agent.tool_registry import LocalToolRegistry, tool

# ── Backward-compatible re-exports from the agent runner module ─────
# The old kazma_core/agent.py was moved to kazma_core/agent_runner.py
# to avoid circular imports when this package was created.
try:
    from kazma_core.agent_runner import (  # noqa: F401
        CHECKPOINT_DB,
        MAX_ITERATIONS,
        AgentConfig,
        KazmaAgent,
        build_graph,
        create_app,
        load_config,
        main,
        run_agent,
    )
except ImportError:
    # If the legacy module hasn't been moved yet, try importing directly
    pass

__all__ = [
    # New
    "SupervisorState",
    "NodeName",
    "initial_supervisor_state",
    "build_supervisor_graph",
    "create_supervisor_app",
    "LocalToolRegistry",
    "tool",
    # Legacy
    "AgentConfig",
    "KazmaAgent",
    "load_config",
    "build_graph",
    "run_agent",
    "main",
]
