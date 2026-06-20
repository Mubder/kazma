"""Kazma Agent — LangGraph state machine with SQLite checkpointer.

Implements the think → act → observe ReAct loop with durable checkpointing.
The agent survives SIGKILL and resumes from its last checkpoint.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import aiosqlite
import yaml
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from kazma_core.state import AgentState, initial_state
from kazma_core.authority import ContextAuthority, create_authority

logger = logging.getLogger(__name__)

CONFIG_FILE = "kazma.yaml"
CHECKPOINT_DB = "kazma-data/checkpoints.db"


# ---------------------------------------------------------------------------
# Configuration (kept from T1 for backward compatibility)
# ---------------------------------------------------------------------------


@dataclass
class AgentConfig:
    """Configuration loaded from kazma.yaml."""

    name: str = "kazma"
    version: str = "0.1.0"
    language: str = "ar"
    rtl: bool = True
    default_model: str = "gpt-4o-mini"
    storage_path: str = "data/kazma.db"
    vector_dim: int = 1536
    raw: dict[str, Any] = field(default_factory=dict)


def load_config(config_path: str | Path | None = None) -> AgentConfig:
    """Load configuration from YAML file."""
    path = Path(config_path) if config_path else Path(CONFIG_FILE)
    if not path.exists():
        logger.warning("Config file %s not found, using defaults", path)
        return AgentConfig()

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    agent_cfg = raw.get("agent", {})
    models_cfg = raw.get("models", {})
    storage_cfg = raw.get("storage", {})

    return AgentConfig(
        name=agent_cfg.get("name", "kazma"),
        version=agent_cfg.get("version", "0.1.0"),
        language=agent_cfg.get("language", "ar"),
        rtl=agent_cfg.get("rtl", True),
        default_model=models_cfg.get("default", "gpt-4o-mini"),
        storage_path=storage_cfg.get("path", "data/kazma.db"),
        vector_dim=storage_cfg.get("vector_dim", 1536),
        raw=raw,
    )


class KazmaAgent:
    """Main agent class — ReAct loop with LangGraph state machine."""

    def __init__(self, config: AgentConfig | None = None):
        self.config = config or load_config()
        self._running = False
        self.authority: ContextAuthority = create_authority(
            model=self.config.default_model,
            window=self.config.raw.get("memory", {}).get("max_context_tokens", 128_000),
        )
        logger.info(
            "Kazma agent initialized: %s v%s (lang=%s, threshold=%d tokens)",
            self.config.name,
            self.config.version,
            self.config.language,
            self.authority.threshold,
        )

    async def run(self, user_input: str, state: AgentState | None = None) -> str:
        """Process user input and return response.

        Uses ContextAuthority to enforce the 80% compaction rule before
        processing input. If compaction is triggered, the state is
        automatically compacted before the LLM call.
        """
        logger.info("Received input: %s", user_input[:100])

        if state is None:
            state = initial_state()
        state["messages"] = state.get("messages", []) + [
            {"role": "user", "content": user_input}
        ]

        # Enforce 80% context compaction authority
        state = await self.authority.check_and_enforce(state)

        # TODO: Connect to tool registry, memory, and providers
        response = f"[Kazma] Echo: {user_input}"
        state["messages"] = state.get("messages", []) + [
            {"role": "assistant", "content": response}
        ]
        return response

    async def shutdown(self) -> None:
        """Clean shutdown of the agent."""
        self._running = False
        logger.info("Kazma agent shut down.")


# ---------------------------------------------------------------------------
# Node functions for LangGraph state machine
# ---------------------------------------------------------------------------


async def think_node(state: AgentState) -> dict[str, Any]:
    """Think phase: analyze current state and decide what to do."""
    messages = state.get("messages", [])
    tool_results = state.get("tool_results", {})

    logger.info(
        "Thinking: %d messages, %d tool results, %d tokens",
        len(messages),
        len(tool_results),
        state.get("context_tokens", 0),
    )

    return {
        "messages": messages + [{"role": "assistant", "content": "[thinking]"}],
        "context_tokens": state.get("context_tokens", 0) + 10,
    }


async def act_node(state: AgentState) -> dict[str, Any]:
    """Act phase: execute the decided action."""
    messages = state.get("messages", [])
    tool_results = dict(state.get("tool_results", {}))

    logger.info("Acting: executing action based on %d messages", len(messages))

    action_id = f"action_{len(tool_results)}"
    tool_results[action_id] = {"status": "completed", "result": "placeholder"}

    return {
        "messages": messages + [{"role": "assistant", "content": "[acted]"}],
        "tool_results": tool_results,
        "context_tokens": state.get("context_tokens", 0) + 5,
    }


async def observe_node(state: AgentState) -> dict[str, Any]:
    """Observe phase: evaluate the action result."""
    messages = state.get("messages", [])
    tool_results = state.get("tool_results", {})

    logger.info("Observing: %d tool results", len(tool_results))

    if len(tool_results) >= 3:
        return {
            "messages": messages + [{"role": "assistant", "content": "[done]"}],
            "context_tokens": state.get("context_tokens", 0) + 5,
            "_should_continue": False,
        }

    return {
        "messages": messages + [{"role": "assistant", "content": "[continue]"}],
        "context_tokens": state.get("context_tokens", 0) + 5,
        "_should_continue": True,
    }


def should_continue(state: AgentState) -> Literal["continue", "end"]:
    """Routing function: should the agent continue the ReAct loop?"""
    if state.get("_should_continue", True):
        return "continue"
    return "end"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_graph(checkpointer: AsyncSqliteSaver | None = None) -> StateGraph:
    """Build the LangGraph state machine."""
    graph = StateGraph(AgentState)

    graph.add_node("think", think_node)
    graph.add_node("act", act_node)
    graph.add_node("observe", observe_node)

    graph.set_entry_point("think")
    graph.add_edge("think", "act")
    graph.add_edge("act", "observe")
    graph.add_conditional_edges(
        "observe",
        should_continue,
        {"continue": "think", "end": END},
    )

    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)  # type: ignore[return-value]
    return graph.compile()  # type: ignore[return-value]


async def create_app(db_path: str = CHECKPOINT_DB) -> Any:
    """Create a compiled LangGraph app with SQLite checkpointer."""
    from pathlib import Path as _Path
    _Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return build_graph(checkpointer=saver), saver


async def run_agent(
    user_input: str,
    db_path: str = CHECKPOINT_DB,
    thread_id: str | None = None,
) -> AgentState:
    """Run the agent on user input with durable checkpointing."""
    from uuid import uuid4

    app, saver = await create_app(db_path)
    tid = thread_id or str(uuid4())
    config: dict[str, Any] = {"configurable": {"thread_id": tid}}

    state = initial_state()
    state["messages"] = [{"role": "user", "content": user_input}]

    result = await app.ainvoke(state, config)  # type: ignore[union-attr]

    await saver.conn.close()
    return result  # type: ignore[return-value]


async def main() -> None:
    """Entry point for running Kazma as a standalone agent."""
    config = load_config()
    agent = KazmaAgent(config)
    agent._running = True

    logger.info("Kazma agent started. Type 'quit' to exit.")
    try:
        while agent._running:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("kazma> ")
            )
            if user_input.strip().lower() in ("quit", "exit"):
                break
            response = await agent.run(user_input)
            print(response)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
