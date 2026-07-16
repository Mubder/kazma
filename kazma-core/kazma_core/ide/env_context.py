"""Environment-context provider — the IDE/workspace/repo awareness layer.

This module bridges the gap between Kazma's *tools* (file ops, exec, git,
GitHub) and its *brain* (the agent system prompt + dispatched worker
prompts). Before this module existed, the supervisor and every swarm worker
were "blind": they had the tools registered but nothing in any prompt told
them a workspace, repo, or IDE existed, so workers got stuck on discovery.

``build_env_context()`` resolves the *current* environment facts and
returns a markdown block suitable for prepending to a system prompt or
injecting as an environment message. It is intentionally cheap (a couple
of ``git`` subprocess calls at most) and re-read on every turn, because
the active workspace can be switched between turns.

Resolution precedence for the workspace root mirrors ``IdeService``:
``KAZMA_WORKSPACE`` env → active ``WorkspaceStore`` row →
``file_write._get_workspace()`` default. When a workspace_id is supplied
(Phase 3 per-task targeting), that specific workspace row is used.

All lookups are defensive: a non-git directory, a missing remote, or a
cold repo-identity cache each degrade gracefully to a minimal context
block rather than raising. The brain always gets *something* useful.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The subset of tools the brain should be told about. These are the
# workspace-scoped, IDE-relevant capabilities. Kept short on purpose —
# the full tool list is already passed as function-calling schemas; this
# is the *narrative* steer that names the high-value tools.
_ANNOUNCED_TOOLS = (
    "file_read",
    "file_write",
    "file_list",
    "file_search",
    "shell_exec",
    "python_exec",
    "git_status",
    "git_commit",
    "git_push_pull",
    "github_create_pr",
    "github_list_issues",
)


def _resolve_root(workspace_id: str | None = None) -> Path:
    """Resolve the workspace root, honoring an optional workspace_id.

    With ``workspace_id`` (Phase 3), the specific WorkspaceStore row is
    used. Without it, precedence mirrors ``IdeService``: env → active
    workspace → file_write default.
    """
    if workspace_id:
        try:
            from kazma_core.stores import get_workspace_store

            store = get_workspace_store()
            for ws in store.list_workspaces():
                if ws.get("id") == workspace_id:
                    rp = ws.get("root_path")
                    if rp:
                        return Path(rp).resolve()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[env_context] workspace_id %s lookup failed: %s", workspace_id, exc)

    env_ws = os.environ.get("KAZMA_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws).expanduser().resolve()

    try:
        from kazma_core.stores import get_workspace_store

        active = get_workspace_store().get_active_workspace()
        if active and active.get("root_path"):
            return Path(active["root_path"]).resolve()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[env_context] active workspace lookup failed: %s", exc)

    try:
        from kazma_core.tools.file_write import _get_workspace

        return _get_workspace().resolve()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[env_context] file_write workspace lookup failed: %s", exc)
        return Path.cwd().resolve()


def _git(command: str, root: Path) -> str | None:
    """Run a single git command in ``root``; return stripped stdout or None.

    Uses ``shlex.split`` + no shell to avoid command injection (the command
    is always a hardcoded literal today, but this is the safe pattern).
    """
    import shlex

    try:
        res = subprocess.run(
            shlex.split(command),
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=4,
        )
        if res.returncode == 0:
            return res.stdout.strip() or None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[env_context] git %r failed in %s: %s", command, root, exc)
    return None


def detect_repo_slug(root: Path) -> str | None:
    """Return ``owner/repo`` for the workspace, preferring the persisted cache.

    Phase 2 stores repo identity in WorkspaceStore; if available we read it
    from there (one-shot, avoids a git subprocess per turn). Otherwise we
    fall back to a live ``git config --get remote.origin.url`` parse.
    """
    # Prefer the persisted cache (Phase 2).
    try:
        from kazma_core.stores import get_workspace_store

        store = get_workspace_store()
        identity = store.repo_for(str(root))
        if identity and identity.get("owner") and identity.get("repo"):
            slug = f"{identity['owner']}/{identity['repo']}"
            return slug
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[env_context] repo_for cache lookup failed: %s", exc)

    # Fallback: live detection from the git remote.
    if not (root / ".git").exists():
        return None
    url = _git("git config --get remote.origin.url", root)
    if not url:
        return None
    return _parse_slug(url)


def _parse_slug(url: str) -> str | None:
    """Parse owner/repo out of a git remote URL (HTTPS or SSH).

    Delegates to the shared ``github_client.parse_github_slug`` so there is
    one canonical parser across the codebase. Falls back to a local regex
    if the gateway module isn't importable (headless / core-only deployments).
    """
    try:
        from kazma_gateway.routers.github_client import parse_github_slug  # type: ignore

        slug = parse_github_slug(url)
        if slug:
            return f"{slug[0]}/{slug[1]}"
    except Exception:
        pass
    # Fallback: local regex (handles HTTPS + SSH URLs).
    import re

    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url.strip())
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def detect_branch(root: Path) -> str | None:
    """Return the current git branch, or None for a non-git workspace."""
    if not (root / ".git").exists():
        return None
    return _git("git branch --show-current", root)


def _list_available_tools() -> list[str]:
    """Return the subset of announced tools actually registered right now."""
    try:
        from kazma_core.agent.tool_registry import get_tool_registry

        registered = {t["name"] for t in get_tool_registry().list_tools()}
        return [t for t in _ANNOUNCED_TOOLS if t in registered]
    except Exception:
        # During early init the registry may not exist yet; announce the
        # canonical list so the brain still knows what *should* be there.
        return list(_ANNOUNCED_TOOLS)


def build_env_context(workspace_id: str | None = None) -> str:
    """Build the environment-awareness markdown block.

    Args:
        workspace_id: Optional WorkspaceStore row id (Phase 3 targeting).
            When given, facts are resolved for that specific workspace
            rather than the process-wide active one.

    Returns:
        A markdown block describing the workspace, repo, branch, and
        available tools. Always non-empty. Safe to prepend to any prompt.
    """
    try:
        root = _resolve_root(workspace_id)
    except Exception:  # pragma: no cover - defensive
        root = Path.cwd()

    lines: list[str] = ["## Environment", f"Workspace root: {root}"]

    slug = detect_repo_slug(root)
    branch = detect_branch(root)
    if slug:
        repo_part = f"Repository: {slug}"
        if branch:
            repo_part += f"  (branch: {branch})"
        lines.append(repo_part)
    elif branch:
        lines.append(f"Branch: {branch}")

    # GitHub integration availability hint.
    try:
        from kazma_gateway.routers.github_client import get_github_token

        if get_github_token():
            lines.append("GitHub: authenticated (can create PRs, list issues).")
    except Exception:
        # gateway may be absent in headless/core-only deployments
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PAT")
        if token:
            lines.append("GitHub: token detected in environment.")

    tools = _list_available_tools()
    if tools:
        lines.append(
            "You have an IDE workspace. Use relative paths; file/shell/git "
            "tools run scoped to the workspace root. Available tools: "
            + ", ".join(tools) + "."
        )
    else:
        lines.append(
            "You have an IDE workspace. Use relative paths scoped to the "
            "workspace root."
        )

    lines.append(
        "Danger-tier operations (file_write, shell_exec, git push) require "
        "HITL approval."
    )

    return "\n".join(lines)


def env_context_for_dispatch(task: Any) -> str:
    """Build env context for a dispatched swarm task, honoring task.workspace_id.

    Convenience wrapper used by the worker prompt-assembly path.
    """
    wid = None
    try:
        wid = getattr(task, "workspace_id", None)
    except Exception:  # pragma: no cover - defensive
        pass
    return build_env_context(workspace_id=wid)
