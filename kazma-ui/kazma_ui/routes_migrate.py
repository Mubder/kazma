"""Config migration API routes — extracted from routes_direct (S3 split)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

logger = logging.getLogger(__name__)

_DB_PATHS = {
    "config": "kazma-data/settings.db",
    "task": "kazma-data/tasks.db",
    "session": "kazma-data/sessions.db",
}


def register_migrate_routes(app: FastAPI) -> None:
    """Mount ``/api/config/migrate/*`` on *app*."""
    from kazma_core.config_store import get_config_store
    from kazma_core.migrations import (
        CONFIG_STORE_MIGRATIONS,
        SESSION_STORE_MIGRATIONS,
        TASK_STORE_MIGRATIONS,
        get_runner,
        run_startup_migrations,
    )

    @app.get("/api/config/migrate/status")
    async def get_migration_status() -> dict[str, Any]:
        """Get migration status for all stores."""
        stores = {
            "config": {
                "db_path": _DB_PATHS["config"],
                "migrations": CONFIG_STORE_MIGRATIONS,
            },
            "task": {
                "db_path": _DB_PATHS["task"],
                "migrations": TASK_STORE_MIGRATIONS,
            },
            "session": {
                "db_path": _DB_PATHS["session"],
                "migrations": SESSION_STORE_MIGRATIONS,
            },
        }

        result: dict[str, Any] = {}
        for store_name, store_info in stores.items():
            try:
                runner = get_runner(store_info["db_path"], store_name)
                status = runner.status()
                result[store_name] = {
                    "db_path": store_info["db_path"],
                    "applied_count": status["applied_count"],
                    "pending_count": status["pending_count"],
                    "latest_applied": status["latest_applied"],
                    "pending_versions": status["pending_versions"],
                    "history": status["history"],
                }
            except Exception as e:
                logger.warning("[Migration] Failed to get status for %s: %s", store_name, e)
                result[store_name] = {
                    "db_path": store_info["db_path"],
                    "error": "status unavailable",
                }

        return {"stores": result, "timestamp": time.time()}

    @app.post("/api/config/migrate/run")
    async def run_migrations(request: Request) -> dict[str, Any]:
        """Run pending migrations for all stores or a specific store."""
        try:
            body = await request.json()
        except Exception:
            body = {}

        store_filter = body.get("store")
        db_paths: dict[str, str] = {}
        if store_filter:
            if store_filter not in _DB_PATHS:
                raise HTTPException(status_code=400, detail=f"Unknown store: {store_filter}")
            db_paths[store_filter] = _DB_PATHS[store_filter]
        else:
            db_paths = dict(_DB_PATHS)

        for path in db_paths.values():
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        results: dict[str, Any] = {}
        try:
            applied = run_startup_migrations(db_paths)
            for store_name, migrations in applied.items():
                results[store_name] = {
                    "applied_count": len(migrations),
                    "migrations": [
                        {"version": m.version, "name": m.name} for m in migrations
                    ],
                }
        except Exception as e:
            logger.error("[Migration] Failed to run migrations: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Migration failed") from e

        return {
            "status": "completed",
            "results": results,
            "timestamp": time.time(),
        }

    @app.post("/api/config/migrate/rollback")
    async def rollback_migrations(request: Request) -> dict[str, Any]:
        """Rollback migrations for a store down to target_version (exclusive)."""
        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail="Invalid JSON") from e

        store_name = body.get("store")
        target_version = body.get("target_version")

        if not store_name or store_name not in _DB_PATHS:
            raise HTTPException(
                status_code=400, detail="store must be one of: config, task, session"
            )
        if target_version is None or not isinstance(target_version, int):
            raise HTTPException(status_code=400, detail="target_version (int) is required")

        db_path = _DB_PATHS[store_name]
        try:
            runner = get_runner(db_path, store_name)
            rolled_back = runner.rollback(target_version)
            return {
                "status": "completed",
                "store": store_name,
                "rolled_back_count": len(rolled_back),
                "migrations": [
                    {"version": m.version, "name": m.name} for m in rolled_back
                ],
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error(
                "[Migration] Rollback failed for %s: %s", store_name, e, exc_info=True
            )
            raise HTTPException(status_code=500, detail="Migration rollback failed") from e

    @app.post("/api/config/migrate/export")
    async def export_config_with_migrations() -> str:
        """Export current config including migration status as YAML."""
        import yaml

        store = get_config_store()
        config_yaml = store.export_yaml()
        status_resp = await get_migration_status()
        full_export = {
            "config": yaml.safe_load(config_yaml),
            "migrations": status_resp["stores"],
        }
        return yaml.dump(
            full_export, default_flow_style=False, allow_unicode=True, sort_keys=False
        )

    logger.info("[routes_migrate] Config migration endpoints mounted at /api/config/migrate/*")
