"""File read tool — Read files from the agent workspace with line-numbered output.

Follows the Kazma read_file format: "{LINE_NUM}|{CONTENT}".
Supports offset/limit pagination. 1-indexed lines.

Safety: reads are restricted to the agent workspace by default, mirroring
``file_write``.  The workspace root and absolute-path policy are shared
with ``file_write`` via ``configure_workspace`` so both tools enforce the
same boundary.

Usage:
    from kazma_core.tools.file_read import file_read
    content = await file_read("/path/to/file.py", offset=10, limit=50)
"""

from __future__ import annotations

from pathlib import Path

# Shared workspace configuration (re-exported for convenience so callers
# can import ``configure_workspace`` from either module).
import kazma_core.tools.file_write as _fw

# Re-export helpers so callers can ``from file_read import configure_workspace``
configure_workspace = _fw.configure_workspace
_get_workspace = _fw._get_workspace
_is_within_workspace = _fw._is_within_workspace

MAX_CHARS = 100_000


def _friendly_error(exc: Exception, path: str) -> str:
    """Map filesystem exceptions to user-friendly messages."""
    if isinstance(exc, FileNotFoundError):
        return f"Error: File not found: {path}"
    if isinstance(exc, PermissionError):
        return f"Error: Permission denied: {path}"
    if isinstance(exc, IsADirectoryError):
        return f"Error: Path is a directory: {path}"
    if isinstance(exc, UnicodeDecodeError):
        return f"Error: File is not valid UTF-8 text: {path}"
    return f"Error: Could not read {path} — {exc}"


async def file_read(path: str, offset: int = 0, limit: int = 500) -> str:
    """Read a file and return its contents with line numbers.

    Args:
        path:   File path (absolute or relative to cwd).
        offset: 1-indexed line number to start from (0 = start of file).
        limit:  Maximum number of lines to return.

    Returns:
        Lines in "{LINE_NUM}|{CONTENT}" format, or a friendly error message.
    """
    if not path or not path.strip():
        return "Error: No path provided."

    workspace = _get_workspace()
    p = Path(path).expanduser().resolve()

    # ── Safety check (mirrors file_write) ─────────────────────────
    within = _is_within_workspace(p, workspace)
    if not within and not _fw._ALLOW_ABSOLUTE:
        return "Safety: reads outside workspace are not allowed."

    try:
        if not p.exists():
            return _friendly_error(FileNotFoundError(), path)
        if p.is_dir():
            return _friendly_error(IsADirectoryError(), path)

        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _friendly_error(FileNotFoundError(), path)
    except PermissionError:
        return _friendly_error(PermissionError(), path)
    except IsADirectoryError:
        return _friendly_error(IsADirectoryError(), path)
    except UnicodeDecodeError as exc:
        return _friendly_error(exc, path)
    except OSError as exc:
        return _friendly_error(exc, path)

    lines = text.splitlines()

    # 1-indexed offset (0 means start from line 1)
    start = max(offset, 1) - 1  # convert to 0-indexed
    end = min(start + limit, len(lines))

    selected = lines[start:end]

    if not selected:
        return f"Error: offset {offset} exceeds file length ({len(lines)} lines)."

    # Build output with line numbers
    output_lines: list[str] = []
    for i, line in enumerate(selected, start=start + 1):
        output_lines.append(f"{i}|{line}")

    result = "\n".join(output_lines)

    # Cap total chars
    if len(result) > MAX_CHARS:
        result = result[:MAX_CHARS] + f"\n[truncated — output exceeded {MAX_CHARS} chars]"

    return result
