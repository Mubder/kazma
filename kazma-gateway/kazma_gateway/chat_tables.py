"""Markdown tables in replies to chat apps (2026-09-29).

Telegram, Discord and Slack show a markdown table as raw pipes and dashes.
A reply the model wrote as a table arrived in Discord as

    | Check | Status |
    |---|---|
    | Message received | ✅ |

(live, 2026-09-29). ``GatewayManager.send`` -- the one way a reply reaches a
chat app -- rewrites each table outside a code block as lines that read on a
phone::

    Check — Status
    • Message received — ✅

The header, when it says anything, is the first line; each row is a bullet
with its cells joined by " — ". Text inside ``` fences is left as written.
The web chat renders tables and never passes through here.
"""

from __future__ import annotations

import re

__all__ = ["tables_to_lines"]

#: A delimiter cell: dashes, optionally with the alignment colons.
_DELIMITER = re.compile(r"^:?-+:?$")
_JOIN = " — "


def _cells(line: str) -> list[str] | None:
    """The cells of a table line, or None when *line* is not one."""
    s = line.strip()
    if "|" not in s:
        return None
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells = [c.strip() for c in re.split(r"(?<!\\)\|", s)]
    return [c.replace("\\|", "|") for c in cells]


def _is_delimiter(cells: list[str] | None, width: int) -> bool:
    return bool(cells) and len(cells) == width and all(
        _DELIMITER.match(c.replace(" ", "")) for c in cells
    )


def _render(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = [_JOIN.join(h for h in header if h)] if any(header) else []
    for row in rows:
        cells = [c for c in row if c]
        if cells:
            lines.append("• " + _JOIN.join(cells))
    return lines


def tables_to_lines(text: str) -> str:
    """*text* with every markdown table outside a code fence rewritten as
    lines; text with no table comes back as it is."""
    if not text or "|" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        header = None if in_fence else _cells(line)
        if header and len(header) >= 2 and i + 1 < len(lines) and _is_delimiter(
            _cells(lines[i + 1]), len(header)
        ):
            rows: list[list[str]] = []
            j = i + 2
            while j < len(lines):
                cells = _cells(lines[j])
                if not cells or lines[j].lstrip().startswith("```"):
                    break
                rows.append(cells)
                j += 1
            end = "\r" if line.endswith("\r") else ""
            out.extend(f"{rendered}{end}" for rendered in _render(header, rows))
            i = j
            continue
        out.append(line)
        i += 1
    return "\n".join(out)
