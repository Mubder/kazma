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
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from kazma_core.errors import safe_error

logger = logging.getLogger(__name__)

__all__ = ["create_workspace_router"]


def _resolve_workspace_root() -> Path:
    """Resolve workspace root via the single binding SoT (tools + IDE + UI)."""
    try:
        from kazma_core.workspace.binding import resolve_active_root

        root = resolve_active_root()
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError as exc:
        # The active row names a root that cannot be created (an unplugged
        # drive, a deleted repo). Show the canonical sandbox rather than a
        # 500 -- never a CWD-relative "kazma-data" the tools do not use.
        from kazma_core.workspace.binding import default_sandbox_root

        logger.warning("[workspace_api] active workspace root unusable (%s); showing the default sandbox", exc)
        return default_sandbox_root()


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


#: Folders the fallback walk never enters, besides hidden ones (``.git``,
#: ``.venv``...): generated trees and the install's own data folder.
_SKIP_DIRS = frozenset(
    {"node_modules", "__pycache__", "venv", "site-packages", "dist", "build", "kazma-data"}
)
#: The fallback walk stops after this many files: a workspace that is not a
#: repository can be any folder.
_WALK_FILE_CAP = 20_000


def _git_project_files(root: Path) -> list[Path] | None:
    """The workspace's own files as git sees them -- tracked, plus untracked
    ones ``.gitignore`` does not exclude -- or None when ``root`` is not the
    top of a repository, or git is missing.

    Only a repository's own top level: the default sandbox
    (``<data dir>/workspace``) sits inside the install's checkout, which
    ignores it, and git would list nothing there.

    No server secrets for git (it runs the repository's configured programs,
    AGENTS.md §26I), and ``core.fsmonitor`` off so none is started at all.
    """
    from kazma_core.security.child_env import tool_child_env

    if not (root / ".git").exists():
        return None
    try:
        res = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            env=tool_child_env(),
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return [root / name for name in res.stdout.decode("utf-8", "replace").split("\0") if name]


def _walk_files(root: Path) -> list[Path]:
    """Files under ``root``, without hidden folders or generated trees; a
    folder it cannot read is skipped, never the whole walk."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):  # errors skip that folder
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
        for name in filenames:
            found.append(Path(dirpath) / name)
            if len(found) >= _WALK_FILE_CAP:
                return found
    return found


def _scan_recent_files(root: Path, limit: int) -> list[dict[str, Any]]:
    """The workspace's most recently changed files, newest first.

    It walked the whole workspace with ``rglob``, hidden and generated
    folders included, and one unreadable folder anywhere returned nothing:
    on the live install (the workspace is the install folder, ``.venv`` and
    ``kazma-data`` included) the Workspace page waited 30 s for an empty list
    (2026-09-28). A repository's files come from git; any other folder gets
    a pruned walk.
    """
    candidates = _git_project_files(root)
    if candidates is None:
        candidates = _walk_files(root)
    stamped: list[tuple[float, int, Path]] = []
    for p in candidates:
        try:
            rel_parts = p.relative_to(root).parts
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel_parts):
            continue  # hidden files and anything in a hidden folder
        try:
            st = p.stat()
        except OSError:
            continue  # listed by git but deleted, or unreadable
        if stat.S_ISREG(st.st_mode):
            stamped.append((st.st_mtime, st.st_size, p))

    stamped.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "name": p.name,
            "path": p.relative_to(root).as_posix(),
            "time": datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d %H:%M"),
            "size": _human_size(size),
        }
        for mtime, size, p in stamped[:limit]
    ]


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

    # Plain ``def``s: FastAPI runs them in its threadpool, and both read or
    # write the settings store. The Workspace page's "Folders outside the
    # workspace" card calls them (2026-09-28); before it the docs promised a
    # Settings control that did not exist.
    @router.get("/extra-roots")
    def get_extra_roots() -> Any:
        """List durable extra roots the agent may access outside the workspace."""
        from kazma_core.workspace.path_grants import list_durable_roots

        try:
            roots = [g.to_dict() for g in list_durable_roots()]
        except Exception as exc:
            # Never an empty list that reads as "no folders granted".
            logger.warning("[workspace_api] extra-roots list failed: %s", exc)
            return JSONResponse(status_code=500, content={"ok": False, "error": safe_error(exc)})
        return {"ok": True, "extra_roots": roots}

    @router.put("/extra-roots")
    def put_extra_roots(body: dict[str, Any]) -> Any:
        """Replace durable extra roots.

        Body: ``{"extra_roots": [{"path": "C:\\\\docs", "mode": "read", "label": "Docs"}]}``.
        A new root must be a full path to an existing folder that is not a
        whole drive and holds none of Kazma's own files; a 400 says why not.
        """
        from kazma_core.workspace.path_grants import set_durable_roots

        raw = body.get("extra_roots") if isinstance(body, dict) else None
        if not isinstance(raw, list):
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "extra_roots must be a list", "extra_roots": []},
            )
        try:
            roots = set_durable_roots(raw)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
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
