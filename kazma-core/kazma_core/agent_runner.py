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

from kazma_core.authority import ContextAuthority, create_authority
from kazma_core.cost_breaker import create_cost_breaker
from kazma_core.llm_provider import LLMProvider
from kazma_core.mcp.manager import UnifiedToolExecutor
from kazma_core.state import AgentState
from kazma_core.tracing import KazmaTracer
from kazma_core.config_schema import TracingConfig

from kazma_core.config_store import apply_sqlite_pragmas_async

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
    version: str = "0.2.0"
    language: str = "ar"
    rtl: bool = True
    default_model: str = "gpt-4o-mini"
    storage_path: str = "data/kazma.db"
    vector_dim: int = 384
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
        version=agent_cfg.get("version", "0.2.0"),
        language=agent_cfg.get("language", "ar"),
        rtl=agent_cfg.get("rtl", True),
        default_model=models_cfg.get("default", "gpt-4o-mini"),
        storage_path=storage_cfg.get("path", "data/kazma.db"),
        vector_dim=storage_cfg.get("vector_dim", 384),
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

        # Streaming graph for SSE path (built lazily, cached).
        # Separate from _graph which includes a checkpointer for run().
        self._streaming_graph: Any = None

        # LLM Provider — route through the singleton ModelRegistry
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()
        self.llm = registry.get_client()
        self.llm_config = self.llm.config  # keep llm_config for backward compat

        # Tool Registry — unified executor over local built-in tools + MCP.
        # ``UnifiedToolExecutor`` is the single canonical tool abstraction:
        # it dispatches ``execute(name, args)`` to the in-process
        # LocalToolRegistry first, then to the AsyncMCPManager. The legacy
        # MCP-only ToolRegistry (kazma_core.tool_registry) has been removed.
        from kazma_core.agent.tool_registry import LocalToolRegistry

        self.tools = UnifiedToolExecutor(local=LocalToolRegistry(include_builtins=True))

        # Cost Circuit Breaker
        self.cost_breaker = create_cost_breaker()

        # Tracer
        tracing_cfg = self.config.raw.get("logging", {})
        # Create TracingConfig from the raw config
        tracing_config = TracingConfig(
            enabled=tracing_cfg.get("langfuse", {}).get("enabled", False),
            backend="langfuse" if tracing_cfg.get("langfuse", {}).get("enabled") else "console",
            otlp_endpoint=tracing_cfg.get("otlp_endpoint", "http://localhost:4317"),
            service_name="kazma-agent",
            sample_rate=1.0,
            langfuse_public_key=tracing_cfg.get("langfuse", {}).get("public_key"),
            langfuse_secret_key=tracing_cfg.get("langfuse", {}).get("secret_key"),
            langfuse_host=tracing_cfg.get("langfuse", {}).get("host", "http://localhost:3000"),
        )
        self.tracer = KazmaTracer(config=tracing_config)

        # Memory System (FTS5/SQLite backend) — initialized *before* the
        # authority so we can pass it as the compaction memory_store.
        self._memory_backend = None
        self._init_memory()

        # Context Authority (80% compaction) — wired with the VectorMemory
        # singleton (wrapped in an async adapter) so that the compaction
        # engine can retrieve and inject relevant memories into the fresh
        # context after summarizing.
        from kazma_core.agent.tool_registry import get_vector_memory
        from kazma_core.memory.async_adapter import wrap_vector_memory

        _vm = get_vector_memory()
        _memory_store = wrap_vector_memory(_vm) if _vm is not None else None
        self.authority: ContextAuthority = create_authority(
            model=self.config.default_model,
            window=self.config.raw.get("memory", {}).get("max_context_tokens", 128_000),
            llm_client=self._make_compaction_client(),
            memory_store=_memory_store,
        )
        if _memory_store is not None:
            logger.info("ContextAuthority wired with VectorMemory for compaction retrieval")
        else:
            logger.warning("ContextAuthority has no memory_store — compaction will not inject memories")

        # System prompt
        self.system_prompt = self.config.system_prompt or self._default_system_prompt()

        # Inject cultural context enrichment
        try:
            from kazma_core.cultural_context_enrichment import get_cultural_prompt_suffix
            cultural_suffix = get_cultural_prompt_suffix()
            if cultural_suffix and cultural_suffix not in self.system_prompt:
                self.system_prompt = self.system_prompt.rstrip() + cultural_suffix
        except Exception:
            pass

        # Universal language directive — injected LAST so it's the final
        # instruction the model sees, after all cultural context. This
        # prevents Arabic cultural context from biasing the model to
        # respond in Arabic when the user writes in English.
        _LANG_DIRECTIVE = (
            "\n\nCRITICAL LANGUAGE RULE: You MUST respond in the EXACT language "
            "the user writes in. Arabic input = Arabic output. English input = "
            "English output. If they mix, match their pattern. This overrides "
            "all other instructions, personality settings, and cultural context. "
            "NEVER switch languages unless explicitly asked."
        )
        if _LANG_DIRECTIVE not in self.system_prompt:
            self.system_prompt = self.system_prompt.rstrip() + _LANG_DIRECTIVE

        logger.info(
            "Kazma agent initialized: %s v%s (model=%s, url=%s)",
            self.config.name,
            self.config.version,
            self.llm_config.model,
            self.llm_config.base_url,
        )

    def _default_system_prompt(self) -> str:
        return (
            "You are Kazma, an autonomous AI agent framework. "
            "You are capable of understanding Arabic dialects including Kuwaiti/Gulf Arabic "
            "when the user speaks Arabic, but your default response language is always "
            "determined by the user's input language. "
            "\n\nBe helpful, precise, and culturally aware. "
            "\n\nCRITICAL LANGUAGE RULE: You MUST respond in the EXACT language the user "
            "writes in. Arabic input = Arabic output. English input = English output. "
            "If they mix, match their pattern. If the input is gibberish or unclear, "
            "respond in English. This overrides all other instructions. "
            "NEVER switch languages unless explicitly asked."
        )

    def _init_memory(self) -> None:
        """Initialize the memory system.

        Historically this constructed a SQLiteMemoryBackend (FTS5) as
        ``self.memory``.  That backend was orphaned — never read or written
        during chat.  Memory retrieval/injection now flows through the
        ``VectorMemory`` singleton (ChromaDB or FTS5 fallback) wired into
        the ``ContextAuthority``.

        This method is retained for backward compatibility but is a no-op;
        ``self.memory`` always reflects the active ``VectorMemory``
        singleton via the ``memory`` property below.
        """
        memory_cfg = self.config.raw.get("memory", {})
        if not memory_cfg.get("enabled", True):
            logger.info("Memory system disabled in config")
            return
        # The canonical memory backend is the VectorMemory singleton,
        # set by app.py at startup.  Nothing to construct here.
        logger.info("Memory system: using VectorMemory singleton (ChromaDB/FTS5)")

    @property
    def memory(self):
        """Return the active VectorMemory singleton (or None if not set).

        This replaces the old orphaned SQLiteMemoryBackend.  Any code that
        referenced ``self.memory`` now transparently uses the canonical
        ChromaDB/FTS5 backend.
        """
        if self._memory_backend is not None:
            return self._memory_backend
        try:
            from kazma_core.agent.tool_registry import get_vector_memory
            return get_vector_memory()
        except Exception:
            return None

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

        Reads from both kazma.yaml (config.raw) AND ConfigStore (SQLite),
        merging the two sources by server name. This ensures servers added
        via the Settings UI (DB) are connected alongside YAML-defined ones.

        Returns:
            Total number of tools registered.
        """
        servers = self.get_mcp_servers_config()
        total = 0
        for server_cfg in servers:
            count = await self.tools.connect_server(server_cfg)
            total += count
            if count > 0:
                logger.info("MCP server '%s': %d tools", server_cfg.get("name"), count)
        return total

    # ------------------------------------------------------------------
    # Service-layer facade (VAL-ARCH-001 / VAL-ARCH-002)
    #
    # The following public methods form a stable API that UI routers and
    # other consumers should use instead of reaching into private
    # attributes (``_running``, ``_servers``, ``_conn``, ``config.raw``,
    # ``llm_config``, etc.).  Every method here delegates to existing
    # internal logic — no behaviour change, only an access-pattern change.
    # ------------------------------------------------------------------

    # ── Running state ───────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """Whether the agent loop is currently active."""
        return self._running

    def set_running(self, running: bool) -> None:
        """Set the agent's running state (start/stop control)."""
        self._running = running

    # ── Tools ───────────────────────────────────────────────────────

    def get_tools_info(self) -> dict[str, Any]:
        """Return tool registry summary for UI consumption.

        Returns a dict with ``count``, ``list`` (up to 20 tool summaries),
        and ``servers`` (number of connected MCP servers).
        """
        tool_defs = self.tools.get_tool_definitions()
        return {
            "count": len(tool_defs),
            "list": [
                {
                    "name": t.get("name", t.get("function", {}).get("name", "?")),
                    "description": t.get(
                        "description", t.get("function", {}).get("description", "")
                    )[:80],
                }
                for t in tool_defs[:20]
            ],
            "servers": len(self.tools.list_servers()),
        }

    # ── MCP server config management ───────────────────────────────

    def get_mcp_servers_config(self) -> list[dict[str, Any]]:
        """Return the MCP server configurations from both YAML and ConfigStore.

        Merges servers from kazma.yaml (config.raw) and ConfigStore (SQLite)
        by server name. YAML servers are checked first, then DB servers that
        aren't already in the YAML list are appended. This ensures both
        config sources contribute to the live server set.
        """
        # YAML servers
        yaml_servers = list(self.config.raw.get("mcp", {}).get("servers", []))
        yaml_names = {s.get("name") for s in yaml_servers}

        # ConfigStore servers (added via Settings UI / mcp_ui)
        try:
            from kazma_core.config_store import get_config_store

            cs = get_config_store()
            db_servers = cs.get("mcp.servers", [])
            if isinstance(db_servers, str):
                import json
                db_servers = json.loads(db_servers)
            if isinstance(db_servers, list):
                for s in db_servers:
                    if isinstance(s, dict) and s.get("name") not in yaml_names:
                        yaml_servers.append(s)
        except Exception as _e:
            logger.debug("[Agent] ConfigStore MCP servers not available, using YAML only: %s", _e)  # fire-and-forget fallback is ok

        return yaml_servers

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        """Return enriched MCP server info (config + connection status + tools).

        This is the public replacement for iterating ``agent.config.raw``
        and calling ``agent.tools.is_server_connected()`` in UI code.
        Reads from the merged YAML + ConfigStore config.
        """
        servers = self.get_mcp_servers_config()
        result: list[dict[str, Any]] = []
        for s in servers:
            name = s.get("name", "unknown")
            is_connected = self.tools.is_server_connected(name)
            tools = []
            if is_connected:
                tools = self.tools.get_mcp_tools_for_server(name)
            result.append(
                {
                    "name": name,
                    "transport": s.get("transport", "stdio"),
                    "command": s.get("command", []),
                    "url": s.get("url", ""),
                    "env": s.get("env", {}),
                    "working_dir": s.get("working_dir"),
                    "status": "running" if is_connected else "stopped",
                    "tool_count": len(tools),
                    "tools": tools,
                }
            )
        return result

    def get_config_section(self, section: str) -> dict[str, Any]:
        """Return a top-level section from the agent config.

        This is the public replacement for ``agent.config.raw.get(section, {})``.
        Returns an empty dict if the section does not exist.
        """
        result = self.config.raw.get(section, {})
        return dict(result) if isinstance(result, dict) else {}

    def add_mcp_server(
        self,
        name: str,
        transport: str = "stdio",
        command: list[str] | None = None,
        url: str = "",
        env: dict[str, str] | None = None,
        working_dir: str | None = None,
    ) -> dict[str, str]:
        """Add an MCP server to the in-memory configuration.

        Returns ``{"status": "ok"}`` on success or
        ``{"status": "error", "error": "..."}`` if a duplicate name exists.
        """
        new_server: dict[str, Any] = {"name": name, "transport": transport}
        if transport == "stdio":
            new_server["command"] = command or []
            if working_dir:
                new_server["working_dir"] = working_dir
        else:
            new_server["url"] = url
        if env:
            new_server["env"] = env

        mcp_section = self.config.raw.setdefault("mcp", {})
        servers_list = mcp_section.setdefault("servers", [])

        for s in servers_list:
            if s.get("name") == name:
                return {"status": "error", "error": f"Server '{name}' already exists"}

        servers_list.append(new_server)
        return {"status": "ok"}

    def remove_mcp_server(self, name: str) -> dict[str, str]:
        """Remove an MCP server from the in-memory configuration.

        Returns ``{"status": "ok"}``. Does NOT raise if the server was absent.
        """
        mcp_section = self.config.raw.get("mcp", {})
        servers = mcp_section.get("servers", [])
        mcp_section["servers"] = [s for s in servers if s.get("name") != name]
        return {"status": "ok"}

    # ── LLM config ─────────────────────────────────────────────────

    def get_llm_config(self) -> dict[str, Any]:
        """Return the LLM configuration as a plain dict.

        This is the public replacement for direct ``agent.llm_config.*``
        access in UI code.
        """
        return {
            "base_url": self.llm_config.base_url,
            "api_key": self.llm_config.api_key,
            "model": self.llm_config.model,
            "max_tokens": self.llm_config.max_tokens,
            "temperature": self.llm_config.temperature,
            "timeout": self.llm_config.timeout,
            "input_cost_per_1m": self.llm_config.input_cost_per_1m,
            "output_cost_per_1m": self.llm_config.output_cost_per_1m,
        }

    async def get_llm_client(self) -> Any:
        """Return the LLM provider's HTTP client for streaming.

        This is the public replacement for ``agent.llm._get_client()``
        access in UI code.
        """
        return await self.llm.get_client()

    # ── Checkpoint summary ─────────────────────────────────────────

    async def get_checkpoint_summary(self) -> dict[str, Any]:
        """Return a summary of checkpointed sessions.

        If the checkpoint graph has not been initialized yet, returns
        an empty summary (``{"sessions": [], "count": 0}``).
        """
        if self._checkpoint_conn is None:
            return {"sessions": [], "count": 0}

        try:
            cursor = await self._checkpoint_conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints LIMIT 100"
            )
            rows = await cursor.fetchall()
            thread_ids = [row[0] for row in rows]
            sessions = [{"thread_id": tid} for tid in thread_ids]
            return {"sessions": sessions, "count": len(sessions)}
        except Exception as e:
            logger.debug("Failed to read checkpoint summary: %s", e)
            return {"sessions": [], "count": 0}

    async def delete_checkpoint_thread(self, thread_id: str) -> bool:
        """Delete all checkpoints for a specific thread.

        Returns True if deletion succeeded, False otherwise.
        """
        if self._checkpoint_conn is None:
            return False
        try:
            await self._checkpoint_conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            )
            await self._checkpoint_conn.commit()
            return True
        except Exception as e:
            logger.debug("Failed to delete checkpoint thread %s: %s", thread_id, e)
            return False

    async def clear_all_checkpoints(self) -> int:
        """Delete ALL checkpointed sessions.

        Returns the number of deleted rows, or -1 on error.
        """
        if self._checkpoint_conn is None:
            return -1
        try:
            cursor = await self._checkpoint_conn.execute(
                "SELECT COUNT(*) FROM checkpoints"
            )
            row = await cursor.fetchone()
            count: int = row[0] if row else 0
            await self._checkpoint_conn.execute("DELETE FROM checkpoints")
            await self._checkpoint_conn.commit()
            return count
        except Exception as e:
            logger.debug("Failed to clear checkpoints: %s", e)
            return -1

    # ── Streaming graph (VAL-ARCH-002) ─────────────────────────────

    def get_streaming_graph(self) -> Any:
        """Return a compiled supervisor graph suitable for SSE streaming.

        This builds (and caches) a graph from the agent's own components
        without a checkpointer, so the SSE path can use ``astream_events``
        without reaching into private graph-builder internals.

        The graph supports ``ainvoke()`` and ``astream_events()``.
        """
        if self._streaming_graph is not None:
            return self._streaming_graph

        from kazma_core.agent.graph_builder import build_supervisor_graph
        from kazma_core.safety.hitl import get_hitl_config

        # Thread the same HITL config as the run path so danger tools
        # interrupt() on the SSE/streaming graph too (VAL-ARCH-002).
        streaming_hitl = get_hitl_config(self.config.raw)
        if not streaming_hitl.get("enabled", True):
            streaming_hitl = None

        self._streaming_graph = build_supervisor_graph(
            llm=self.llm,
            system_prompt=self.system_prompt,
            tool_definitions=self.tools.get_tool_definitions(),
            tool_executor=self.tools,
            cost_breaker=self.cost_breaker,
            authority=self.authority,
            tracer=self.tracer,
            hitl_config=streaming_hitl,
        )
        return self._streaming_graph

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
        await apply_sqlite_pragmas_async(self._checkpoint_conn)
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
            return "عذراً، حدث خطأ تقني. يرجى المحاولة مرة أخرى."

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
            self._streaming_graph = None
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

    # Wire thread_id for durable resume
    if thread_id:
        agent._thread_id = thread_id

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
        except Exception as _e:
            logger.debug("[Agent] shutdown_asyncgens error (harmless on exit): %s", _e)
        loop.close()
    # Skip atexit/threading shutdown noise
    _os._exit(0)
