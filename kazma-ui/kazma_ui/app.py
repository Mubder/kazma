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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse

logger = logging.getLogger(__name__)

__all__ = ["KazmaAppBuilder", "create_app", "main"]

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
        self._documents = None
        self._documents_maintenance = None

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
        # Ensure .env is loaded FIRST, before any subsystem that may need
        # env-derived config (notably KAZMA_DB_BACKEND / KAZMA_DATABASE_URL,
        # which the Postgres pool resolver reads). Load the CWD's .env
        # explicitly with override=True — load_dotenv()'s default search
        # walks up from the kazma package location (possibly a different
        # editable-install repo) and would load the WRONG .env; and override
        # is required so stale empty shell values don't shadow real ones.
        try:
            from dotenv import load_dotenv
            from pathlib import Path

            # Priority ladder: KAZMA_WORKSPACE / default user workspace .env -> CWD .env
            user_env = Path(os.environ.get("KAZMA_WORKSPACE", "C:/Users/balfa/kazma")) / ".env"
            cwd_env = Path.cwd() / ".env"

            # Load CWD env first, then override with user workspace env if present
            if cwd_env.exists():
                load_dotenv(dotenv_path=cwd_env, override=True)
            if user_env.exists() and user_env.resolve() != cwd_env.resolve():
                load_dotenv(dotenv_path=user_env, override=True)

            logger.info("[Auth] Loaded environment variables from .env")
        except Exception as e:
            logger.debug("[Auth] Failed to load .env: %s", e)


        # Setup structured JSON logging if requested (now env-aware).
        try:
            from kazma_core.logging_config import setup_logging
            setup_logging()
        except Exception as e:
            logger.warning("[App] Failed to setup logging configurations: %s", e)

        from kazma_core.agent import KazmaAgent, load_config
        from kazma_core.config_store import ConfigStore, set_config_store
        from kazma_core.model_registry import initialize_model_registry, ModelRegistry
        from kazma_core.service_container import get_container

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

            # Ensure KAZMA_VAULT_KEY is configured (for the encrypted secret vault)
            _vault_key = os.environ.get("KAZMA_VAULT_KEY", "").strip()
            _prod = (os.environ.get("KAZMA_PRODUCTION") or "").strip().lower() in (
                "1", "true", "on", "yes",
            )
            if not _vault_key and _prod:
                # Production must not invent vault keys silently (audit M5 / 3.4)
                logger.error(
                    "[Vault] KAZMA_PRODUCTION=1 requires KAZMA_VAULT_KEY — "
                    "set a Fernet key before starting (see .env.example)"
                )
                raise RuntimeError(
                    "KAZMA_PRODUCTION=1 requires KAZMA_VAULT_KEY to be set. "
                    "Generate one: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if not _vault_key:
                import secrets as _sec2
                _vault_generated = _sec2.token_hex(32)
                os.environ["KAZMA_VAULT_KEY"] = _vault_generated

                env_path = Path(".env")
                if env_path.exists():
                    try:
                        content = env_path.read_text(encoding="utf-8")
                        lines = content.splitlines()
                        updated = False
                        for i, line in enumerate(lines):
                            stripped = line.strip()
                            if stripped.startswith("# KAZMA_VAULT_KEY=") or stripped.startswith("KAZMA_VAULT_KEY="):
                                lines[i] = f"KAZMA_VAULT_KEY={_vault_generated}"
                                updated = True
                                break
                        if updated:
                            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                            logger.info("[Vault] Auto-generated and updated KAZMA_VAULT_KEY in .env file")
                        else:
                            with open(env_path, "a", encoding="utf-8") as f:
                                f.write(f"\nKAZMA_VAULT_KEY={_vault_generated}\n")
                            logger.info("[Vault] Auto-generated and appended KAZMA_VAULT_KEY to .env file")
                    except Exception as e:
                        logger.warning("[Vault] Failed to write KAZMA_VAULT_KEY to .env: %s", e)
                else:
                    try:
                        env_path.write_text(f"KAZMA_VAULT_KEY={_vault_generated}\n", encoding="utf-8")
                        logger.info("[Vault] Created .env and persisted auto-generated KAZMA_VAULT_KEY")
                    except Exception as e:
                        logger.warning("[Vault] Failed to create .env for KAZMA_VAULT_KEY: %s", e)

        self.config = load_config(self.config_path)
        # Process-wide singleton — never construct ConfigStore() elsewhere.
        from kazma_core.config_store import get_config_store
        self.config_store = get_config_store()
        
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

        # ── Env-var override: KAZMA_PROVIDER / KAZMA_MODEL / *_API_KEY ──
        # Lets cloud deployments (Fly.io, Docker, etc.) configure the LLM
        # without needing a pre-seeded ConfigStore or settings UI access.
        _env_provider = os.environ.get("KAZMA_PROVIDER", "").strip()
        if _env_provider:
            from kazma_core.providers import get_preset
            preset = get_preset(_env_provider)
            base_url = preset.get("base_url", "") if preset else ""
            # Resolve API key from provider-specific env var or generic
            _env_key = (
                os.environ.get(f"{_env_provider.upper()}_API_KEY", "")
                or os.environ.get("KAZMA_API_KEY", "")
                or os.environ.get("OPENAI_API_KEY", "")
            ).strip()
            _env_model = os.environ.get("KAZMA_MODEL", "").strip()
            if base_url and _env_key:
                self.config_store.batch_set([
                    ("llm.base_url", base_url, "llm"),
                    ("llm.api_key", _env_key, "llm"),
                ] + ([("llm.model", _env_model, "llm")] if _env_model else []))
                self.registry._active_provider = _env_provider
                self.registry._active_model = _env_model or "gpt-4o-mini"
                self.registry._clients.clear()
                logger.info(
                    "[App] Env-var override: provider=%s model=%s base_url=%s",
                    _env_provider, _env_model or "(default)", base_url,
                )

        # ── Pre-warm the V2 embedder BEFORE agent creation ──────────────
        # V2 recall() uses the shared embedder (memory/embedder.py) for dense
        # retrieval. Warming it at boot avoids a first-turn stall on the
        # MiniLM download/load. The legacy VectorMemory / 4-layer adapter /
        # integrity backfill were removed with the V1 stack.
        _demo_mode = os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes")
        if _demo_mode:
            logger.info("[Memory] Skipped embedder pre-warm — KAZMA_DEMO_MODE is set")
        else:
            try:
                from kazma_core.memory.embedder import get_embedder

                emb = get_embedder()
                if emb is not None:
                    vec = emb.encode("kazma memory warmup")
                    logger.info(
                        "[Memory] V2 embedder ready (%s, dim=%s, sample=%d)",
                        type(emb).__name__,
                        getattr(emb, "dim", "?"),
                        len(vec or []),
                    )
                else:
                    logger.warning(
                        "[Memory] V2 embedder is None — dense recall will degrade to FTS5. "
                        "Install the rag extra: pip install -e '.[rag]'"
                    )
            except Exception as e:
                logger.warning("[Memory] V2 embedder pre-warm failed: %s", e)

        self.agent = KazmaAgent(self.config)

        # Configure workspace
        try:
            from kazma_core.tools.file_write import configure_workspace

            _workspace_env = os.environ.get("KAZMA_WORKSPACE", "").strip()
            # Prefer the WorkspaceStore's active workspace (the real repo the
            # user selected) over the kazma-data/workspace default. Without
            # this, the boot-time configure_workspace() pins _WORKSPACE_ROOT
            # to the default and the file tools reject every real repo file
            # as "outside workspace".
            _workspace_path = _workspace_env
            if not _workspace_path:
                try:
                    from kazma_core.stores import get_workspace_store

                    active = get_workspace_store().get_active_workspace()
                    if active and active.get("root_path"):
                        _workspace_path = active["root_path"]
                except Exception:
                    pass
            if not _workspace_path:
                try:
                    from kazma_core.workspace.binding import default_sandbox_root

                    _workspace_path = str(default_sandbox_root())
                except Exception:
                    _workspace_path = "kazma-data/workspace"
            configure_workspace(workspace=_workspace_path)
            # Fire binding bus so MCP (if already connected later) shares the root
            try:
                from kazma_core.workspace.binding import notify_root_changed

                notify_root_changed(_workspace_path, reason="app_boot")
            except Exception:
                pass
            logger.info("[Workspace] Configured to %s", _workspace_path)
        except Exception as e:
            logger.warning("[Workspace] Failed to configure: %s", e)

        # Create FastAPI app. In production, disable the auto-generated docs
        # and OpenAPI schema: (1) the schema currently throws 500 under
        # `from __future__ import annotations` (PEP 563 ForwardRef on bare
        # `Response` return types — a known FastAPI/Pydantic-v2 interaction),
        # and (2) the API surface should not be internet-exposed. Dev/loopback
        # builds keep them. Docs/redoc are never mounted in prod.
        _prod = (os.environ.get("KAZMA_PRODUCTION") or "").strip().lower() in (
            "1", "true", "on", "yes",
        )
        self.app = FastAPI(
            title="Kazma",
            version=self.config.version,
            description="Autonomous AI Agent Framework — Arabic RTL Dashboard",
            docs_url=None if _prod else "/docs",
            redoc_url=None if _prod else "/redoc",
            openapi_url=None if _prod else "/openapi.json",
        )

        # Register services in Dependency Injection Container
        container = get_container()
        container.register(ConfigStore, self.config_store)
        container.register(ModelRegistry, self.registry)
        container.register(KazmaAgent, self.agent)

    def _setup_templates_and_middlewares(self) -> None:
        """Configure auth, CORS, CSRF, language middleware, static files, and templates."""
        from kazma_ui.auth import create_auth_middleware, create_tenant_middleware
        from kazma_ui.csrf import create_csrf_middleware
        from kazma_ui.replica_affinity import create_replica_affinity_middleware

        self.app.middleware("http")(create_auth_middleware())
        self.app.middleware("http")(create_tenant_middleware())
        # Cross-origin mutation guard (CSRF) — see kazma_ui/csrf.py.
        self.app.middleware("http")(create_csrf_middleware())
        # Sticky-session cookie for multi-replica LB (SSE / in-process state)
        self.app.middleware("http")(create_replica_affinity_middleware())

        # CORS
        from fastapi.middleware.cors import CORSMiddleware

        _default_cors_origins = [
            "http://localhost:9090",
            "http://127.0.0.1:9090",
            "http://localhost:9091",
            "http://127.0.0.1:9091",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:4321",
            "http://localhost:4322",
            "https://kazma.ai",
            "https://www.kazma.ai",
        ]
        _cors_env = os.environ.get("KAZMA_CORS_ORIGINS", "").strip()
        if _cors_env:
            _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
        else:
            _cors_origins = _default_cors_origins
        if "*" in _cors_origins:
            logger.warning(
                "[CORS] rejecting wildcard origin with credentials; "
                "using default loopback origins"
            )
            _cors_origins = [o for o in _cors_origins if o != "*"] or list(
                _default_cors_origins
            )

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

        # Browsers always request /favicon.ico (ignores <link rel="icon"> alone)
        _favicon = _STATIC_DIR / "img" / "favicon.png"
        if not _favicon.is_file():
            _favicon = _STATIC_DIR / "img" / "kazma-icon.png"

        @self.app.get("/favicon.ico", include_in_schema=False)
        async def _favicon_ico() -> FileResponse:
            if not _favicon.is_file():
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="favicon not found")
            return FileResponse(
                path=str(_favicon),
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400"},
            )

        # RFC 9116 security.txt — prefer repo root .well-known/, fall back to
        # package-local copy so installs without a git checkout still work.
        # _PACKAGE_DIR = …/kazma-ui/kazma_ui → parents[2] = monorepo root.
        _repo_security_txt = _PACKAGE_DIR.parents[2] / ".well-known" / "security.txt"
        _pkg_security_txt = _PACKAGE_DIR / "well_known" / "security.txt"
        _security_txt = (
            _repo_security_txt
            if _repo_security_txt.is_file()
            else _pkg_security_txt
        )

        async def _security_txt_response() -> FileResponse:
            if not _security_txt.is_file():
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="security.txt not found")
            return FileResponse(
                path=str(_security_txt),
                media_type="text/plain; charset=utf-8",
                headers={"Cache-Control": "public, max-age=3600"},
            )

        @self.app.get("/.well-known/security.txt", include_in_schema=False)
        async def _well_known_security_txt() -> FileResponse:
            return await _security_txt_response()

        @self.app.get("/security.txt", include_in_schema=False)
        async def _root_security_txt() -> FileResponse:
            return await _security_txt_response()

        # Setup Jinja2 templates (auto_reload=True so template edits
        # are picked up without restarting — essential for development).
        _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        import jinja2 as _jinja2
        _tpl_env = _jinja2.Environment(
            loader=_jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=_jinja2.select_autoescape(),
            auto_reload=True,
        )
        self.templates = Jinja2Templates(env=_tpl_env)

        # Global template context for language/direction
        _lang = self.agent.config.language if hasattr(self.agent.config, "language") else "en"

        # i18n
        import contextvars
        import json as _json
        from kazma_ui.i18n import make_translator as _make_translator, TRANSLATIONS

        _startup_lang = _lang
        self._current_lang = contextvars.ContextVar("_current_lang", default=_startup_lang)

        def _dynamic_translate(key: str, **kwargs) -> str:
            return _make_translator(self._current_lang.get())(key, **kwargs)

        # `lang`/`dir` are exposed as callables backed by the request-scoped
        # `_current_lang` contextvar (like `t()` above) rather than plain
        # dict values on the shared Jinja2 Environment. `env.globals` is one
        # object shared by every request; mutating it per-request created a
        # race where a concurrent request could render with another
        # request's language/direction.
        def _dynamic_lang() -> str:
            return self._current_lang.get()

        def _dynamic_dir() -> str:
            return "rtl" if self._current_lang.get() == "ar" else "ltr"

        # SSR the user's stored appearance theme so the very first paint
        # already matches their choice on every device (no dark flash, and
        # no dependence on browser localStorage / device preference). The
        # frontend makes this same value authoritative after JS boot.
        def _dynamic_theme() -> str:
            # Read via the same SettingsManager path as /api/settings/appearance
            # so SSR and the API always agree (stored choice wins; else default).
            # 'auto' is resolved to a concrete light/dark here so SSR always
            # emits a single data-theme value (the Phase-0 iOS canvas fix needs
            # a concrete color-scheme, never 'auto'). SSR can't read the OS
            # preference, so 'auto' defaults to dark (the :root canvas default);
            # the client re-resolves it from matchMedia on boot
            # (settings.js previewTheme/_resolveAutoTheme) and corrects if needed.
            try:
                from kazma_core.settings_manager import SettingsManager
                t = SettingsManager(self.config_store).get_appearance().get("theme")
                if t in ("light", "dark"):
                    return t
                if t == "auto":
                    return "dark"
                return "light"
            except Exception:
                return "light"

        # Inject the full translation dict as JSON so Alpine.js expressions
        # can call a client-side t() — server-side t() only covers Jinja2.
        _translations_json = _json.dumps(TRANSLATIONS, ensure_ascii=False)
        self.templates.env.globals["t"] = _dynamic_translate
        self.templates.env.globals["lang"] = _dynamic_lang
        self.templates.env.globals["dir"] = _dynamic_dir
        self.templates.env.globals["theme"] = _dynamic_theme
        self.templates.env.globals["translations_json"] = _translations_json

        # Cache-busting versions for static assets. Derived from file mtimes
        # so every edit forces browsers to reload (otherwise a stale cached
        # kazma.css / app.js keeps old soft-nav bugs alive). Computed PER
        # REQUEST so edits take effect without restarting the server.
        _css_files = (
            _STATIC_DIR / "css" / "kazma.css",
            _STATIC_DIR / "css" / "kazma.v5.css",
        )
        _js_version_files = (
            _STATIC_DIR / "js" / "app.js",
            _STATIC_DIR / "js" / "modules" / "nav.js",
            _STATIC_DIR / "js" / "modules" / "stores.js",
            _STATIC_DIR / "js" / "modules" / "components.js",
            _STATIC_DIR / "js" / "modules" / "util.js",
            _STATIC_DIR / "js" / "icons.js",
            # Chat/HITL transport — must bust cache when YOLO/stream fixes land
            _STATIC_DIR / "js" / "chat.js",
            _STATIC_DIR / "js" / "streaming.js",
            _STATIC_DIR / "js" / "stores" / "agentStore.js",
            _STATIC_DIR / "js" / "hitl_approval.js",
            # Settings page scripts — must bust cache or the Embedder tab etc.
            # runs stale JS against fresh HTML (empty status cards symptom).
            _STATIC_DIR / "js" / "settings.js",
            _STATIC_DIR / "js" / "providers.js",
            _STATIC_DIR / "js" / "models.js",
            # MCP lifecycle controls run in a standalone page script; include
            # it so a pulled UI never keeps a stale Start/Test handler cached.
            _STATIC_DIR / "js" / "mcp.js",
            # Memory page — graph ops / cut-hub UI lives here; omit = stale canvas JS
            _STATIC_DIR / "js" / "memory.js",
            _STATIC_DIR / "js" / "memory_console.js",
            # Swarm Workflow Editor DAG renderer (vendored, no CDN)
            _STATIC_DIR / "js" / "mermaid.min.js",
        )

        def _css_version() -> int:
            latest = 1
            for path in _css_files:
                try:
                    latest = max(latest, int(os.path.getmtime(path)))
                except Exception:
                    pass
            return latest

        def _js_version() -> int:
            latest = 1
            for path in _js_version_files:
                try:
                    latest = max(latest, int(os.path.getmtime(path)))
                except Exception:
                    pass
            return latest

        self.templates.env.globals["css_version"] = _css_version
        self.templates.env.globals["js_version"] = _js_version

        # NOTE: no browser WS token is injected anymore. The per-session WS
        # token was embedded as a <meta> tag + ?token= query param, which
        # leaked the bearer token into page source, proxy/access logs, browser
        # history, and referrer headers. The browser now authenticates the WS
        # via the same-origin kazma-session cookie (sent automatically) —
        # plus loopback trust for localhost. Programmatic WS clients can still
        # pass ?token= or the secret headers; the server-side acceptance of
        # those was left unchanged (audit finding MED #6).

        # Startup warning: DEV_WS_BYPASS is a security backdoor
        if os.environ.get("KAZMA_DEV_WS_BYPASS", "").strip().lower() in ("1", "true", "yes", "on"):
            logger.warning(
                "[SECURITY] KAZMA_DEV_WS_BYPASS is set — all WebSocket auth is "
                "DISABLED. This is a security risk. Remove it from .env for "
                "production deployments."
            )

        @self.app.middleware("http")
        async def language_middleware(request: Request, call_next):
            cookie_lang = request.cookies.get("kazma-lang")
            if cookie_lang in ("ar", "en"):
                req_lang = cookie_lang
            else:
                req_lang = _startup_lang
            self._current_lang.set(req_lang)
            return await call_next(request)

        @self.app.middleware("http")
        async def html_no_cache_middleware(request: Request, call_next):
            """Force HTML pages to always revalidate.

            Without this, mobile Safari heuristically caches page HTML and
            serves a STALE copy after a deploy — e.g. the old <head> without
            the inline critical theme <style>, which re-introduces the iOS
            dark-canvas flash on the first tab tap after an update ("first
            tap shows dark, next tap shows light"). Static assets stay
            cacheable (CSS/JS carry a ?v= mtime cache-buster)."""
            response = await call_next(request)
            if "text/html" in response.headers.get("content-type", ""):
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

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

            # Orphan recovery: tasks left in 'running' state by a crashed/
            # killed process would otherwise be stranded forever (audit §2.1).
            # Requeue them (bounded by metadata.recovery_count) so long-
            # horizon swarm work survives restarts.
            try:
                store = self.swarm_manager.engine.task_store
                if store is not None:
                    recovery = store.requeue_orphaned_running()
                    if recovery["requeued"] or recovery["failed"]:
                        logger.info(
                            "[Swarm] Orphan recovery: %d requeued, %d terminally failed",
                            len(recovery["requeued"]),
                            len(recovery["failed"]),
                        )
            except Exception as e:
                logger.warning("[Swarm] Orphan task recovery failed: %s", e)

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
            # connectors.<platform>.enabled is authoritative (audit N1):
            # previously only token presence gated the adapter, so the YAML
            # `enabled: false` flag was dead config.
            tg_enabled = bool(
                self.config.raw.get("connectors", {}).get("telegram", {}).get("enabled", True)
            )
            if not tg_enabled:
                logger.info("[Gateway] Telegram disabled via connectors.telegram.enabled — skipped")
            elif telegram_token:
                voice_cfg = self.config.raw.get("gateway", {}).get("voice", {})
                webhook_secret = (
                    self.config_store.get("connectors.telegram.webhook_secret", "")
                    or os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
                    or ""
                )
                tg_adapter = TelegramAdapter(
                    token=telegram_token,
                    voice_enabled=voice_cfg.get("enabled", False),
                    voice_provider=voice_cfg.get("stt_provider", "openai"),
                    stt_api_key=None,  # reads from env vars
                    tts_provider=voice_cfg.get("tts_provider", "edgetts"),
                    tts_voice=voice_cfg.get("tts_voice", "default"),
                    tts_output_format=voice_cfg.get("tts_output_format", "mp3"),
                    stt_language=voice_cfg.get("stt_language", "auto"),
                    webhook_secret=webhook_secret or None,
                )
                # Set allowed users (backward compat: empty = allow_all for existing installs)
                allowed = self.config_store.get("connectors.telegram.allowed_users", "")
                tg_adapter._allow_all = True  # backward compat: existing single-operator installs
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
            discord_enabled = bool(
                self.config.raw.get("connectors", {}).get("discord", {}).get("enabled", True)
            )
            if not discord_enabled:
                logger.info("[Gateway] Discord disabled via connectors.discord.enabled — skipped")
            elif discord_token:
                from kazma_gateway.adapters.discord import DiscordAdapter

                discord_adapter = DiscordAdapter(token=discord_token)
                discord_adapter._allow_all = True  # backward compat
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
            slack_enabled = bool(
                self.config.raw.get("connectors", {}).get("slack", {}).get("enabled", True)
            )
            if not slack_enabled:
                logger.info("[Gateway] Slack disabled via connectors.slack.enabled — skipped")
            elif slack_bot_token:
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
                    allow_all=True,  # backward compat: existing installs
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

            # Rate feedback (from gateway.rate_limits YAML config)
            rate_limits_cfg = self.config.raw.get("gateway", {}).get("rate_limits", {})
            if rate_limits_cfg:
                try:
                    from kazma_gateway.rate_feedback import RateFeedbackManager

                    # Build per-platform active limits dictionary
                    _active_limits_dict = {}
                    if tg_adapter is not None and "telegram" in rate_limits_cfg:
                        _active_limits_dict["telegram"] = int(rate_limits_cfg["telegram"])
                    if discord_token and "discord" in rate_limits_cfg:
                        _active_limits_dict["discord"] = int(rate_limits_cfg["discord"])
                    if slack_bot_token and "slack" in rate_limits_cfg:
                        _active_limits_dict["slack"] = int(rate_limits_cfg["slack"])
                    if _active_limits_dict:
                        rfm = RateFeedbackManager(
                            limit=_active_limits_dict,
                            window_seconds=60,
                            cooldown_seconds=30,
                        )
                        self.gateway.set_rate_feedback(rfm)
                        logger.info(
                            "[Gateway] Rate feedback wired (limits=%s, cooldown=30s)",
                            _active_limits_dict,
                        )
                except Exception as e:
                    logger.warning("[Gateway] Rate feedback wiring failed: %s", e)

            # Suggestions (from gateway.suggestions.enabled YAML config)
            try:
                from kazma_gateway.suggestions import suggestions_from_config

                suggester = suggestions_from_config(self.config.raw)
                self.gateway.set_suggester(suggester)
                logger.info(
                    "[Gateway] Suggestions wired (enabled=%s)",
                    suggester.enabled,
                )
            except Exception as e:
                logger.warning("[Gateway] Suggestions wiring failed: %s", e)

            # Register brain handler (live graph_getter so model switches apply)
            try:
                initial_graph = self.agent.get_streaming_graph()
                self._graph_holder["graph"] = initial_graph
                if initial_graph is not None:
                    brain_handler = create_graph_handler(
                        graph=initial_graph,
                        graph_getter=lambda: self._graph_holder.get("graph"),
                        manager=self.gateway,
                        system_prompt=self.agent.system_prompt,
                        cost_breaker=self.agent.cost_breaker,
                        store=self.session_store,
                    )
                    self.gateway.on_message(brain_handler)
                    logger.info("[Gateway] Brain handler registered (live graph_getter)")
                else:
                    logger.warning("[Gateway] No graph available — Brain handler not registered")
            except Exception as e:
                logger.warning("[Gateway] Brain handler failed to register: %s", e)

            # SwarmMessageBus (swarm -> platform outbound)
            if self.swarm_manager is not None:
                try:
                    from kazma_core.swarm.bus import get_message_bus

                    bus = get_message_bus()
                    # Never wire real platform adapters under pytest: tests
                    # call create_app() with the real kazma.yaml, which would
                    # wire a live TelegramBusAdapter and cause test dispatches
                    # to send real messages to the operator's chat. NullBusAdapter
                    # (the bus default) keeps swarm events in-process for tests.
                    import sys as _sys
                    _skip_real_adapters = "pytest" in _sys.modules
                    _wired_adapters: list[Any] = []

                    # Collect every available platform bus (fan-out, not exclusive).
                    if not _skip_real_adapters and tg_adapter is not None and telegram_token:
                        try:
                            from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter

                            _wired_adapters.append(
                                TelegramBusAdapter(
                                    bot_token=telegram_token,
                                    chat_id=self.config_store.get(
                                        "connectors.telegram.swarm_chat_id", ""
                                    ),
                                )
                            )
                            logger.info(
                                "[SwarmBus] TelegramBusAdapter ready"
                            )
                        except ImportError:
                            logger.debug("[SwarmBus] TelegramBusAdapter not available")
                        except Exception as e:
                            logger.warning("[SwarmBus] Failed to build TelegramBusAdapter: %s", e)

                    _discord_tok = self.config_store.get("connectors.discord.token", "") or os.environ.get("DISCORD_BOT_TOKEN", "")
                    _discord_chan = self.config_store.get("connectors.discord.swarm_channel_id", "")
                    if not _skip_real_adapters and _discord_tok and _discord_chan:
                        try:
                            from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

                            _wired_adapters.append(
                                DiscordBusAdapter(
                                    bot_token=_discord_tok, channel_id=_discord_chan
                                )
                            )
                            logger.info("[SwarmBus] DiscordBusAdapter ready")
                        except ImportError:
                            logger.debug("[SwarmBus] DiscordBusAdapter not available")
                        except Exception as e:
                            logger.warning("[SwarmBus] Failed to build DiscordBusAdapter: %s", e)

                    _slack_tok = self.config_store.get("connectors.slack.token", "") or os.environ.get("SLACK_BOT_TOKEN", "")
                    _slack_chan = self.config_store.get("connectors.slack.swarm_channel_id", "")
                    if not _skip_real_adapters and _slack_tok and _slack_chan:
                        try:
                            from kazma_gateway.adapters.slack_bus import SlackBusAdapter

                            _wired_adapters.append(
                                SlackBusAdapter(
                                    bot_token=_slack_tok, channel_id=_slack_chan
                                )
                            )
                            logger.info("[SwarmBus] SlackBusAdapter ready")
                        except ImportError:
                            logger.debug("[SwarmBus] SlackBusAdapter not available")
                        except Exception as e:
                            logger.warning("[SwarmBus] Failed to build SlackBusAdapter: %s", e)

                    if len(_wired_adapters) == 1:
                        bus.set_adapter(_wired_adapters[0])
                        logger.info(
                            "[SwarmBus] Single adapter wired: %s",
                            type(_wired_adapters[0]).__name__,
                        )
                    elif len(_wired_adapters) > 1:
                        from kazma_core.swarm.bus import FanOutBusAdapter

                        bus.set_adapter(FanOutBusAdapter(_wired_adapters))
                        logger.info(
                            "[SwarmBus] FanOutBusAdapter wired with %d platforms: %s",
                            len(_wired_adapters),
                            ", ".join(type(a).__name__ for a in _wired_adapters),
                        )
                    else:
                        logger.info(
                            "[SwarmBus] No platform adapter — swarm events stay internal (NullBusAdapter)"
                        )
                except Exception as e:
                    logger.warning("[SwarmBus] Failed to initialize message bus: %s", e)

            # Register services in Dependency Injection Container
            container = get_container()
            container.register(GatewayManager, self.gateway)
            container.register(SQLiteSessionStore, self.session_store)

            # ── Sub-Agent Manager ─────────────────────────────────────
            try:
                from kazma_core.agent.sub_agent import SubAgentManager, set_sub_agent_manager

                def _sub_graph_builder(**kwargs: Any) -> Any:
                    """Honor hitl_config + tools (audit M19)."""
                    agent = self.agent
                    if agent is not None and hasattr(agent, "build_child_graph"):
                        return agent.build_child_graph(
                            tools=kwargs.get("tools"),
                            hitl_config=kwargs.get("hitl_config"),
                        )
                    return agent.get_streaming_graph()

                sub_agent_mgr = SubAgentManager(
                    graph_builder=_sub_graph_builder,
                    max_concurrent=3,
                )
                set_sub_agent_manager(sub_agent_mgr)
                logger.info("[SubAgent] Manager initialized (max_concurrent=3)")
            except Exception as e:
                logger.warning("[SubAgent] Manager not available: %s", e)

            # ── Cron Scheduler ────────────────────────────────────────
            try:
                from kazma_core.cron.scheduler import CronScheduler, SQLiteCronStore, set_cron_scheduler

                # Graph builder for scheduled-job execution. Uses the agent's
                # one-shot child graph (checkpointer=None) — correct for a
                # fire-and-deliver cron job. Mirrors the _sub_graph_builder
                # closure above. Without this, _execute() raises
                # "No graph builder configured" the moment a job fires.
                def _cron_graph_builder(**_kwargs: Any) -> Any:
                    agent = self.agent
                    if agent is not None and hasattr(agent, "build_child_graph"):
                        return agent.build_child_graph()
                    return agent.get_streaming_graph()

                self.cron_store = SQLiteCronStore("kazma-data/cron.db")
                self.cron_scheduler = CronScheduler(
                    store=self.cron_store,
                    graph_builder=_cron_graph_builder,
                    poll_interval=30.0,
                )
                set_cron_scheduler(self.cron_scheduler)
                logger.info("[Cron] Scheduler initialized (graph_builder wired)")
            except Exception as e:
                logger.warning("[Cron] Scheduler not available: %s", e)
                self.cron_store = None

        except Exception as e:
            # Log the full type + repr + traceback. A bare Exception with an
            # empty message (e.g. a swallowed cryptography InvalidTag raised
            # deep in vault.retrieve during connector token decryption) used
            # to log "Gateway failed to initialize: " with NO clue what went
            # wrong — making diagnosis impossible. Include the exception type
            # so the real cause is always visible.
            logger.warning(
                "Gateway failed to initialize: %s: %s", type(e).__name__, e,
                exc_info=True,
            )
            self._init_errors.append({
                "subsystem": "gateway",
                "error": f"{type(e).__name__}: {e}",
            })

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
        try:
            from kazma_ui.saas_api import create_saas_router

            self.app.include_router(create_saas_router())
            logger.info("[SaaS] multi-user / tenant API mounted at /api/saas")
        except Exception as e:
            logger.warning("[SaaS] router not available: %s", e)
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
                # Live getters — never freeze agent.llm at mount time (model switch
                # replaces the client instance; mount snapshots become orphans).
                llm_provider=self.agent.llm,
                llm_provider_getter=lambda: self.agent.llm,
                agent_getter=lambda: self.agent,
                registry=self.registry,
            )
            self.app.include_router(sse_router)
            logger.info("SSE chat router mounted at /api/chat/stream")

            # ── WebSocket Chat Gateway ──
            from kazma_ui.routes.ws_chat import create_ws_chat_router
            ws_router = create_ws_chat_router(
                graph_holder=self._graph_holder,
                graph_getter=lambda: self._graph_holder.get("graph"),
                agent_getter=lambda: self.agent,
            )
            self.app.include_router(ws_router)
            logger.info("WebSocket chat gateway router mounted at /ws/chat/{session_id}")
        except Exception as e:
            logger.warning("SSE/WS chat router failed to initialize: %s", e)
            self._init_errors.append({"subsystem": "sse_chat", "error": str(e)})

        # ── Chat attachment upload ──
        try:
            from kazma_ui.routes_chat_upload import router as chat_upload_router

            self.app.include_router(chat_upload_router)
            logger.info("Chat upload router mounted at /api/chat/upload")
        except Exception as e:
            logger.warning("Chat upload router failed to initialize: %s", e)
            self._init_errors.append({"subsystem": "chat_upload", "error": str(e)})

        # ── Voice API Route ──
        try:
            from kazma_ui.routes_voice import router as voice_router

            self.app.include_router(voice_router)
            logger.info("Voice API router mounted at /api/voice")
        except Exception as e:
            logger.warning("Voice API router failed to initialize: %s", e)

        # ── Voice Streaming WebSocket ──
        try:
            from kazma_ui.routes_voice_ws import handle_voice_websocket

            async def _ws_voice(websocket: WebSocket) -> None:
                from kazma_ui.auth import websocket_is_authenticated

                if not websocket_is_authenticated(websocket):
                    await websocket.close(code=4003, reason="Unauthorized")
                    return

                def _voice_graph_getter() -> Any:
                    agent = self.agent
                    if agent is not None:
                        return agent.get_streaming_graph()
                    return None

                await handle_voice_websocket(
                    websocket, graph_getter=_voice_graph_getter,
                )

            self.app.websocket("/ws/voice")(_ws_voice)
            logger.info("Voice streaming WebSocket mounted at /ws/voice")
        except Exception as e:
            logger.warning("Voice WebSocket failed to initialize: %s", e)

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

        # ── IDE API (delegates to transport-agnostic IdeService) ──
        from kazma_ui.ide_api import create_ide_router

        ide_router = create_ide_router()
        self.app.include_router(ide_router)
        logger.info("IDE API router mounted at /api/ide/*")

        # ── Knowledge Base API (delegates to KnowledgeStore / Index) ──
        # Optional: guarded so a missing optional dep (chromadb, etc.) doesn't
        # break the whole app — the page still renders, just empty.
        try:
            from kazma_ui.kb_api import create_kb_router

            kb_router = create_kb_router()
            self.app.include_router(kb_router)
            logger.info("Knowledge Base API router mounted at /api/kb/*")
        except Exception as e:
            logger.warning("Knowledge Base API router failed to mount: %s", e)
            self._init_errors.append({"subsystem": "kb_api", "error": str(e)})

        # ── Memory admin UI + API (beliefs / entities / merge / hygiene) ──
        try:
            from kazma_ui.memory_api import mount_memory_api, register_memory_page

            mount_memory_api(self.app)
            register_memory_page(self.app, self.templates, self.agent)
            logger.info("[Memory] Admin UI routes mounted at /memory")
        except Exception as e:
            logger.warning("[App] Memory admin UI failed to mount: %s", e)
            self._init_errors.append({"subsystem": "memory_api", "error": str(e)})

        # ── Email integration (status + Microsoft device OAuth) ──
        # Open router (GET / status / OAuth callbacks) + protected router
        # (state-mutating POSTs guarded by Origin + X-Requested-With check).
        try:
            from kazma_ui.email_api import protected_router as email_protected
            from kazma_ui.email_api import router as email_router

            self.app.include_router(email_router)
            self.app.include_router(email_protected)
            logger.info("Email API router mounted at /api/email/*")
        except Exception as e:
            logger.warning("Email API router failed to mount: %s", e)
            self._init_errors.append({"subsystem": "email_api", "error": str(e)})

        # ── Documents API (shared DocumentIngestionService) ──
        # The router delegates to app.state.documents (wired in _on_startup).
        # Mounted unconditionally so the /documents page always has an API;
        # returns 503 until the coordinator is live.
        try:
            from kazma_ui.documents_api import create_documents_router

            self.app.include_router(create_documents_router())
            logger.info("[Documents] API router mounted at /api/documents/*")
        except Exception as e:
            logger.warning("[Documents] API router failed to mount: %s", e)
            self._init_errors.append({"subsystem": "documents_api", "error": str(e)})

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
        # ── Early shutdown signal hooks ───────────────────────────────
        # Install BEFORE subsystems come up so a Ctrl+C (while a long-lived
        # SSE/WS stream is open, or during boot) flips the global shutdown
        # flag at signal time. Streams that check is_shutting_down() then
        # self-close inside uvicorn's graceful window, instead of being
        # hard-cancelled with a noisy CancelledError traceback.
        try:
            from kazma_core.shutdown import install_shutdown_signal_hooks

            install_shutdown_signal_hooks()
        except Exception as e:  # noqa: BLE001
            logger.debug("[app] shutdown signal hooks not installed: %s", e)

        # ── Lifecycle status notification: "starting" ────────────────
        # Emitted before any subsystem comes up. Pairs with the "started"
        # message at the end of this method — if you see "starting" but no
        # "started" in chat, the boot hung or crashed mid-way. Best-effort:
        # a failure here (or no platform bus configured) never blocks boot.
        try:
            from kazma_core.lifecycle_notifier import notify_lifecycle

            await notify_lifecycle("starting")
        except Exception as e:  # noqa: BLE001
            logger.debug("[App] lifecycle 'starting' notification failed: %s", e)

        try:
            # ── Arm deferred checkpoint timeouts ────────────────────────
            # _setup_swarm() (sync, constructor) restores paused HITL
            # pipeline tasks but can't asyncio.create_task their auto-reject
            # timeouts (no event loop yet). Arm them now that the loop is up
            # so a paused task never hangs forever after a restart.
            try:
                if getattr(self, "swarm_manager", None) and self.swarm_manager.engine:
                    _armed = await self.swarm_manager.engine.arm_pending_checkpoint_timeouts()
                    if _armed:
                        logger.info("[Swarm] Armed %d deferred checkpoint timeout(s)", _armed)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[Swarm] deferred checkpoint arming failed: %s", exc)

            # ── Connect MCP servers ────────────────────────────────────
            # The CLI path (agent_runner.run_once/main) calls
            # connect_mcp_servers() explicitly, but the web path was
            # creating the agent in _setup_services() and never connecting
            # its MCP servers — so the filesystem MCP (and any other
            # kazma.yaml mcp.servers entry) stayed dormant on the web UI.
            # Connect here, at the start of the async lifespan, so MCP
            # tools are available before the first chat turn.
            try:
                mcp_tool_count = await self.agent.connect_mcp_servers()
                if mcp_tool_count > 0:
                    logger.info("[App] Connected %d MCP tool(s) from kazma.yaml", mcp_tool_count)
                else:
                    logger.info("[App] No MCP tools connected (no servers configured or all failed)")
            except Exception as exc:
                logger.warning("[App] MCP server connection failed at startup: %s", exc)

            from kazma_gateway.stores.checkpoint import create_checkpointer

            self._checkpointer = await create_checkpointer("kazma-data/checkpoints.db")
            logger.info("[Checkpoint] SQLite checkpointer initialized")

            # ── Postgres schema assurance ─────────────────────────────
            # A second app was once pointed at the shared `kazma` DB and its
            # migration dropped Kazma's tables mid-flight (the 2026-08-14
            # UndefinedTable incident). Verify the required PG tables exist
            # at boot; if not, log a CRITICAL with the exact restore command
            # instead of limping along with runtime UndefinedTable errors.
            # Best-effort and fail-open: a verification problem must never
            # block boot (SQLite-side features keep working).
            try:
                from kazma_core.db.pg_backup import (
                    KAZMA_PG_TABLES,
                    latest_pg_backup,
                    pg_backup_enabled,
                    verify_required_pg_tables,
                )

                if pg_backup_enabled():
                    from kazma_core.db.postgres_pool import get_postgres_pool

                    _pool = get_postgres_pool()
                    if _pool is not None:
                        import asyncio as _aio

                        _missing = await _aio.to_thread(verify_required_pg_tables, _pool)
                        if _missing is None:
                            # Pool connection raced startup — retry once, then
                            # report UNKNOWN. Never log a green "OK" for None:
                            # None means "couldn't check", not "all present".
                            await _aio.sleep(2.0)
                            _missing = await _aio.to_thread(verify_required_pg_tables, _pool)
                        if _missing is None:
                            logger.warning(
                                "[PG-BACKUP] schema verification skipped (pool unavailable) — "
                                "could not confirm the required Postgres tables"
                            )
                        elif _missing:
                            _backup = latest_pg_backup()
                            _hint = (
                                f"Restore the latest backup with: "
                                f"python scripts/pg_backup.py restore --latest"
                                if _backup
                                else "No pg_backup dump exists yet — restore from your "
                                "migration bundle, then run: python scripts/pg_backup.py backup"
                            )
                            logger.critical(
                                "[PG-BACKUP] REQUIRED POSTGRES TABLES MISSING: %s. "
                                "Chat history / settings / document jobs are broken until restored. %s",
                                ", ".join(_missing),
                                _hint,
                            )
                        else:
                            logger.info(
                                "[PG-BACKUP] schema verification OK (all %d tables present)",
                                len(KAZMA_PG_TABLES),
                            )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[PG-BACKUP] boot schema verification failed: %s", exc)

            from kazma_ui.dashboard import set_dashboard_context

            set_dashboard_context(checkpoint_manager=self._checkpointer)

            # Always recompile graph with checkpointer + HITL for SSE holder
            from kazma_core.agent.graph_builder import build_supervisor_graph
            from kazma_core.safety.hitl import get_hitl_config

            recompile_hitl = get_hitl_config(self.config.raw)
            if not recompile_hitl.get("enabled", True):
                recompile_hitl = None

            # Time Travel — reuse the agent's snapshot recorder so the SSE
            # path captures snapshots too. Create lazily if the agent hasn't
            # built its graph yet. Failure is LOUD (warning, not debug): a
            # silent None here previously left /api/replay/* unmounted while
            # the UI polled it forever (the 404-spam symptom).
            _recorder = getattr(self.agent, "_snapshot_recorder", None)
            if _recorder is None:
                try:
                    from kazma_core.time_travel import create_recorder
                    _recorder = create_recorder(config=self.config.raw)
                    self.agent._snapshot_recorder = _recorder
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[Replay] snapshot recorder creation failed: %s", exc, exc_info=True)
            self._snapshot_recorder = _recorder

            def _recompile_holder_graph() -> None:
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
                    snapshot_recorder=_recorder,
                )
                self._graph_holder["graph"] = recompiled
                self._hitl_state["graph"] = recompiled
                _m = getattr(getattr(self.agent.llm, "config", None), "model", None) or "unknown"
                logger.info("[App] Graph recompiled on model switch (model=%s)", _m)

            _recompile_holder_graph()
            if hasattr(self.agent, "set_on_model_change_callback"):
                self.agent.set_on_model_change_callback(_recompile_holder_graph)

            self._hitl_state["checkpointer"] = self._checkpointer
            logger.info("[Checkpoint] Graph recompiled with checkpointer")
            logger.info("[HITL] Pending approvals endpoint linked to checkpointed graph")

            # ── HITL approval-timeout watchdog ─────────────────────
            # The graph interrupt() gate has no intrinsic timeout — without
            # this, a missed approval deadlocks the turn forever (audit
            # §2.3). The watchdog auto-denies expired approvals per
            # safety.hitl.approval_timeout_seconds / auto_deny_on_timeout.
            try:
                from kazma_ui.hitl_timeout import start_hitl_timeout_watchdog

                start_hitl_timeout_watchdog(
                    graph_getter=lambda: self._hitl_state.get("graph")
                    or self._graph_holder.get("graph"),
                    checkpointer_getter=lambda: self._hitl_state.get("checkpointer"),
                )
                logger.info("[HITL] Approval-timeout watchdog started")
            except Exception as exc:
                logger.warning("[HITL] Failed to start approval-timeout watchdog: %s", exc)

            # ── Time Travel: mount replay API + page route ──────────
            # ALWAYS mounted — even when the recorder failed to initialize.
            # The router then serves a structured 503 (time_travel_unavailable)
            # instead of a bare 404, so the UI can show a clear state and stop
            # polling instead of spamming 404s every 10s forever.
            try:
                from kazma_core.time_travel import ReplayEngine
                from kazma_ui.replay_routes import create_replay_router

                _replay_engine = (
                    ReplayEngine(self._snapshot_recorder)
                    if self._snapshot_recorder is not None
                    else None
                )
                self.app.include_router(create_replay_router(
                    recorder=self._snapshot_recorder,
                    engine=_replay_engine,
                    graph=self._hitl_state.get("graph") or self._graph_holder.get("graph"),
                ))
                if self._snapshot_recorder is not None:
                    logger.info("[Replay] Time-travel API mounted at /api/replay/*")
                else:
                    logger.warning(
                        "[Replay] Time-travel API mounted in UNAVAILABLE mode "
                        "(recorder init failed) — /api/replay/* returns 503"
                    )
            except Exception as exc:
                logger.warning("[Replay] Failed to mount replay API: %s", exc)

            # ── Research panel API ────────────────────────────────
            try:
                from kazma_ui.research_panel import create_research_router
                self.app.include_router(create_research_router())
                logger.info("[Research] API mounted at /api/research/*")
            except Exception as exc:
                logger.warning("[Research] Failed to mount research API: %s", exc)

            if self.gateway is not None:
                from kazma_gateway.agent_handler import create_graph_handler

                # graph_getter: every platform turn reads the live holder so
                # web/Telegram model switches rebind without re-registering.
                brain_handler = create_graph_handler(
                    graph=self._graph_holder.get("graph"),
                    graph_getter=lambda: self._graph_holder.get("graph"),
                    manager=self.gateway,
                    system_prompt=self.agent.system_prompt,
                    cost_breaker=self.agent.cost_breaker,
                    store=self.session_store,
                )
                self.gateway.on_message(brain_handler)
                logger.info("[Checkpoint] Brain handler re-registered with live graph_getter")
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
                # Surface the most common boot failure (bad token, network)
                # to chat — this is the highest-signal startup_failed event.
                try:
                    from kazma_core.lifecycle_notifier import notify_lifecycle

                    await notify_lifecycle("startup_failed", detail=f"Gateway: {e}")
                except Exception as ne:  # noqa: BLE001
                    logger.debug("[App] lifecycle 'startup_failed' notification failed: %s", ne)

        # ── Document intelligence platform (durable ingestion) ───────
        # Instantiate the shared coordinator (repository, CAS storage, job
        # store, DocumentService, knowledge adapter) and start its bounded
        # worker pool. Exposed via app.state.documents for the API router.
        # Restart recovery reclaims expired leases on worker start. Best-
        # effort: a failure here never blocks boot.
        try:
            from kazma_core.documents.ingestion import (
                create_default_ingestion_service,
                set_ingestion_service,
            )

            documents = create_default_ingestion_service()
            await documents.start_workers()
            self.app.state.documents = documents
            self._documents = documents
            set_ingestion_service(documents)
            logger.info("[Documents] ingestion coordinator started")
            # Periodic garbage-collection / retention loop (cancellable,
            # reads documents.gc.* live from the ConfigStore each run).
            try:
                from kazma_core.documents.retention import (
                    start_document_maintenance_loop,
                )

                self._documents_maintenance = start_document_maintenance_loop()
                logger.info("[Documents] retention/GC maintenance loop scheduled")
            except Exception as e:  # noqa: BLE001
                logger.warning("[Documents] maintenance loop start failed: %s", e)
        except Exception as e:
            logger.warning("[Documents] ingestion coordinator start failed: %s", e)

        # ── V2 memory worker (durable task queue) ────────────────────
        # Registers the macro_sleep / entity_merge / micro_consolidation
        # handlers and starts draining memory_ops.db. Pending rows
        # survive restarts. Best-effort: a failure here never blocks boot.
        try:
            from kazma_core.memory.worker_bootstrap import start_memory_worker

            start_memory_worker()
            logger.info("[Memory] V2 durable worker started")
        except Exception as e:
            logger.warning("[Memory] V2 worker start failed: %s", e)

        # ── Time-travel snapshot maintenance loop ────────────────────
        # Daily prune (TTL) + VACUUM of snapshots.db so replay/fork
        # history never grows without bound. Reads auto_maintain /
        # retention_days LIVE from the ConfigStore (Settings UI) on every
        # run, so Settings changes apply without a restart. Best-effort.
        try:
            from kazma_core.time_travel import start_snapshot_maintenance_loop

            start_snapshot_maintenance_loop()
            logger.info("[TimeTravel] snapshot maintenance loop scheduled (24h cadence)")
        except Exception as e:
            logger.warning("[TimeTravel] maintenance loop start failed: %s", e)

        # ── Periodic health-alert watchdog (audit M3) ────────────────
        # Proactive subsystem probes (memory degradation, RAM pressure) every
        # 5 min; previously alerts only fired reactively on errors.
        try:
            from kazma_core.observability.alerts import start_health_watchdog

            start_health_watchdog()
            logger.info("[Alerts] periodic health watchdog started (5m cadence)")
        except Exception as e:
            logger.warning("[Alerts] health watchdog start failed: %s", e)

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

        # ── Lifecycle status notification: "started" (or "restarted") ─
        # Emitted once all subsystems are up. The notifier upgrades this to
        # "🔄 Restarted" when a recent graceful-shutdown marker exists, so
        # an operator can tell an intentional restart from crash-recovery.
        # Best-effort: never blocks boot. NullBusAdapter (no platform
        # configured / pytest) drops it silently.
        try:
            from kazma_core.lifecycle_notifier import notify_lifecycle

            _adapter_names = (
                ", ".join(a.name for a in self.gateway.adapters)
                if self.gateway is not None
                else "none"
            )
            _model = self.config_store.get("registry.active_model") or "unknown"
            await notify_lifecycle(
                "started",
                detail=f"Adapters: {_adapter_names}\nModel: {_model}",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[App] lifecycle 'started' notification failed: %s", e)

        # (VectorMemory degradation-alert flush removed with the V1 stack.)

    async def _on_shutdown(self) -> None:
        """Application shutdown: flag, cron, swarm, agent, stores, gateway."""
        # ── Lifecycle status notification: "shutting down" ───────────
        # Emitted FIRST, before any teardown, while subsystems are still up
        # and the bus adapter is still reachable (the inbound gateway
        # adapters are torn down LAST at the bottom of this method). Its
        # absence in chat means a crash / kill -9 rather than graceful stop.
        # Also stamps the restart-detection marker consumed on next startup.
        # Best-effort: never blocks teardown.
        try:
            from kazma_core.lifecycle_notifier import notify_lifecycle

            await notify_lifecycle("shutting_down")
        except Exception as e:  # noqa: BLE001
            logger.debug("[App] lifecycle 'shutting_down' notification failed: %s", e)

        # Global drain flag so long-lived SSE / telemetry loops exit cleanly
        try:
            from kazma_core.shutdown import signal_shutdown

            signal_shutdown()
            logger.info("[app] signal_shutdown() fired")
        except Exception as e:
            logger.warning("[app] signal_shutdown failed: %s", e)

        # Stop the HITL approval-timeout watchdog
        try:
            from kazma_ui.hitl_timeout import stop_hitl_timeout_watchdog

            await stop_hitl_timeout_watchdog()
        except Exception as e:
            logger.debug("[app] HITL watchdog stop: %s", e)

        # Stop document ingestion workers (drain in-flight stages) before
        # cron/agent teardown. Jobs are durable — pending rows resume on the
        # next boot via lease recovery. Best-effort; never blocks shutdown.
        try:
            documents = getattr(self, "_documents", None)
            maintenance = getattr(self, "_documents_maintenance", None)
            if maintenance is not None:
                try:
                    maintenance.cancel()
                    await maintenance
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    pass
                self._documents_maintenance = None
            if documents is not None:
                await documents.stop_workers()
                documents.close()
                try:
                    from kazma_core.documents.ingestion import set_ingestion_service

                    set_ingestion_service(None)
                except Exception:  # noqa: BLE001
                    pass
            # Drain the V2 memory worker's in-flight handler tasks so a
            # mid-execution belief extraction / entity merge is awaited rather
            # than abandoned on loop close (audit finding). The schedulers are
            # fire-and-forget loops the loop cancellation handles.
            try:
                from kazma_core.memory.worker_bootstrap import stop_memory_worker

                await stop_memory_worker()
            except Exception:  # noqa: BLE001
                pass
            logger.info("[Documents] ingestion coordinator stopped")
        except Exception as e:
            logger.warning("[Documents] ingestion coordinator stop failed: %s", e)

        # 1) Stop cron first so no new jobs fire (audit C3)
        try:
            from kazma_core.cron.scheduler import get_cron_scheduler

            cron_sched = get_cron_scheduler()
            if cron_sched is not None and hasattr(cron_sched, "stop"):
                maybe = cron_sched.stop()
                if asyncio.iscoroutine(maybe):
                    await maybe
                logger.info("[app] Cron scheduler stopped")
        except Exception as e:
            logger.warning("[app] cron stop failed: %s", e)

        # 2) Drain swarm in-flight tasks
        try:
            engine = getattr(self, "swarm_engine", None) or getattr(self, "_swarm_engine", None)
            if engine is None:
                try:
                    from kazma_core.swarm.engine import get_swarm_engine

                    engine = get_swarm_engine()
                except Exception:
                    engine = None
            if engine is not None:
                handles = getattr(engine, "_task_handles", None) or {}
                for _tid, handle in list(handles.items()):
                    if handle is not None and hasattr(handle, "done") and not handle.done():
                        handle.cancel()
                if hasattr(engine, "stop_all"):
                    maybe = engine.stop_all()
                    if asyncio.iscoroutine(maybe):
                        await maybe
                logger.info("[app] Swarm engine drained")
        except Exception as e:
            logger.warning("[app] swarm drain failed: %s", e)

        # 3) Shut down the agent (closes MCP processes, LLM clients)
        if self.agent is not None:
            try:
                await self.agent.shutdown()
                logger.info("[app] Agent shut down cleanly")
            except Exception as e:
                logger.warning("[app] Error during agent shutdown: %s", e)

        # 4) Close app-level checkpointer / CheckpointManager if present
        try:
            cp = self._checkpointer
            if cp is not None:
                if hasattr(cp, "aclose"):
                    await cp.aclose()
                elif hasattr(cp, "close"):
                    maybe = cp.close()
                    if asyncio.iscoroutine(maybe):
                        await maybe
                logger.info("[app] Checkpointer closed")
        except BaseException as e:
            # BaseException (not Exception): asyncio.CancelledError is raised here when
            # uvicorn cancels the lifespan task mid-teardown (Ctrl+C during the Postgres
            # pool close). It is a BaseException since Py3.8, so `except Exception` would
            # miss it and leak a scary traceback. Best-effort teardown — log and continue.
            if isinstance(e, asyncio.CancelledError):
                logger.info("[app] Checkpointer close interrupted by shutdown cancellation (expected)")
            else:
                logger.warning("[app] Error closing checkpointer: %s", e)

        # 5) Close all cached ModelRegistry clients
        try:
            from kazma_core.model_registry import get_model_registry

            registry = get_model_registry()
            if registry is not None:
                await registry.close()
        except Exception as e:
            logger.warning("[app] Error closing model registry: %s", e)

        try:
            from kazma_core.http_pool import close_http_client

            await close_http_client()
        except Exception as e:
            logger.warning("[app] Error closing http client during shutdown: %s", e)

        # 5b) Close auxiliary SQLite stores that previously lived for the
        # whole process with no close path (audit B.10): swarm task store,
        # LLM semantic cache, pipeline logger. Best-effort each so one bad
        # handle cannot block the rest of teardown.
        try:
            engine = getattr(self, "swarm_engine", None) or getattr(self, "_swarm_engine", None)
            if engine is None:
                try:
                    from kazma_core.swarm.engine import get_swarm_engine

                    engine = get_swarm_engine()
                except Exception:
                    engine = None
            store = getattr(engine, "task_store", None) if engine is not None else None
            if store is not None and hasattr(store, "close"):
                store.close()
                logger.info("[app] Swarm TaskStore closed")
        except Exception as e:
            logger.debug("[app] TaskStore close: %s", e)

        try:
            from kazma_core import llm_provider as _llm_mod

            cache = getattr(_llm_mod, "_semantic_cache_singleton", None)
            if cache is not None and hasattr(cache, "close"):
                cache.close()
                _llm_mod._semantic_cache_singleton = None
                logger.info("[app] Semantic cache closed")
        except Exception as e:
            logger.debug("[app] Semantic cache close: %s", e)

        try:
            from kazma_core.swarm.memory.pipeline_logger import close_pipeline_logger

            close_pipeline_logger()
        except Exception as e:
            logger.debug("[app] Pipeline logger close: %s", e)

        try:
            from kazma_core.observability.llm_ledger import close_llm_ledger

            close_llm_ledger()
        except Exception as e:
            logger.debug("[app] LLM ledger close: %s", e)

        # Stop the periodic alert health watchdog
        try:
            from kazma_core.observability.alerts import stop_health_watchdog

            await stop_health_watchdog()
        except Exception as e:
            logger.debug("[app] alert watchdog stop: %s", e)

        # 6) Best-effort vector memory close & SessionManager close
        try:
            from kazma_ui.session_manager import get_session_manager

            sm = get_session_manager()
            if sm is not None and hasattr(sm, "close"):
                sm.close()
                logger.info("[app] SessionManager closed cleanly")
        except Exception as e:
            logger.debug("[app] SessionManager close: %s", e)

        # (VectorMemory close removed with the V1 stack.)

        if self.gateway is None:
            return
        try:
            await self.gateway.stop()
            logger.info("[Gateway] Stopped cleanly")
        except Exception as e:
            logger.warning("[Gateway] Error during shutdown: %s", e)

        # Swarm bus adapters are standalone httpx pools independent of
        # gateway.start()/stop() (lifecycle-notifier requirement) — close
        # them explicitly, or each restart leaks one pool per platform.
        try:
            from kazma_core.swarm.bus import get_message_bus

            _bus_adapter = get_message_bus().adapter
            for _child in getattr(_bus_adapter, "adapters", [_bus_adapter]):
                _close = getattr(_child, "close", None)
                if _close is not None:
                    await _close()
            logger.info("[SwarmBus] Adapters closed cleanly")
        except Exception as e:
            logger.debug("[SwarmBus] adapter close: %s", e)

    def _setup_lifecycle_and_errors(self) -> None:
        """Register lifespan (replaces deprecated on_event) and exception handlers."""
        builder = self

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            await builder._on_startup()
            try:
                yield
            finally:
                # Shield teardown from lifespan cancellation: on Ctrl+C uvicorn cancels
                # this task, which would interrupt _on_shutdown mid-await (e.g. during the
                # Postgres pool close) and leak an asyncio.CancelledError traceback. shield
                # lets teardown finish; the except is a backstop if cancellation is already
                # in flight when the awaited coroutine resumes.
                try:
                    await asyncio.shield(builder._on_shutdown())
                except asyncio.CancelledError:
                    logger.info("[app] Shutdown completed (task was cancelled during teardown)")
                except BaseException as e:  # pragma: no cover - last-resort
                    logger.warning("[app] Error during lifespan shutdown: %s", e)
                finally:
                    try:
                        from kazma_core.shutdown import uninstall_shutdown_signal_hooks

                        uninstall_shutdown_signal_hooks()
                    except Exception:  # noqa: BLE001
                        pass

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
        kazma-web              # port 9090 (host default; matches CLI)
        kazma-web --port 8080  # custom port
        KAZMA_PORT=9091 kazma-web
    """
    import argparse
    import os as _os3

    # Windows: SelectorEventLoop for psycopg async compat (see
    # kazma_core.eventloop). Must run before uvicorn.run below.
    try:
        from kazma_core.eventloop import set_windows_selector_policy

        set_windows_selector_policy()
    except Exception:
        pass

    import uvicorn

    _default_port = int(_os3.environ.get("KAZMA_PORT", "9090") or "9090")
    parser = argparse.ArgumentParser(description="Kazma Web UI")
    parser.add_argument(
        "--port", "-p", type=int, default=_default_port,
        help=f"Port to bind (default: {_default_port} / KAZMA_PORT)",
    )
    args, _ = parser.parse_known_args()

    # Security: default to localhost.  Use KAZMA_HOST env var to
    # explicitly bind to all interfaces (decoupled from KAZMA_SECRET).
    host = _os3.environ.get("KAZMA_HOST", "127.0.0.1")
    _loopback = host in ("127.0.0.1", "localhost", "::1")
    if not _loopback and not (_os3.environ.get("KAZMA_SECRET") or "").strip():
        raise SystemExit(
            "Refusing non-loopback bind without KAZMA_SECRET. "
            "Set KAZMA_HOST=127.0.0.1 or pin KAZMA_SECRET."
        )

    app = create_app()
    from kazma_core.eventloop import uvicorn_loop_factory
    uvicorn.run(app, host=host, port=args.port, log_level="info",
                loop=uvicorn_loop_factory())


if __name__ == "__main__":
    main()
