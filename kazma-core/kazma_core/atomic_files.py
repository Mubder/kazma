"""Write a file whole or not at all (2026-11, FA-144).

A backup's or a bundle's ``manifest.json`` is what says what it holds and
what it checks against: a restore, ``verify`` and retention read it. It was
written in place with ``write_text``, so a crash or a full disk during the
write left a truncated manifest -- a backup that no longer verifies, or one
that reads as something else. :func:`atomic_write_text` writes a temporary
file beside the target, flushes it to disk and renames it over the target,
so a reader finds the old file or the new one. ``tests/test_atomic_manifests.py``
holds every manifest write to it.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = ["atomic_write_bytes", "atomic_write_text"]


def atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Replace *path* with *text*: the old content or the new, never a part."""
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """Replace *path* with *data*: the old content or the new, never a part."""
    target = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if os.name != "nt":
        # The rename itself reaches the disk with its folder's entry.
        try:
            dir_fd = os.open(str(target.parent), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)
