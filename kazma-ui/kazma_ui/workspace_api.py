"""Workspace API — File browser for the active Kazma workspace.

Uses the same resolver as agent tools (``resolve_active_root``) so the
Workspace tab, IDE, and file tools never disagree after Switch Repo.

Endpoints:
  GET /api/workspace/files?path=<subdir>  — list files/dirs in workspace
  GET /api/workspace/recent               — recently modified files

Security:
  - All file paths are resolved and checked to be within the workspace root
    (path traversal prevention).
  - Write and execute operations are deliberately not exposed here. The Web
    workspace terminal uses ``/api/ide/run`` so commands follow the shared
    ``IdeService`` -> ``LocalToolRegistry`` -> HITL safety chain.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

__all__ = ["create_workspace_router"]


def _resolve_workspace_root() -> Path:
    """Resolve workspace root via the single binding SoT (tools + IDE + UI)."""
    try:
        from kazma_core.workspace.binding import resolve_active_root

        root = resolve_active_root()
        root.mkdir(parents=True, exist_ok=True)
        return root
    except Exception as exc:
        logger.debug("[workspace_api] resolve_active_root failed: %s", exc)
        root = (Path.cwd() / "kazma-data" / "workspace").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root


def _is_within_workspace(target: Path, workspace: Path) -> bool:
    """Return True if *target* is inside *workspace* (after resolution)."""
    try:
        target.resolve().relative_to(workspace)
        return True
    except (ValueError, OSError):
        return False


def _human_size(size: int) -> str:
    """Format a byte count as a human-readable string."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _file_mtime_str(p: Path) -> str:
    """Return a short human-readable modification-time string."""
    try:
        ts = p.stat().st_mtime
        dt = datetime.fromtimestamp(ts, tz=UTC)
        return dt.strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


def _scan_recent_files(root: Path, limit: int) -> list[dict[str, Any]]:
    """Scan workspace for recently modified files."""
    all_files: list[tuple[float, Path]] = []
    try:
        for p in root.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                try:
                    all_files.append((p.stat().st_mtime, p))
                except OSError:
                    continue
    except PermissionError:
        return []

    all_files.sort(key=lambda pair: pair[0], reverse=True)
    recent: list[dict[str, Any]] = []
    for mtime, p in all_files[:limit]:
        rel = str(p.relative_to(root)).replace("\\", "/")
        recent.append(
            {
                "name": p.name,
                "path": rel,
                "time": _file_mtime_str(p),
                "size": _human_size(p.stat().st_size) if p.exists() else "",
            }
        )
    return recent


# ── Router factory ─────────────────────────────────────────────────────


def create_workspace_router() -> APIRouter:
    """Create and return the workspace API router."""

    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    # ------------------------------------------------------------------
    # GET /api/workspace/files — directory listing
    # ------------------------------------------------------------------

    @router.get("/files")
    async def list_files(
        path: str = Query("", description="Sub-directory within the workspace root"),
    ) -> dict[str, Any]:
        """List the contents of a directory inside the workspace.

        Query params:
          path — a relative sub-path inside the workspace (default: root).

        Returns ``{"files": [...], "path": "...", "parent": "..."}`` where
        each file entry has ``name``, ``path``, ``is_dir``, ``size``, and
        ``modified`` keys.
        """
        root = _resolve_workspace_root()

        # Resolve requested sub-path
        if path and path.strip("/"):
            # Strip leading slashes to keep it relative
            rel = path.strip("/")
            target = (root / rel).resolve()
        else:
            rel = ""
            target = root

        if not _is_within_workspace(target, root):
            return {"files": [], "path": "", "parent": "", "error": "Path outside workspace"}

        if not target.exists() or not target.is_dir():
            return {"files": [], "path": rel, "parent": "", "error": "Directory not found"}

        entries: list[dict[str, Any]] = []
        try:
            for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
                # Skip hidden files/dirs (dotfiles)
                if child.name.startswith("."):
                    continue
                try:
                    is_dir = child.is_dir()
                    size = "" if is_dir else _human_size(child.stat().st_size)
                except OSError:
                    continue
                child_rel = str(child.relative_to(root)).replace("\\", "/")
                entries.append(
                    {
                        "name": child.name,
                        "path": child_rel,
                        "is_dir": is_dir,
                        "size": size,
                        "modified": _file_mtime_str(child),
                    }
                )
        except PermissionError:
            return {"files": [], "path": rel, "parent": "", "error": "Permission denied"}

        # Compute parent path for breadcrumb navigation
        if rel:
            parent_rel = str(Path(rel).parent).replace("\\", "/")
            if parent_rel == ".":
                parent_rel = ""
        else:
            parent_rel = ""

        return {
            "files": entries,
            "path": rel,
            "parent": parent_rel,
            "root": str(root),
        }


    # ------------------------------------------------------------------
    # Extra folders (durable path grants outside the active workspace)
    # ------------------------------------------------------------------

    @router.get("/extra-roots")
    async def get_extra_roots() -> dict[str, Any]:
        """List durable extra roots the agent may access outside the workspace."""
        try:
            from kazma_core.workspace.path_grants import list_durable_roots

            roots = [g.to_dict() for g in list_durable_roots()]
        except Exception as exc:
            logger.debug("[workspace_api] extra-roots list failed: %s", exc)
            roots = []
        return {"extra_roots": roots}

    @router.put("/extra-roots")
    async def put_extra_roots(body: dict[str, Any]) -> dict[str, Any]:
        """Replace durable extra roots.

        Body: ``{"extra_roots": [{"path": "C:\\\\docs", "mode": "read", "label": "Docs"}]}``
        """
        from kazma_core.workspace.path_grants import set_durable_roots

        raw = body.get("extra_roots") if isinstance(body, dict) else None
        if not isinstance(raw, list):
            return {"ok": False, "error": "extra_roots must be a list", "extra_roots": []}
        roots = set_durable_roots(raw)
        return {"ok": True, "extra_roots": [g.to_dict() for g in roots]}

    # ------------------------------------------------------------------
    # GET /api/workspace/recent — recently modified files
    # ------------------------------------------------------------------

    @router.get("/recent")
    async def recent_files(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
        """Return the most recently modified files in the workspace."""
        root = _resolve_workspace_root()
        files = await asyncio.to_thread(_scan_recent_files, root, limit)
        return {"files": files}

    return router
