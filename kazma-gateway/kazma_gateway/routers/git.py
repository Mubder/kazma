"""Git status router — live git information for the active workspace.

Endpoint
--------
GET /api/git/status
    Run non-blocking subprocess queries against the active workspace
    folder and return a structured JSON payload describing the current
    git state.

Output schema::

    {
        "is_git":  bool,
        "branch":  str | "",
        "dirty":   bool,
        "staged":  ["M  file.py", ...],
        "modified":["_M file.py", ...],
        "untracked":["?? file.py", ...],
        "raw_status": "<raw porcelain output>"
    }

Non-git folders are handled gracefully (``is_git: false``) without
raising exceptions or polluting the log with errors.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 5  # seconds per subprocess call


def _run_git(args: list[str], cwd: str) -> tuple[bool, str]:
    """Run a ``git …`` command inside *cwd*.

    Returns:
        ``(success, stdout_stripped)`` — *success* is False on non-zero
        exit, timeout, or if ``git`` is not found on PATH.
    """
    try:
        result = subprocess.run(  # noqa: S603 — args are strictly controlled
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("[git/status] git command failed (%s): %s", args, exc)
        return False, ""


def _parse_porcelain(raw: str) -> dict[str, list[str]]:
    """Parse ``git status --porcelain`` output into categorised lists.

    Porcelain v1 line format: ``XY filename``

    Returns:
        Dict with ``staged``, ``modified``, and ``untracked`` lists.
        Each element is the full raw porcelain line.
    """
    staged: list[str] = []
    modified: list[str] = []
    untracked: list[str] = []

    for line in raw.splitlines():
        if not line:
            continue
        x = line[0]  # index status (staged changes)
        y = line[1]  # worktree status (unstaged changes)

        if x == "?" and y == "?":
            untracked.append(line)
        else:
            if x in ("M", "A", "D", "R", "C"):
                staged.append(line)
            if y in ("M", "D"):
                modified.append(line)

    return {"staged": staged, "modified": modified, "untracked": untracked}


def create_git_router() -> APIRouter:
    """Return an APIRouter providing the git status endpoint."""

    router = APIRouter(prefix="/api/git", tags=["git"])

    @router.get("/status")
    async def git_status() -> JSONResponse:
        """Return live git status for the active workspace folder.

        The workspace root is fetched from ``ConfigStore`` key
        ``workspace.selected_path``.  If none is configured the CWD is
        used as a best-effort fallback.

        The response never raises a 5xx for non-git directories — instead
        it returns ``{"is_git": false, ...}`` so the UI degrades
        gracefully.
        """
        # Resolve the active workspace root
        try:
            from kazma_core.stores import get_workspace_store
            active_ws = get_workspace_store().get_active_workspace()
            if active_ws:
                raw_root = active_ws["root_path"]
            else:
                from kazma_core.config_store import get_config_store
                raw_root = get_config_store().get("workspace.selected_path")
        except Exception:
            raw_root = None

        if raw_root:
            cwd = str(Path(str(raw_root)).resolve())
        else:
            cwd = str(Path.cwd())

        empty: dict[str, Any] = {
            "is_git": False,
            "branch": "",
            "dirty": False,
            "staged": [],
            "modified": [],
            "untracked": [],
            "raw_status": "",
        }

        # Check whether this is actually a git repo
        ok, rev_parse = _run_git(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd
        )
        if not ok or rev_parse.lower() != "true":
            return JSONResponse(empty)

        # Get current branch
        _, branch = _run_git(["git", "branch", "--show-current"], cwd)
        if not branch:
            # Detached HEAD — try to get short SHA
            _, branch = _run_git(["git", "rev-parse", "--short", "HEAD"], cwd)

        # Get porcelain status
        _, porcelain = _run_git(["git", "status", "--porcelain"], cwd)
        parsed = _parse_porcelain(porcelain)

        is_dirty = bool(parsed["staged"] or parsed["modified"] or parsed["untracked"])

        return JSONResponse(
            {
                "is_git": True,
                "branch": branch,
                "dirty": is_dirty,
                "staged": parsed["staged"],
                "modified": parsed["modified"],
                "untracked": parsed["untracked"],
                "raw_status": porcelain,
            }
        )

    return router
