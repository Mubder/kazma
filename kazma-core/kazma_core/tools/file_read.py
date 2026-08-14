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

__all__ = ["MAX_CHARS", "file_read"]

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


# Per-turn file-read dedup cache: the model re-reads the same file up to 9x
# per task (audit 2026-08-15) because context trimming makes it forget the
# content. This cache stores reads keyed by (path, offset, limit) for the
# current turn; a re-read returns the cached content with a "already read"
# note instead of a fresh disk read + full context injection.
_turn_read_cache: dict[tuple, str] = {}
_turn_read_cache_order: list[tuple] = []
_READ_CACHE_MAX = 50


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

    p = Path(path).expanduser().resolve()

    # ── Per-turn dedup: same path+offset+limit already read this turn ─
    cache_key = (str(p), int(offset or 0), int(limit or 500))
    cached = _turn_read_cache.get(cache_key)
    if cached is not None:
        return (
            f"[ALREADY READ THIS TURN — file_read({path}, offset={offset}, "
            f"limit={limit}). The content below is IDENTICAL to what you "
            "already received. Do NOT re-read; use the content from your "
            "context. If you need different lines, use a DIFFERENT offset/"
            f"limit.]\n\n{cached}"
        )

    # ── Safety check (workspace + path grants + allow_absolute) ───
    from kazma_core.workspace.path_policy import check_path_access, denied_message

    access = check_path_access(p, "read")
    if not access.allowed:
        # Allow Agent Skills resource reads (SKILL.md scripts/references)
        skill_ok = False
        try:
            from kazma_core.agent.tool_registry import _is_under_agent_skill_dir

            skill_ok = _is_under_agent_skill_dir(p)
        except Exception:
            skill_ok = False
        if not skill_ok:
            return denied_message(path, "read", result=access)

    try:
        if not p.exists():
            return _friendly_error(FileNotFoundError(), path)
        if p.is_dir():
            return _friendly_error(IsADirectoryError(), path)

        # ── Runtime-ready document format delegation ─────────────────
        suffix = p.suffix.lower()
        from kazma_core.documents.registry import get_parser_registry

        capability = get_parser_registry().capability_for_extension(suffix)
        text_suffixes = {".txt", ".md", ".markdown", ".log"}
        if capability is not None and capability.available and suffix not in text_suffixes:
            try:
                from kazma_core.documents.service import DocumentService

                parsed = await DocumentService().read_transient(
                    p,
                    approved_path=p,
                    max_chars=MAX_CHARS,
                    fence=True,
                )
                return parsed.as_tool_output()
            except Exception as exc:
                from kazma_core.documents.errors import DocumentParseError

                if isinstance(exc, DocumentParseError):
                    return f"Error: {exc.safe_message}"
                return f"Error: Document parser failed safely ({type(exc).__name__})"
        if capability is not None and not capability.available:
            return (
                f"Error: Parser for {suffix} is unavailable: "
                f"{capability.reason or 'runtime health probe failed'}"
            )

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

    # Store in per-turn dedup cache (LRU-bounded)
    if len(_turn_read_cache) >= _READ_CACHE_MAX:
        oldest = _turn_read_cache_order.pop(0)
        _turn_read_cache.pop(oldest, None)
    _turn_read_cache[cache_key] = result
    if cache_key not in _turn_read_cache_order:
        _turn_read_cache_order.append(cache_key)

    return result


def clear_turn_read_cache() -> None:
    """Clear the per-turn file-read dedup cache (called on turn start/end)."""
    _turn_read_cache.clear()
    _turn_read_cache_order.clear()
