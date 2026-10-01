"""Unified-diff hunk split / reverse-apply for IDE per-hunk reject."""

from __future__ import annotations

import difflib
import re
from typing import Any

__all__ = ["apply_reverse_hunk", "file_diff", "split_hunks"]


def file_diff(before: str, after: str, name: str = "file") -> str:
    """The unified diff of a file's checkpoint text against its text now.

    One function for the checkpoint review (the hunks the page lists) and the
    per-hunk restore (the hunk it undoes), so an index means the same hunk in
    both. Lines are compared without their endings.
    """
    return "\n".join(difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile="a/" + name,
        tofile="b/" + name,
        lineterm="",
    ))

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def split_hunks(diff: str) -> list[dict[str, Any]]:
    hunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in str(diff or "").splitlines():
        if line.startswith("@@"):
            if current is not None:
                hunks.append(current)
            current = {"header": line, "lines": []}
            continue
        if current is not None and not line.startswith("---") and not line.startswith("+++"):
            current["lines"].append(line)
    if current is not None:
        hunks.append(current)
    out: list[dict[str, Any]] = []
    for i, h in enumerate(hunks):
        out.append({
            "index": i,
            "header": str(h.get("header") or ""),
            "diff": str(h.get("header") or "") + "\n" + "\n".join(h.get("lines") or []),
        })
    return out


def apply_reverse_hunk(text: str, header: str, hunk_lines: list[str]) -> str:
    """Turn current file (post-patch) back for one hunk only."""
    m = _HUNK_RE.match(header.strip())
    if not m:
        raise ValueError(f"bad hunk header: {header}")
    new_start = int(m.group(3)) - 1
    new_count = int(m.group(4) or "1")
    if new_start < 0:
        new_start = 0
    lines = str(text or "").splitlines()
    old_region: list[str] = []
    for hl in hunk_lines:
        if not hl:
            continue
        tag, body = hl[0], hl[1:]
        if tag == " ":
            old_region.append(body)
        elif tag == "-":
            old_region.append(body)
        elif tag == "+":
            continue
        else:
            old_region.append(hl)
    end = new_start + max(new_count, 0)
    rebuilt = lines[:new_start] + old_region + lines[end:]
    return "\n".join(rebuilt) + ("\n" if text.endswith("\n") else "")
