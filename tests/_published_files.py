"""Read a file another process published, on a busy Windows machine too.

A child process publishes a result by writing ``<name>.pending`` and renaming
it into place (``os.replace``), so a reader never sees half a file. On Windows
a file just renamed can still be held for a moment -- by the antivirus
scanner, or the indexer -- and opening it then fails with a sharing violation
(``PermissionError``). The full suite on a loaded machine met it on
2026-11-14 (``tests/test_process_budgets.py``), and a child that wrote its
file in place was read empty (``tests/test_durable_crawls.py``).
"""

from __future__ import annotations

import time
from pathlib import Path


def publish_code(path: Path | str, value_code: str) -> str:
    """Python source (one statement per line) that publishes the text of
    *value_code* at *path*: written to ``<path>.pending``, then renamed into
    place. The caller's program must have imported ``os``."""
    pending = str(path) + ".pending"
    return (
        f"with open({pending!r}, 'w', encoding='utf-8') as _published:\n"
        f"    _published.write({value_code})\n"
        f"os.replace({pending!r}, {str(path)!r})\n"
    )


def read_published_text(path: Path, *, timeout: float = 10.0) -> str:
    """The text of *path*, once it exists and can be opened; a sharing
    violation is waited out, never a reason to fail."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
