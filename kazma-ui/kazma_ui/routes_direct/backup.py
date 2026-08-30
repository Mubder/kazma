"""Backup, restore, and export endpoints.

Extracted from the former ``kazma_ui/routes_direct.py`` god module
(3,862 lines) — audit O5. Handler bodies are unchanged; only their
module changed. Registration order within this group is preserved.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends
from fastapi.responses import JSONResponse as _JSONResponse
from kazma_core.background import spawn_background
from kazma_core.errors import safe_error

from kazma_ui.rate_limit import rate_limit

logger = logging.getLogger(__name__)

__all__ = ["register_backup_routes"]


def register_backup_routes(self: Any) -> None:
    """Register the backup routes onto ``self.app``."""
    # ── Universal Backup ────────────────────────────────────────────────
    @self.app.post("/api/backup/now", dependencies=[Depends(rate_limit("backup", 3))])
    async def _backup_now() -> Any:
        """Trigger a universal backup in the background. Returns immediately."""
        import asyncio

        from kazma_core.backup.universal import (
            _backup_progress,
            backup_progress_is_stale,
            get_backup_progress,
        )

        # A genuinely running backup blocks a new one — but a mid phase older
        # than the stale threshold is a crashed/hung run; let it through so the
        # button can't be bricked (perform_universal_backup's own lock applies
        # the same crash detection and starts fresh).
        if (_backup_progress.get("phase") not in ("idle", "done", "error")
                and not backup_progress_is_stale()):
            return {"ok": False, "error": "A backup is already running", "progress": get_backup_progress()}

        async def _run():
            try:
                from kazma_core.backup.universal import perform_universal_backup
                await asyncio.to_thread(perform_universal_backup, trigger="manual")
            except Exception as exc:
                from kazma_core.backup.universal import _set_progress
                _set_progress("error", detail=safe_error(exc), error=str(exc)[:300])

        spawn_background(_run(), name="memory-maintenance")

        # perform_universal_backup deliberately does NOT dump Postgres -- that
        # belongs to the native_pg_backup task, which only the 24-hourly sweep
        # was enqueueing. So this button backed up 25 SQLite databases, said
        # "Done", and never touched the main database. Enqueue it here too,
        # exactly as the scheduled sweep does, so "Backup Now" means it.
        pg_queued = False
        try:
            from kazma_core.db.pg_backup import pg_backup_enabled
            from kazma_core.memory.task_queue import enqueue_task

            if pg_backup_enabled():
                enqueue_task("native_pg_backup", {})
                pg_queued = True
        except Exception:  # noqa: BLE001 -- never block the button
            logger.warning("[backup] could not enqueue the Postgres dump", exc_info=True)

        return {
            "ok": True,
            "message": "Backup started" + (" (including Postgres)" if pg_queued else ""),
            "postgres_queued": pg_queued,
            "progress": get_backup_progress(),
        }
    @self.app.get("/api/backup/status")
    async def _backup_status() -> Any:
        """Poll the current backup progress (phase + detail)."""
        from kazma_core.backup.universal import get_backup_progress

        return get_backup_progress()
    @self.app.get("/api/backup/list")
    async def _backup_list() -> Any:
        """List all universal backups (newest first)."""
        from kazma_core.backup.universal import list_universal_backups

        return {"backups": list_universal_backups()}
    @self.app.delete("/api/backup/{dir_name}")
    async def _backup_delete(dir_name: str) -> Any:
        """Delete a universal backup by its directory name (timestamp)."""
        from kazma_core.backup.universal import delete_universal_backup

        return delete_universal_backup(dir_name)
    @self.app.post("/api/backup/{dir_name}/archive", dependencies=[Depends(rate_limit("backup", 3))])
    async def _backup_archive(dir_name: str) -> Any:
        """Archive a universal backup into a downloadable .zip."""
        from kazma_core.backup.universal import archive_universal_backup

        return archive_universal_backup(dir_name)
    @self.app.get("/api/backup/{dir_name}/download")
    async def _backup_download(dir_name: str) -> Any:
        """Download an archived backup (.zip)."""
        import re

        from fastapi.responses import FileResponse
        from kazma_core.backup.universal import _universal_dir

        # Same containment guard as delete/archive (universal.py) — without
        # it, an encoded ..\ segment could read arbitrary .zip files outside
        # the backups dir on Windows.
        if not re.match(r"^\d+$", dir_name):
            return _JSONResponse(
                status_code=400,
                content={"error": "Invalid backup name (must be a timestamp)"},
            )
        zip_path = _universal_dir() / f"{dir_name}.zip"
        if not zip_path.is_file():
            # 404 (not a bare JSON dict) — the UI opens this in a tab via
            # window.open, so a bare dict rendered as raw JSON text.
            return _JSONResponse(
                status_code=404,
                content={"error": "Archive not found. Archive the backup first."},
            )
        return FileResponse(str(zip_path), filename=f"kazma-backup-{dir_name}.zip",
                            media_type="application/zip")
