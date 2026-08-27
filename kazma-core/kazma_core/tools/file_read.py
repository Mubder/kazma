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

import codecs
from pathlib import Path

# Shared workspace configuration (re-exported for convenience so callers
# can import ``configure_workspace`` from either module).
import kazma_core.tools.file_write as _fw

__all__ = ["MAX_CHARS", "MAX_READ_BUDGET", "clear_read_cache", "file_read"]

# Re-export helpers so callers can ``from file_read import configure_workspace``
configure_workspace = _fw.configure_workspace
_get_workspace = _fw._get_workspace
_is_within_workspace = _fw._is_within_workspace

MAX_CHARS = 100_000

# H16: hard cap on BYTES touched per read call (whole-file reads are only
# allowed when the file stat fits under this). Monkeypatchable in tests.
MAX_READ_BUDGET = 32 * 1024 * 1024  # 32 MiB
_STREAM_CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming chunks


def _max_read_budget() -> int:
    """Effective per-read byte budget (clamped low bound for monkeypatches)."""
    return max(64 * 1024, int(MAX_READ_BUDGET))


def _fmt_budget(budget: int) -> str:
    """Human-readable byte budget for tool output ('32 MiB', '0.1 MiB')."""
    mib = budget / (1024 * 1024)
    s = f"{mib:.1f}".rstrip("0").rstrip(".")
    return f"{s} MiB"


def _stream_window(
    p: Path, offset: int, limit: int, budget: int
) -> tuple[list[str], bool, int]:
    """Stream up to ``limit`` lines starting at 1-indexed ``offset``.

    Memory stays O(chunk): fixed-size binary chunks flow through an
    incremental UTF-8 decoder (``errors='replace'``), so multi-byte
    sequences split across chunk boundaries still decode correctly and the
    file is NEVER materialised whole (H16).

    Byte-offset approximation caveat: the window is located by COUNTING
    newline-terminated lines from the start (no per-line byte index), so
    reaching a large offset costs I/O proportional to its distance — but
    memory stays bounded by ``budget`` regardless of file size. Line
    semantics approximate ``str.splitlines``: ``\\n`` terminates a line
    (a trailing ``\\r`` is stripped); exotic Unicode separators (U+2028,
    U+0085, ...) are NOT treated as breaks here.

    Returns ``(window_lines, reached_eof, total_lines_seen)`` where
    ``total_lines_seen`` is exact only when ``reached_eof`` is True.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    start_line = max(offset, 1)
    want = max(limit, 1)
    out: list[str] = []
    cur_line = 0
    pending = ""  # carryover partial line between chunks
    scanned = 0
    reached_eof = False

    with p.open("rb") as fh:
        while True:
            remaining = budget - scanned
            if remaining <= 0:
                break  # budget exhausted before EOF (truncated read)
            chunk = fh.read(min(_STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                reached_eof = True
                break
            scanned += len(chunk)
            text = decoder.decode(chunk)
            if not text:
                continue
            parts = (pending + text).split("\n")
            pending = parts.pop()  # last element is unterminated (may be "")
            for part in parts:
                cur_line += 1
                if start_line <= cur_line and len(out) < want:
                    out.append(part[:-1] if part.endswith("\r") else part)
            if len(out) >= want:
                break

        if reached_eof:
            tail = pending + decoder.decode(b"", final=True)
            if tail:
                cur_line += 1
                if start_line <= cur_line and len(out) < want:
                    out.append(tail[:-1] if tail.endswith("\r") else tail)

    return out, reached_eof, cur_line


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

    # ── Safety check FIRST (workspace + path grants + allow_absolute) ──
    # Reordered (M31/H16): a cached read must never bypass a grant
    # revocation — validate access BEFORE serving per-turn dedup content.
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

    # ── Per-turn dedup: same path+offset+limit already read this turn ──
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

        limit_n = max(int(limit or 500), 1)
        offset_n = int(offset or 0)
        budget = _max_read_budget()

        if p.stat().st_size <= budget:
            # H16: the old `p.read_text()` loaded the ENTIRE file before
            # the MAX_CHARS cap — an OOM vector on multi-GB files. Only
            # files that fit under the byte budget take this path, and the
            # decode is lenient (invalid UTF-8 → U+FFFD instead of raising).
            text = p.read_bytes().decode("utf-8", errors="replace")
            all_lines = text.splitlines()
            start = max(offset_n, 1) - 1  # convert to 0-indexed
            selected = all_lines[start : start + limit_n]
            if not selected:
                return (
                    f"Error: offset {offset} exceeds file length "
                    f"({len(all_lines)} lines)."
                )
            output_lines: list[str] = [
                f"{i}|{line}" for i, line in enumerate(selected, start=start + 1)
            ]
            truncated_read = False
        else:
            selected, reached_eof, seen_lines = _stream_window(
                p, offset_n, limit_n, budget
            )
            if not selected:
                if reached_eof:
                    return (
                        f"Error: offset {offset} exceeds file length "
                        f"({seen_lines} lines)."
                    )
                return (
                    f"Error: file exceeds the {_fmt_budget(budget)} "
                    f"per-read budget before line {offset}. Use file_search "
                    "/ shell tools for bulk inspection."
                )
            output_lines = [
                f"{i}|{line}"
                for i, line in enumerate(selected, start=max(offset_n, 1))
            ]
            truncated_read = not reached_eof
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

    result = "\n".join(output_lines)

    if truncated_read:
        result += (
            f"\n[truncated — file exceeds the {_fmt_budget(budget)} "
            "per-read budget; the shown window may end mid-file]"
        )

    # Cap total chars (unchanged contract)
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


def clear_read_cache() -> None:
    """Clear the per-turn file-read dedup cache (canonical entry point).

    Called on turn start/end (``graph_respond``) and at swarm worker
    dispatch begin (``swarm/worker.py``) so "[ALREADY READ THIS TURN]"
    content cached by a previous task/agent is never served to the next.
    """
    _turn_read_cache.clear()
    _turn_read_cache_order.clear()


# Back-compat alias (existing importers use this name).
clear_turn_read_cache = clear_read_cache
