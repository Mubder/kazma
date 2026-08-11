"""File write tool — Write content to files within the agent workspace.

Safety: writes are restricted to the agent workspace by default.
Parent directories are created automatically. Overwrites existing files.

Usage:
    from kazma_core.tools.file_write import file_write
    result = await file_write("/path/to/file.py", "content here")
"""

from __future__ import annotations

from pathlib import Path

from kazma_core.workspace.binding import (
    allow_absolute_paths as _allow_absolute_paths,
    configure_workspace,
    resolve_active_root,
)
from kazma_core.workspace.path_policy import check_path_access, denied_message

__all__ = ["configure_workspace", "file_write"]


def _get_workspace() -> Path:
    """Get the configured workspace root (delegates to workspace binding SoT).

    Precedence — see ``kazma_core.workspace.binding.resolve_active_root``:

      1. Per-task ``workspace_scope``
      2. Active WorkspaceStore row
      3. Process pin (``configure_workspace``)
      4. ``KAZMA_WORKSPACE`` env
      5. Default sandbox under project ``data_dir()/workspace``
    """
    return resolve_active_root()


class _AllowAbsoluteProxy:
    """Bool-like proxy so ``if not _ALLOW_ABSOLUTE`` stays correct after pin changes.

    Prefer :func:`kazma_core.workspace.binding.allow_absolute_paths` for new code.
    """

    def __bool__(self) -> bool:
        return _allow_absolute_paths()

    def __eq__(self, other: object) -> bool:
        return bool(self) == other

    def __repr__(self) -> str:
        return f"_ALLOW_ABSOLUTE({bool(self)!r})"


_ALLOW_ABSOLUTE = _AllowAbsoluteProxy()


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

    p = Path(path).expanduser().resolve()

    # ── Safety check (workspace + path grants + allow_absolute) ───
    access = check_path_access(p, "write")
    if not access.allowed:
        return denied_message(path, "write", result=access)

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
