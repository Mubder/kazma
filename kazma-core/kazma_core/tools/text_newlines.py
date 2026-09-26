"""Keep a text file's own line endings when a tool rewrites it.

Python's text mode translates newlines both ways: ``read_text`` turns
``\\r\\n`` into ``\\n`` and ``write_text`` turns ``\\n`` into the platform's
newline. So on Windows every tool that read a file, changed it and wrote it
back turned an LF file into CRLF (a whole-file diff for one edited line), and
on Linux it turned a CRLF file into LF. ``file_apply_patch`` had logic to
keep a CRLF file CRLF, but it could never run: the text it inspected had
already been translated. The checkpoint rollback was worse -- it stored a
file's exact bytes and wrote them back in text mode, so a CRLF file came back
as ``\\r\\r\\n`` (found 2026-09-26 by ``tests/test_tool_paths.py``).

The rule for every file tool: read with :func:`read_exact`, write with
``newline=""`` in the file's existing style (:func:`existing_newline` +
:func:`in_newline_style`). A NEW file keeps the platform default.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["existing_newline", "in_newline_style", "newline_of", "read_exact"]

_SNIFF_BYTES = 65536


def newline_of(text: str) -> str:
    """``"\\r\\n"`` when most of *text*'s line breaks are CRLF, else ``"\\n"``."""
    crlf = text.count("\r\n")
    return "\r\n" if crlf and crlf >= max(1, text.count("\n") // 2) else "\n"


def existing_newline(path: Path) -> str | None:
    """The newline *path* already uses, or None (new, empty, one line, unreadable)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(_SNIFF_BYTES)
    except OSError:
        return None
    if b"\n" not in head:
        return None
    crlf = head.count(b"\r\n")
    return "\r\n" if crlf and crlf >= max(1, head.count(b"\n") // 2) else "\n"


def in_newline_style(text: str, newline: str) -> str:
    """*text* with every line break written as *newline*."""
    lf = text.replace("\r\n", "\n")
    return lf.replace("\n", "\r\n") if newline == "\r\n" else lf


def read_exact(path: Path, encoding: str = "utf-8") -> str:
    """A file's text with its line endings untouched (no newline translation)."""
    return path.read_bytes().decode(encoding)
