"""Chaos testing API routes — extracted from routes_direct (S3 split).

Only mounts when ``KAZMA_CHAOS_ENABLED`` is truthy (default off).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request

logger = logging.getLogger(__name__)

__all__ = ["register_chaos_routes"]


def _chaos_enabled() -> bool:
    return os.environ.get("KAZMA_CHAOS_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def register_chaos_routes(app: FastAPI) -> None:
    """Mount ``/api/chaos/*`` when chaos is explicitly enabled."""
    if not _chaos_enabled():
        logger.info(
            "[routes_chaos] Chaos endpoints disabled "
            "(set KAZMA_CHAOS_ENABLED=true to enable)"
        )
        return

    from kazma_core.chaos import (
        FailureInjection,
        FailureType,
        InjectionTarget,
        get_injector,
        list_active_injections,
        list_predefined_experiments,
        run_predefined_experiment,
    )

    @app.get("/api/chaos/experiments")
    async def get_predefined_experiments() -> Any:
        """List all predefined chaos experiments."""
        return await list_predefined_experiments()

    @app.post("/api/chaos/experiments/{experiment_name}/run")
    async def run_experiment(experiment_name: str) -> dict[str, Any]:
        """Run a predefined chaos experiment."""
        try:
            injection_id = await run_predefined_experiment(experiment_name)
            return {
                "status": "started",
                "injection_id": injection_id,
                "experiment": experiment_name,
            }
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except Exception as e:
            logger.error(
                "[Chaos] Failed to run experiment %s: %s",
                experiment_name,
                e,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail="Chaos experiment failed") from e

    @app.get("/api/chaos/injections")
    async def get_active_injections() -> dict[str, Any]:
        """Get all currently active fault injections."""
        injections = await list_active_injections()
        return {"injections": injections, "count": len(injections)}

    @app.delete("/api/chaos/injections/{injection_id}")
    async def stop_injection(injection_id: str) -> dict[str, Any]:
        """Stop a specific fault injection."""
        injector = get_injector()
        try:
            await injector.remove_injection(injection_id)
            return {"status": "stopped", "injection_id": injection_id}
        except KeyError as e:
            raise HTTPException(
                status_code=404, detail=f"Injection not found: {injection_id}"
            ) from e
        except Exception as e:
            logger.error(
                "[Chaos] Failed to stop injection %s: %s",
                injection_id,
                e,
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail="Failed to stop injection") from e

    @app.delete("/api/chaos/injections")
    async def stop_all_injections() -> dict[str, str]:
        """Stop all active fault injections."""
        injector = get_injector()
        try:
            await injector.stop_all()
            return {"status": "all_stopped"}
        except Exception as e:
            logger.error("[Chaos] Failed to stop all injections: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to stop injections") from e

    @app.post("/api/chaos/injections/custom")
    async def create_custom_injection(request: Request) -> dict[str, Any]:
        """Create a custom fault injection."""
        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail="Invalid JSON") from e

        required = ["failure_type", "target", "probability", "duration_seconds"]
        for field in required:
            if field not in body:
                raise HTTPException(
                    status_code=400, detail=f"Missing required field: {field}"
                )

        try:
            failure_type = FailureType(body["failure_type"])
            target = InjectionTarget(body["target"])
            probability = float(body["probability"])
            duration_seconds = int(body["duration_seconds"])
            params = body.get("params") or {}
            if not 0 <= probability <= 1:
                raise HTTPException(
                    status_code=400, detail="probability must be between 0 and 1"
                )
            if duration_seconds <= 0:
                raise HTTPException(
                    status_code=400, detail="duration_seconds must be positive"
                )
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid value: {e}") from e

        injection = FailureInjection(
            failure_type=failure_type,
            target=target,
            probability=probability,
            duration_seconds=duration_seconds,
            **params,
        )
        injector = get_injector()
        injection_id = await injector.add_injection(injection)
        return {"status": "created", "injection_id": injection_id}

    logger.info(
        "[routes_chaos] Chaos endpoints mounted at /api/chaos/* "
        "(KAZMA_CHAOS_ENABLED=true)"
    )
