"""Bidirectional text utilities for Kazma — Unicode LTR/RTL isolation helpers.

Provides helpers for isolating English timestamps, model identifiers, URLs,
UUIDs, and numeric tokens embedded inside Arabic text strings across backend,
gateway, and terminal outputs.
"""

from __future__ import annotations

import re
from datetime import datetime

# Unicode Directional Formatting Characters
LRI = "\u2066"  # Left-to-Right Isolate
RLI = "\u2067"  # Right-to-Left Isolate
FSI = "\u2068"  # First Strong Isolate
PDI = "\u2069"  # Pop Directional Isolate

__all__ = [
    "FSI",
    "LRI",
    "PDI",
    "RLI",
    "format_bidi_timestamp",
    "isolate_ltr",
    "wrap_mixed_arabic_tokens",
]


def isolate_ltr(content: str) -> str:
    """Wrap content in Unicode LTR directional isolation markers (LRI ... PDI).

    Prevents English numbers, timestamps, model IDs, and URLs from leaking
    their directionality into surrounding Arabic text or flipping colons/punctuation.
    """
    if not content:
        return ""
    return f"{LRI}{content}{PDI}"


def format_bidi_timestamp(
    dt: datetime | None = None, fmt: str = "%Y-%m-%d %H:%M:%S"
) -> str:
    """Format a datetime wrapped in Unicode LTR isolation for clean RTL embedding."""
    if dt is None:
        dt = datetime.now()
    formatted_time = dt.strftime(fmt)
    return isolate_ltr(formatted_time)


def wrap_mixed_arabic_tokens(text: str) -> str:
    """Identify LTR tokens (URLs, model names like 'gemini-2.5-flash', UUIDs)

    embedded in Arabic text and wrap them in LTR isolators.
    """
    if not text:
        return ""

    # Matches URLs, UUIDs, emails, model identifiers (e.g. gemini-2.5-flash, gpt-4o)
    ltr_pattern = re.compile(
        r"(https?://\S+|[a-zA-Z0-9_\.\-]+@[a-zA-Z0-9_\.\-]+|[0-9a-fA-F\-]{36}|[a-zA-Z0-9]+[\-_\.][a-zA-Z0-9\-_.]+)"
    )

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return isolate_ltr(token)

    return ltr_pattern.sub(_replace, text)
