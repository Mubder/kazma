"""Heading identity helpers — keep-with-next and de-duplication.

Section payloads often repeat the title (or the section heading as a
leading ``##`` in the body). Comparing raw strings misses ``1.`` prefixes
and ``(Quick Research Summary)`` parentheticals.
"""

from __future__ import annotations

import re

__all__ = ["heading_key", "headings_equivalent", "drop_leading_heading"]

_NUM_PREFIX = re.compile(
    r"^[\d\u0660-\u0669\u06F0-\u06F9]+[\.\)\-\u2013\u2014]\s*"
)
_PARENS = re.compile(r"\([^)]*\)")
_WS = re.compile(r"\s+")


def heading_key(text: str) -> str:
    """Normalize a heading for equality (hashes, numbering, parentheticals)."""
    s = (text or "").lstrip("#").strip()
    s = _NUM_PREFIX.sub("", s)
    s = _PARENS.sub(" ", s)
    return _WS.sub(" ", s).casefold().strip(" \t.:;—-")


def headings_equivalent(a: str, b: str) -> bool:
    """True when two headings are the same title written two ways."""
    ka, kb = heading_key(a), heading_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    # One is the other plus leftover title debris (min length avoids
    # matching a short word like "الخلاصة" inside a longer heading).
    if min(len(ka), len(kb)) < 12:
        return False
    return ka in kb or kb in ka


def drop_leading_heading(body: str, heading: str) -> str:
    """Remove a leading markdown/plain heading line that repeats ``heading``."""
    if not (body or "").strip() or not (heading or "").strip():
        return body
    lines = body.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return body
    first = lines[i].lstrip("#").strip()
    if not headings_equivalent(first, heading):
        return body
    rest = lines[i + 1 :]
    while rest and not rest[0].strip():
        rest = rest[1:]
    return "\n".join(rest)
