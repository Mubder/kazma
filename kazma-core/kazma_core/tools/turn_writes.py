"""What the running turn has written, by content (resilience plan D4).

The 1.43-million-token DOCX turn wrote the same document again under
another name and generated it twice. Within one turn (the reader the
per-turn read cache uses: the chat thread, or a swarm task's scope):

* ``file_write`` of content this turn already wrote to another file is
  refused before anything is written, naming that file;
* ``generate_pdf`` / ``generate_docx`` with the same title, sections and
  tables as an earlier call of the turn are refused, naming its file.

Small contents (under :data:`MIN_BYTES`) are not judged: two empty
``__init__.py`` files are a project, not a loop. The record is dropped at the
turn's end with the read cache (``file_read.clear_read_cache``).
"""

from __future__ import annotations

import hashlib
import threading

__all__ = ["MIN_BYTES", "earlier_copy", "forget_reader", "note_written"]

#: Contents shorter than this are never judged duplicates.
MIN_BYTES = 512
#: At most this many contents remembered per reader.
_MAX_PER_READER = 64

_lock = threading.Lock()
_written: dict[str, dict[str, str]] = {}


def _reader() -> str:
    from kazma_core.tools.file_read import _reader as read_cache_reader

    return read_cache_reader()


def _digest(kind: str, content: str | bytes) -> str | None:
    data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    if len(data) < MIN_BYTES:
        return None
    return hashlib.sha256(kind.encode("utf-8") + b"\0" + data).hexdigest()


def earlier_copy(kind: str, content: str | bytes, path: str) -> str | None:
    """The file the running turn already wrote *content* to, when it is not
    *path*; ``None`` otherwise."""
    digest = _digest(kind, content)
    if digest is None:
        return None
    with _lock:
        earlier = _written.get(_reader(), {}).get(digest)
    return earlier if earlier and earlier != path else None


def note_written(kind: str, content: str | bytes, path: str) -> None:
    """The running turn wrote *content* to *path*."""
    digest = _digest(kind, content)
    if digest is None or not path:
        return
    with _lock:
        mine = _written.setdefault(_reader(), {})
        mine.pop(digest, None)
        mine[digest] = path
        while len(mine) > _MAX_PER_READER:
            mine.pop(next(iter(mine)))


def forget_reader(reader: str) -> None:
    """The turn of *reader* ended (``clear_read_cache``)."""
    with _lock:
        _written.pop(reader, None)
