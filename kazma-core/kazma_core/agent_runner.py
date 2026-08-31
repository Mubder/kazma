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
from typing import Any, Callable, Literal

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

__all__ = ["AgentConfig", "CHECKPOINT_DB", "CONFIG_FILE", "KazmaAgent", "MAX_ITERATIONS", "load_config", "main", "run_agent"]

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
    version: str = "0.10.0"
    language: str = "ar"
    rtl: bool = True
    default_model: str = "gpt-4o-mini"
    storage_path: str = "data/kazma.db"
    vector_dim: int = 384
    system_prompt: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def load_config(config_path: str | Path | None = None) -> AgentConfig:
    """Load configuration from shipped YAML + optional local overrides.

    See :mod:`kazma_core.config_loader` — users should not edit tracked
    ``kazma.yaml`` for day-to-day settings (use Settings UI or
    ``kazma.local.yaml``).
    """
    from kazma_core.config_loader import load_merged_yaml

    path = Path(config_path) if config_path else Path(CONFIG_FILE)
    raw = load_merged_yaml(path if path.exists() or config_path else None)
    if not raw and not path.exists():
        logger.warning("Config file %s not found, using defaults", path)
        return AgentConfig()

    agent_cfg = raw.get("agent", {})
    models_cfg = raw.get("models", {})
    storage_cfg = raw.get("storage", {})

    # Display version is always base+gSHA (kazma_core.version). YAML holds
    # the public base only; never trust a stale static string for banners/API.
    try:
        from kazma_core.version import get_version as _product_version

        _version = _product_version()
    except Exception:
        _version = str(agent_cfg.get("version", "0.10.0")).split("+", 1)[0]

    # Settings-UI overrides (ConfigStore) win over YAML for the agent identity
    # fields — the Settings page persists agent.name / agent.language /
    # agent.system_prompt there. Previously these keys were read back for
    # display only, so UI edits silently never reached the running agent.
    # Applied at agent construction (next boot), mirroring settings.py's
    # ConfigStore-first display fallback.
    try:
        from kazma_core.config_store import get_config_store

        _cs = get_config_store()
        _name = _cs.get("agent.name") or agent_cfg.get("name", "kazma")
        _language = _cs.get("agent.language") or agent_cfg.get("language", "en")
        _system_prompt = _cs.get("agent.system_prompt") or raw.get("system_prompt", "")
    except Exception:
        logger.debug(
            "[AgentRunner] ConfigStore agent-identity read failed — YAML values apply",
            exc_info=True,
        )
        _name = agent_cfg.get("name", "kazma")
        _language = agent_cfg.get("language", "en")
        _system_prompt = raw.get("system_prompt", "")

    return AgentConfig(
        name=_name,
        version=_version,
        # Default to English so cultural Arabic context does not bias every
        # reply when the user has not set agent.language explicitly.
        language=_language,
        rtl=agent_cfg.get("rtl", False),
        default_model=models_cfg.get("default", "gpt-4o-mini"),
        storage_path=storage_cfg.get("path", "data/kazma.db"),
        vector_dim=storage_cfg.get("vector_dim", 384),
        system_prompt=_system_prompt,
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

        # Serializes _ensure_graph so two concurrent first run() calls build
        # the graph once instead of racing (leaked checkpointer connection).
        self._graph_build_lock = asyncio.Lock()

        # Time Travel — snapshot recorder (lazy-init in _ensure_graph).
        self._snapshot_recorder: Any = None

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
        if not isinstance(tracing_cfg, dict):
            tracing_cfg = {}
        from kazma_core.tracing.langfuse_enable import (
            langfuse_keys,
            resolve_langfuse_enabled,
        )

        _lf = tracing_cfg.get("langfuse", {}) if isinstance(tracing_cfg, dict) else {}
        if not isinstance(_lf, dict):
            _lf = {}
        _lf_on = resolve_langfuse_enabled(tracing_cfg)
        _pub, _sec, _host = langfuse_keys(_lf)
        tracing_config = TracingConfig(
            enabled=_lf_on,
            backend="langfuse" if _lf_on else "console",
            otlp_endpoint=tracing_cfg.get("otlp_endpoint", "http://localhost:4317"),
            service_name="kazma-agent",
            sample_rate=1.0,
            langfuse_public_key=_pub or None,
            langfuse_secret_key=_sec or None,
            langfuse_host=_host,
        )
        self.tracer = KazmaTracer(config=tracing_config)

        # Memory System (FTS5/SQLite backend) — initialized *before* the
        # authority so we can pass it as the compaction memory_store.
        self._memory_backend = None
        self._init_memory()

        # Context Authority (80% compaction) — V2 is the single memory stack.
        # Compaction retrieves via V2 recall() directly (memory_store=None),
        # so no V1 adapter / VectorMemory is constructed here.
        _memory_store = None
        logger.info("ContextAuthority wired for V2 recall (memory_store deferred to V2)")

        self.authority: ContextAuthority = create_authority(
            model=self.config.default_model,
            window=self._resolve_context_window(),
            llm_client=self._make_compaction_client(),
            memory_store=_memory_store,
        )
        self.system_prompt = self.config.system_prompt or self._default_system_prompt()

        # ── Product self-knowledge (identity, Arabic name, how-to) ──
        # Always append unless already present so ConfigStore/YAML overrides
        # still get correct branding + capability map.
        try:
            from kazma_core.product_knowledge import (
                build_product_knowledge,
                knowledge_already_present,
            )

            if not knowledge_already_present(self.system_prompt):
                self.system_prompt = (
                    self.system_prompt.rstrip() + "\n\n" + build_product_knowledge()
                )
        except Exception:
            logger.debug("[agent_runner] product knowledge injection skipped", exc_info=True)

        # Inject cultural context enrichment
        try:
            from kazma_core.cultural_context_enrichment import get_cultural_prompt_suffix
            cultural_suffix = get_cultural_prompt_suffix()
            if cultural_suffix and cultural_suffix not in self.system_prompt:
                self.system_prompt = self.system_prompt.rstrip() + cultural_suffix
        except Exception:
            pass

        # ── Environment awareness (IDE/workspace/repo) ──────────────
        # Tell the brain it has an IDE workspace + tools + GitHub. Re-read
        # per turn in build_env_context() via the streaming path; this base
        # injection covers the initial system prompt. See env_context.py.
        try:
            # Sync constructor context (__init__ cannot await): call the
            # blocking builder directly (git probes bounded at 2 × 4s).
            from kazma_core.ide.env_context import _build_env_context_sync

            env_block = _build_env_context_sync()
            if env_block and env_block not in self.system_prompt:
                self.system_prompt = self.system_prompt.rstrip() + "\n\n" + env_block
        except Exception:
            logger.debug("[agent_runner] env context injection skipped", exc_info=True)

        # Agent Skills catalog (agentskills.io progressive disclosure tier 1).
        # Only name+description — full body loads via activate_skill. The catalog
        # descriptions are parsed from untrusted SKILL.md frontmatter, so the
        # whole block is fenced as untrusted data (same defense as env_context /
        # self-improvement). The full body is additionally fenced + integrity-
        # verified at activation (catalog.format_skill_activation).
        try:
            from kazma_core.agent_skills.catalog import build_catalog_prompt
            from kazma_core.safety.prompt_fence import format_untrusted_block

            skills_block = build_catalog_prompt()
            if skills_block and skills_block not in self.system_prompt:
                fenced = format_untrusted_block(skills_block, source="agent_skills_catalog")
                self.system_prompt = self.system_prompt.rstrip() + "\n\n" + fenced
            elif not skills_block:
                # Still teach install path when catalog is empty
                install_hint = (
                    "\n\n## Agent Skills\n"
                    "You can install skills from https://agentskills.io/ using "
                    "`install_agent_skill(source='owner/repo')` "
                    "(e.g. `shadcn/improve`). Do **not** use npx/npm/shell for "
                    "skill installs — use install_agent_skill (one approval)."
                )
                if "install_agent_skill" not in self.system_prompt:
                    self.system_prompt = self.system_prompt.rstrip() + install_hint
        except Exception:
            logger.debug("[agent_runner] agent skills catalog injection skipped", exc_info=True)

        # Self-improvement Soul (Kazma-wide) — accumulated from past chat/swarm
        # outcomes. Also re-injected per turn in SSE (may grow after init).
        # Wrapped in an untrusted data fence so the model treats deltas as
        # observation context, never instructions (prompt-injection defense).
        try:
            from kazma_core.safety.prompt_fence import format_untrusted_block
            from kazma_core.skills.self_improvement import get_agent_evolution_block

            evo = get_agent_evolution_block("supervisor")
            fenced = format_untrusted_block(evo, source="self_improvement")
            if fenced and fenced not in self.system_prompt:
                self.system_prompt = (
                    self.system_prompt.rstrip() + "\n\n" + fenced
                )
        except Exception:
            logger.debug("[agent_runner] agent evolution injection skipped", exc_info=True)

        # Knowledge Library auto-inject (kill-switch + per-library opt-in
        # checked live in the per-turn path; init-time this is a no-op since
        # there is no user message to retrieve against).  See AGENTS.md §11.
        try:
            from kazma_core.safety.prompt_fence import format_untrusted_block
            from kazma_core.stores.knowledge_index import (
                get_knowledge_auto_inject_block_sync,
            )

            kb_block = get_knowledge_auto_inject_block_sync("")
            fenced = format_untrusted_block(kb_block, source="knowledge")
            if fenced and fenced not in self.system_prompt:
                self.system_prompt = (
                    self.system_prompt.rstrip() + "\n\n" + fenced
                )
        except Exception:
            logger.debug("[agent_runner] knowledge auto-inject skipped", exc_info=True)

        # Universal language directive — injected LAST so it's the final
        # instruction the model sees, after all cultural context. This
        # prevents Arabic cultural context from biasing the model to
        # respond in Arabic when the user writes in English.
        _LANG_DIRECTIVE = (
            "\n\nCRITICAL LANGUAGE RULE: Match the language of the user's "
            "*latest* message only. Arabic latest message = Arabic reply. "
            "English latest message = English reply. If they mix, match their "
            "pattern. If they switch mid-session (e.g. Arabic history then English "
            "now), you MUST switch immediately — do not stay on the first language "
            "of the thread. This overrides personality, cultural context, and "
            "prior turns. A LANGUAGE LOCK system message for this turn is absolute."
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

    @property
    def snapshot_recorder(self) -> Any:
        """The active SnapshotRecorder (time travel), or None before first graph build."""
        return self._snapshot_recorder

    @snapshot_recorder.setter
    def snapshot_recorder(self, value: Any) -> None:
        self._snapshot_recorder = value

    def _default_system_prompt(self) -> str:
        return (
            "You are Kazma (Arabic: كاظمه / كاظمة — never كازما), "
            "an autonomous multi-platform AI agent framework. "
            "You are capable of understanding Arabic dialects including Kuwaiti/Gulf Arabic "
            "when the user speaks Arabic, but your default response language is always "
            "determined by the user's input language. "
            "\n\nBe helpful, precise, and culturally aware. Teach users how to use Kazma "
            "(chat, IDE, swarm, settings, HITL, memory) when they ask about the product. "
            "\n\nTOOL SELECTION: Prefer native tools over shell_exec. "
            "Use file_list/file_read/file_search/file_write for files; git_* for git; "
            "python_exec/code_exec for short scripts; install_agent_skill for skills. "
            "shell_exec runs with cwd already set to the active workspace — do not use "
            "cd (blocked). For multi-step shell, use absolute paths under the workspace. "
            "\n\nDOCUMENT GENERATION RULE: For ANY PDF, DOCX, XLSX, or Markdown "
            "artifact, ALWAYS use the dedicated tools (generate_pdf, generate_docx, "
            "generate_xlsx, generate_markdown_doc) — NEVER write Python code with "
            "reportlab/fpdf/openpyxl to build documents manually. For LARGE documents "
            "(>5 sections): (1) write the content to a .md file via file_write, "
            "(2) call generate_pdf with markdown_path=\"<filename>\". This is the ONLY "
            "correct path — python_exec for document generation wastes iterations "
            "and produces inferior output."
            "\n\nITERATION EFFICIENCY: You have a LIMITED number of iterations per "
            "turn. Maximize each one:\n"
            "- BATCH tool calls: issue MULTIPLE reads/writes in ONE response\n"
            "- python_exec is for SHORT utility scripts only (<50 lines) — not for "
            "document generation, file parsing, or data extraction\n"
            "- Do NOT re-read files you already read this turn (they are cached)\n"
            "- If you find yourself writing debug files and re-reading them, STOP "
            "and produce the final output with what you already have"
            "\n\nCRITICAL SYSTEM RULE: If you receive a tool_error or a rejection notification "
            "from a tool execution (e.g. 'SYSTEM OVERRIDE: Tool blocked...'), you MUST IMMEDIATELY "
            "stop issuing further tool calls. Synthesize a final text answer explaining the blockage "
            "or error to the user, and ask for their guidance before continuing."
            "\n\nCRITICAL LANGUAGE RULE: Match the language of the user's *latest* "
            "message only. Arabic latest = Arabic reply; English latest = English reply. "
            "If they mix, match their pattern. Mid-session switches are required "
            "(do not stick to the first language of the thread). Unclear input → English. "
            "A LANGUAGE LOCK system message for this turn is absolute."
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
        # V2 is the single memory stack; the legacy VectorMemory singleton is
        # gone. This property is retained for backward-compat but returns the
        # explicit backend only (None unless one was injected at construction).
        return self._memory_backend

    def _resolve_context_window(self) -> int:
        """Resolve the context window from Settings (ConfigStore) → yaml → model table → default.

        The Settings UI writes to ``context.max_context_tokens`` in ConfigStore;
        the yaml has ``memory.max_context_tokens``. Check both so the Settings
        UI value actually takes effect (previously only the yaml was read).

        Model-aware layer (audit §2.6): when no *explicit* non-default value
        is configured (the shipped default is 128000), the known context
        window of the active model wins (e.g. Claude → 200k, GPT-4 → 8k),
        so compaction fires at the model's real 80% instead of a global
        constant. Per-model override: ConfigStore ``models.context_window.<model>``.
        """
        try:
            from kazma_core.token_counter import resolve_context_window

            try:
                from kazma_core.model_registry import get_model_registry

                active_model = get_model_registry().active_model or self.config.default_model
            except Exception:
                active_model = self.config.default_model
            return resolve_context_window(self.config.raw, active_model)
        except Exception:
            pass
        return 128_000

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
            if not server_cfg.get("enabled", True):
                logger.info(
                    "MCP server '%s' disabled in configuration; skipping startup",
                    server_cfg.get("name", "unnamed"),
                )
                continue
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

    def _mcp_yaml_path(self) -> str:
        """Resolve the shipped kazma.yaml path for dual-write persistence."""
        return str(getattr(self.config, "config_path", None) or CONFIG_FILE)

    def get_mcp_servers_config(self) -> list[dict[str, Any]]:
        """Return MCP server configs from the unified dual store.

        Merges ``kazma.yaml`` + agent ``config.raw`` + ConfigStore via
        :mod:`kazma_core.mcp_servers_store` (ConfigStore wins on name
        conflict). Both the ``/mcp`` page and Settings use this SoT.
        """
        from kazma_core.mcp_servers_store import list_mcp_servers

        yaml_servers = list(self.config.raw.get("mcp", {}).get("servers", []))
        return list_mcp_servers(
            yaml_servers=yaml_servers,
            yaml_path=self._mcp_yaml_path(),
        )

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        """Return enriched MCP server info (config + connection status + tools).

        This is the public replacement for iterating ``agent.config.raw``
        and calling ``agent.tools.is_server_connected()`` in UI code.
        Reads from the unified YAML + ConfigStore SoT.
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
        auth: dict[str, str] | None = None,
        trust: Literal["trusted", "approval_required", "sandboxed"] = "approval_required",
    ) -> dict[str, str]:
        """Add an MCP server and dual-write ConfigStore + kazma.yaml.

        Returns ``{"status": "ok"}`` on success or
        ``{"status": "error", "error": "..."}`` if a duplicate name exists
        or persistence fails.
        """
        from kazma_core.mcp_servers_store import list_mcp_servers, upsert_mcp_server

        # Duplicate check against the merged view (not just config.raw).
        existing = list_mcp_servers(
            yaml_servers=self.config.raw.get("mcp", {}).get("servers", []),
            yaml_path=self._mcp_yaml_path(),
        )
        if any(s.get("name") == name for s in existing):
            return {"status": "error", "error": f"Server '{name}' already exists"}

        new_server: dict[str, Any] = {"name": name, "transport": transport}
        if transport == "stdio":
            new_server["command"] = command or []
            if working_dir:
                new_server["working_dir"] = working_dir
        else:
            new_server["url"] = url
        if env:
            new_server["env"] = env
        if auth:
            new_server["auth"] = auth
        new_server["trust"] = trust

        try:
            upsert_mcp_server(
                new_server,
                config_raw=self.config.raw,
                yaml_path=self._mcp_yaml_path(),
                replace=False,
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:
            logger.warning("[MCP] dual-write failed for add: %s", exc)
            return {"status": "error", "error": f"Persist failed: {exc}"}
        return {"status": "ok"}

    def remove_mcp_server(self, name: str) -> dict[str, str]:
        """Remove an MCP server from ConfigStore + yaml + config.raw.

        Returns ``{"status": "ok"}`` on success or
        ``{"status": "error", "error": "..."}`` if persistence fails. Does
        NOT raise if the server was absent (idempotent removal).
        """
        from kazma_core.mcp_servers_store import delete_mcp_server

        try:
            delete_mcp_server(
                name,
                config_raw=self.config.raw,
                yaml_path=self._mcp_yaml_path(),
            )
        except Exception as exc:
            logger.warning("[MCP] dual-write failed for remove: %s", exc)
            return {"status": "error", "error": f"Removed in-memory but not persisted: {exc}"}
        return {"status": "ok"}

    def _persist_mcp_servers(self) -> str | None:
        """Write the current ``mcp.servers`` list to ConfigStore + kazma.yaml.

        Kept for callers that mutate ``config.raw`` then flush. Prefer
        :meth:`add_mcp_server` / :meth:`remove_mcp_server` which dual-write
        via :mod:`kazma_core.mcp_servers_store`.
        """
        from kazma_core.mcp_servers_store import sync_mcp_servers

        servers = list(self.config.raw.get("mcp", {}).get("servers", []))
        try:
            return sync_mcp_servers(
                servers,
                config_raw=self.config.raw,
                yaml_path=self._mcp_yaml_path(),
            )
        except Exception as exc:
            logger.warning("[MCP] dual persist failed: %s", exc)
            return str(exc)

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
        without reaching into private graph-builder internals. Because the
        graph is checkpointer-less, its HITL config carries ``auto_deny`` —
        an interrupt() pause here could never be resumed (audit F2/H1).

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
        else:
            # The streaming graph is cached WITHOUT a checkpointer: this
            # method is sync (called from sync app-builder closures), so it
            # cannot await the AsyncSqliteSaver construction that
            # _ensure_graph performs. Its live consumers — the voice WS
            # getter and the boot-window _graph_holder (replaced post-startup
            # by app.py's checkpointed recompile) — therefore can never
            # resume an interrupt() pause via /api/approve. Force auto_deny
            # (audit F2/H1): danger tools deny with a clear message instead
            # of minting an unresumable interrupt that kills the turn.
            streaming_hitl = {**streaming_hitl, "auto_deny": True}

        # Ensure the snapshot recorder exists BEFORE building/caching the
        # streaming graph. app.py calls get_streaming_graph() at startup
        # (before the post-startup recorder-creation block runs), which
        # previously cached a graph built with snapshot_recorder=None — so
        # the voice/WS path never captured Time-Travel snapshots for the
        # process lifetime (audit finding / AGENTS.md §12A). Mirror
        # _ensure_graph's lazy-creation block.
        if self._snapshot_recorder is None:
            try:
                from kazma_core.time_travel import create_recorder
                self._snapshot_recorder = create_recorder(config=self.config.raw)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Snapshot recorder unavailable: %s", exc)

        self._streaming_graph = build_supervisor_graph(
            llm=self.llm,
            system_prompt=self.system_prompt,
            tool_definitions=self.tools.get_tool_definitions(),
            tool_executor=self.tools,
            cost_breaker=self.cost_breaker,
            authority=self.authority,
            tracer=self.tracer,
            hitl_config=streaming_hitl,
            snapshot_recorder=self._snapshot_recorder,
        )
        return self._streaming_graph

    def build_child_graph(
        self,
        *,
        tools: list[str] | None = None,
        hitl_config: dict[str, Any] | None = None,
        checkpointer: Any | None = None,
    ) -> Any:
        """Build a one-shot supervisor graph for sub-agents (audit M19).

        Not cached — each spawn can carry auto-deny HITL and tool filters.

        Args:
            tools: Optional tool-name allowlist filter.
            hitl_config: Optional HITL config; defaults to the live
                ``get_hitl_config()``.
            checkpointer: Optional checkpointer. Child graphs default to
                ``checkpointer=None`` (one-shot), which means LangGraph
                ``interrupt()`` can never persist a pause — so a
                checkpointer-less build forces ``auto_deny`` into the HITL
                config (audit H1): danger tools deny with a clear message
                instead of minting an unresumable interrupt that silently
                kills the turn.
        """
        from kazma_core.agent.graph_builder import build_supervisor_graph
        from kazma_core.safety.hitl import get_hitl_config

        tool_defs = list(self.tools.get_tool_definitions())
        if tools:
            allow = {str(t).lower() for t in tools}
            tool_defs = [
                d for d in tool_defs
                if str(d.get("function", d).get("name", d.get("name", ""))).lower()
                in allow
                or str(d.get("name", "")).lower() in allow
            ]

        hitl = hitl_config if hitl_config is not None else get_hitl_config(self.config.raw)
        if isinstance(hitl, dict) and not hitl.get("enabled", True):
            hitl = None

        # Checkpointer-less children can never resume an interrupt() pause
        # (audit H1 — cron/sub-agent child graphs died silently on danger
        # tools). Force auto_deny so tool_worker_node denies directly.
        # Callers that pass a checkpointer keep the resumable gate.
        if checkpointer is None and isinstance(hitl, dict):
            hitl = {**hitl, "auto_deny": True}

        return build_supervisor_graph(
            llm=self.llm,
            system_prompt=self.system_prompt,
            tool_definitions=tool_defs,
            tool_executor=self.tools,
            cost_breaker=self.cost_breaker,
            authority=self.authority,
            tracer=self.tracer,
            hitl_config=hitl,
            checkpointer=checkpointer,
            snapshot_recorder=self._snapshot_recorder,
        )

    async def _ensure_graph(self) -> Any:
        """Build (once) the LangGraph supervisor graph this agent runs on.

        The graph is wired from the agent's own already-constructed components
        (LLM, tool registry, cost breaker, context authority, tracer) and a
        durable AsyncSqliteSaver checkpointer, so run() executes the real
        supervisor StateGraph instead of a separate hand-rolled loop.
        """
        if self._graph is not None:
            return self._graph

        async with self._graph_build_lock:
            # Double-checked: a concurrent caller may have built it while we
            # waited for the lock.
            if self._graph is not None:
                return self._graph

            from kazma_core.agent.graph_builder import build_supervisor_graph

            # Durable checkpointer — Postgres when multi-replica, else SQLite.
            db_path = self.config.raw.get("storage", {}).get("checkpoint_path", CHECKPOINT_DB)
            # Close any stale checkpointer left behind by a model switch:
            # sync_active_model() nulls _graph without closing, so each
            # rebuild used to leak the aiosqlite connection / PG pool for
            # the process lifetime.
            await self._close_checkpointer()
            try:
                from kazma_core.db.backend import get_database_url, is_postgres

                if is_postgres():
                    dsn = get_database_url() or ""
                    if dsn.startswith("postgres://"):
                        dsn = "postgresql://" + dsn[len("postgres://") :]
                    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore
                    from psycopg_pool import AsyncConnectionPool  # type: ignore

                    from psycopg.rows import dict_row  # type: ignore

                    pool = AsyncConnectionPool(
                        conninfo=dsn,
                        min_size=1,
                        max_size=8,
                        kwargs={
                            "autocommit": True,
                            "prepare_threshold": 0,
                            "row_factory": dict_row,
                        },
                        open=False,
                    )
                    await pool.open()
                    self._checkpointer = AsyncPostgresSaver(conn=pool)  # type: ignore[arg-type]
                    await self._checkpointer.setup()
                    logger.info("KazmaAgent checkpointer: AsyncPostgresSaver")
            except Exception as exc:
                logger.warning(
                    "Postgres checkpointer unavailable (%s) — SQLite fallback",
                    exc,
                    exc_info=True,
                )

            if self._checkpointer is None:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
                self._checkpoint_conn = await aiosqlite.connect(db_path)
                await apply_sqlite_pragmas_async(self._checkpoint_conn)
                self._checkpointer = AsyncSqliteSaver(self._checkpoint_conn)
                await self._checkpointer.setup()
                logger.info("KazmaAgent checkpointer: AsyncSqliteSaver path=%s", db_path)

            # Prefer get_hitl_config() so ConfigStore / Settings UI overrides
            # apply on the run path the same way as the streaming graph.
            from kazma_core.safety.hitl import get_hitl_config
            hitl_config = get_hitl_config(self.config.raw)

            # Time Travel — create the snapshot recorder once (honors kazma.yaml
            # time_travel.enabled / max_snapshots / db_path).
            if self._snapshot_recorder is None:
                try:
                    from kazma_core.time_travel import create_recorder
                    self._snapshot_recorder = create_recorder(config=self.config.raw)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Snapshot recorder unavailable: %s", exc)

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
                snapshot_recorder=self._snapshot_recorder,
            )
            logger.info("KazmaAgent run path bound to supervisor graph")
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

        # Turn correlation id — tags every log record emitted during this
        # turn (and surfaces in the SSE done payload on streaming paths).
        from kazma_core.observability.correlation import (
            bind_turn_id,
            new_turn_id,
            reset_turn_id,
        )

        _turn_token = bind_turn_id(new_turn_id())

        # Cost breaker gate (kept here so a halted session short-circuits before
        # building/invoking the graph).
        if self.cost_breaker.should_halt():
            reset_turn_id(_turn_token)
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
        try:
            from kazma_core.agent.long_task import resolve_turn_budgets

            _run_recursion = int(
                resolve_turn_budgets(self._thread_id)["recursion_limit"]
            )
        except Exception:
            _run_recursion = 100
        config = {
            "configurable": {"thread_id": self._thread_id},
            "recursion_limit": _run_recursion,
        }

        from kazma_core.safety.hitl import set_current_thread_id, reset_current_thread_id

        token = set_current_thread_id(self._thread_id)
        try:
            import os as _os_to

            # Wall-clock turn budget (audit M14). Override: KAZMA_TURN_TIMEOUT_SECONDS
            raw_to = (_os_to.environ.get("KAZMA_TURN_TIMEOUT_SECONDS") or "600").strip()
            try:
                turn_timeout = float(raw_to)
            except ValueError:
                turn_timeout = 600.0

            # Non-stop self-healing envelope (opt-in): heartbeat stall
            # detection + checkpoint rollback + reflection + bounded resume.
            # With the master toggle off, the path below is unchanged.
            try:
                from kazma_core.agent.nonstop import get_nonstop_config

                _ns_cfg = get_nonstop_config(self.config.raw)
            except Exception:
                _ns_cfg = None

            if _ns_cfg is not None and _ns_cfg.enabled:
                from kazma_core.agent.supervisor_watchdog import (
                    reset_heartbeat,
                    supervised_invoke,
                )

                try:
                    result = await supervised_invoke(
                        graph,
                        graph_state,
                        config,
                        nonstop_config=_ns_cfg,
                        turn_timeout=turn_timeout,
                    )
                finally:
                    reset_heartbeat(self._thread_id)
            elif turn_timeout <= 0:
                result = await graph.ainvoke(graph_state, config)
            else:
                import asyncio as _asyncio

                try:
                    result = await _asyncio.wait_for(
                        graph.ainvoke(graph_state, config),
                        timeout=turn_timeout,
                    )
                except _asyncio.TimeoutError:
                    logger.error(
                        "Graph turn timed out after %.0fs (thread=%s)",
                        turn_timeout,
                        self._thread_id,
                    )
                    return (
                        f"⚠️ Turn timed out after {int(turn_timeout)}s. "
                        "Try a shorter request or raise KAZMA_TURN_TIMEOUT_SECONDS."
                    )
        except Exception as e:
            logger.error("Graph invocation failed: %s", e, exc_info=True)
            # Classified, actionable message (same helper the graph path
            # uses) — the old generic Arabic string hid the failure class.
            try:
                from kazma_core.retry import friendly_llm_error

                return friendly_llm_error(e)
            except Exception:
                return "⚠️ حدث خطأ تقني أثناء تنفيذ الطلب. يرجى المحاولة مرة أخرى."
        finally:
            reset_current_thread_id(token)
            reset_turn_id(_turn_token)

        # Extract the final assistant message text.
        final_messages = result.get("messages", [])
        for msg in reversed(final_messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
                return str(msg["content"])

        logger.warning("Graph produced no assistant response")
        return ""

    def set_on_model_change_callback(self, cb: Callable[[], None]) -> None:
        """Register a callback to run when the active model is synced/switched."""
        self._on_model_change_cb = cb

    def sync_active_model(self) -> None:
        """Re-fetch active LLM client from ModelRegistry and reset cached graphs."""
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()
        self.llm = registry.get_client()
        self.llm_config = self.llm.config
        self._graph = None
        self._streaming_graph = None
        # Drop cached failover clients so a provider reconfigure/removal takes
        # effect immediately — previously the stale client (base_url/creds)
        # was reused for failover until process restart (audit finding).
        try:
            from kazma_core.agent import graph_builder as _gb

            _gb._failover_clients.clear()
            _gb._failover_cooldowns.clear()
        except Exception:
            pass
        _synced_model = (
            getattr(getattr(self.llm, "config", None), "model", None)
            or getattr(self.llm, "model", None)
            or "unknown"
        )
        logger.info("[KazmaAgent] Active model synced to %s", _synced_model)
        if getattr(self, "_on_model_change_cb", None) is not None:
            try:
                self._on_model_change_cb()
            except Exception as cb_exc:
                logger.warning("[KazmaAgent] Model change callback failed: %s", cb_exc)

    async def _close_checkpointer(self) -> None:
        """Close the current checkpointer's resources (SQLite conn / PG pool).

        Only safe once no live graph references it (self._graph is None).
        Idempotent.
        """
        if self._checkpoint_conn is not None:
            try:
                await self._checkpoint_conn.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("Error closing stale checkpointer connection: %s", e)
            self._checkpoint_conn = None
        elif self._checkpointer is not None:
            # Postgres path: the saver wraps an AsyncConnectionPool.
            pool = getattr(self._checkpointer, "conn", None)
            aclose = getattr(pool, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception as e:  # noqa: BLE001
                    logger.debug("Error closing stale PG checkpointer pool: %s", e)
        self._checkpointer = None

    async def shutdown(self) -> None:
        """Clean shutdown of the agent."""
        self._running = False
        await self.tools.disconnect_all()
        await self.llm.close()
        self.tracer.shutdown()
        # Time Travel — close the snapshot recorder's SQLite handle.
        if self._snapshot_recorder is not None:
            try:
                self._snapshot_recorder.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("Error closing snapshot recorder: %s", e)
            self._snapshot_recorder = None
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
    # Initialise logging before any subsystem boots so every log line lands
    # in <repo>/.kazma/kazma.log. Idempotent + safe; --debug via env.
    try:
        from kazma_core.logging_config import setup_logging

        setup_logging()
    except Exception:
        pass  # Logging must never block boot.

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
