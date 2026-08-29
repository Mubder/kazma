"""System status, diagnostics, maintenance, and flush endpoints.

Extracted from the former ``kazma_ui/routes_direct.py`` god module
(3,862 lines) — audit O5. Handler bodies are unchanged; only their
module changed. Registration order within this group is preserved.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, Request
from kazma_core.errors import safe_error

from kazma_ui.rate_limit import rate_limit

logger = logging.getLogger(__name__)

__all__ = ["register_system_routes"]


def register_system_routes(self: Any) -> None:
    """Register the system routes onto ``self.app``."""
    # NOTE: GET /metrics is provided by kazma_ui.metrics.create_metrics_router
    # (mounted in app.py), which exposes the full set (swarm gauges,
    # kazma_commitment_decisions_total, etc.). A minimal duplicate handler
    # here shadowed it depending on registration order — removed (audit).

    @self.app.get("/api/system/debug/registry")
    async def _debug_registry():
        import kazma_core.model_registry as _mr

        from kazma_ui.settings import mask_deep

        reg = _mr._registry
        if reg is None:
            return {"status": "not_initialized", "hint": "ModelRegistry not initialized. Start the app normally."}
        # Masked wholesale (audit F-02): _list_all_providers() returns provider
        # dicts carrying live api_key values, which used to ship in the clear
        # from this debug endpoint even though its sibling llm.api_key field
        # was masked.
        return mask_deep({
            "status": "initialized",
            "active_provider": reg._active_provider or "none",
            "active_profile": reg.get_active_profile(),
            "providers": reg._list_all_providers() if hasattr(reg, '_list_all_providers') else [],
            "saved_profiles": reg.list_model_profiles(mask_api_key=True),
            "registered_models": reg._registered_models if hasattr(reg, '_registered_models') else {},
            "discovered_models": reg.get_discovered_models(),
            "unified_options": reg.list_unified_options(),
        })
    @self.app.post("/api/system/flush", dependencies=[Depends(rate_limit("system_flush", 6))])
    async def _system_flush():
        import glob as _glob_sys
        import os as _os_sys

        try:
            from kazma_core.paths import data_dir, settings_db, user_home

            _home = user_home()
            paths = {
                "kazma_home": str(_home),
                "config_db": settings_db(),
                "config_yaml": next(iter(_glob_sys.glob(str(_home / "*.yaml"))), ""),
                "pending_evolution": str(_home / "pending_evolution.json"),
                "knowledge_graph": str(data_dir() / "knowledge_graph.db"),
            }
        except Exception:
            paths = {
                "kazma_home": str(_os_sys.path.expanduser("~/.kazma")),
                "config_db": str(_os_sys.path.expanduser("~/.kazma/config.db")),
                "config_yaml": next(
                    iter(_glob_sys.glob(_os_sys.path.expanduser("~/.kazma/*.yaml"))), ""
                ),
                "pending_evolution": str(
                    _os_sys.path.expanduser("~/.kazma/pending_evolution.json")
                ),
                "knowledge_graph": str(
                    _os_sys.path.expanduser("kazma-data/knowledge_graph.json")
                ),
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

            # LocalToolRegistry caches the singleton in _builtin_registry.
            import kazma_core.agent.tool_registry as _tr_mod

            _tr_mod._builtin_registry = None
        except Exception as exc:
            logger.debug("Tool registry cache flush failed: %s", exc)
        return {"status": "flushed", "config_paths": paths}
    @self.app.get("/api/system/config-paths")
    async def _system_config_paths():
        import os as _osp

        try:
            from kazma_core.paths import data_dir, settings_db, snapshots_db, user_home

            home = str(user_home())
            cfg = settings_db()
            kg = str(data_dir() / "knowledge_graph.db")
            snap = snapshots_db()
            pending = str(user_home() / "pending_evolution.json")
        except Exception:
            home = _osp.path.expanduser("~/.kazma")
            cfg = _osp.path.join(home, "config.db")
            kg = _osp.path.expanduser("kazma-data/knowledge_graph.json")
            snap = _osp.path.expanduser("kazma-data/snapshots.db")
            pending = _osp.path.join(home, "pending_evolution.json")
        return {
            "kazma_home": home,
            "config_db": cfg if _osp.path.exists(cfg) else "NOT FOUND",
            "swarm_registry": (
                _osp.path.expanduser("swarm_registry.json")
                if _osp.path.exists(_osp.path.expanduser("swarm_registry.json"))
                else "NOT FOUND"
            ),
            "pending_evolution": pending if _osp.path.exists(pending) else "NOT FOUND",
            "knowledge_graph": kg if _osp.path.exists(kg) else "NOT FOUND",
            "snapshots_db": snap if _osp.path.exists(snap) else "NOT FOUND",
        }
    @self.app.get("/api/system/status")
    def _get_system_status():
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
                # Canonical L3 schema first; legacy memory_fts as fallback.
                for table in ("memories", "memory_fts", "memory_fts_migrated"):
                    cursor.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?",
                        (table,),
                    )
                    if cursor.fetchone():
                        try:
                            cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
                            fts5_count = int(cursor.fetchone()[0] or 0)
                            break
                        except Exception:
                            continue
                conn.close()
            except Exception as _e:
                logger.debug("fts5 count failed: %s", _e)
                try:
                    conn.close()  # type: ignore[name-defined]
                except Exception:
                    pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass  # already closed / never opened

        vector_size = 0
        if vector_path.exists() and vector_path.is_dir():
            vector_size = sum(
                f.stat().st_size for f in vector_path.glob("**/*") if f.is_file()
            )

        # V1 ChromaDB vector count — always 0 now (V1 stack removed). Kept in
        # the payload for dashboard backward-compat (the KPI grid still reads
        # vector_count/vector_size); the values are inert.
        vector_count = 0

        # V2 cognitive-engine KPIs (computed early so graph_stats below can
        # reuse the belief counts). When V2 is the active stack, the dashboard
        # should prefer this `v2` block over the legacy layer counts above.
        v2_block: dict = {}
        memory_stack = "v1"
        try:
            from kazma_core.memory.config import memory_v2_enabled

            if memory_v2_enabled():
                memory_stack = "v2"
                from kazma_core.memory.v2_health import build_v2_health

                v2_block = build_v2_health()
        except Exception as _e:
            logger.debug("v2 health probe failed: %s", _e)

        # V2 belief counts, surfaced in the legacy `graph` field shape so the
        # dashboard KPI strip keeps working. "nodes" = active beliefs;
        # "edges" = active + superseded (the full belief graph). The real V2
        # KPI board (/api/memory/v2/health) is the authoritative source.
        graph_stats: dict = {"nodes": 0, "edges": 0, "backend": "v2", "path": ""}
        try:
            bel = (v2_block.get("beliefs") if v2_block else None) or {}
            active = int(bel.get("active", 0))
            superseded = int(bel.get("superseded", 0))
            graph_stats["nodes"] = active
            graph_stats["edges"] = active + superseded
        except Exception as _e:
            logger.debug("v2 belief stats failed: %s", _e)

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

        # Compact feature flags from health components for the KPI strip.
        flags: dict = {}
        for c in health.get("components") or []:
            cid = c.get("id")
            if cid in (
                "memory_enabled",
                "per_turn_retrieval",
                "auto_store",
                "consolidation",
                "embedder",
                "vector_memory",
                "layer_l1",
                "layer_l2",
                "layer_l3",
                "layer_l4",
            ):
                flags[cid] = {
                    "ok": bool(c.get("ok")),
                    "status": c.get("status"),
                    "detail": c.get("detail"),
                    "meta": c.get("meta") or {},
                }

        return {
            "status": status,
            "memory_stack": memory_stack,
            "fts5_size": fts5_size,
            "fts5_count": fts5_count,
            "vector_size": vector_size,
            "vector_count": vector_count,
            "graph": graph_stats,
            "flags": flags,
            "v2": v2_block,
            "components": health.get("components", []),
            "issues": health.get("issues", []),
            "summary": health.get("summary", ""),
            "headline": health.get("headline", ""),
            "backend": health.get("backend", {}),
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
    @self.app.get("/api/system/memory/backups")
    async def _list_memory_backups():
        """List V2 native backup files (memory_state_<ts>.db / memory_ops_<ts>.db)."""
        from pathlib import Path

        from kazma_core.paths import backups_dir

        try:
            bdir = Path(backups_dir())
            backups: list[dict[str, Any]] = []
            if bdir.is_dir():
                for f in sorted(bdir.glob("memory_*_*.db"), reverse=True):
                    try:
                        st = f.stat()
                    except Exception:
                        continue
                    backups.append({
                        "name": f.name,
                        "size": st.st_size,
                        "timestamp": int(st.st_mtime),
                        "path": str(f),
                    })
            return {"backups": backups}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=safe_error(e))
    @self.app.get("/api/system/packages")
    async def _get_packages(request: Request):
        """List installed Python packages with metadata and extras status."""
        import importlib.metadata as ilm

        lang = request.cookies.get("kazma-lang") or "en"
        if lang not in ("ar", "en"):
            lang = "en"

        def _i18n(key: str, fallback: str) -> str:
            try:
                from kazma_ui.i18n import t as i18n_t

                loc = i18n_t(key, lang=lang)
                return loc if loc and loc != key else fallback
            except Exception:
                return fallback

        # ── Define the extras groups and their member packages ──
        # Keep package lists aligned with pyproject.toml optional-deps.
        # Keep package lists aligned with pyproject.toml [project.optional-dependencies]
        EXTRA_GROUPS = {
            "rag": {
                "title": _i18n("packages.extra.rag.title", "Memory & RAG"),
                "priority": 0,
                "description": _i18n(
                    "packages.extra.rag.desc",
                    "V2 cognitive memory: local embeddings (sentence-transformers) + sqlite-vec. chromadb optional legacy.",
                ),
                "packages": ["sentence-transformers", "sqlite-vec", "chromadb"],
                "install_cmd": 'uv pip install -e ".[rag]"   # embedder + local vectors',
            },
            "postgres": {
                "title": _i18n("packages.extra.postgres.title", "Postgres (multi-replica)"),
                "priority": 1,
                "description": _i18n(
                    "packages.extra.postgres.desc",
                    "Multi-replica shared state on Postgres + LangGraph Postgres checkpointer.",
                ),
                "packages": ["psycopg", "langgraph-checkpoint-postgres"],
                "install_cmd": 'uv pip install -e ".[postgres]"   # then set KAZMA_DATABASE_URL + migrate',
            },
            "document": {
                "title": _i18n("packages.extra.document.title", "Document generation"),
                "priority": 4,
                "description": _i18n(
                    "packages.extra.document.desc",
                    "PDF/DOCX/XLSX generation for document_generator skill.",
                ),
                "packages": [
                    "reportlab", "python-docx", "openpyxl", "arabic-reshaper",
                    "python-bidi", "pypdf", "pdfplumber", "python-pptx",
                ],
                "install_cmd": 'uv pip install -e ".[document]"',
            },
            "database": {
                "title": _i18n("packages.extra.database.title", "Extra DB drivers"),
                "priority": 5,
                "description": _i18n(
                    "packages.extra.database.desc",
                    "MySQL/Mongo drivers for database_client skill (beyond SQLite/Postgres).",
                ),
                "packages": ["psycopg", "pymysql", "pymongo"],
                "install_cmd": 'uv pip install -e ".[database]"',
            },
            "dev": {
                "title": _i18n("packages.extra.dev.title", "Development"),
                "priority": 10,
                "description": _i18n(
                    "packages.extra.dev.desc",
                    "Development tools — pytest, ruff, mypy, locust.",
                ),
                "packages": ["pytest", "pytest-asyncio", "pytest-cov", "pytest-mock", "ruff", "mypy", "locust"],
                "install_cmd": 'uv pip install -e ".[dev]"   # additive — won\'t remove other extras',
            },
            "test": {
                "title": _i18n("packages.extra.test.title", "Test"),
                "priority": 11,
                "description": _i18n(
                    "packages.extra.test.desc",
                    "Test-specific dependencies (lighter than dev).",
                ),
                "packages": ["pytest", "pytest-asyncio", "pytest-cov", "pytest-mock", "fakeredis", "httpx"],
                "install_cmd": 'uv pip install -e ".[test]"   # additive — won\'t remove other extras',
            },
            "tui": {
                "title": _i18n("packages.extra.tui.title", "TUI dashboard"),
                "priority": 6,
                "description": _i18n(
                    "packages.extra.tui.desc",
                    "Terminal dashboard UI (Textual) with RTL text.",
                ),
                "packages": ["textual", "python-bidi"],
                "install_cmd": 'uv pip install -e ".[tui]"   # additive — won\'t remove other extras',
            },
            "observability": {
                "title": _i18n("packages.extra.observability.title", "Observability"),
                "priority": 7,
                "description": _i18n(
                    "packages.extra.observability.desc",
                    "Prometheus metrics export for production monitoring.",
                ),
                "packages": ["prometheus-client"],
                "install_cmd": 'uv pip install -e ".[observability]"   # additive — won\'t remove other extras',
            },
            "web": {
                "title": _i18n("packages.extra.web.title", "Browser automation"),
                "priority": 8,
                "description": _i18n(
                    "packages.extra.web.desc",
                    "Browser automation via Playwright for JS-heavy pages.",
                ),
                "packages": ["playwright"],
                "install_cmd": 'uv pip install -e ".[web]"   # then: python -m playwright install chromium',
            },
            "index": {
                "title": _i18n("packages.extra.index.title", "Codebase index"),
                "priority": 2,
                "description": _i18n(
                    "packages.extra.index.desc",
                    "tree-sitter grammars for codebase_search. Regex fallback works without this extra.",
                ),
                "packages": ["tree-sitter", "tree-sitter-python", "tree-sitter-javascript"],
                "install_cmd": 'uv pip install -e ".[index]"',
            },
            "sandbox": {
                "title": _i18n("packages.extra.sandbox.title", "E2B sandbox"),
                "priority": 3,
                "description": _i18n(
                    "packages.extra.sandbox.desc",
                    "Firecracker python_exec via E2B. Needs E2B_API_KEY. Default remains local exec.",
                ),
                "packages": ["e2b-code-interpreter"],
                "install_cmd": 'uv pip install -e ".[sandbox]"   # then set E2B_API_KEY',
            },
            "durable": {
                "title": _i18n("packages.extra.durable.title", "Temporal durable swarm"),
                "priority": 3,
                "description": _i18n(
                    "packages.extra.durable.desc",
                    "Temporal-wrapped swarm dispatch (crash-resume). Needs KAZMA_TEMPORAL_HOST. Default is in-process.",
                ),
                "packages": ["temporalio"],
                "install_cmd": 'uv pip install -e ".[durable]"   # then set KAZMA_TEMPORAL_HOST',
            },
            "docling": {
                "title": _i18n("packages.extra.docling.title", "Docling PDF salvage"),
                "priority": 4,
                "description": _i18n(
                    "packages.extra.docling.desc",
                    "Local Docling extract for hard PDFs after PyMuPDF. Optional; skip if unused.",
                ),
                "packages": ["docling"],
                "install_cmd": 'uv pip install -e ".[docling]"',
            },
            "ocr": {
                "title": _i18n("packages.extra.ocr.title", "OCR"),
                "priority": 5,
                "description": _i18n(
                    "packages.extra.ocr.desc",
                    "Tesseract OCR for scanned documents. Also install system tesseract-ocr.",
                ),
                "packages": ["pytesseract", "pdf2image", "pillow"],
                "install_cmd": 'uv pip install -e ".[ocr]"   # plus OS Tesseract',
            },
            "convert": {
                "title": _i18n("packages.extra.convert.title", "HTML/Markdown → PDF"),
                "priority": 6,
                "description": _i18n(
                    "packages.extra.convert.desc",
                    "WeasyPrint conversion. Needs OS fonts.",
                ),
                "packages": ["weasyprint"],
                "install_cmd": 'uv pip install -e ".[convert]"',
            },
            "document-platform": {
                "title": _i18n("packages.extra.document_platform.title", "Document Intelligence engines"),
                "priority": 4,
                "description": _i18n(
                    "packages.extra.document_platform.desc",
                    "Parse/redact/render (PyMuPDF + PDFium) plus document/ocr/convert extras.",
                ),
                "packages": ["pymupdf", "pypdfium2"],
                "install_cmd": 'uv pip install -e ".[document-platform]"',
            },
            "push": {
                "title": _i18n("packages.extra.push.title", "Web Push"),
                "priority": 9,
                "description": _i18n(
                    "packages.extra.push.desc",
                    "pywebpush for turn-complete notifications. Feature self-disables if missing.",
                ),
                "packages": ["pywebpush"],
                "install_cmd": 'uv pip install -e ".[push]"',
            },
        }

        EXTRA_PKG_DESCRIPTIONS = {
            "chromadb": "Optional legacy vector store (not required for V2 SQLite-first memory)",
            "sentence-transformers": "Local embeddings for V2 dense recall (e.g. BAAI/bge-m3)",
            "sqlite-vec": "Local vector tables for V2 hybrid dense recall",
            "neo4j": "Bolt driver for optional Neo4j belief dual-write (pip install neo4j)",
            "psycopg": "Postgres driver for multi-replica ConfigStore / sessions / dual-mirror",
            "langgraph-checkpoint-postgres": "Shared LangGraph checkpoints across replicas",
            "playwright": "Headless browser for JS-heavy crawl / research",
            "prometheus-client": "Prometheus /metrics exposition",
            "textual": "TUI framework for kazma-tui",
            "python-bidi": "Bidirectional text for Arabic TUI / documents",
            "reportlab": "PDF generation",
            "python-docx": "Word document generation",
            "openpyxl": "Excel generation",
            "arabic-reshaper": "Arabic text shaping for PDF/DOCX",
            "pymysql": "MySQL driver for database_client skill",
            "pymongo": "MongoDB driver for database_client skill",
            "fakeredis": "In-memory Redis stub for tests",
            "httpx": "HTTP client (also listed in test extra for completeness)",
            "tree-sitter": "Parser runtime for the codebase index",
            "tree-sitter-python": "Python grammar for codebase_search",
            "tree-sitter-javascript": "JavaScript grammar for codebase_search",
            "e2b-code-interpreter": "E2B Firecracker sandboxes for python_exec",
            "temporalio": "Temporal SDK for durable swarm steps",
            "docling": "Local hard-PDF salvage after PyMuPDF",
            "pywebpush": "Web Push (VAPID) for turn-complete",
            "pytesseract": "Tesseract Python bindings",
            "pdf2image": "PDF page rasterizer for OCR",
            "pillow": "Image I/O for OCR / documents",
            "weasyprint": "HTML/Markdown to PDF",
            "pymupdf": "MuPDF parser (import fitz)",
            "pypdfium2": "PDFium parser peer",
            "pypdf": "PDF read/write",
            "pdfplumber": "PDF table/text extract",
            "python-pptx": "PowerPoint generation",
        }

        # ── Core dependencies (always installed via monorepo packages) ──
        CORE_PACKAGES = [
            "fastapi", "uvicorn", "langgraph", "langgraph-checkpoint-sqlite",
            "aiosqlite", "langfuse", "pyyaml", "httpx", "cryptography",
            "PyJWT", "jinja2", "python-multipart", "psutil",
            "aiogram", "websockets", "duckduckgo-search", "trafilatura",
            "markdown", "tenacity", "networkx", "click", "rich",
            "google-cloud-aiplatform", "python-dotenv",
            # Workspace packages (editable install)
            "kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli",
        ]

        CORE_DESCRIPTIONS = {
            "fastapi": "Web framework powering the Kazma dashboard + REST API",
            "uvicorn": "ASGI server that runs the FastAPI app",
            "langgraph": "LangGraph supervisor brain — the ReAct loop, checkpointing, interrupt()",
            "langgraph-checkpoint-sqlite": "SQLite-backed LangGraph checkpoints (default single-node)",
            "aiosqlite": "Async SQLite driver for default local stores",
            "langfuse": "Observability/tracing platform for LLM calls",
            "pyyaml": "YAML parser for kazma.yaml config + skill manifests",
            "httpx": "HTTP client for LLM API calls + web tools",
            "cryptography": "AES-256-GCM encryption for the secret vault",
            "PyJWT": "JWT token generation for GitHub App authentication",
            "jinja2": "HTML template engine for the web UI",
            "python-multipart": "File upload handling for FastAPI",
            "psutil": "System resource monitoring for telemetry",
            "aiogram": "Telegram Bot API framework",
            "websockets": "WebSocket support for real-time chat + gateway",
            "duckduckgo-search": "Privacy-focused web search (no API key needed)",
            "trafilatura": "Web content extraction (clean text from URLs)",
            "markdown": "Markdown rendering for chat messages",
            "tenacity": "Retry logic with exponential backoff for LLM calls",
            "networkx": "Graph algorithms for swarm DAG/topology",
            "click": "CLI framework for the `kazma` command",
            "rich": "Beautiful terminal output (colors, tables, progress bars)",
            "google-cloud-aiplatform": "Google Vertex AI provider integration",
            "python-dotenv": ".env file loading for local development",
            "kazma-core": "Agent brain, LLM providers, swarm, V2 memory, IDE",
            "kazma-ui": "FastAPI web app + Settings + Dashboard",
            "kazma-gateway": "Telegram / Discord / Slack adapters",
            "kazma-cli": "`kazma` CLI entrypoints",
        }

        # Surface neo4j driver if installed (not a pyproject extra — pip install neo4j)
        try:
            import importlib.util as _ilu

            if _ilu.find_spec("neo4j") is not None:
                EXTRA_GROUPS["neo4j"] = {
                    "title": "Neo4j (graph dual-write)",
                    "priority": 2,
                    "description": "Official neo4j Python driver for optional belief graph dual-write.",
                    "packages": ["neo4j"],
                    "install_cmd": "pip install neo4j   # then Settings → Memory → Neo4j",
                }
        except Exception:
            pass

        # Runtime DB backend badge for the Packages tab
        try:
            from kazma_core.db.backend import get_database_url, is_postgres

            _db_backend = "postgres" if is_postgres() else "sqlite"
            _db_url = get_database_url() or ""
        except Exception:
            _db_backend = "sqlite"
            _db_url = ""

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
                info["description"] = EXTRA_PKG_DESCRIPTIONS.get(name, "")
                pkg_list.append(info)
            n_total = len(pkg_list)
            n_ok = sum(1 for p in pkg_list if p["installed"])
            group_installed = n_total > 0 and n_ok == n_total
            extras_list.append({
                "name": group_name,
                "title": group_data.get("title") or group_name,
                "priority": int(group_data.get("priority", 50)),
                "description": group_data["description"],
                "install_cmd": group_data["install_cmd"],
                "installed": group_installed,
                "partial": n_ok > 0 and n_ok < n_total,
                "installed_count": n_ok,
                "package_count": n_total,
                "packages": pkg_list,
            })
        extras_list.sort(key=lambda e: (e.get("priority", 50), e.get("name") or ""))

        # Live memory health snapshot for the Packages tab Memory card.
        memory_summary: dict = {
            "status": "UNKNOWN",
            "summary": "",
            "headline": "",
            "layers": {},
            "issues": [],
        }
        try:
            from kazma_core.memory.health import build_memory_health

            mh = build_memory_health()
            layers = {}
            for c in mh.get("components") or []:
                cid = c.get("id") or ""
                if cid in (
                    "embedder",
                    "vector_memory",
                    "layer_l1",
                    "layer_l2",
                    "layer_l3",
                    "layer_l4",
                    "pkg_chromadb",
                    "pkg_st",
                    "pkg_sqlite_vec",
                    "per_turn_retrieval",
                    "auto_store",
                    "consolidation",
                ):
                    layers[cid] = {
                        "ok": bool(c.get("ok")),
                        "status": c.get("status"),
                        "name": c.get("name"),
                        "detail": c.get("detail"),
                    }
            memory_summary = {
                "status": mh.get("status") or "UNKNOWN",
                "summary": mh.get("summary") or "",
                "headline": mh.get("headline") or "",
                "layers": layers,
                "issues": (mh.get("issues") or [])[:6],
            }
        except Exception as exc:
            memory_summary["summary"] = f"health probe failed: {exc}"

        # Count total installed (from distributions, not just our deps)
        total_installed = len(all_dists)

        return {
            "core": core_list,
            "extras": extras_list,
            "total_installed": total_installed,
            "python_version": __import__("sys").version.split()[0],
            "db_backend": _db_backend,
            "db_url_set": bool(_db_url),
            "memory": memory_summary,
        }
    @self.app.post("/api/system/memory/backup")
    async def _create_memory_backup():
        """V2 native backup — sqlite3.backup() of both V2 memory DBs."""
        try:
            from kazma_core.memory.backup import perform_native_backups

            written = perform_native_backups(retention=10)
            return {
                "status": "success",
                "manifest": {
                    "files": [str(p) for p in written],
                    "count": len(written),
                    "dir": "backups/",
                },
            }
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=safe_error(e))
    @self.app.post("/api/system/memory/restore")
    def _restore_memory_backup(req: dict):
        """V2 restore — file-swap memory_state.db from a chosen backup.

        Body: ``{"backup_name": "memory_state_<ts>.db"}``. Quiesces the V2
        worker first (best-effort), then overwrites the live primary DB with
        the backup copy via sqlite3.restore(). Bi-temporal ops DB is NOT
        restored (it is rebuildable from the primary). Returns the new counts.
        """
        backup_name = str(req.get("backup_name", "")).strip()
        if not backup_name:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="backup_name is required")
        # Path-traversal guard: backup_name must be a plain filename inside
        # backups_dir() — no separators, no parent refs. Otherwise a caller
        # could point at any SQLite file on disk (clobber the live DB, or read
        # it back via /api/memory/v2/beliefs).
        if "/" in backup_name or "\\" in backup_name or ".." in backup_name:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="invalid backup name")
        import sqlite3
        from pathlib import Path

        from kazma_core.paths import backups_dir, primary_memory_db

        try:
            _backups_root = Path(backups_dir()).resolve()
            src = (Path(backups_dir()) / backup_name).resolve()
            try:
                src.relative_to(_backups_root)
            except ValueError:
                from fastapi import HTTPException
                raise HTTPException(status_code=400, detail="invalid backup name")
            if not src.exists() or not src.is_file():
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail=f"backup {backup_name!r} not found")
            # Quiesce: nudge the durable worker to pause so no write races the swap.
            try:
                from kazma_core.memory.task_queue import get_worker

                get_worker()._wake.clear()
            except Exception:
                pass
            dest = Path(primary_memory_db())
            # Online restore: open the live DB and restore from the backup file.
            conn = sqlite3.connect(str(dest))
            try:
                with sqlite3.connect(str(src)) as bkp:
                    bkp.backup(conn, pages=100, sleep=0.01)
                conn.commit()
            finally:
                conn.close()
            # Count active beliefs in the restored DB for confirmation.
            conn = sqlite3.connect(str(dest))
            conn.row_factory = sqlite3.Row
            active = conn.execute(
                "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
            ).fetchone()[0]
            conn.close()
            return {
                "status": "success",
                "restored_from": backup_name,
                "active_beliefs": active,
            }
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=safe_error(e))
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.post("/api/system/memory/maintenance")
    def _run_memory_maintenance():
        """V2 maintenance — VACUUM + ANALYZE both V2 memory DBs."""
        import sqlite3

        from kazma_core.paths import memory_ops_db, primary_memory_db

        try:
            details: dict[str, Any] = {}
            for label, db in (("primary", primary_memory_db()), ("ops", memory_ops_db())):
                try:
                    conn = sqlite3.connect(db)
                    conn.execute("VACUUM")
                    conn.execute("ANALYZE")
                    conn.close()
                    details[label] = "VACUUM + ANALYZE ok"
                except Exception as exc:
                    details[label] = f"failed: {exc}"
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass  # already closed / never opened
            return {"status": "success", "details": details}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=safe_error(e))
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.post("/api/system/snapshots/maintain")
    async def _run_snapshot_maintenance():
        """Time-travel snapshots: TTL prune + VACUUM to reclaim disk.

        Retention is read LIVE from the ConfigStore (Settings UI), so the
        manual run and the daily auto-loop always agree.
        """
        from kazma_core.time_travel import _live_maintenance_config, maintain_snapshots

        try:
            cfg = _live_maintenance_config()
            stats = maintain_snapshots(retention_days=cfg["retention_days"])
            return {"status": "success", "stats": stats, "auto_maintain": cfg["auto_maintain"]}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=safe_error(e))
