"""File write tool — Write content to files within the agent workspace.

Safety: writes are restricted to the agent workspace by default.
Parent directories are created automatically. Overwrites existing files.

Usage:
    from kazma_core.tools.file_write import file_write
    result = await file_write("/path/to/file.py", "content here")
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["configure_workspace", "file_write"]

# ── Workspace resolution ──────────────────────────────────────────────

_WORKSPACE_ROOT: Path | None = None
_ALLOW_ABSOLUTE: bool = False


def configure_workspace(workspace: str | None = None, allow_absolute: bool = False) -> None:
    """Configure the workspace root and absolute-path policy.

    Args:
        workspace:      Path to agent workspace. Defaults to cwd.
        allow_absolute: If True, absolute paths outside workspace are allowed.
    """
    global _WORKSPACE_ROOT, _ALLOW_ABSOLUTE
    _WORKSPACE_ROOT = Path(workspace).expanduser().resolve() if workspace else None
    _ALLOW_ABSOLUTE = allow_absolute


def _get_workspace() -> Path:
    """Get the configured workspace root.

    Resolution precedence (mirrors ``IdeService._resolve_workspace_root``):

      1. Per-task ``workspace_scope`` (Phase 3 — concurrent multi-repo).
      2. Explicitly configured ``_WORKSPACE_ROOT`` (``configure_workspace``).
      3. ``KAZMA_WORKSPACE`` env var.
      4. The **active WorkspaceStore** row (the real workspace the user
         selected — e.g. a cloned repo). This is the fix for the dual-root
         bug where repo files were wrongly rejected as "outside workspace".
      5. ``cwd/kazma-data/workspace`` (last-resort default).

    Defaults to ``kazma-data/workspace`` relative to the current working
    directory (NOT the drive root) when nothing else is configured. This
    prevents accidental creation of a ``C:\\workspace`` folder on Windows.
    """
    # 1. Per-task scope (Phase 3) takes top precedence.
    try:
        from kazma_core.ide.workspace_scope import resolve_workspace_root

        scoped = resolve_workspace_root()
        if scoped is not None:
            return scoped
    except Exception:
        pass

    # 2. Explicitly configured root.
    if _WORKSPACE_ROOT is not None:
        return _WORKSPACE_ROOT

    import os

    # 3. Env var override.
    env_ws = os.environ.get("KAZMA_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws).expanduser().resolve()

    # 4. Active WorkspaceStore row — the workspace the user actually
    #    selected (e.g. the active repo). Without this, the file tools
    #    scoped against the kazma-data/workspace default and rejected
    #    every real repo file as "outside workspace".
    try:
        from kazma_core.stores import get_workspace_store

        active = get_workspace_store().get_active_workspace()
        if active and active.get("root_path"):
            return Path(active["root_path"]).resolve()
    except Exception:
        pass

    # 5. Last-resort default.
    return (Path.cwd() / "kazma-data" / "workspace").resolve()


def _is_within_workspace(target: Path, workspace: Path) -> bool:
    """Check if target path is within the workspace directory."""
    try:
        target.resolve().relative_to(workspace)
        return True
    except ValueError:
        return False


def _friendly_error(exc: Exception, path: str) -> str:
    """Map filesystem exceptions to user-friendly messages."""
    if isinstance(exc, PermissionError):
        return f"Error: Permission denied: {path}"
    if isinstance(exc, IsADirectoryError):
        return f"Error: Path is a directory: {path}"
    if isinstance(exc, OSError):
        return f"Error: Could not write to {path} — {exc}"
    return f"Error: Write failed for {path} — {exc}"


async def file_write(path: str, content: str) -> str:
    """Write content to a file.

    Args:
        path:    Destination file path.
        content: Text content to write.

    Returns:
        Success message with line/byte counts, or a friendly error.
    """
    if not path or not path.strip():
        return "Error: No path provided."

    workspace = _get_workspace()
    p = Path(path).expanduser().resolve()

    # ── Safety check ──────────────────────────────────────────────
    within = _is_within_workspace(p, workspace)

    if not within:
        # Path is outside workspace — block unless explicitly allowed
        if not _ALLOW_ABSOLUTE:
            return "Safety: writes outside workspace are not allowed."

    # Block obvious ../.. escape attempts regardless
    raw = Path(path)
    if ".." in raw.parts and not within and not _ALLOW_ABSOLUTE:
        return "Safety: writes outside workspace are not allowed."

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except PermissionError:
        return _friendly_error(PermissionError(), path)
    except IsADirectoryError:
        return _friendly_error(IsADirectoryError(), path)
    except OSError as exc:
        return _friendly_error(exc, path)

    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    byte_count = len(content.encode("utf-8"))
    return f"Wrote {line_count} lines, {byte_count} bytes to {path}"
