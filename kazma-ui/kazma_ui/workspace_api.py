"""Workspace API — File browser for the Kazma workspace directory.

Provides read-only file browsing of the ``kazma-data/workspace`` directory
so the Workspace tab is functional instead of showing hardcoded fallback
data.

Endpoints:
  GET /api/workspace/files?path=<subdir>  — list files/dirs in workspace
  GET /api/workspace/git                  — git status (best-effort)
  GET /api/workspace/recent               — recently modified files

Security:
  - All file paths are resolved and checked to be within the workspace root
    (path traversal prevention).
  - No write or execute operations are exposed.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

# ── Workspace root resolution ──────────────────────────────────────────

_DEFAULT_WORKSPACE_RELS = [
    "kazma-data/workspace",
    "data/workspace",
]


def _resolve_workspace_root() -> Path:
    """Resolve the workspace root directory.

    Order of precedence:
      1. ``KAZMA_WORKSPACE`` env var (if set and non-empty).
      2. ``kazma-data/workspace`` relative to CWD.
      3. ``data/workspace`` relative to CWD.
      4. Fallback: ``kazma-data/workspace`` (created on first list).

    The directory is created if it does not yet exist so the UI always
    has a valid, browsable location.
    """
    env_ws = os.environ.get("KAZMA_WORKSPACE", "").strip()
    if env_ws:
        root = Path(env_ws).expanduser().resolve()
    else:
        cwd = Path.cwd()
        for rel in _DEFAULT_WORKSPACE_RELS:
            candidate = (cwd / rel).resolve()
            if candidate.exists():
                root = candidate
                break
        else:
            root = (cwd / "kazma-data" / "workspace").resolve()

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
        }

    # ------------------------------------------------------------------
    # GET /api/workspace/git — best-effort git status
    # ------------------------------------------------------------------

    @router.get("/git")
    async def git_status() -> dict[str, Any]:
        """Return best-effort git status for the workspace directory.

        If the workspace is not inside a git repo (or git is unavailable),
        a graceful empty result is returned.
        """
        root = _resolve_workspace_root()

        def _run_git(args: list[str]) -> str:
            try:
                result = subprocess.run(  # noqa: S603 — args are fixed
                    args,
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return result.stdout.strip() if result.returncode == 0 else ""
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                return ""

        branch = _run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        if not branch:
            return {"branch": "", "dirty": False, "status": ""}

        porcelain = _run_git(["git", "status", "--porcelain"])
        status_lines = [line for line in porcelain.split("\n") if line.strip()] if porcelain else []
        return {
            "branch": branch,
            "dirty": len(status_lines) > 0,
            "status": porcelain,
        }

    # ------------------------------------------------------------------
    # GET /api/workspace/recent — recently modified files
    # ------------------------------------------------------------------

    @router.get("/recent")
    async def recent_files(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
        """Return the most recently modified files in the workspace."""
        root = _resolve_workspace_root()

        all_files: list[tuple[float, Path]] = []
        try:
            for p in root.rglob("*"):
                if p.is_file() and not p.name.startswith("."):
                    try:
                        all_files.append((p.stat().st_mtime, p))
                    except OSError:
                        continue
        except PermissionError:
            return {"files": []}

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
        return {"files": recent}

    return router
