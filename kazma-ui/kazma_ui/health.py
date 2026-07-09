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

router = APIRouter(tags=["health"])


def get_health_dependencies():
    """Get all dependencies for health checks.
    
    Returns a dict of component checkers that can be called.
    """
    from kazma_core.config_store import get_config_store
    from kazma_core.swarm import get_swarm_engine
    from kazma_core.agent_runner import get_agent_runner
    from kazma_core.model_registry import get_registry
    
    return {
        "config_store": get_config_store,
        "swarm_engine": get_swarm_engine,
        "agent_runner": get_agent_runner,
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
    """Check ModelRegistry availability."""
    try:
        from kazma_core.model_registry import get_model_registry
        registry = get_model_registry()
        if registry is None:
            return {"status": "not_initialized", "component": "model_registry"}
        providers = registry.list_providers() if hasattr(registry, "list_providers") else []
        return {"status": "ok", "component": "model_registry", "providers": len(providers)}
    except Exception as e:
        logger.error("ModelRegistry health check failed: %s", e)
        return {"status": "failed", "component": "model_registry", "error": "check failed"}


def check_agent_runner() -> dict[str, Any]:
    """Check AgentRunner availability."""
    try:
        from kazma_core.agent_runner import get_agent_runner
        runner = get_agent_runner()
        if runner is None:
            return {"status": "not_initialized", "component": "agent_runner"}
        return {"status": "ok", "component": "agent_runner"}
    except Exception as e:
        logger.error("AgentRunner health check failed: %s", e)
        return {"status": "failed", "component": "agent_runner", "error": str(e)}


@router.get("/health/live")
async def liveness():
    """Liveness probe - returns 200 if process is alive.
    
    This endpoint should never fail - it only checks that the
    Python process is running and can respond to HTTP requests.
    """
    return {"status": "alive", "timestamp": time.time()}


@router.get("/health/ready")
async def readiness():
    """Readiness probe - returns 200 if all critical dependencies are healthy.
    
    Checks:
    - ConfigStore (SQLite or in-memory fallback)
    - SwarmEngine (if enabled)
    - ModelRegistry
    - AgentRunner
    
    Returns 200 if all critical components are "ok",
    503 if any critical component has "failed" status.
    """
    checks = {}
    
    # Run all health checks
    checks["config_store"] = check_config_store()
    checks["swarm_engine"] = check_swarm_engine()
    checks["model_registry"] = check_model_registry()
    checks["agent_runner"] = check_agent_runner()
    
    # Determine overall status
    failed = [name for name, check in checks.items() if check.get("status") == "failed"]
    not_initialized = [name for name, check in checks.items() if check.get("status") == "not_initialized"]
    
    if failed:
        overall_status = "degraded"
        http_status = 503
    elif not_initialized:
        overall_status = "degraded"
        http_status = 200  # Not initialized is not a failure
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
    
    response = {
        "timestamp": time.time(),
        "checks": checks,
        "system": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
        },
    }
    
    return response