"""Kazma Agent — LangGraph ReAct loop with real LLM and MCP tool execution.

The agent runs a think → act → observe loop:
  1. THINK: Call the LLM with conversation history + available tools
  2. ACT:   If the LLM requested tools, execute them via MCP
  3. OBSERVE: Evaluate results, decide to continue or end

Supports durable checkpointing (survives SIGKILL), context compaction
at 80% threshold, cost circuit breaking, and full tracing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
import yaml
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph

from kazma_core.authority import ContextAuthority, create_authority
from kazma_core.cost_breaker import create_cost_breaker
from kazma_core.llm_provider import LLMConfig, LLMProvider
from kazma_core.state import AgentState
from kazma_core.tool_registry import ToolRegistry
from kazma_core.tracing import create_tracer

# NOTE: kazma_core.agent.graph_builder / .state are imported lazily inside
# run()/_ensure_graph() to avoid a circular import — the kazma_core.agent
# package __init__ re-exports names from this module.

logger = logging.getLogger(__name__)

CONFIG_FILE = "kazma.yaml"
CHECKPOINT_DB = "kazma-data/checkpoints.db"

# Maximum ReAct iterations before forced stop
MAX_ITERATIONS = 10


# ---------------------------------------------------------------------------
# Configuration
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
    system_prompt: str = ""
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
        system_prompt=raw.get("system_prompt", ""),
        raw=raw,
    )


# ---------------------------------------------------------------------------
# KazmaAgent — Main agent class
# ---------------------------------------------------------------------------


class KazmaAgent:
    """Main agent class — ReAct loop with LLM and MCP tool execution.

    Wires together:
    - LLMProvider: OpenAI-compatible chat completions
    - ToolRegistry: MCP tool discovery and execution
    - ContextAuthority: 80% compaction enforcement
    - CostCircuitBreaker: runaway cost prevention
    - KazmaTracer: observability
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or load_config()
        self._running = False

        # Supervisor graph + checkpointer, built lazily on first run() so the
        # agent's entry point actually executes the LangGraph supervisor graph
        # (with durable AsyncSqliteSaver checkpointing) rather than a separate
        # hand-rolled loop. See run() / _ensure_graph().
        self._graph: Any = None
        self._checkpointer: AsyncSqliteSaver | None = None
        self._checkpoint_conn: aiosqlite.Connection | None = None
        self._thread_id: str = ""

        # LLM Provider
        llm_cfg_dict = self.config.raw.get("llm", {})
        llm_cfg_dict.setdefault("model", self.config.default_model)
        self.llm_config = LLMConfig.from_dict(llm_cfg_dict)
        self.llm = LLMProvider(self.llm_config)

        # Tool Registry
        self.tools = ToolRegistry()

        # Cost Circuit Breaker
        self.cost_breaker = create_cost_breaker()

        # Tracer
        tracing_cfg = self.config.raw.get("logging", {})
        self.tracer = create_tracer(
            backend="langfuse" if tracing_cfg.get("langfuse", {}).get("enabled") else "console",
            config=tracing_cfg.get("langfuse", {}),
        )

        # Context Authority (80% compaction)
        self.authority: ContextAuthority = create_authority(
            model=self.config.default_model,
            window=self.config.raw.get("memory", {}).get("max_context_tokens", 128_000),
            llm_client=self._make_compaction_client(),
        )

        # Memory System (Tantivy backend)
        self.memory = None
        self._init_memory()

        # System prompt
        self.system_prompt = self.config.system_prompt or self._default_system_prompt()

        logger.info(
            "Kazma agent initialized: %s v%s (model=%s, url=%s)",
            self.config.name,
            self.config.version,
            self.llm_config.model,
            self.llm_config.base_url,
        )

    def _default_system_prompt(self) -> str:
        return (
            "You are Kazma (كاظمه), an autonomous AI agent framework. "
            "You understand Arabic dialects including Kuwaiti/Gulf Arabic. "
            "Respond in the same language and dialect the user uses. "
            "Be helpful, precise, and culturally aware."
        )

    def _init_memory(self) -> None:
        """Initialize the memory system.

        Uses Tantivy + SQLite when tantivy-py is available.
        Falls back to SQLite-only when tantivy-py is not installed.
        """
        try:
            memory_cfg = self.config.raw.get("memory", {})
            if not memory_cfg.get("enabled", True):
                logger.info("Memory system disabled in config")
                return

            tantivy_available = False
            try:
                import tantivy  # noqa: F401

                tantivy_available = True
            except ImportError:
                logger.info("tantivy-py not installed — using SQLite-only memory")

            from kazma_memory import SQLiteMemoryBackend

            sqlite_backend = SQLiteMemoryBackend(
                db_path=memory_cfg.get("sqlite_path", "kazma-data/memory.db"),
            )
            self.memory = sqlite_backend
            logger.info("Memory system initialized (SQLite FTS5)")

        except ImportError:
            logger.warning("kazma_memory module not found — memory disabled")
        except Exception as e:
            logger.error("Failed to initialize memory: %s", e)

    def _make_compaction_client(self) -> Any:
        """Create a lightweight LLM client for the compaction engine."""

        class _CompactionLLM:
            def __init__(self, provider: LLMProvider) -> None:
                self._provider = provider

            async def chat(self, messages: list[dict[str, Any]]) -> str:
                resp = await self._provider.chat(messages, max_tokens=2048, temperature=0.3)
                return resp.content

        return _CompactionLLM(self.llm)

    async def connect_mcp_servers(self) -> int:
        """Connect to all configured MCP servers.

        Returns:
            Total number of tools registered.
        """
        servers = self.config.raw.get("mcp", {}).get("servers", [])
        total = 0
        for server_cfg in servers:
            count = await self.tools.connect_server(server_cfg)
            total += count
            if count > 0:
                logger.info("MCP server '%s': %d tools", server_cfg.get("name"), count)
        return total

    async def _ensure_graph(self) -> Any:
        """Build (once) the LangGraph supervisor graph this agent runs on.

        The graph is wired from the agent's own already-constructed components
        (LLM, tool registry, cost breaker, context authority, tracer) and a
        durable AsyncSqliteSaver checkpointer, so run() executes the real
        supervisor StateGraph instead of a separate hand-rolled loop.
        """
        if self._graph is not None:
            return self._graph

        from kazma_core.agent.graph_builder import build_supervisor_graph

        # Durable checkpointer (SIGKILL-safe). One connection per agent,
        # closed in shutdown().
        db_path = self.config.raw.get("storage", {}).get("checkpoint_path", CHECKPOINT_DB)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._checkpoint_conn = await aiosqlite.connect(db_path)
        await self._checkpoint_conn.execute("PRAGMA journal_mode=WAL")
        await self._checkpoint_conn.execute("PRAGMA synchronous=NORMAL")
        self._checkpointer = AsyncSqliteSaver(self._checkpoint_conn)
        await self._checkpointer.setup()

        hitl_config = self.config.raw.get("safety", {}).get("hitl") or None

        self._graph = build_supervisor_graph(
            llm=self.llm,
            system_prompt=self.system_prompt,
            tool_definitions=self.tools.get_tool_definitions(),
            tool_executor=self.tools,
            cost_breaker=self.cost_breaker,
            authority=self.authority,
            tracer=self.tracer,
            checkpointer=self._checkpointer,
            hitl_config=hitl_config,
        )
        logger.info("KazmaAgent run path bound to supervisor graph (checkpoint=%s)", db_path)
        return self._graph

    async def run(self, user_input: str, state: AgentState | None = None) -> str:
        """Process user input by invoking the LangGraph supervisor graph.

        The agent compiles (once) the supervisor StateGraph with a durable
        AsyncSqliteSaver checkpointer and ``ainvoke()``s it with the user input.
        The graph runs the real SUPERVISOR -> TOOL_WORKER -> SUPERVISOR ->
        RESPOND ReAct loop with checkpointing; this method extracts and returns
        the final assistant text (preserving the historical ``str`` contract).
        """
        logger.info("Received input: %s", user_input[:100])
        self.cost_breaker.record_user_interaction()

        # Cost breaker gate (kept here so a halted session short-circuits before
        # building/invoking the graph).
        if self.cost_breaker.should_halt():
            return "⚠️ ميزانية الجلسة انتهت. أعد التشغيل أو اتصل بالمسؤول. (Budget exceeded)"

        graph = await self._ensure_graph()

        # Carry any prior conversation in `state` into the graph's messages, then
        # append the new user turn. The graph inserts the system prompt itself.
        prior = list(state.get("messages", [])) if state else []
        messages = prior + [{"role": "user", "content": user_input}]

        # Stable thread id per agent instance → checkpoints accumulate across
        # turns of a session under one thread.
        if not self._thread_id:
            import uuid

            self._thread_id = str(uuid.uuid4())

        from kazma_core.agent.state import initial_supervisor_state

        graph_state = initial_supervisor_state(thread_id=self._thread_id)
        graph_state["messages"] = messages
        config = {"configurable": {"thread_id": self._thread_id}}

        try:
            result = await graph.ainvoke(graph_state, config)
        except Exception as e:
            logger.error("Graph invocation failed: %s", e)
            return f"عذراً، حدث خطأ في الاتصال: {e}"

        # Extract the final assistant message text.
        final_messages = result.get("messages", [])
        for msg in reversed(final_messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
                return str(msg["content"])

        logger.warning("Graph produced no assistant response")
        return ""

    async def shutdown(self) -> None:
        """Clean shutdown of the agent."""
        self._running = False
        await self.tools.disconnect_all()
        await self.llm.close()
        self.tracer.shutdown()
        if self._checkpoint_conn is not None:
            try:
                await self._checkpoint_conn.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("Error closing checkpointer connection: %s", e)
            self._checkpoint_conn = None
            self._checkpointer = None
            self._graph = None
        logger.info("Kazma agent shut down.")


# ---------------------------------------------------------------------------
# Standalone functions (for backward compatibility with tests)
# ---------------------------------------------------------------------------


async def run_agent(
    user_input: str,
    config: AgentConfig | None = None,
    db_path: str = CHECKPOINT_DB,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Run the agent on user input with durable checkpointing.

    This is the high-level entry point that:
    1. Creates the agent from config
    2. Connects MCP servers
    3. Runs the input through the ReAct loop
    4. Returns the result state
    """
    agent = KazmaAgent(config)

    # Connect MCP servers
    tool_count = await agent.connect_mcp_servers()
    if tool_count > 0:
        logger.info("Connected %d MCP tools", tool_count)

    try:
        response = await agent.run(user_input)
        return {
            "messages": [{"role": "assistant", "content": response}],
            "response": response,
            "model": agent.llm_config.model,
        }
    finally:
        await agent.shutdown()


async def main() -> None:
    """Entry point for running Kazma as a standalone agent."""
    config = load_config()
    agent = KazmaAgent(config)

    # Connect MCP servers
    tool_count = await agent.connect_mcp_servers()
    if tool_count > 0:
        print(f"🔗 Connected {tool_count} MCP tools")

    agent._running = True
    print(f"🇰🇼 كاظمه — {config.name} v{config.version}")
    print(f"   Model: {agent.llm_config.model} @ {agent.llm_config.base_url}")
    print("   Type 'quit' to exit\n")

    try:
        while agent._running:
            user_input = await asyncio.get_running_loop().run_in_executor(None, lambda: input("kazma> "))
            if user_input.strip().lower() in ("quit", "exit"):
                break
            if not user_input.strip():
                continue

            response = await agent.run(user_input)
            print(response)
            print()
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        await agent.shutdown()
        print("\n🇰🇼 مع السلامة!")


# ---------------------------------------------------------------------------
# Backward-compatible aliases for tests that import these names
# ---------------------------------------------------------------------------


# Keep build_graph available for tests that import it directly
def build_graph(checkpointer: AsyncSqliteSaver | None = None) -> Any:
    """Build a basic LangGraph state machine (backward-compatible stub).

    The real ReAct loop now runs inside KazmaAgent.run(). This function
    is kept for backward compatibility with existing tests.
    """

    async def _think(state: AgentState) -> dict[str, Any]:
        return {"messages": state.get("messages", []) + [{"role": "assistant", "content": "[thinking]"}]}

    async def _act(state: AgentState) -> dict[str, Any]:
        tool_results = dict(state.get("tool_results", {}))
        tool_results[f"action_{len(tool_results)}"] = {"status": "completed", "result": "placeholder"}
        return {
            "messages": state.get("messages", []) + [{"role": "assistant", "content": "[acted]"}],
            "tool_results": tool_results,
        }

    async def _observe(state: AgentState) -> dict[str, Any]:
        tool_results = state.get("tool_results", {})
        done = len(tool_results) >= 3
        return {
            "messages": state.get("messages", [])
            + [{"role": "assistant", "content": "[done]" if done else "[continue]"}],
            "_should_continue": not done,
        }

    def _route(state: AgentState) -> str:
        return "end" if not state.get("_should_continue", True) else "continue"

    graph = StateGraph(AgentState)
    graph.add_node("think", _think)
    graph.add_node("act", _act)
    graph.add_node("observe", _observe)
    graph.set_entry_point("think")
    graph.add_edge("think", "act")
    graph.add_edge("act", "observe")
    graph.add_conditional_edges("observe", _route, {"continue": "think", "end": END})

    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


async def create_app(db_path: str = CHECKPOINT_DB) -> Any:
    """Create a compiled LangGraph app with SQLite checkpointer (backward-compatible)."""
    from pathlib import Path as _Path

    _Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        return build_graph(checkpointer=saver), saver
    except Exception:
        await conn.close()
        raise


if __name__ == "__main__":
    import os as _os

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
    # Skip atexit/threading shutdown noise
    _os._exit(0)
