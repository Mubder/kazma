"""What a scheduled task may do when it fires (Scheduled audit SCH-S05, decision D12).

A fired task runs with nobody watching. It used to get the whole agent minus
the danger tools: every write-tier tool (sub-agents, the swarm, research
pipelines, memory writes, skill activation), and with approvals switched off,
everything. Now each job declares its capability set when it is booked, the
approval card shows it, and the fire is built with exactly those tools:

* ``read`` (the default): read-tier tools only, plus the one delivery the
  scheduler makes itself.
* ``read_write``: read- and write-tier tools, declared at booking.

Danger, always-ask, unclassified and MCP tools are never offered to a fire
(its graph has no checkpointer, so an approval could never be answered), and
a reminder (kind ``reminder``) runs no model and no tool at all. The fire's
executor refuses a tool outside the set even if the model names one it was
not offered (:func:`kazma_core.agent_runner.DeclaredToolExecutor`).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CAPABILITIES",
    "DEFAULT_CAPABILITIES",
    "KINDS",
    "cron_fire_graph",
    "describe_capabilities",
    "normalize_capabilities",
    "normalize_kind",
]

KINDS = ("reminder", "task")
CAPABILITIES = ("read", "read_write")
DEFAULT_CAPABILITIES = "read"

_TIERS = {"read": frozenset({"read"}), "read_write": frozenset({"read", "write"})}


def normalize_kind(kind: str | None, *, default: str = "reminder") -> str:
    """``reminder`` or ``task``; raises ValueError for anything else."""
    value = (kind or default).strip().lower()
    if value in ("agent", "agent_task"):
        value = "task"
    if value not in KINDS:
        raise ValueError(f"kind must be 'reminder' or 'task', not {kind!r}.")
    return value


def normalize_capabilities(capabilities: str | None) -> str:
    """``read`` or ``read_write``; raises ValueError for anything else."""
    value = (capabilities or DEFAULT_CAPABILITIES).strip().lower().replace("+", "_").replace("-", "_")
    if value in ("write", "readwrite", "read_and_write"):
        value = "read_write"
    if value not in CAPABILITIES:
        raise ValueError(f"tools must be 'read' or 'read_write', not {capabilities!r}.")
    return value


def describe_capabilities(kind: str, capabilities: str) -> str:
    """One line for a card, a page or a tool result."""
    if kind == "reminder":
        return "sends the stored message; runs no model and no tool"
    if capabilities == "read_write":
        return "runs the agent with read and write tools (no approval-gated tool), then sends its answer"
    return "runs the agent with read-only tools, then sends its answer"


def _tool_name(definition: dict[str, Any]) -> str:
    fn = definition.get("function", definition)
    return str(fn.get("name", definition.get("name", "")) or "")


def _declared_tool_names(definitions: list[dict[str, Any]], capabilities: str) -> list[str]:
    """The tools from *definitions* a fire with *capabilities* is offered."""
    from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS, get_tool_tier

    tiers = _TIERS[normalize_capabilities(capabilities)]
    names: list[str] = []
    for definition in definitions:
        name = _tool_name(definition)
        if not name or name.startswith("mcp__") or name in ALWAYS_HITL_TOOLS:
            continue
        if get_tool_tier(name) in tiers:
            names.append(name)
    return sorted(set(names))


def cron_fire_graph(agent: Any, *, capabilities: str = DEFAULT_CAPABILITIES) -> Any:
    """The child graph a fired task runs on: exactly its declared tools.

    The app's cron graph builder calls this; nothing else builds a fire.
    """
    names = _declared_tool_names(list(agent.tools.get_tool_definitions()), capabilities)
    return agent.build_child_graph(declared_tools=names)
