"""Atomic, non-clobbering publication of already path-checked extracts."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def create_extract(target: Path, text: str) -> None:
    """Publish complete bytes exactly once; existing paths are never replaced.

    Call on a worker thread after workspace/path authorization. Hard-link
    publication is atomic and exclusive on supported filesystems; unsupported
    filesystems fail visibly instead of falling back to a clobbering write.
    """
    if target.exists():
        raise FileExistsError(str(target))
    from kazma_core.web_acquire.research_llm import active_route
    if active_route.get() is None:
        from kazma_core.web_acquire.output_allowance import reserve
        reserve(target, len(text.encode('utf-8')))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".kazma-extract-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
