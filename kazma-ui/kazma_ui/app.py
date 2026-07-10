"""Kazma WebUI — FastAPI app factory.

Creates and configures the FastAPI application with all routers,
WebSocket endpoints, static files, and template engine.
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="typing_extensions")

import os
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

import asyncio
import logging
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse

logger = logging.getLogger(__name__)

# Package paths
_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


class KazmaAppBuilder:
    """Builder class for constructing and configuring the Kazma FastAPI application."""

    def __init__(self, config_path: str | None = None) -> None:
        self.config_path = config_path
        self.config = None
        self.config_store = None
        self.registry = None
        self.agent = None
        self.templates = None
        self.app = None
        self.gateway = None
        self.session_store = None
        self.swarm_manager = None
        self.cron_scheduler = None
        self.cron_store = None
        self._init_errors: list[dict[str, str]] = []
        self._graph_holder: dict[str, Any] = {"graph": None}  # mutable holder so SSE router sees post-startup recompiled graph+checkpointer+HITL (fixes C-3)
        self._checkpointer = None
        self._hitl_state: dict[str, Any] = {}
        self._current_lang = None

    def build(self) -> FastAPI:
        """Execute all phases of application construction and return the FastAPI instance."""
        self._bootstrap_environment()
        self._setup_templates_and_middlewares()
        self._setup_swarm()
        self._setup_gateway_and_bus()
        self._setup_routers()
        self._setup_lifecycle_and_errors()
        return self.app

    def _bootstrap_environment(self) -> None:
        """Initialize configurations, core agent, and model registry."""
        # Setup structured JSON logging if requested
        try:
            from kazma_core.logging_config import setup_logging
            setup_logging()
        except Exception as e:
            logger.warning("[App] Failed to setup logging configurations: %s", e)

        from kazma_core.agent import KazmaAgent, load_config
        from kazma_core.config_store import ConfigStore, set_config_store
        from kazma_core.model_registry import initialize_model_registry, ModelRegistry
        from kazma_core.service_container import get_container

        # Ensure .env is loaded on startup if present
        try:
            from dotenv import load_dotenv
            load_dotenv()
            logger.info("[Auth] Loaded environment variables from .env")
        except Exception as e:
            logger.debug("[Auth] Failed to load .env: %s", e)

        # Ensure KAZMA_SECRET is configured
        import sys
        if "pytest" not in sys.modules:
            _secret = os.environ.get("KAZMA_SECRET", "").strip()
            if not _secret:
                import secrets
                generated = secrets.token_hex(32)
                os.environ["KAZMA_SECRET"] = generated
                
                # Persist to .env if possible
                env_path = Path(".env")
                if env_path.exists():
                    try:
                        content = env_path.read_text(encoding="utf-8")
                        lines = content.splitlines()
                        updated = False
                        for i, line in enumerate(lines):
                            stripped = line.strip()
                            if stripped.startswith("# KAZMA_SECRET=") or stripped.startswith("KAZMA_SECRET="):
                                lines[i] = f"KAZMA_SECRET={generated}"
                                updated = True
                                break
                        if updated:
                            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                            logger.info("[Auth] Auto-generated and updated KAZMA_SECRET in .env file")
                        else:
                            with open(env_path, "a", encoding="utf-8") as f:
                                f.write(f"\nKAZMA_SECRET={generated}\n")
                            logger.info("[Auth] Auto-generated and appended KAZMA_SECRET to .env file")
                    except Exception as e:
                        logger.warning("[Auth] Failed to write auto-generated KAZMA_SECRET to .env: %s", e)
                else:
                    try:
                        env_path.write_text(f"KAZMA_SECRET={generated}\n", encoding="utf-8")
                        logger.info("[Auth] Created .env and persisted auto-generated KAZMA_SECRET")
                    except Exception as e:
                        logger.warning("[Auth] Failed to create .env for auto-generated KAZMA_SECRET: %s", e)

        self.config = load_config(self.config_path)
        self.config_store = ConfigStore()
        
        # Register as process-wide singleton
        set_config_store(self.config_store)
        self.config_store.reconcile_from_yaml()

        # Initialize WorkspaceStore and align active workspace configurations on boot
        try:
            from kazma_core.stores import get_workspace_store
            ws_store = get_workspace_store()
            active_ws = ws_store.get_active_workspace()
            if active_ws:
                self.config_store.set("workspace.selected_path", active_ws["root_path"], category="workspace")
                self.config_store.reload_from_root(active_ws["root_path"])
                logger.info("[App] Aligned ConfigStore with active workspace root: %s", active_ws["root_path"])
        except Exception as e:
            logger.warning("[App] Failed to align active workspace on boot: %s", e)
        
        self.registry = initialize_model_registry(self.config_store)
        self.agent = KazmaAgent(self.config)

        # Configure workspace
        try:
            from kazma_core.tools.file_write import configure_workspace

            _workspace_env = os.environ.get("KAZMA_WORKSPACE", "").strip()
            _workspace_path = _workspace_env or "kazma-data/workspace"
            configure_workspace(workspace=_workspace_path)
            logger.info("[Workspace] Configured to %s", _workspace_path)
        except Exception as e:
            logger.warning("[Workspace] Failed to configure: %s", e)

        # Create FastAPI app
        self.app = FastAPI(
            title="Kazma",
            version=self.config.version,
            description="Autonomous AI Agent Framework — Arabic RTL Dashboard",
        )

        # Register services in Dependency Injection Container
        container = get_container()
        container.register(ConfigStore, self.config_store)
        container.register(ModelRegistry, self.registry)
        container.register(KazmaAgent, self.agent)

    def _setup_templates_and_middlewares(self) -> None:
        """Configure auth, CORS, language middleware, static files, and templates."""
        from kazma_ui.auth import create_auth_middleware, create_tenant_middleware

        self.app.middleware("http")(create_auth_middleware())
        self.app.middleware("http")(create_tenant_middleware())

        # CORS
        from fastapi.middleware.cors import CORSMiddleware

        _default_cors_origins = [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
        _cors_env = os.environ.get("KAZMA_CORS_ORIGINS", "").strip()
        if _cors_env:
            _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
        else:
            _cors_origins = _default_cors_origins

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=_cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
            allow_headers=["Content-Type", "X-Kazma-Secret", "X-Api-Key", "Accept", "X-Tenant-ID"],
        )
        logger.info("[CORS] allow_origins=%s", _cors_origins)

        # Mount static files
        _STATIC_DIR.mkdir(parents=True, exist_ok=True)
        self.app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

        # Setup Jinja2 templates
        _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        self.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

        # Global template context for language/direction
        _lang = self.agent.config.language if hasattr(self.agent.config, "language") else "en"
        _dir = "rtl" if getattr(self.agent.config, "rtl", False) else "ltr"
        self.templates.env.globals["lang"] = _lang
        self.templates.env.globals["dir"] = _dir

        # i18n
        import contextvars
        import json as _json
        from kazma_ui.i18n import make_translator as _make_translator, TRANSLATIONS

        _startup_lang = _lang
        self._current_lang = contextvars.ContextVar("_current_lang", default=_startup_lang)

        def _dynamic_translate(key: str, **kwargs) -> str:
            return _make_translator(self._current_lang.get())(key, **kwargs)

        # Inject the full translation dict as JSON so Alpine.js expressions
        # can call a client-side t() — server-side t() only covers Jinja2.
        _translations_json = _json.dumps(TRANSLATIONS, ensure_ascii=False)
        self.templates.env.globals["t"] = _dynamic_translate
        self.templates.env.globals["lang"] = _startup_lang
        self.templates.env.globals["dir"] = "rtl" if _startup_lang == "ar" else "ltr"
        self.templates.env.globals["translations_json"] = _translations_json

        @self.app.middleware("http")
        async def language_middleware(request: Request, call_next):
            cookie_lang = request.cookies.get("kazma-lang")
            if cookie_lang in ("ar", "en"):
                req_lang = cookie_lang
            else:
                req_lang = _startup_lang
            self._current_lang.set(req_lang)
            self.templates.env.globals["lang"] = req_lang
            self.templates.env.globals["dir"] = "rtl" if req_lang == "ar" else "ltr"
            return await call_next(request)

    def _setup_swarm(self) -> None:
        """Initialize SwarmManager, load persisted workers, and restore paused tasks."""
        from kazma_core.service_container import get_container

        try:
            from kazma_core.swarm import (
                SwarmConfig,
                SwarmManager,
                TaskStore,
                set_swarm_engine,
            )

            swarm_task_store = TaskStore()
            swarm_cfg_path = self.config_path or "kazma.yaml"
            swarm_cfg = SwarmConfig.from_yaml(swarm_cfg_path)
            if swarm_cfg is not None and swarm_cfg.enabled:
                self.swarm_manager = SwarmManager(swarm_cfg, task_store=swarm_task_store)
                logger.info(
                    "[Swarm] SwarmManager initialized from %s — %d worker(s)",
                    swarm_cfg_path,
                    len(self.swarm_manager.worker_names),
                )
            else:
                self.swarm_manager = SwarmManager(
                    SwarmConfig(enabled=True, workers=[]),
                    task_store=swarm_task_store,
                )
                logger.info("[Swarm] SwarmManager initialized (empty — UI-driven mode)")
            set_swarm_engine(self.swarm_manager.engine)

            # Load persisted workers from WorkerRegistry (swarm_registry.json)
            try:
                from kazma_core.swarm.config import WorkerConfig as _WC
                from kazma_core.swarm.registry import get_worker_registry
                from kazma_core.swarm.task import WorkerCapabilities as _Caps

                _reg = get_worker_registry()
                _yaml_count = len(self.swarm_manager.worker_names)
                for entry in _reg.list_all():
                    if self.swarm_manager.engine.get_worker(entry.name) is None if hasattr(self.swarm_manager.engine, "get_worker") else entry.name not in getattr(self.swarm_manager.engine, "_workers", {}):
                        self.swarm_manager.engine.add_worker(
                            _WC(
                                name=entry.name,
                                type=entry.worker_type or "in_process",
                                model=entry.model,
                                provider=entry.provider,
                                role=entry.roles[0] if entry.roles else "",
                                system_prompt=entry.system_prompt,
                                capabilities=_Caps(
                                    role=entry.roles[0] if entry.roles else "",
                                    expertise=entry.expertise,
                                    tools=getattr(entry, "tools", []),
                                ),
                            )
                        )
                _total = len(self.swarm_manager.worker_names)
                if _total > _yaml_count:
                    logger.info(
                        "[Swarm] Loaded %d persisted worker(s) from swarm_registry.json",
                        _total - _yaml_count,
                    )
            except Exception as e:
                logger.warning("[Swarm] Failed to load persisted workers: %s", e)

            try:
                self.swarm_manager.engine.restore_paused_tasks()
                logger.info("[Swarm] Restored paused tasks from TaskStore")
            except Exception as e:
                logger.warning("[Swarm] Failed to restore paused tasks: %s", e)

        except Exception as e:
            logger.warning("[Swarm] SwarmManager not available: %s", e)
            self.swarm_manager = None

        if self.swarm_manager is not None:
            container = get_container()
            container.register(SwarmManager, self.swarm_manager)

    def _setup_gateway_and_bus(self) -> None:
        """Initialize GatewayManager, register adapters, and wire the message bus."""
        from kazma_core.service_container import get_container

        try:
            from kazma_gateway import GatewayManager
            from kazma_gateway.adapters.telegram import TelegramAdapter
            from kazma_gateway.agent_handler import create_graph_handler
            from kazma_gateway.stores import SQLiteSessionStore

            self.gateway = GatewayManager(max_queue_size=100)

            # Resolve Telegram token
            telegram_token = (
                self.config_store.get("connectors.telegram.token", "")
                or self.config.raw.get("connectors", {}).get("telegram", {}).get("token", "")
            )
            if not telegram_token:
                telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

            tg_adapter: TelegramAdapter | None = None
            if telegram_token:
                tg_adapter = TelegramAdapter(token=telegram_token)
                # Set allowed users
                allowed = self.config_store.get("connectors.telegram.allowed_users", "")
                if allowed:
                    try:
                        allowed_ids = [int(uid.strip()) for uid in allowed.split(",") if uid.strip()]
                        tg_adapter.set_allowed_users(allowed_ids)
                        logger.info("[Gateway] Telegram allowed users: %d IDs", len(allowed_ids))
                    except ValueError:
                        logger.warning("[Gateway] Invalid allowed_users format: %s", allowed)
                self.gateway.add_adapter(tg_adapter)
                logger.info("[Gateway] Telegram adapter registered (polling mode)")

                # Webhook ingress
                webhook_router = tg_adapter.create_webhook_router()
                self.app.include_router(webhook_router, prefix="/api/webhooks/telegram")
                logger.info("[Gateway] Webhook ingress mounted at /api/webhooks/telegram")
            else:
                logger.info("[Gateway] No Telegram token — Telegram adapter skipped")

            # Discord adapter
            discord_token = self.config_store.get("connectors.discord.token", "") or os.environ.get("DISCORD_BOT_TOKEN", "")
            if discord_token:
                from kazma_gateway.adapters.discord import DiscordAdapter

                discord_adapter = DiscordAdapter(token=discord_token)
                # User-level allowlist (mirrors Telegram). Stored in ConfigStore
                # as a comma-separated string of Discord user IDs.
                discord_allowed = self.config_store.get("connectors.discord.allowed_users", "")
                if discord_allowed:
                    discord_ids = [uid.strip() for uid in discord_allowed.split(",") if uid.strip()]
                    discord_adapter.set_allowed_users(discord_ids)
                    logger.info("[Gateway] Discord allowed users: %d IDs", len(discord_ids))
                self.gateway.add_adapter(discord_adapter)
                logger.info("[Gateway] Discord adapter registered")
            else:
                logger.info("[Gateway] No DISCORD_BOT_TOKEN — Discord adapter skipped")

            # Slack adapter
            # Slack adapter — resolve tokens from config_store or env.
            # ConfigStore may store masked tokens (e.g. "***3554") from the
            # settings UI, so fall back to env vars when the stored value
            # doesn't look like a real token.
            _cs_slack_bot = self.config_store.get("connectors.slack.token", "")
            _cs_slack_app = self.config_store.get("connectors.slack.app_token", "")
            slack_bot_token = (_cs_slack_bot if _cs_slack_bot.startswith("xoxb-") else "") or os.environ.get("SLACK_BOT_TOKEN", "")
            slack_app_token = (_cs_slack_app if _cs_slack_app.startswith("xapp-") else "") or os.environ.get("SLACK_APP_TOKEN", "")
            if slack_bot_token:
                from kazma_gateway.adapters.slack import SlackAdapter

                # Team/channel allowlists — empty = allow all. Stored in
                # ConfigStore as comma-separated strings. Without these the
                # adapter accepts messages from any team/channel.
                def _split_ids(raw: str) -> list[str]:
                    return [s.strip() for s in raw.split(",") if s.strip()]

                slack_teams = _split_ids(self.config_store.get("connectors.slack.allowed_teams", ""))
                slack_channels = _split_ids(self.config_store.get("connectors.slack.allowed_channels", ""))
                slack_adapter = SlackAdapter(
                    bot_token=slack_bot_token,
                    app_token=slack_app_token or None,
                    allowed_teams=slack_teams or None,
                    allowed_channels=slack_channels or None,
                )
                self.gateway.add_adapter(slack_adapter)
                if slack_app_token:
                    logger.info("[Gateway] Slack adapter registered (Socket Mode)")
                else:
                    logger.info("[Gateway] Slack adapter registered (polling mode — no app token)")
                if slack_teams:
                    logger.info("[Gateway] Slack allowed teams: %d", len(slack_teams))
                if slack_channels:
                    logger.info("[Gateway] Slack allowed channels: %d", len(slack_channels))
            else:
                logger.info("[Gateway] No SLACK_BOT_TOKEN — Slack adapter skipped")

            # Session Store
            self.session_store = SQLiteSessionStore("kazma-data/sessions.db")
            self.gateway.set_persistence(
                session_store=self.session_store,
                session_store_path="kazma-data/sessions.db",
            )

            # Wire legacy dashboard context
            from kazma_ui.dashboard import set_dashboard_context

            set_dashboard_context(
                tracer=self.agent.tracer,
                cost_breaker=self.agent.cost_breaker,
                session_store=self.session_store,
            )

            # Vector Memory (RAG)
            _demo_mode = os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes")
            if _demo_mode:
                logger.info("[VectorMemory] Skipped — KAZMA_DEMO_MODE is set")
            else:
                try:
                    from kazma_core.agent.tool_registry import set_vector_memory
                    from kazma_core.memory.vector_store import VectorMemory

                    vector_memory_path = os.environ.get("KAZMA_VECTOR_PATH", "~/.kazma/vector_memory")
                    vector_memory_collection = os.environ.get("KAZMA_VECTOR_COLLECTION", "agent_memory")
                    vector_memory_model = os.environ.get("KAZMA_VECTOR_MODEL", "all-MiniLM-L6-v2")

                    vector_memory = VectorMemory(
                        path=vector_memory_path,
                        collection_name=vector_memory_collection,
                        model_name=vector_memory_model,
                    )
                    set_vector_memory(vector_memory)
                    logger.info(
                        "[VectorMemory] Initialized at %s (collection=%s, model=%s)",
                        vector_memory_path,
                        vector_memory_collection,
                        vector_memory_model,
                    )
                except Exception as e:
                    logger.debug("[VectorMemory] Not available: %s", e)
                    if not getattr(self.app.state, "_vector_memory_hint_shown", False):
                        logger.info(
                            "[VectorMemory] RAG memory disabled. "
                            "Install the 'rag' extra (pip install -e '.[rag]') to enable."
                        )
                        self.app.state._vector_memory_hint_shown = True

            # Register brain handler
            try:
                initial_graph = self.agent.get_streaming_graph()
                self._graph_holder["graph"] = initial_graph
                if initial_graph is not None:
                    brain_handler = create_graph_handler(
                        graph=initial_graph,
                        manager=self.gateway,
                        system_prompt=self.agent.system_prompt,
                        cost_breaker=self.agent.cost_breaker,
                        store=self.session_store,
                    )
                    self.gateway.on_message(brain_handler)
                    logger.info("[Gateway] Brain handler registered")
                else:
                    logger.warning("[Gateway] No graph available — Brain handler not registered")
            except Exception as e:
                logger.warning("[Gateway] Brain handler failed to register: %s", e)

            # SwarmMessageBus (swarm -> platform outbound)
            if self.swarm_manager is not None:
                try:
                    from kazma_core.swarm.bus import get_message_bus

                    bus = get_message_bus()
                    _bus_wired = False
                    # Never wire real platform adapters under pytest: tests
                    # call create_app() with the real kazma.yaml, which would
                    # wire a live TelegramBusAdapter and cause test dispatches
                    # to send real messages to the operator's chat. NullBusAdapter
                    # (the bus default) keeps swarm events in-process for tests.
                    import sys as _sys
                    _skip_real_adapters = "pytest" in _sys.modules

                    # TelegramBusAdapter
                    if not _skip_real_adapters and tg_adapter is not None and telegram_token:
                        try:
                            from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter

                            tg_bus = TelegramBusAdapter(
                                bot_token=telegram_token,
                                chat_id=self.config_store.get("connectors.telegram.swarm_chat_id", ""),
                            )
                            bus.set_adapter(tg_bus)
                            _bus_wired = True
                            logger.info("[SwarmBus] TelegramBusAdapter wired — swarm events will appear in Telegram")
                        except ImportError:
                            logger.debug("[SwarmBus] TelegramBusAdapter not available")
                        except Exception as e:
                            logger.warning("[SwarmBus] Failed to wire TelegramBusAdapter: %s", e)

                    # DiscordBusAdapter
                    if not _bus_wired:
                        _discord_tok = self.config_store.get("connectors.discord.token", "") or os.environ.get("DISCORD_BOT_TOKEN", "")
                        _discord_chan = self.config_store.get("connectors.discord.swarm_channel_id", "")
                        if not _skip_real_adapters and _discord_tok and _discord_chan:
                            try:
                                from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

                                bus.set_adapter(DiscordBusAdapter(bot_token=_discord_tok, channel_id=_discord_chan))
                                _bus_wired = True
                                logger.info("[SwarmBus] DiscordBusAdapter wired — swarm events will appear in Discord")
                            except ImportError:
                                logger.debug("[SwarmBus] DiscordBusAdapter not available")
                            except Exception as e:
                                logger.warning("[SwarmBus] Failed to wire DiscordBusAdapter: %s", e)

                    # SlackBusAdapter
                    if not _bus_wired:
                        _slack_tok = self.config_store.get("connectors.slack.token", "") or os.environ.get("SLACK_BOT_TOKEN", "")
                        _slack_chan = self.config_store.get("connectors.slack.swarm_channel_id", "")
                        if not _skip_real_adapters and _slack_tok and _slack_chan:
                            try:
                                from kazma_gateway.adapters.slack_bus import SlackBusAdapter

                                bus.set_adapter(SlackBusAdapter(bot_token=_slack_tok, channel_id=_slack_chan))
                                _bus_wired = True
                                logger.info("[SwarmBus] SlackBusAdapter wired — swarm events will appear in Slack")
                            except ImportError:
                                logger.debug("[SwarmBus] SlackBusAdapter not available")
                            except Exception as e:
                                logger.warning("[SwarmBus] Failed to wire SlackBusAdapter: %s", e)

                    if not _bus_wired:
                        logger.info("[SwarmBus] No platform adapter — swarm events stay internal (NullBusAdapter)")
                except Exception as e:
                    logger.warning("[SwarmBus] Failed to initialize message bus: %s", e)

            # Register services in Dependency Injection Container
            container = get_container()
            container.register(GatewayManager, self.gateway)
            container.register(SQLiteSessionStore, self.session_store)

            # ── Sub-Agent Manager ─────────────────────────────────────
            try:
                from kazma_core.agent.sub_agent import SubAgentManager, set_sub_agent_manager

                sub_agent_mgr = SubAgentManager(
                    graph_builder=lambda **kwargs: self.agent.get_streaming_graph(),
                    max_concurrent=3,
                )
                set_sub_agent_manager(sub_agent_mgr)
                logger.info("[SubAgent] Manager initialized (max_concurrent=3)")
            except Exception as e:
                logger.warning("[SubAgent] Manager not available: %s", e)

            # ── Cron Scheduler ────────────────────────────────────────
            try:
                from kazma_core.cron.scheduler import CronScheduler, SQLiteCronStore, set_cron_scheduler

                self.cron_store = SQLiteCronStore("kazma-data/cron.db")
                self.cron_scheduler = CronScheduler(
                    store=self.cron_store,
                    poll_interval=30.0,
                )
                set_cron_scheduler(self.cron_scheduler)
                logger.info("[Cron] Scheduler initialized")
            except Exception as e:
                logger.warning("[Cron] Scheduler not available: %s", e)
                self.cron_store = None

        except Exception as e:
            logger.warning("Gateway failed to initialize: %s", e)
            self._init_errors.append({"subsystem": "gateway", "error": str(e)})

    def _setup_routers(self) -> None:
        """Create and mount FastAPI routers."""
        from kazma_ui.agents import create_agents_router
        from kazma_ui.chat import create_chat_router
        from kazma_ui.mcp_ui import create_mcp_router
        from kazma_ui.providers import create_providers_router
        from kazma_ui.settings import create_settings_router
        from kazma_ui.skills_ui import create_skills_router
        from kazma_ui.health import router as health_router

        chat_router = create_chat_router(self.agent, self.templates)
        settings_router = create_settings_router(self.agent, self.config_store, self.templates)
        skills_router = create_skills_router(self.agent, self.templates)
        mcp_router = create_mcp_router(self.agent, self.templates)
        agents_router = create_agents_router(self.agent, self.templates)
        providers_router = create_providers_router(self.config_store)

        # Health router (no auth, for load balancer probes)
        self.app.include_router(health_router)

        # Mount routers
        self.app.include_router(chat_router)
        self.app.include_router(settings_router)
        self.app.include_router(skills_router)
        self.app.include_router(mcp_router)
        self.app.include_router(agents_router)
        self.app.include_router(providers_router)
        logger.info("Providers & Connectors router mounted at /api/providers, /api/connectors, /api/models/profiles")

        # ── SSE Chat Router ──
        try:
            from kazma_ui.sse_chat import create_sse_chat_router

            sse_router = create_sse_chat_router(
                graph_holder=self._graph_holder,
                graph_getter=lambda: self._graph_holder.get("graph"),
                checkpointer=None,
                system_prompt=self.agent.system_prompt,
                cost_breaker=self.agent.cost_breaker,
                authority=self.agent.authority,
                tracer=self.agent.tracer,
                provider_profile=self.registry.get_active_profile(),
                llm_provider=self.agent.llm,
                registry=self.registry,
            )
            self.app.include_router(sse_router)
            logger.info("SSE chat router mounted at /api/chat/stream")
        except Exception as e:
            logger.warning("SSE chat router failed to initialize: %s", e)
            self._init_errors.append({"subsystem": "sse_chat", "error": str(e)})

        # ── Telemetry SSE Route ──
        try:
            from kazma_core.telemetry import HardwareMonitor
            from kazma_ui.telemetry_route import create_telemetry_router

            hw_monitor = HardwareMonitor()
            telemetry_router = create_telemetry_router(monitor=hw_monitor)
            self.app.include_router(telemetry_router)
            logger.info("Telemetry SSE router mounted at /api/telemetry/stream")
        except Exception as e:
            logger.warning("Telemetry router failed to initialize: %s", e)
            self._init_errors.append({"subsystem": "telemetry", "error": str(e)})

        # Dashboard (legacy)
        from kazma_ui.dashboard import router as dashboard_router
        from kazma_ui.dashboard import set_templates as set_dashboard_templates

        set_dashboard_templates(self.templates)
        self.app.include_router(dashboard_router)

        # ── Models & Ollama Management Router ──
        from kazma_ui.models_route import create_models_router

        models_router = create_models_router(config_store=self.config_store)
        self.app.include_router(models_router)
        logger.info("Models router mounted at /api/models, /api/ollama/*")

        # ── Workspace File Browser API ──
        from kazma_ui.workspace_api import create_workspace_router

        workspace_router = create_workspace_router()
        self.app.include_router(workspace_router)
        logger.info("Workspace API router mounted at /api/workspace/*")

        # ── Swarm Panel ──
        from kazma_ui.swarm_panel import create_swarm_router

        swarm_router = create_swarm_router(
            self.templates,
            swarm_manager=self.swarm_manager,
            config_store=self.config_store,
        )
        self.app.include_router(swarm_router)
        logger.info("[Swarm] Swarm Panel mounted at /api/swarm/*, /swarm")

        # ── Gateway monitor router ──
        if self.gateway is not None:
            from kazma_ui.gateway_monitor import create_gateway_router

            monitor_router = create_gateway_router(
                gateway=self.gateway,
                session_store=self.session_store,
                checkpointer=None,
            )
            self.app.include_router(monitor_router)
            logger.info("[Gateway] Monitor router mounted at /api/gateway/*")

            # Prometheus Metrics Endpoint
            from kazma_ui.metrics import create_metrics_router

            metrics_router = create_metrics_router(gateway=self.gateway, session_store=self.session_store)
            self.app.include_router(metrics_router)
            logger.info("[Metrics] Prometheus /metrics endpoint mounted")

        # Register direct routes
        self._register_direct_routes()

    def _register_direct_routes(self) -> None:
        """Register route handlers directly onto the FastAPI instance."""
        from kazma_ui.routes_direct import register_direct_routes

        register_direct_routes(self)

    async def _on_startup(self) -> None:
        """Application startup: checkpointer, HITL graph, gateway, cron."""
        try:
            from kazma_gateway.stores.checkpoint import create_checkpointer

            self._checkpointer = await create_checkpointer("kazma-data/checkpoints.db")
            logger.info("[Checkpoint] SQLite checkpointer initialized")

            from kazma_ui.dashboard import set_dashboard_context

            set_dashboard_context(checkpoint_manager=self._checkpointer)

            # Always recompile graph with checkpointer + HITL for SSE holder
            from kazma_core.agent.graph_builder import build_supervisor_graph
            from kazma_core.safety.hitl import get_hitl_config

            recompile_hitl = get_hitl_config(self.config.raw)
            if not recompile_hitl.get("enabled", True):
                recompile_hitl = None

            recompiled = build_supervisor_graph(
                llm=self.agent.llm,
                system_prompt=self.agent.system_prompt,
                tool_definitions=self.agent.tools.get_tool_definitions(),
                tool_executor=self.agent.tools,
                cost_breaker=self.agent.cost_breaker,
                authority=self.agent.authority,
                tracer=self.agent.tracer,
                checkpointer=self._checkpointer,
                hitl_config=recompile_hitl,
            )
            self._graph_holder["graph"] = recompiled
            logger.info("[Checkpoint] Graph recompiled with checkpointer")

            self._hitl_state["graph"] = recompiled
            self._hitl_state["checkpointer"] = self._checkpointer
            logger.info("[HITL] Pending approvals endpoint linked to checkpointed graph")

            if self.gateway is not None:
                from kazma_gateway.agent_handler import create_graph_handler

                brain_handler = create_graph_handler(
                    graph=self._graph_holder.get("graph"),
                    manager=self.gateway,
                    system_prompt=self.agent.system_prompt,
                    cost_breaker=self.agent.cost_breaker,
                    store=self.session_store,
                )
                self.gateway.on_message(brain_handler)
                logger.info("[Checkpoint] Brain handler re-registered with checkpointed graph")
        except Exception as e:
            logger.warning("[Checkpoint] Checkpointer not available: %s", e)

        if self.gateway is not None:
            try:
                await self.gateway.start()
                logger.info(
                    "[Gateway] Started — adapters: [%s], queue maxsize=%d",
                    ", ".join(a.name for a in self.gateway.adapters),
                    self.gateway.queue.maxsize,
                )
            except Exception as e:
                logger.warning("[Gateway] Failed to start: %s", e)

        if self.cron_store is not None:
            try:
                await self.cron_store.init()
                from kazma_core.cron.scheduler import get_cron_scheduler

                cron_sched = get_cron_scheduler()
                if cron_sched:
                    await cron_sched.start()
                    logger.info("[Cron] Scheduler started")
            except Exception as e:
                logger.warning("[Cron] Failed to start: %s", e)

    async def _on_shutdown(self) -> None:
        """Application shutdown: HTTP pool + gateway."""
        try:
            from kazma_core.http_pool import close_http_client

            await close_http_client()
        except Exception as e:
            logger.warning("[app] Error closing http client during shutdown: %s", e)

        if self.gateway is None:
            return
        try:
            await self.gateway.stop()
            logger.info("[Gateway] Stopped cleanly")
        except Exception as e:
            logger.warning("[Gateway] Error during shutdown: %s", e)

    def _setup_lifecycle_and_errors(self) -> None:
        """Register lifespan (replaces deprecated on_event) and exception handlers."""
        builder = self

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            await builder._on_startup()
            try:
                yield
            finally:
                await builder._on_shutdown()

        # Attach lifespan after app construction (Starlette/FastAPI)
        self.app.router.lifespan_context = lifespan

        from starlette.exceptions import HTTPException as StarletteHTTPException

        @self.app.exception_handler(StarletteHTTPException)
        async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Any:
            path = request.url.path
            if path.startswith("/api/") or request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                )
            return self.templates.TemplateResponse(
                request,
                "error.html",
                {"code": exc.status_code, "message": exc.detail, "detail": ""},
                status_code=exc.status_code,
            )

        @self.app.exception_handler(Exception)
        async def catch_all(request: Request, exc: Any) -> Any:
            """Unified catch-all — returns JSON for API routes, HTML for pages."""
            # Log full traceback server-side; never expose internals to clients.
            logger.exception("[app] Unhandled exception on %s %s", request.method, request.url.path)
            path = request.url.path
            # API routes get JSON errors; page routes get HTML error page
            if path.startswith("/api/") or request.headers.get("accept", "").startswith("application/json"):
                try:
                    from kazma_core.swarm.middleware import GracefulErrorFallback as _gef
                    return JSONResponse(
                        status_code=500,
                        content=_gef.to_json_error(exc),
                    )
                except Exception as _e:
                    logger.debug("[app] Fallback error handler itself failed: %s", _e)
                    is_prod = os.environ.get("KAZMA_ENV") == "production"
                    return JSONResponse(
                        status_code=500,
                        content={"error": "Internal server error", "detail": "" if is_prod else str(exc)},
                    )
            return self.templates.TemplateResponse(
                request,
                "error.html",
                {"code": 500, "message": "Internal server error", "detail": ""},
                status_code=500,
            )


def create_app(config_path: str | None = None) -> FastAPI:
    """Create and configure the FastAPI application using KazmaAppBuilder."""
    builder = KazmaAppBuilder(config_path)
    return builder.build()


def main() -> None:
    """Entry point for `kazma-web` command.

    Usage:
        kazma-web              # port 8000
        kazma-web --port 8080  # custom port
    """
    import argparse

    parser = argparse.ArgumentParser(description="Kazma Web UI")
    parser.add_argument("--port", "-p", type=int, default=8000, help="Port to bind (default: 8000)")
    args, _ = parser.parse_known_args()

    import os as _os3

    import uvicorn

    # Security: default to localhost.  Use KAZMA_HOST env var to
    # explicitly bind to all interfaces (decoupled from KAZMA_SECRET).
    host = _os3.environ.get("KAZMA_HOST", "127.0.0.1")
    if host == "0.0.0.0" and not _os3.environ.get("KAZMA_SECRET"):
        logger.warning(
            "[app] Binding to 0.0.0.0 without KAZMA_SECRET — "
            "anyone on the network can access the UI. "
            "Set KAZMA_SECRET to enable authentication."
        )

    app = create_app()
    uvicorn.run(app, host=host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
