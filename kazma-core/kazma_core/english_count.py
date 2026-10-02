"""An English count with its noun in the form the number takes.

The web UI's counts come from the catalog's plural forms
(``kazma_ui.i18n.t_plural``, ``window.kazmaCount``). The terminal UI, the CLI
and the chat-app replies speak English only, and printed a count beside a
plural whatever the number: the TUI's "(1 msgs)" after loading a one-message
chat, "1 new chunks" after a ``/kb`` ingest, "Slow down — 0/1 requests
available" (2026-10-03, ``tests/test_english_counts.py``).
"""

from __future__ import annotations


def count_noun(n: int | float, singular: str, plural: str | None = None, *, shown: str | None = None) -> str:
    """``"1 message"``, ``"3 messages"``, ``"12,345 tokens"``.

    ``plural`` defaults to ``singular + "s"``; pass it for any other plural
    ("match" -> "matches"). A whole number is shown with thousands
    separators; ``shown`` prints it another way while the noun still follows
    the number.
    """
    word = singular if n == 1 else (plural if plural is not None else singular + "s")
    if shown is None:
        shown = f"{n:,}" if isinstance(n, int) and not isinstance(n, bool) else str(n)
    return f"{shown} {word}"
