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

__all__ = ["build_env_context", "detect_branch", "detect_repo_slug", "env_context_for_dispatch"]

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
    used. Without it, **must** match ``file_write._get_workspace()`` so the
    brain and tools never disagree (Switch Repo / clone).
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

    ws_name = root.name
    ws_id = ""
    try:
        from kazma_core.stores import get_workspace_store

        active = get_workspace_store().get_active_workspace()
        if active and active.get("root_path"):
            try:
                if Path(active["root_path"]).resolve() == root.resolve():
                    ws_name = active.get("name") or ws_name
                    ws_id = str(active.get("id") or "")
            except Exception:
                pass
    except Exception:
        pass

    slug = detect_repo_slug(root)
    branch = detect_branch(root)

    lines: list[str] = [
        "## Active Workspace (BINDING — not optional)",
        f"- **Workspace name:** {ws_name}",
        f"- **Workspace root:** `{root}`",
    ]
    if ws_id:
        lines.append(f"- **Workspace id:** `{ws_id}`")
    if slug:
        repo_part = f"- **Repository:** `{slug}`"
        if branch:
            repo_part += f" (branch: `{branch}`)"
        lines.append(repo_part)
    elif branch:
        lines.append(f"- **Branch:** `{branch}`")
    else:
        lines.append("- **Repository:** (not a git remote / unknown)")

    lines.extend(
        [
            "",
            "### Hard rules (must follow)",
            "1. **Only this root is in scope.** `file_*`, `shell_exec`, and git tools "
            "run inside the workspace root above. Prefer **relative paths**.",
            "2. **Do not audit or edit another project** (including the Kazma agent "
            "framework host) unless *this* workspace root / Repository line is "
            "actually that project.",
            "3. If the user names a repo (e.g. \"ShipX\"), it must match the "
            "**Workspace name** or **Repository** lines above. If it does not match, "
            "**stop and ask** — do not invent findings for a different codebase.",
            "4. Before a multi-file audit, confirm identity: read this workspace's "
            "`README` / `pyproject.toml` / `package.json` and state the project name "
            "you found. If it conflicts with the user's request, report the conflict.",
            "5. Never claim the folder *name* is the product if package metadata "
            "says otherwise — report both.",
        ]
    )

    # GitHub integration availability hint.
    try:
        from kazma_gateway.routers.github_client import get_github_token

        if get_github_token():
            lines.append("")
            lines.append("GitHub: authenticated (can create PRs, list issues).")
    except Exception:
        # gateway may be absent in headless/core-only deployments
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PAT")
        if token:
            lines.append("")
            lines.append("GitHub: token detected in environment.")

    tools = _list_available_tools()
    lines.append("")
    if tools:
        lines.append(
            "Available workspace tools: " + ", ".join(tools) + "."
        )
    else:
        lines.append("Workspace tools are registered for relative paths under the root.")

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
