"""Health check endpoints for Kazma.

Provides /health/live and /health/ready endpoints for Kubernetes
liveness and readiness probes.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

__all__ = [
    "check_agent_runner",
    "check_config_store",
    "check_model_registry",
    "check_swarm_engine",
    "get_health_dependencies",
    "router",
]

router = APIRouter(tags=["health"])


def get_health_dependencies():
    """Get all dependencies for health checks.

    Returns a dict of component checkers that can be called.
    """
    from kazma_core.config_store import get_config_store
    from kazma_core.swarm import get_swarm_engine
    from kazma_core.model_registry import get_registry

    return {
        "config_store": get_config_store,
        "swarm_engine": get_swarm_engine,
        "agent_runner": check_agent_runner,
        "model_registry": get_registry,
    }


def check_config_store() -> dict[str, Any]:
    """Check ConfigStore connectivity."""
    try:
        from kazma_core.config_store import get_config_store
        store = get_config_store()
        # Test read
        _ = store.get("health.check", "ok")
        return {"status": "ok", "component": "config_store"}
    except Exception as e:
        logger.error("ConfigStore health check failed: %s", e)
        return {"status": "failed", "component": "config_store", "error": str(e)}


def check_swarm_engine() -> dict[str, Any]:
    """Check SwarmEngine availability."""
    try:
        from kazma_core.swarm import get_swarm_engine
        engine = get_swarm_engine()
        if engine is None:
            return {"status": "not_initialized", "component": "swarm_engine"}
        # Public API only (no private _workers access)
        workers = engine.list_workers() if hasattr(engine, "list_workers") else []
        return {"status": "ok", "component": "swarm_engine", "workers": len(workers)}
    except Exception as e:
        logger.error("SwarmEngine health check failed: %s", e)
        return {"status": "failed", "component": "swarm_engine", "error": "check failed"}


def check_model_registry() -> dict[str, Any]:
    """Check ModelRegistry availability and surface the active profile."""
    try:
        from kazma_core.model_registry import get_model_registry
        registry = get_model_registry()
        if registry is None:
            return {"status": "not_initialized", "component": "model_registry"}
        providers = registry.list_providers() if hasattr(registry, "list_providers") else []
        profile: dict[str, Any] = {}
        try:
            profile = registry.get_active_profile() or {}
        except Exception:
            profile = {}
        return {
            "status": "ok",
            "component": "model_registry",
            "providers": len(providers),
            "active_model": str(profile.get("model") or ""),
            "active_provider": str(profile.get("provider") or ""),
        }
    except Exception as e:
        logger.error("ModelRegistry health check failed: %s", e)
        return {"status": "failed", "component": "model_registry", "error": "check failed"}


def check_agent_runner() -> dict[str, Any]:
    """Check AgentRunner availability (structural — module + class importable).

    ``KazmaAgent`` is constructed on-demand per chat turn (not held as a
    process singleton), so this verifies the ``agent_runner`` module and
    ``KazmaAgent`` class import cleanly and that ``get_streaming_graph`` is
    present. A failure here means the chat subsystem cannot build its graph.
    """
    try:
        from kazma_core.agent_runner import KazmaAgent

        # get_streaming_graph is the per-request entry the SSE chat path uses.
        if not hasattr(KazmaAgent, "get_streaming_graph"):
            return {
                "status": "degraded",
                "component": "agent_runner",
                "error": "KazmaAgent.get_streaming_graph missing",
            }
        return {"status": "ok", "component": "agent_runner"}
    except Exception as e:
        logger.error("AgentRunner health check failed: %s", e)
        return {"status": "failed", "component": "agent_runner", "error": str(e)}


def check_database() -> dict[str, Any]:
    """Check configured DB backend (SQLite always ok; Postgres must ping)."""
    try:
        from kazma_core.db.backend import get_backend, is_postgres

        backend = get_backend().value
        if not is_postgres():
            return {"status": "ok", "component": "database", "backend": backend}
        from kazma_core.db.postgres_pool import get_postgres_pool

        pool = get_postgres_pool()
        if pool is None:
            return {
                "status": "failed",
                "component": "database",
                "backend": backend,
                "error": "pool unavailable",
            }
        row = pool.execute_one("SELECT 1 AS ok")
        if not row:
            return {
                "status": "failed",
                "component": "database",
                "backend": backend,
                "error": "ping empty",
            }
        return {"status": "ok", "component": "database", "backend": backend}
    except Exception as e:
        logger.error("Database health check failed: %s", e)
        return {"status": "failed", "component": "database", "error": str(e)}


@router.get("/health/live")
async def liveness():
    """Liveness probe - returns 200 if process is alive.
    
    This endpoint should never fail - it only checks that the
    Python process is running and can respond to HTTP requests.
    Used by multi-replica load balancers / Kubernetes.
    """
    return {"status": "alive", "timestamp": time.time()}


@router.get("/health/ready")
async def readiness():
    """Readiness probe - returns 200 if all critical dependencies are healthy.
    
    Checks:
    - ConfigStore
    - Database backend (Postgres ping when configured)
    - SwarmEngine (if enabled)
    - ModelRegistry
    - AgentRunner
    
    Returns 200 if ready, 503 if critical dependency failed
    (so LB / multi-replica can stop routing traffic).
    """
    checks = {}
    
    # Run all health checks
    checks["config_store"] = check_config_store()
    checks["database"] = check_database()
    checks["swarm_engine"] = check_swarm_engine()
    checks["model_registry"] = check_model_registry()
    checks["agent_runner"] = check_agent_runner()
    
    # Determine overall status — database + config_store are critical
    critical_failed = [
        name
        for name, check in checks.items()
        if name in ("config_store", "database") and check.get("status") == "failed"
    ]
    failed = [name for name, check in checks.items() if check.get("status") == "failed"]
    not_initialized = [name for name, check in checks.items() if check.get("status") == "not_initialized"]
    
    if critical_failed:
        overall_status = "not_ready"
        http_status = 503
    elif failed:
        overall_status = "degraded"
        http_status = 200  # non-critical failure still accepts traffic
    elif not_initialized:
        overall_status = "starting"
        http_status = 200
    else:
        overall_status = "ready"
        http_status = 200
    
    response = {
        "status": overall_status,
        "timestamp": time.time(),
        "checks": checks,
    }
    
    return JSONResponse(content=response, status_code=http_status)


@router.get("/health/details")
async def health_details():
    """Detailed health information for debugging."""
    checks = {}

    checks["config_store"] = check_config_store()
    checks["swarm_engine"] = check_swarm_engine()
    checks["model_registry"] = check_model_registry()
    checks["agent_runner"] = check_agent_runner()

    # Add system info
    import sys
    import platform

    active_model = str(checks.get("model_registry", {}).get("active_model") or "")
    active_provider = str(checks.get("model_registry", {}).get("active_provider") or "")

    response = {
        "timestamp": time.time(),
        "checks": checks,
        "active_model": active_model,
        "active_provider": active_provider,
        "system": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
        },
    }

    return response


# ── Deep canary (/health/deep) ─────────────────────────────────────────
#
# The structural checks above prove components EXIST; this canary proves
# they WORK by exercising one real roundtrip per critical path. It exists
# because this codebase's worst bugs were silent no-ops (a recall
# NameError swallowed for a day, dead buttons, write-only settings) —
# "quietly broken" must show up red somewhere.
#
# Bounded + TTL-cached (30s): hammering the endpoint replays the cached
# payload, so it is safe to poll aggressively.

_DEEP_TTL_S = 30.0
_deep_cache: dict[str, Any] = {"ts": 0.0, "payload": None}


def _check_config_roundtrip() -> dict[str, Any]:
    """Write → read → delete one ConfigStore key (catches read-only / locked
    / corrupted-settings breakage the plain read check misses)."""
    t0 = time.perf_counter()
    key = "system.canary.config_roundtrip"
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        store.set(key, "ping")
        value = store.get(key)
        store.delete(key)
        if value != "ping":
            return {
                "status": "failed",
                "component": "config_roundtrip",
                "error": f"roundtrip mismatch: {value!r}",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        return {
            "status": "ok",
            "component": "config_roundtrip",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as e:
        logger.error("[health.deep] config roundtrip failed: %s", e)
        return {
            "status": "failed",
            "component": "config_roundtrip",
            "error": f"{type(e).__name__}: {e}"[:300],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


async def _check_memory_recall() -> dict[str, Any]:
    """One REAL recall() roundtrip — the exact call the supervisor makes
    every turn (this is the check that would have caught the swallowed
    recall NameError)."""
    import asyncio

    t0 = time.perf_counter()
    try:
        from kazma_core.memory.recall import recall

        result = await asyncio.to_thread(
            recall,
            "health canary probe",
            limit=1,
            session_id="health-canary",
            tenant_id="default",
            explain=False,
        )
        beliefs = getattr(result, "beliefs", None)
        episodes = getattr(result, "episodes", None)
        return {
            "status": "ok",
            "component": "memory_recall",
            "beliefs": len(beliefs) if beliefs is not None else None,
            "episodes": len(episodes) if episodes is not None else None,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as e:
        logger.error("[health.deep] memory recall failed: %s", e)
        return {
            "status": "failed",
            "component": "memory_recall",
            "error": f"{type(e).__name__}: {e}"[:300],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


def _check_workspace_binding() -> dict[str, Any]:
    """resolve_active_root() returns an existing directory (the §10A ladder
    the file tools gate on)."""
    t0 = time.perf_counter()
    try:
        from kazma_core.workspace.binding import resolve_active_root

        root = resolve_active_root()
        if not root or not root.exists():
            return {
                "status": "failed",
                "component": "workspace_binding",
                "error": f"resolved root missing: {root}",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        return {
            "status": "ok",
            "component": "workspace_binding",
            "root": str(root),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as e:
        logger.error("[health.deep] workspace binding failed: %s", e)
        return {
            "status": "failed",
            "component": "workspace_binding",
            "error": f"{type(e).__name__}: {e}"[:300],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


def _check_research_stack() -> dict[str, Any]:
    """Reuse the research pipeline's own preflight (local-only — no network
    probe) so 'deep research quietly broken' is visible here too."""
    t0 = time.perf_counter()
    try:
        from kazma_core.tools.research_readiness import (
            format_readiness_message,
            research_readiness,
        )

        ready = research_readiness(probe_search=False)
        return {
            "status": "ok" if ready.get("ready") else "degraded",
            "component": "research_stack",
            "message": format_readiness_message(ready),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as e:
        logger.error("[health.deep] research readiness failed: %s", e)
        return {
            "status": "failed",
            "component": "research_stack",
            "error": f"{type(e).__name__}: {e}"[:300],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


def _check_brain_imports() -> dict[str, Any]:
    """The crawl.py incident class: a dangling import breaks the brain at
    first USE, not at boot. Import the load-bearing entry points here."""
    t0 = time.perf_counter()
    try:
        import importlib

        gb = importlib.import_module("kazma_core.agent.graph_builder")
        ru = importlib.import_module("kazma_core.tools.read_url")
        wa = importlib.import_module("kazma_core.web_acquire")
        missing = [
            name
            for name, mod, attr in (
                ("build_supervisor_graph", gb, "build_supervisor_graph"),
                ("fetch_full_text", ru, "fetch_full_text"),
                ("fetch_text", wa, "fetch_text"),
                ("search", wa, "search"),
            )
            if not hasattr(mod, attr)
        ]
        if missing:
            return {
                "status": "failed",
                "component": "brain_imports",
                "error": f"missing entry points: {missing}",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        return {
            "status": "ok",
            "component": "brain_imports",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as e:
        logger.error("[health.deep] brain import check failed: %s", e)
        return {
            "status": "failed",
            "component": "brain_imports",
            "error": f"{type(e).__name__}: {e}"[:300],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


@router.get("/health/deep")
async def deep_canary() -> JSONResponse:
    """Deep health canary — one real roundtrip per critical path.

    Checks: config write→read→delete, a real memory recall(), workspace
    binding, research-stack readiness, brain entry-point imports, and the
    DB backend ping. 200 when every check is ok/degraded, 503 when any
    failed. Results are TTL-cached for 30s so aggressive polling is free.
    """
    now = time.time()
    cached = _deep_cache.get("payload")
    if cached is not None and now - float(_deep_cache.get("ts") or 0.0) < _DEEP_TTL_S:
        return JSONResponse(
            content={
                **cached,
                "cached": True,
                "age_seconds": round(now - float(_deep_cache.get("ts") or 0.0), 1),
            }
        )

    import asyncio

    checks: dict[str, Any] = {}
    checks["config_roundtrip"] = await asyncio.to_thread(_check_config_roundtrip)
    checks["memory_recall"] = await _check_memory_recall()
    checks["workspace_binding"] = _check_workspace_binding()
    checks["research_stack"] = _check_research_stack()
    checks["brain_imports"] = _check_brain_imports()
    checks["database"] = check_database()

    failed = [n for n, c in checks.items() if c.get("status") == "failed"]
    payload = {
        "status": "unhealthy" if failed else "healthy",
        "ok": not failed,
        "failed": failed,
        "timestamp": now,
        "checks": checks,
    }
    _deep_cache.update(ts=now, payload=payload)
    return JSONResponse(content=payload, status_code=503 if failed else 200)