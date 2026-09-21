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
import hashlib
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
#
# Entries are STAMPED with the file's on-disk identity and revalidated on
# every hit (incident 2026-09-21): the cache used to live until the end of
# the turn, so a read -> write -> read inside one turn served the PRE-write
# content. A probe run hit exactly that — file_apply_patch landed three
# lines, the verifying file_read replayed the two-line original, and only a
# different offset/limit shook it loose. Read-after-write is how an agent
# checks its own work, so the cache was lying at the one moment it mattered.
#
# Stamping rather than having the five mutating tools (file_write,
# file_append, file_apply_patch, file_apply_patch_set, file_delete) call an
# invalidate hook: a hook is only as good as the next tool that remembers to
# call it, and it cannot see writes Kazma did not perform — a shell_exec
# redirect, an MCP filesystem server, a sibling swarm worker, or the user's
# own editor. The stamp is a property of the bytes, so every writer is
# covered including the ones that do not exist yet.
_turn_read_cache: dict[tuple, tuple[tuple | None, str]] = {}
_turn_read_cache_order: list[tuple] = []
_READ_CACHE_MAX = 50


#: Files at or under this size carry a content digest in their stamp as well.
#:
#: ``(mtime_ns, size)`` alone cannot see a write that lands inside a single
#: filesystem mtime tick AND leaves the length unchanged — a same-length edit
#: saved twice in the same instant. Hashing was rejected when the stamp first
#: landed, on the grounds that it re-reads the file the cache exists to avoid
#: reading. That is true of a 40 MB PDF, where what the cache saves is the
#: PARSE, and false of the source files an agent actually writes and reads back,
#: where a blake2b over a few KB is microseconds. So hash the small ones and
#: stat the big ones, and say which is which rather than claiming both.
_HASH_MAX_BYTES = 1_048_576


def _stat_stamp(p: Path) -> tuple | None:
    """Identity of the bytes on disk.

    ``(mtime_ns, size, digest)`` always. Up to ``_HASH_MAX_BYTES`` the digest
    covers every byte. Above that it covers eight windows spread through the
    file. A same-tick, same-length rewrite that touches only a gap between
    those windows can still collide; that residual is in ``docs/KNOWN_GAPS.md``.

    ``None`` when the file cannot be read at all (deleted, replaced by a
    directory, permissions) — which compares unequal to any real stamp and
    therefore invalidates, the safe direction.
    """
    try:
        st = p.stat()
    except OSError:
        return None

    try:
        digest = _content_digest(p, st.st_size)
    except OSError:
        # Unreadable now, whatever stat said. Treat as invalid.
        return None
    return (st.st_mtime_ns, st.st_size, digest)


def _content_digest(p: Path, size: int) -> bytes:
    """Full blake2b up to ``_HASH_MAX_BYTES``, then eight sampled windows.

    A 40 MB PDF is not re-read in full. The samples are spread across the
    file so a same-tick, same-length rewrite of the head, the tail, or one
    of the middle windows changes the stamp. A rewrite that lands only in
    a gap between windows can still collide; that is the residual.
    """
    h = hashlib.blake2b(digest_size=16)
    window = 65536
    with p.open("rb") as fh:
        if size <= _HASH_MAX_BYTES:
            for block in iter(lambda: fh.read(window), b""):
                h.update(block)
            return h.digest()
        slots = 8
        step = max(window, size // slots)
        offset = 0
        seen = 0
        while offset < size and seen < slots:
            fh.seek(offset)
            h.update(fh.read(window))
            offset += step
            seen += 1
        h.update(int(size).to_bytes(8, "little"))
    return h.digest()


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
    entry = _turn_read_cache.get(cache_key)
    cached: str | None = None
    if entry is not None:
        stamped, content = entry
        if stamped is not None and stamped == _stat_stamp(p):
            cached = content
        else:
            # Changed underneath us (or vanished). Drop it and read fresh —
            # never serve "IDENTICAL to what you already received" about
            # bytes that are no longer there.
            _turn_read_cache.pop(cache_key, None)
            if cache_key in _turn_read_cache_order:
                _turn_read_cache_order.remove(cache_key)
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

        # Stamp BEFORE reading, deliberately. A write landing between this
        # stat and the read gives us new content carrying the old stamp, so
        # the next hit revalidates and re-reads: a wasted read, never a lie.
        # Stamping after the read inverts that — old content carrying the
        # new stamp would look valid forever. Do not "tidy" this downward.
        pre_read_stamp = _stat_stamp(p)

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

        import asyncio

        limit_n = max(int(limit or 500), 1)
        offset_n = int(offset or 0)
        budget = _max_read_budget()

        def _read_sync() -> tuple[list[str], bool] | str:
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
                return (
                    [f"{i}|{line}" for i, line in enumerate(selected, start=start + 1)],
                    False,
                )
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
            return (
                [
                    f"{i}|{line}"
                    for i, line in enumerate(selected, start=max(offset_n, 1))
                ],
                not reached_eof,
            )

        synced = await asyncio.to_thread(_read_sync)
        if isinstance(synced, str):
            return synced
        output_lines, truncated_read = synced
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
    _turn_read_cache[cache_key] = (pre_read_stamp, result)
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
