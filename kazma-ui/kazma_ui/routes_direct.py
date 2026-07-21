"""Direct route registrations for the Kazma UI web application.

Extracted from the god-module app.py to keep route registration highly modular and maintainable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from fastapi import Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse as _JSONResponse

logger = logging.getLogger(__name__)

__all__ = ["register_direct_routes"]


def register_direct_routes(self: Any) -> None:
    """Register direct FastAPI route handlers onto self.app."""

    @self.app.get("/metrics")
    async def _metrics():
        """Prometheus metrics endpoint."""
        from kazma_core.metrics import get_metrics_response
        body, status, headers = get_metrics_response()
        return _JSONResponse(content=body.decode() if isinstance(body, bytes) else body, media_type=headers["content-type"])

    @self.app.get("/api/system/debug/registry")
    async def _debug_registry():
        import kazma_core.model_registry as _mr

        reg = _mr._registry
        if reg is None:
            return {"status": "not_initialized", "hint": "ModelRegistry not initialized. Start the app normally."}
        return {
            "status": "initialized",
            "active_provider": reg._active_provider or "none",
            "active_profile": reg.get_active_profile(),
            "providers": reg._list_all_providers() if hasattr(reg, '_list_all_providers') else [],
            "saved_profiles": reg.list_model_profiles(mask_api_key=True),
            "registered_models": reg._registered_models if hasattr(reg, '_registered_models') else {},
            "discovered_models": reg.get_discovered_models(),
            "unified_options": reg.list_unified_options(),
        }

    @self.app.post("/api/system/flush")
    async def _system_flush():
        import glob as _glob_sys
        import os as _os_sys

        paths = {
            "kazma_home": str(_os_sys.path.expanduser("~/.kazma")),
            "config_db": str(_os_sys.path.expanduser("~/.kazma/config.db")),
            "config_yaml": next(iter(_glob_sys.glob(_os_sys.path.expanduser("~/.kazma/*.yaml"))), ""),
            "pending_evolution": str(_os_sys.path.expanduser("~/.kazma/pending_evolution.json")),
            "knowledge_graph": str(_os_sys.path.expanduser("kazma-data/knowledge_graph.json")),
        }
        # Flush model registry cache
        try:
            import kazma_core.model_registry as _mr

            _mr._registry = None
        except Exception as exc:
            logger.debug("Model registry cache flush failed: %s", exc)
        # Flush WorkerRegistry cache
        try:
            from kazma_core.swarm.registry import WorkerRegistry

            WorkerRegistry._instance = None
        except Exception as exc:
            logger.debug("Worker registry cache flush failed: %s", exc)
        # Flush tool registry (the real registry is LocalToolRegistry)
        try:
            from kazma_core.agent.tool_registry import get_tool_registry

            # LocalToolRegistry caches the singleton in _builtin_registry.
            import kazma_core.agent.tool_registry as _tr_mod

            _tr_mod._builtin_registry = None
        except Exception as exc:
            logger.debug("Tool registry cache flush failed: %s", exc)
        return {"status": "flushed", "config_paths": paths}

    @self.app.get("/api/system/config-paths")
    async def _system_config_paths():
        import os as _osp

        home = _osp.path.expanduser("~/.kazma")
        return {
            "kazma_home": home,
            "config_db": _osp.path.join(home, "config.db") if _osp.path.exists(_osp.path.join(home, "config.db")) else "NOT FOUND",
            "swarm_registry": _osp.path.expanduser("swarm_registry.json") if _osp.path.exists(_osp.path.expanduser("swarm_registry.json")) else "NOT FOUND",
            "pending_evolution": _osp.path.join(home, "pending_evolution.json") if _osp.path.exists(_osp.path.join(home, "pending_evolution.json")) else "NOT FOUND",
            "knowledge_graph": _osp.path.expanduser("kazma-data/knowledge_graph.json") if _osp.path.exists(_osp.path.expanduser("kazma-data/knowledge_graph.json")) else "NOT FOUND",
            "snapshots_db": _osp.path.expanduser("kazma-data/snapshots.db") if _osp.path.exists(_osp.path.expanduser("kazma-data/snapshots.db")) else "NOT FOUND",
        }

    @self.app.delete("/api/mcp/servers/{server_name}")
    async def _delete_mcp_server(server_name: str):
        try:
            self.agent.remove_mcp_server(server_name)
            return {"status": "ok", "message": f"Server '{server_name}' deleted"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @self.app.get("/api/telemetry/typing")
    async def _typing_signal():
        return {"status": "processing", "timestamp": __import__("time").time()}

    @self.app.post("/api/telemetry/typing/stream_start")
    async def _stream_start(req: dict):
        worker_name = req.get("worker_name", "unknown")
        task_id = req.get("task_id", "")
        logger.info("[Stream] Typing started — worker=%s task=%s", worker_name, task_id)
        return {"status": "stream_started", "worker_name": worker_name, "task_id": task_id}

    import kazma_core.swarm.memory.graph as _kg_mod

    @self.app.get("/api/memory/graph")
    async def _memory_graph():
        kg = _kg_mod.KnowledgeGraph()
        return kg.to_json()

    @self.app.get("/api/memory/graph/stats")
    async def _memory_graph_stats():
        kg = _kg_mod.KnowledgeGraph()
        return kg.stats()

    import kazma_core.time_travel as _tt_mod

    @self.app.get("/api/session/history")
    async def _session_history(thread_id: str = "", limit: int = 20):
        store = _tt_mod.SnapshotStore()
        if thread_id:
            records = store.list_for_thread(thread_id)[:limit]
        else:
            records = []
        return {"sessions": [r.to_dict() for r in records]}

    @self.app.post("/api/session/replay")
    async def _session_replay(req: dict):
        thread_id = req.get("thread_id", "")
        iteration = req.get("iteration", 0)
        if not thread_id:
            from fastapi import HTTPException as _httpx

            raise _httpx(status_code=400, detail="thread_id required")
        engine = _tt_mod.ReplayEngine()
        return await engine.replay_from(thread_id, iteration)

    @self.app.get("/api/system/status")
    async def _get_system_status():
        import os as _os
        import sqlite3

        from kazma_core.config_store import get_config_store
        from kazma_core.system.maintenance import get_memory_paths

        # Demo mode: report DEMO instead of DEGRADED so the UI hides the
        # install button and shows a clean "demo mode" message.
        _demo_mode = _os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes")

        store = get_config_store()
        if _demo_mode:
            status = "DEMO"
        else:
            status = store.get("system.memory.status") or "ACTIVE"

        fts5_path, vector_path, _ = get_memory_paths()

        fts5_size = fts5_path.stat().st_size if fts5_path.exists() else 0
        fts5_count = 0
        if fts5_path.exists():
            try:
                conn = sqlite3.connect(fts5_path)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_fts'"
                )
                if cursor.fetchone():
                    cursor.execute("SELECT COUNT(*) FROM memory_fts")
                    fts5_count = cursor.fetchone()[0]
                conn.close()
            except Exception as _e:
                logger.debug("fts5 count failed: %s", _e)
                if "conn" in dir():
                    try:
                        conn.close()
                    except Exception:
                        pass

        vector_size = 0
        if vector_path.exists() and vector_path.is_dir():
            vector_size = sum(
                f.stat().st_size for f in vector_path.glob("**/*") if f.is_file()
            )

        vector_count = 0
        from kazma_core.agent.tool_registry import get_vector_memory

        vm = get_vector_memory()
        if vm and not getattr(vm, "degraded", False):
            try:
                vector_count = vm.count
                if callable(vector_count):
                    vector_count = vector_count()
            except Exception as _e:
                logger.debug("vector count failed: %s", _e)

        # Per-component green/red board for Memory & Governance UI.
        health: dict = {"components": [], "issues": [], "summary": ""}
        try:
            from kazma_core.memory.health import build_memory_health

            health = build_memory_health()
            live = str(health.get("status") or "")
            # INSTALLING from ConfigStore takes priority; otherwise live probe wins.
            if status == "INSTALLING" or live == "INSTALLING":
                status = "INSTALLING"
            elif live in ("DEMO", "DEGRADED", "ACTIVE"):
                status = live
        except Exception as _e:
            logger.warning("memory health probe failed: %s", _e)
            health = {
                "components": [],
                "issues": [str(_e)],
                "summary": "health probe failed",
            }

        return {
            "status": status,
            "fts5_size": fts5_size,
            "fts5_count": fts5_count,
            "vector_size": vector_size,
            "vector_count": vector_count,
            "components": health.get("components", []),
            "issues": health.get("issues", []),
            "summary": health.get("summary", ""),
        }

    @self.app.post("/api/system/install")
    async def _post_system_install(req: dict = None):
        """Install an allowlisted package or pyproject extra in the background.

        Body (JSON)::
            {"extra": "rag"}                  # preferred — uv pip install -e ".[rag]"
            {"package_name": "chromadb"}     # single allowlisted package

        Supply-chain safe: only extras/packages in the installer allowlists.
        """
        req = req or {}
        import os as _os
        # In demo mode, ML deps can't be installed (container has no build
        # tools and not enough RAM). Return a clean message.
        if _os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes"):
            return {"status": "unavailable", "message": "ML dependencies are not available in demo mode."}

        from kazma_core.system import (
            ALLOWED_EXTRAS,
            ALLOWED_PACKAGES,
            asynchronous_install_extra,
            asynchronous_install_package,
        )

        extra = (req.get("extra") or "").strip().lower()
        package_name = (req.get("package_name") or "").strip()

        if extra:
            if extra not in ALLOWED_EXTRAS:
                return {
                    "status": "error",
                    "message": f"Extra '{extra}' is not in the allowed list: {sorted(ALLOWED_EXTRAS)}",
                }
            await asynchronous_install_extra(extra)
            return {"status": "started", "extra": extra}

        if not package_name:
            package_name = "sentence-transformers"
        if package_name not in ALLOWED_PACKAGES:
            return {
                "status": "error",
                "message": f"Package '{package_name}' is not in the allowed list: {sorted(ALLOWED_PACKAGES)}",
            }
        await asynchronous_install_package(package_name)
        return {"status": "started", "package": package_name}

    @self.app.get("/api/system/install/status")
    async def _get_install_status() -> dict[str, Any]:
        """Last background install status (for Settings → Packages UI)."""
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        return {
            "target": store.get("system.install.last_target", ""),
            "status": store.get("system.install.last_status", ""),
            "error": store.get("system.install.last_error", ""),
            "memory_status": store.get("system.memory.status", ""),
        }

    @self.app.get("/api/alerts/recent")
    async def _get_recent_alerts():
        from kazma_core.observability.alerts import AlertDispatcher
        return [
            a.to_dict() if hasattr(a, "to_dict") else a
            for a in AlertDispatcher.get_recent_alerts()
        ]

    @self.app.get("/api/system/memory/backups")
    async def _list_memory_backups():
        from kazma_core.system.maintenance import list_memory_backups
        try:
            backups = list_memory_backups()
            return {"backups": backups}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.get("/packages")
    async def _packages_redirect() -> RedirectResponse:
        """Legacy /packages page → Settings Packages tab."""
        return RedirectResponse("/settings?tab=packages", status_code=307)

    # ── Auth bootstrap (remote clients — loopback auto-cookie is disabled) ──
    @self.app.get("/login", response_class=HTMLResponse)
    async def _login_page(request: Request) -> HTMLResponse:
        """Render the secret login form for non-loopback browsers."""
        return self.templates.TemplateResponse(
            request,
            "login.html",
            {},
        )

    @self.app.get("/api/auth/status")
    async def _auth_status(request: Request) -> dict[str, Any]:
        """Whether auth is enabled and whether this request is authenticated."""
        from kazma_ui.auth import (
            get_kazma_secret,
            get_request_principal,
            is_authenticated,
            _is_loopback_client,
        )

        expected = get_kazma_secret()
        oidc = False
        multi_user = False
        try:
            from kazma_core.security.oidc import oidc_configured
            from kazma_core.security.platform_rbac import multi_user_enabled

            oidc = oidc_configured()
            multi_user = multi_user_enabled()
        except Exception:
            pass
        if not expected:
            return {
                "auth_enabled": False,
                "authenticated": True,
                "mode": "open",
                "oidc": oidc,
                "multi_user": multi_user,
            }
        ok = is_authenticated(request, expected)
        principal = get_request_principal(request) if ok else None
        return {
            "auth_enabled": True,
            "authenticated": ok,
            "loopback": _is_loopback_client(request),
            "mode": "secret",
            "oidc": oidc,
            "multi_user": multi_user,
            "principal": principal,
        }

    # Login brute-force throttle (audit M3) — in-process sliding window per IP
    _login_failures: dict[str, list[float]] = {}
    _LOGIN_WINDOW_S = 300.0
    _LOGIN_MAX_FAILS = 10

    @self.app.post("/api/auth/login")
    async def _auth_login(request: Request) -> Response:
        """Exchange KAZMA_SECRET for an HttpOnly session cookie."""
        import time as _time

        from kazma_ui.auth import (
            SECRET_COOKIE,
            get_kazma_secret,
            verify_secret,
            _is_https,
        )

        client_ip = (request.client.host if request.client else "") or "unknown"
        now = _time.time()
        recent = [
            t for t in _login_failures.get(client_ip, [])
            if now - t < _LOGIN_WINDOW_S
        ]
        _login_failures[client_ip] = recent
        if len(recent) >= _LOGIN_MAX_FAILS:
            logger.warning("[auth] login rate limit hit for %s", client_ip)
            return _JSONResponse(
                {"detail": "Too many failed login attempts — try again later"},
                status_code=429,
            )

        expected = get_kazma_secret()
        if not expected:
            return _JSONResponse(
                {"status": "ok", "message": "Auth disabled (no KAZMA_SECRET)"},
                status_code=200,
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        secret = str(body.get("secret") or "").strip()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "").strip()
        session_user = None
        session_role = "admin"
        session_uid = None
        authenticated = False

        # Path A: multi-user local username + password (Phase 4.4)
        if username and password:
            try:
                from kazma_core.security.platform_rbac import authenticate_local_user

                pu = authenticate_local_user(username, password)
                if pu is not None:
                    authenticated = True
                    session_user = pu.username
                    session_role = pu.role
                    session_uid = pu.user_id
            except Exception:
                logger.debug("[auth] local user auth failed", exc_info=True)

        # Path B: shared operator secret
        if not authenticated:
            check = secret or password
            if check and verify_secret(check, expected):
                authenticated = True
                session_user = "operator"
                session_role = "admin"
                session_uid = "shared-secret"

        if not authenticated:
            recent.append(now)
            _login_failures[client_ip] = recent
            return _JSONResponse(
                {"detail": "Invalid credentials"},
                status_code=401,
            )

        # Success — clear failures for this IP
        _login_failures.pop(client_ip, None)

        resp = _JSONResponse({
            "status": "ok",
            "authenticated": True,
            "username": session_user,
            "role": session_role,
        })
        # Opaque session cookie preferred (audit H1)
        try:
            from kazma_core.security.web_sessions import (
                SESSION_COOKIE,
                create_session,
                use_opaque_sessions,
            )

            if use_opaque_sessions():
                sid = create_session(
                    actor="login",
                    username=session_user,
                    role=session_role,
                    user_id=session_uid,
                )
                resp.set_cookie(
                    key=SESSION_COOKIE,
                    value=sid,
                    httponly=True,
                    samesite="lax",
                    path="/",
                    secure=_is_https(request),
                    max_age=60 * 60 * 24 * 14,
                )
                resp.delete_cookie(SECRET_COOKIE, path="/")
                return resp
        except Exception:
            logger.debug("[auth] opaque session create failed; legacy cookie", exc_info=True)
        resp.set_cookie(
            key=SECRET_COOKIE,
            value=expected,
            httponly=True,
            samesite="lax",  # LAN/IP + form POST login
            path="/",
            secure=_is_https(request),
            max_age=60 * 60 * 24 * 14,  # 14 days
        )
        return resp

    @self.app.get("/api/auth/oidc/start")
    async def _oidc_start(request: Request) -> Response:
        """Redirect browser to configured OIDC IdP (Phase 4.4)."""
        from fastapi.responses import RedirectResponse

        try:
            from kazma_core.security.oidc import build_authorize_url, oidc_configured

            if not oidc_configured():
                return _JSONResponse(
                    {"error": "OIDC not configured (KAZMA_OIDC_ISSUER + CLIENT_ID)"},
                    status_code=503,
                )
            info = await build_authorize_url()
            return RedirectResponse(url=info["url"], status_code=302)
        except Exception as exc:
            logger.exception("[oidc] start failed")
            return _JSONResponse({"error": str(exc)}, status_code=500)

    @self.app.get("/api/auth/oidc/callback")
    async def _oidc_callback(request: Request) -> Response:
        """OIDC callback — mint opaque session from IdP claims."""
        from fastapi.responses import RedirectResponse
        from kazma_ui.auth import SESSION_COOKIE, _is_https

        code = request.query_params.get("code") or ""
        state = request.query_params.get("state") or ""
        if not code or not state:
            return _JSONResponse({"error": "Missing code/state"}, status_code=400)
        try:
            from kazma_core.security.oidc import exchange_code
            from kazma_core.security.web_sessions import create_session, use_opaque_sessions

            result = await exchange_code(code, state)
            if not use_opaque_sessions():
                return _JSONResponse(
                    {"error": "Opaque sessions required for OIDC"},
                    status_code=500,
                )
            sid = create_session(
                actor="oidc",
                username=result.get("username"),
                role=result.get("role") or "operator",
                user_id=result.get("user_id"),
            )
            resp = RedirectResponse(url="/", status_code=302)
            resp.set_cookie(
                key=SESSION_COOKIE,
                value=sid,
                httponly=True,
                samesite="lax",
                path="/",
                secure=_is_https(request),
                max_age=60 * 60 * 24 * 14,
            )
            return resp
        except Exception as exc:
            logger.exception("[oidc] callback failed")
            return _JSONResponse({"error": str(exc)}, status_code=400)

    @self.app.get("/api/auth/me")
    async def _auth_me(request: Request) -> Response:
        """Return current principal (role/username) for UI chrome."""
        from kazma_ui.auth import get_kazma_secret, get_request_principal, is_authenticated

        secret = get_kazma_secret()
        if secret and not is_authenticated(request, secret):
            return _JSONResponse({"authenticated": False}, status_code=401)
        principal = get_request_principal(request) or {}
        return _JSONResponse({"authenticated": True, **principal})

    @self.app.post("/api/auth/logout")
    async def _auth_logout(request: Request) -> Response:
        """Clear auth cookies and revoke opaque session."""
        from kazma_ui.auth import SECRET_COOKIE, SESSION_COOKIE

        try:
            from kazma_core.security.web_sessions import revoke_session

            sid = request.cookies.get(SESSION_COOKIE) or ""
            if sid:
                revoke_session(sid)
        except Exception:
            pass
        resp = _JSONResponse({"status": "ok", "authenticated": False})
        resp.delete_cookie(SECRET_COOKIE, path="/")
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    @self.app.get("/api/system/packages")
    async def _get_packages():
        """List installed Python packages with metadata and extras status."""
        import importlib.metadata as ilm

        # ── Define the extras groups and their member packages ──
        EXTRA_GROUPS = {
            "rag": {
                "description": "Vector memory (ChromaDB) + local embeddings (sentence-transformers). Without this, memory search falls back to FTS5 text-only search.",
                "packages": ["chromadb", "sentence-transformers"],
                "install_cmd": 'uv pip install -e ".[rag]"   # additive — won\'t remove other extras',
            },
            "dev": {
                "description": "Development tools — testing (pytest), linting (ruff), type checking (mypy), load testing (locust).",
                "packages": ["pytest", "pytest-asyncio", "pytest-cov", "pytest-mock", "ruff", "mypy", "locust"],
                "install_cmd": 'uv pip install -e ".[dev]"   # additive — won\'t remove other extras',
            },
            "test": {
                "description": "Test-specific dependencies (lighter than dev). Includes pytest + fakeredis for unit/integration tests.",
                "packages": ["pytest", "pytest-asyncio", "pytest-cov", "pytest-mock", "fakeredis"],
                "install_cmd": 'uv pip install -e ".[test]"   # additive — won\'t remove other extras',
            },
            "tui": {
                "description": "Terminal dashboard UI (Textual) with RTL/bidirectional text rendering (python-bidi).",
                "packages": ["textual", "python-bidi"],
                "install_cmd": 'uv pip install -e ".[tui]"   # additive — won\'t remove other extras',
            },
            "observability": {
                "description": "Prometheus metrics export for monitoring Kazma in production (Grafana dashboards, alerting).",
                "packages": ["prometheus-client"],
                "install_cmd": 'uv pip install -e ".[observability]"   # additive — won\'t remove other extras',
            },
            "web": {
                "description": "Browser automation via Playwright. Used by the web crawler skill to render JavaScript-heavy pages.",
                "packages": ["playwright"],
                "install_cmd": 'uv pip install -e ".[web]"   # additive — won\'t remove other extras',
            },
        }

        # ── Core dependencies (always installed) ──
        CORE_PACKAGES = [
            "fastapi", "uvicorn", "langgraph", "langgraph-checkpoint-sqlite",
            "aiosqlite", "langfuse", "pyyaml", "httpx", "cryptography",
            "PyJWT", "jinja2", "python-multipart", "textual", "psutil",
            "aiogram", "websockets", "duckduckgo-search", "trafilatura",
            "markdown", "tenacity", "networkx", "click", "rich",
            "google-cloud-aiplatform", "python-dotenv",
        ]

        CORE_DESCRIPTIONS = {
            "fastapi": "Web framework powering the Kazma dashboard + REST API",
            "uvicorn": "ASGI server that runs the FastAPI app",
            "langgraph": "LangGraph supervisor brain — the ReAct loop, checkpointing, interrupt()",
            "langgraph-checkpoint-sqlite": "SQLite-backed checkpoint persistence for LangGraph",
            "aiosqlite": "Async SQLite driver used by all Kazma data stores",
            "langfuse": "Observability/tracing platform for LLM calls",
            "pyyaml": "YAML parser for kazma.yaml config + skill manifests",
            "httpx": "HTTP client for LLM API calls + web tools",
            "cryptography": "AES-256-GCM encryption for the secret vault",
            "PyJWT": "JWT token generation for GitHub App authentication",
            "jinja2": "HTML template engine for the web UI",
            "python-multipart": "File upload handling for FastAPI",
            "textual": "Terminal UI framework (also a core dep for the TUI)",
            "psutil": "System resource monitoring (CPU, RAM, disk) for telemetry",
            "aiogram": "Telegram Bot API framework",
            "websockets": "WebSocket support for real-time chat + gateway",
            "duckduckgo-search": "Privacy-focused web search (no API key needed)",
            "trafilatura": "Web content extraction (clean text from URLs)",
            "markdown": "Markdown rendering for chat messages",
            "tenacity": "Retry logic with exponential backoff for LLM calls",
            "networkx": "Graph algorithms for swarm DAG/topology validation",
            "click": "CLI framework for the `kazma` command",
            "rich": "Beautiful terminal output (colors, tables, progress bars)",
            "google-cloud-aiplatform": "Google Vertex AI provider integration",
            "python-dotenv": ".env file loading for local development",
        }

        # ── Build the package list ──
        try:
            all_dists = {d.metadata["Name"]: d for d in ilm.distributions()}
            # Build a normalized lookup: lowercase + dashes/underscores unified
            norm_dists = {
                k.lower().replace("-", "_"): v for k, v in all_dists.items()
            }
        except Exception:
            all_dists = {}
            norm_dists = {}

        def _pkg_info(name: str) -> dict:
            # Normalize the search name the same way (lowercase, _ instead of -)
            norm = name.lower().replace("-", "_")
            dist = norm_dists.get(norm)
            if dist:
                return {
                    "name": dist.metadata["Name"],
                    "version": dist.version,
                    "installed": True,
                }
            return {"name": name, "version": "", "installed": False}

        # Core packages
        core_list = []
        for name in CORE_PACKAGES:
            info = _pkg_info(name)
            info["description"] = CORE_DESCRIPTIONS.get(name, "")
            info["group"] = "core"
            core_list.append(info)

        # Extras — fully installed only when *every* member package is present.
        # Partial (e.g. pytest from [dev] but no fakeredis) is reported so the
        # UI does not look like a broken install when only one niche dep is missing.
        extras_list = []
        for group_name, group_data in EXTRA_GROUPS.items():
            pkg_list = []
            for name in group_data["packages"]:
                info = _pkg_info(name)
                info["group"] = group_name
                pkg_list.append(info)
            n_total = len(pkg_list)
            n_ok = sum(1 for p in pkg_list if p["installed"])
            group_installed = n_total > 0 and n_ok == n_total
            extras_list.append({
                "name": group_name,
                "description": group_data["description"],
                "install_cmd": group_data["install_cmd"],
                "installed": group_installed,
                "partial": n_ok > 0 and n_ok < n_total,
                "installed_count": n_ok,
                "package_count": n_total,
                "packages": pkg_list,
            })

        # Count total installed (from distributions, not just our deps)
        total_installed = len(all_dists)

        return {
            "core": core_list,
            "extras": extras_list,
            "total_installed": total_installed,
            "python_version": __import__("sys").version.split()[0],
        }

    @self.app.post("/api/system/memory/backup")
    async def _create_memory_backup():
        from kazma_core.system.maintenance import create_memory_backup
        try:
            manifest = create_memory_backup()
            return {"status": "success", "manifest": manifest}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.post("/api/system/memory/restore")
    async def _restore_memory_backup(req: dict):
        backup_name = req.get("backup_name", "")
        if not backup_name:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="backup_name is required")
        from kazma_core.system.maintenance import restore_memory_backup
        try:
            res = await restore_memory_backup(backup_name)
            return res
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.post("/api/system/memory/maintenance")
    async def _run_memory_maintenance():
        from kazma_core.system.maintenance import run_memory_maintenance
        try:
            res = run_memory_maintenance()
            return {"status": "success", "details": res}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.websocket("/ws/dashboard")
    async def ws_dashboard(websocket: WebSocket) -> None:
        from kazma_ui.auth import get_kazma_secret, SECRET_COOKIE
        expected = get_kazma_secret()
        if expected:
            provided = websocket.headers.get("x-kazma-secret", "")
            if not provided:
                provided = websocket.cookies.get(SECRET_COOKIE, "")
            import hmac as _hmac

            if not provided or not _hmac.compare_digest(provided, expected):
                await websocket.close(code=4003, reason="Unauthorized")
                return
        await websocket.accept()
        from kazma_core.shutdown import is_shutting_down
        from kazma_core.tracing import get_trace_store

        store = get_trace_store()
        store.register_ws(websocket)
        try:
            import json

            await websocket.send_text(
                json.dumps(
                    {
                        "type": "connected",
                        "message": "Real-time dashboard feed active",
                    }
                )
            )
            while not is_shutting_down():
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                except TimeoutError:
                    continue
                except Exception as _e:
                    logger.debug("[WS] events receive error, closing: %s", _e)
                    break
        except Exception as exc:
            logger.debug("WS events handler stopped: %s", exc)
        finally:
            store.unregister_ws(websocket)

    @self.app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket) -> None:
        from kazma_ui.auth import get_kazma_secret, SECRET_COOKIE
        expected2 = get_kazma_secret()
        if expected2:
            provided2 = websocket.headers.get("x-kazma-secret", "")
            if not provided2:
                provided2 = websocket.cookies.get(SECRET_COOKIE, "")
            import hmac as _hmac2

            if not provided2 or not _hmac2.compare_digest(provided2, expected2):
                await websocket.close(code=4003, reason="Unauthorized")
                return
        from kazma_ui.chat import chat_websocket_handler

        await chat_websocket_handler(websocket, self.agent)

    @self.app.get("/", response_class=HTMLResponse)
    async def root(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "config": self.agent.config,
                "active_page": "dashboard",
                "cost_current": 0.0,
                "cost_max": 0.50,
                "cost_headroom": 0.50,
                "cost_color": "var(--success)",
                "breaker_status": "closed",
                "breaker_color": "var(--success)",
                "silence_info": "",
                "tracing_backend": "console",
                "traces": [],
                "metrics": {},
            },
        )

    @self.app.get("/chat", response_class=HTMLResponse)
    async def chat_redirect() -> RedirectResponse:
        return RedirectResponse("/", status_code=307)

    @self.app.get("/workspace", response_class=HTMLResponse)
    async def workspace_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "workspace.html",
            {
                "config": self.agent.config,
                "active_page": "workspace",
            },
        )

    @self.app.get("/ide", response_class=HTMLResponse)
    async def ide_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "ide.html",
            {
                "config": self.agent.config,
                "active_page": "ide",
            },
        )

    @self.app.post("/api/gateway/refresh-adapters")
    async def refresh_gateway_adapters() -> dict[str, Any]:
        if self.gateway is None:
            return {"status": "error", "message": "Gateway not initialized"}
        logger.info("[Gateway] Refreshing adapters — stopping old adapters")

        for old_adapter in self.gateway.adapters:
            try:
                await old_adapter.stop()
            except Exception:
                logger.warning("[Gateway] Error stopping adapter %s during refresh", old_adapter.name, exc_info=True)

        self.gateway.adapters.clear()

        telegram_token = (
            self.config_store.get("connectors.telegram.token", "")
            or self.config.raw.get("connectors", {}).get("telegram", {}).get("token", "")
        )
        if not telegram_token:
            telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

        if telegram_token:
            from kazma_gateway.adapters.telegram import TelegramAdapter

            voice_cfg = self.config.raw.get("gateway", {}).get("voice", {})
            tg_adapter = TelegramAdapter(
                token=telegram_token,
                voice_enabled=voice_cfg.get("enabled", False),
                voice_provider=voice_cfg.get("stt_provider", "openai"),
                stt_api_key=None,
                tts_provider=voice_cfg.get("tts_provider", "edgetts"),
                tts_voice=voice_cfg.get("tts_voice", "default"),
                tts_output_format=voice_cfg.get("tts_output_format", "mp3"),
                stt_language=voice_cfg.get("stt_language", "auto"),
            )
            allowed = self.config_store.get("connectors.telegram.allowed_users", "")
            if allowed:
                try:
                    allowed_ids = [int(uid.strip()) for uid in allowed.split(",") if uid.strip()]
                    tg_adapter.set_allowed_users(allowed_ids)
                except ValueError:
                    logger.warning("[Gateway] Invalid allowed_users format: %s", allowed)
            self.gateway.add_adapter(tg_adapter)
            logger.info("[Gateway] Telegram adapter re-registered via refresh")

        discord_token = self.config_store.get("connectors.discord.token", "") or os.environ.get("DISCORD_BOT_TOKEN", "")
        if discord_token:
            from kazma_gateway.adapters.discord import DiscordAdapter

            discord_adapter = DiscordAdapter(token=discord_token)
            self.gateway.add_adapter(discord_adapter)
            logger.info("[Gateway] Discord adapter re-registered via refresh")

        _cs_slack_bot2 = self.config_store.get("connectors.slack.token", "")
        _cs_slack_app2 = self.config_store.get("connectors.slack.app_token", "")
        slack_bot_token = (_cs_slack_bot2 if _cs_slack_bot2.startswith("xoxb-") else "") or os.environ.get("SLACK_BOT_TOKEN", "")
        slack_app_token = (_cs_slack_app2 if _cs_slack_app2.startswith("xapp-") else "") or os.environ.get("SLACK_APP_TOKEN", "")
        if slack_bot_token:
            from kazma_gateway.adapters.slack import SlackAdapter

            slack_adapter = SlackAdapter(bot_token=slack_bot_token, app_token=slack_app_token or None)
            self.gateway.add_adapter(slack_adapter)
            logger.info("[Gateway] Slack adapter re-registered via refresh")

        for new_adapter in self.gateway.adapters:
            try:
                await new_adapter.start(self.gateway.queue, self.gateway._shutdown)
                logger.info("[Gateway] Adapter %s started via refresh", new_adapter.name)
            except Exception:
                logger.warning("[Gateway] Failed to start adapter %s during refresh", new_adapter.name, exc_info=True)

        logger.info("[Gateway] Adapter refresh complete — %d adapter(s) running", len(self.gateway.adapters))
        return {
            "status": "ok",
            "adapters_count": len(self.gateway.adapters),
            "adapters": [a.name for a in self.gateway.adapters],
        }

    @self.app.get("/health")
    async def health_check() -> dict[str, Any]:
        if self.gateway is None:
            return {
                "status": "ok",
                "gateway_started": False,
                "queue_depth": 0,
                "queue_maxsize": 100,
                "adapters_count": 0,
                "adapters_running": 0,
                "adapters": [],
                "init_errors": self._init_errors,
            }
        adapters = [_a for _a in self.gateway.adapters] if hasattr(self.gateway, 'adapters') else []
        queue = getattr(self.gateway, 'queue', None)
        return {
            "status": "ok",
            "gateway_started": getattr(self.gateway, '_started', False),
            "queue_depth": queue.qsize() if queue else 0,
            "queue_maxsize": queue.maxsize if queue and hasattr(queue, 'maxsize') else 100,
            "adapters_count": len(adapters),
            "adapters_running": sum(1 for a in adapters if getattr(a, '_running', False)),
            "adapters": [
                {
                    "name": getattr(a, 'name', '?'),
                    "platform": getattr(a, 'platform', getattr(a, 'name', '?')),
                    "running": getattr(a, '_running', False),
                }
                for a in adapters
            ],
            "init_errors": self._init_errors,
        }

    def _resolve_hitl_graph() -> Any:
        return self._hitl_state.get("graph") or self._graph_holder.get("graph")

    def _resolve_hitl_checkpointer() -> Any:
        return self._hitl_state.get("checkpointer")

    @self.app.post("/api/approve/{thread_id}")
    async def approve_tool(thread_id: str, request: Request) -> _JSONResponse:
        # Use shared auth: KAZMA_SECRET *or* Account API token.
        from kazma_ui.auth import get_kazma_secret, is_authenticated

        _secret = get_kazma_secret()
        if _secret and not is_authenticated(request, _secret):
            return _JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            logger.debug("[HITL] Malformed or missing JSON body in approval request", exc_info=True)
            return _JSONResponse({"error": "Invalid JSON"}, status_code=400)

        action = body.get("action", "deny")
        approved = action == "approve"
        # scope: once (default) | tool (session grant for this tool) | yolo
        scope = str(body.get("scope") or "once").strip().lower()
        if scope not in ("once", "tool", "yolo", "allow_tool", "session"):
            scope = "once"
        if scope == "allow_tool":
            scope = "tool"
        if scope == "session":
            scope = "yolo"

        graph_ref = _resolve_hitl_graph()
        if graph_ref is None:
            return _JSONResponse({"error": "Graph not available"}, status_code=503)

        # H-2 / S0-3: ownership for *gateway* threads only. Web chat sessions
        # live in SessionManager (not gateway session_store) and already pass
        # session_id from the browser — never 403 web users who legitimately
        # clicked Approve on their own card just because gateway has no row.
        try:
            if self.session_store is not None:
                ctx = None
                try:
                    ctx = await self.session_store.get(thread_id)
                except Exception as _e:
                    logger.debug("[HITL] Failed to fetch session context for ownership check: %s", _e)
                if ctx and isinstance(ctx, dict):
                    owner = (
                        ctx.get("sender_id")
                        or ctx.get("owner")
                        or ctx.get("session_id")
                        or ctx.get("user_id")
                    )
                    # Only enforce when this is clearly a non-web gateway owner
                    # (telegram:/discord:/slack: prefixes or numeric platform ids).
                    owner_s = str(owner or "")
                    is_gateway_owner = bool(
                        owner_s
                        and (
                            owner_s.startswith("telegram:")
                            or owner_s.startswith("discord:")
                            or owner_s.startswith("slack:")
                            or ":" in owner_s
                        )
                    )
                    if is_gateway_owner:
                        # Fail-closed: require session_id for gateway-owned threads
                        # (audit H3 — omit used to skip ownership check entirely)
                        caller_session = body.get("session_id")
                        if not caller_session:
                            logger.warning(
                                "[HITL] Web approve missing session_id for gateway thread %s owner=%s",
                                thread_id,
                                owner,
                            )
                            return _JSONResponse(
                                {
                                    "error": (
                                        "session_id required to approve gateway-owned "
                                        "HITL requests"
                                    )
                                },
                                status_code=403,
                            )
                        if str(owner) != str(caller_session):
                            logger.warning(
                                "[HITL] Web approve ownership mismatch for thread %s: owner=%s caller=%s",
                                thread_id,
                                owner,
                                caller_session,
                            )
                            return _JSONResponse(
                                {"error": "Ownership mismatch: you cannot approve another user's request"},
                                status_code=403,
                            )
        except Exception as _e:
            # Fail-closed (audit M7): never skip ownership on store errors
            logger.warning("[HITL] Ownership check failed — denying: %s", _e)
            return _JSONResponse(
                {"error": "Ownership check failed — approval denied"},
                status_code=403,
            )

        try:
            from langgraph.types import Command

            # Prefer the live checkpointed graph (same instance as SSE).
            graph_ref = _resolve_hitl_graph() or self._graph_holder.get("graph")
            if graph_ref is None:
                return _JSONResponse({"error": "Graph not available"}, status_code=503)

            config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

            # Verify this thread is actually paused before resume — avoids a
            # silent no-op when the wrong graph/checkpointer is wired.
            # Also snapshot messages + pending tools for scope grants / delta text.
            pre = None
            pre_msg_count = 0
            pending_tool_name = ""
            pending_tools: list[Any] = []
            try:
                pre = await graph_ref.aget_state(config)
                has_interrupt = False
                if pre and getattr(pre, "tasks", None):
                    for task in pre.tasks or []:
                        if getattr(task, "interrupts", None):
                            has_interrupt = True
                            break
                if pre and getattr(pre, "next", None) and not has_interrupt:
                    # Pending next but no interrupt payload — still try resume.
                    has_interrupt = True
                if not has_interrupt and not (pre and getattr(pre, "next", None)):
                    logger.warning(
                        "[HITL] No pending interrupt for thread=%s — approve is a no-op",
                        thread_id,
                    )
                    return _JSONResponse(
                        {
                            "status": "noop",
                            "thread_id": thread_id,
                            "content": "",
                            "error": "No pending approval for this thread (already resumed or wrong thread_id).",
                        },
                        status_code=409,
                    )
                if pre is not None:
                    vals = getattr(pre, "values", None) or {}
                    if isinstance(vals, dict):
                        pre_msgs = vals.get("messages") or []
                        pre_msg_count = len(pre_msgs) if isinstance(pre_msgs, list) else 0
                    for task in getattr(pre, "tasks", None) or []:
                        for intr in getattr(task, "interrupts", None) or []:
                            payload = getattr(intr, "value", None)
                            if isinstance(payload, dict) and payload.get("type") == "hitl_approval":
                                pending_tool_name = str(payload.get("tool") or "")
                                pending_tools = list(payload.get("tools") or [])
                                break
            except Exception:
                logger.debug("[HITL] pre-resume state probe failed", exc_info=True)

            # Apply scope grants *before* resume so subsequent danger tools in
            # later supervisor rounds skip interrupt entirely.
            actor = f"web:{(body.get('session_id') or '')[:12] or 'anon'}"
            grant_info: dict[str, Any] | None = None
            if approved and scope == "yolo":
                try:
                    from kazma_core.safety.yolo import YoloDisabledError, enable_yolo

                    grant_info = enable_yolo(thread_id, actor=actor)
                except YoloDisabledError as yde:
                    logger.warning("[HITL] YOLO scope blocked: %s", yde)
                    return _JSONResponse(
                        {
                            "error": str(yde),
                            "status": "yolo_disabled",
                        },
                        status_code=403,
                    )
                except Exception:
                    logger.exception("[HITL] failed to enable YOLO scope")
            elif approved and scope == "tool":
                try:
                    from kazma_core.safety.hitl_grants import grant_tool

                    tools_to_grant: list[str] = []
                    if pending_tools:
                        for t in pending_tools:
                            if isinstance(t, dict) and t.get("name"):
                                tools_to_grant.append(str(t["name"]))
                    elif pending_tool_name and " tools" not in pending_tool_name:
                        tools_to_grant.append(pending_tool_name)
                    # Client may also pass explicit tool name
                    explicit = body.get("tool") or body.get("grant_tool")
                    if explicit:
                        tools_to_grant.append(str(explicit))
                    tools_to_grant = list(dict.fromkeys(tools_to_grant))  # dedupe
                    grant_info = {"tools": []}
                    for tname in tools_to_grant:
                        st = grant_tool(thread_id, tname, actor=actor)
                        grant_info["tools"].append(st)
                except Exception:
                    logger.exception("[HITL] failed to apply tool grant")

            resume_value: dict[str, Any] = {
                "approved": approved,
                "reason": body.get("reason", ""),
                "scope": scope,
            }
            if isinstance(body.get("approved_ids"), list):
                resume_value["approved_ids"] = body["approved_ids"]

            # Thread id for requires_approval/grants during the resumed run
            from kazma_core.safety.hitl import (
                reset_current_thread_id,
                set_current_thread_id,
            )

            _tid_token = set_current_thread_id(thread_id)
            try:
                result = await graph_ref.ainvoke(
                    Command(resume=resume_value),
                    config=config,
                )
            finally:
                reset_current_thread_id(_tid_token)

            def _assistant_text(m: Any) -> str:
                if isinstance(m, dict):
                    role = m.get("role") or m.get("type")
                    text = m.get("content")
                else:
                    role = getattr(m, "type", None) or getattr(m, "role", None)
                    text = getattr(m, "content", None)
                if role not in ("assistant", "ai"):
                    return ""
                if isinstance(text, list):
                    parts: list[str] = []
                    for block in text:
                        if isinstance(block, str):
                            parts.append(block)
                        elif isinstance(block, dict) and block.get("type") == "text":
                            parts.append(str(block.get("text") or ""))
                        else:
                            t = getattr(block, "text", None)
                            if t:
                                parts.append(str(t))
                    text = "".join(parts)
                if text is None:
                    return ""
                s = str(text).strip()
                return s

            # Extract only *new* assistant text produced after resume.
            content = ""
            msgs: list[Any] = []
            if isinstance(result, dict):
                msgs = result.get("messages") or []
            if not msgs:
                try:
                    post = await graph_ref.aget_state(config)
                    vals = getattr(post, "values", None) or {}
                    if isinstance(vals, dict):
                        msgs = vals.get("messages") or []
                except Exception:
                    pass
            if isinstance(msgs, list) and msgs:
                new_msgs = msgs[pre_msg_count:] if pre_msg_count else msgs
                # If pre_msg_count was 0 or wrong, still prefer trailing new AI text
                candidates = [
                    _assistant_text(m) for m in new_msgs if _assistant_text(m)
                ]
                if candidates:
                    content = candidates[-1]
                else:
                    # Fallback: last assistant in full history that is *not*
                    # identical to the last pre-resume assistant (avoid replay).
                    pre_last = ""
                    if pre_msg_count and pre_msg_count <= len(msgs):
                        for m in reversed(msgs[:pre_msg_count]):
                            t = _assistant_text(m)
                            if t:
                                pre_last = t
                                break
                    for m in reversed(msgs):
                        t = _assistant_text(m)
                        if t and t != pre_last:
                            content = t
                            break

            # Detect a *new* HITL interrupt after resume (chain of danger tools).
            next_approval: dict[str, Any] | None = None
            try:
                snapshot = await graph_ref.aget_state(config)
                if snapshot and getattr(snapshot, "next", None):
                    for task in getattr(snapshot, "tasks", []) or []:
                        for intr in getattr(task, "interrupts", []) or []:
                            payload = getattr(intr, "value", None)
                            if isinstance(payload, dict) and payload.get("type") == "hitl_approval":
                                next_approval = {
                                    "thread_id": thread_id,
                                    "tool": payload.get("tool", ""),
                                    "args": payload.get("args", {}),
                                    "tools": payload.get("tools") or [],
                                    "message": payload.get("message", ""),
                                }
                                break
                        if next_approval:
                            break
            except Exception:
                logger.debug("[HITL] post-resume interrupt probe failed", exc_info=True)

            # If the turn finished with no new prose, give the UI a clear note
            # instead of going silent (common after long shell_exec chains).
            if approved and not content and not next_approval:
                content = (
                    "_Tools finished. The model did not return more text — "
                    "ask a follow-up, or check tool results above._"
                )
            elif approved and not content and next_approval:
                # Mid-chain: don't spam old text; optional quiet status
                content = ""

            # Persist *new* assistant text into SessionManager (not replays).
            if content and not content.startswith("_Tools finished"):
                try:
                    from kazma_ui.session_manager import get_session_manager

                    store = get_session_manager()
                    sid = (body.get("session_id") or "").strip()
                    sess = store.get(sid) if sid else None
                    if sess is None:
                        for s in store.list_all(include_archived=True):
                            if s.thread_id == thread_id:
                                sess = s
                                break
                    if sess is not None:
                        sess.messages.append({"role": "assistant", "content": content})
                        store.put(sess)
                except Exception:
                    logger.debug("[HITL] session persist after resume failed", exc_info=True)

            payload_out: dict[str, Any] = {
                "status": "approved" if approved else "denied",
                "thread_id": thread_id,
                "content": content,
                "scope": scope,
            }
            if grant_info is not None:
                payload_out["grant"] = grant_info
            if next_approval:
                payload_out["approval_required"] = next_approval
            return _JSONResponse(payload_out, status_code=202)
        except Exception:
            logger.exception("[HITL] Failed to resume graph for thread=%s", thread_id)
            return _JSONResponse({"error": "Internal error"}, status_code=500)

    @self.app.get("/api/pending-approvals")
    async def list_pending_approvals() -> _JSONResponse:
        from kazma_ui.hitl_approval import _get_pending_approvals

        graph = _resolve_hitl_graph()
        checkpointer = _resolve_hitl_checkpointer()
        if graph is None or checkpointer is None:
            return _JSONResponse(
                {"pending": [], "count": 0, "error": "Graph/checkpointer not yet initialized"},
                status_code=503,
            )
        try:
            pending = await _get_pending_approvals(graph, checkpointer)
            return _JSONResponse({"pending": pending, "count": len(pending)})
        except Exception:
            logger.exception("[HITL] Failed to list pending approvals")
            return _JSONResponse({"pending": [], "count": 0, "error": "Internal error"}, status_code=500)

    @self.app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        return {
            "status": "degraded" if self._init_errors else "ok",
            "init_errors": self._init_errors,
        }

    # ── Workspace selection + file-tree scanner ────────────────────────
    try:
        from kazma_gateway.routers.workspace import create_workspace_select_router

        self.app.include_router(create_workspace_select_router())
        logger.info("[routes_direct] Workspace select/tree router mounted at /api/workspace/select, /api/workspace/tree")
    except Exception as _exc:
        logger.warning("[routes_direct] Workspace select/tree router failed to mount: %s", _exc)

    # ── Workspaces Multi-Project Router ────────────────────────────────
    try:
        from kazma_gateway.routers.workspaces import create_workspaces_router

        self.app.include_router(create_workspaces_router())
        logger.info("[routes_direct] Workspaces router mounted at /api/workspaces")
    except Exception as _exc:
        logger.warning("[routes_direct] Workspaces router failed to mount: %s", _exc)

    # ── Live Git status ────────────────────────────────────────────────
    try:
        from kazma_gateway.routers.git import create_git_router

        self.app.include_router(create_git_router())
        logger.info("[routes_direct] Git router mounted at /api/git/status")
    except Exception as _exc:
        logger.warning("[routes_direct] Git router failed to mount: %s", _exc)

    # ── Live GitHub integration ────────────────────────────────────────
    try:
        from kazma_gateway.routers.github import create_github_router

        self.app.include_router(create_github_router())
        logger.info("[routes_direct] GitHub router mounted at /api/github")
    except Exception as _exc:
        logger.warning("[routes_direct] GitHub router failed to mount: %s", _exc)

    # ── Bookmarks CRUD ─────────────────────────────────────────────────
    try:
        from kazma_gateway.routers.bookmarks import create_bookmarks_router

        self.app.include_router(create_bookmarks_router())
        logger.info("[routes_direct] Bookmarks router mounted at /api/bookmarks")
    except Exception as _exc:
        logger.warning("[routes_direct] Bookmarks router failed to mount: %s", _exc)

    # ── Visual Pipeline Sandbox ────────────────────────────────────────
    try:
        from kazma_gateway.routers.pipeline import create_pipeline_router

        self.app.include_router(create_pipeline_router())
        logger.info("[routes_direct] Visual pipeline router mounted at /api/pipelines")
    except Exception as _exc:
        logger.warning("[routes_direct] Visual pipeline router failed to mount: %s", _exc)

    # Config Migration UI (extracted to routes_migrate)
    try:
        from kazma_ui.routes_migrate import register_migrate_routes

        register_migrate_routes(self.app)
    except Exception as _exc:
        logger.warning("[routes_direct] Config migration endpoints failed to mount: %s", _exc)

    # Chaos Testing UI (extracted to routes_chaos; env-gated)
    try:
        from kazma_ui.routes_chaos import register_chaos_routes

        register_chaos_routes(self.app)
    except Exception as _exc:
        logger.warning("[routes_direct] Chaos testing endpoints failed to mount: %s", _exc)
