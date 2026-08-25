"""Workspace / skill-dir path guards (extracted from tool_registry)."""

from __future__ import annotations

from pathlib import Path

def _is_under_agent_skill_dir(resolved_p: Path) -> bool:
    """True if path is inside a known Agent Skills install/scan directory.

    Allows progressive disclosure (tier 3) — agents can ``file_read``
    scripts/references/assets under installed skills without opening the
    whole filesystem. Write/delete ops must still reject these paths.
    """
    try:
        from kazma_core.agent_skills.discovery import skill_base_dirs

        for _scope, base in skill_base_dirs():
            if not base.is_dir():
                continue
            try:
                resolved_p.relative_to(base.resolve())
                return True
            except ValueError:
                continue
    except Exception:
        pass
    return False


def _workspace_scope_error(p: Path, path: str, op: str) -> str | None:
    """Return a safety error string if *p* is outside workspace/grants.

    Returns ``None`` when the path is allowed.  Denies by default when
    the workspace module cannot be imported (fail-closed) so a broken
    install never silently opens the whole filesystem.

    Read-like ops (``reads``, ``listings``, ``searches``) may also access
    Agent Skills directories so skill resources load on demand.

    External paths may be allowed via durable extra roots or session path
    grants (see ``workspace.path_policy`` / ``request_path_access``).
    """
    try:
        from kazma_core.workspace.path_policy import check_path_access, denied_message
    except (ImportError, OSError):
        return f"Safety: workspace module unavailable — {op} denied. Path: {path}"

    resolved_p = p.expanduser().resolve()
    # Writes/deletions need write mode; listings/searches/reads need read.
    mode = "write" if op in ("writes", "deletions", "write") else "read"
    access = check_path_access(resolved_p, mode)
    if access.allowed:
        return None
    if op in ("reads", "listings", "searches") and _is_under_agent_skill_dir(resolved_p):
        return None
    return denied_message(path, mode, result=access)  # type: ignore[arg-type]
