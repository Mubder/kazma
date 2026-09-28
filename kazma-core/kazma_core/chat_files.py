"""Files the agent shares into a web chat.

A web conversation has no platform to send a file to. Its ``_gateway`` block
names the operator's Telegram chat so that a reminder booked there rings
somewhere (``tools.send_message.web_gateway_block``), and ``send_file`` used
that address: a file asked for in the web chat went to Telegram while the
answer said "sent to this chat above", and a generated image was never shown
on the page at all (found on the live install 2026-09-28).

A web turn now shares a copy instead. It is stored beside the chat uploads
(``<data dir>/attachments/shared/``, under an opaque id, with a small JSON
record of its name, type, size and chat), served back only to the owner of
that chat by ``GET /api/chat/files/{id}``, and shown in the answer through
the Markdown the tool hands the model: an image inline, anything else as a
download link.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "INLINE_IMAGE_TYPES",
    "MAX_SHARED_BYTES",
    "SharedFile",
    "load_shared",
    "share_file",
    "web_chat_thread",
]

#: The only types a browser is allowed to render in the page. Everything
#: else -- SVG and HTML included, which can carry script -- is downloaded.
INLINE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
#: The same ceiling send_file applies to every platform.
MAX_SHARED_BYTES = 50 * 1024 * 1024

_ID_RE = re.compile(r"^att_[0-9a-f]{32}$")
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _shared_dir() -> Path:
    """Where shared copies live: beside the uploads, so they travel with
    them (migration bundle, backups)."""
    from kazma_core.paths import data_dir

    return data_dir() / "attachments" / "shared"


def _sniff(head: bytes) -> str:
    """The file's type from its first bytes -- never from its name: the
    image tool names every file ``.png`` whatever its backend returned."""
    for prefix, mime in _SIGNATURES:
        if head.startswith(prefix):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


@dataclass(frozen=True)
class SharedFile:
    id: str
    name: str
    mime: str
    size: int
    thread_id: str

    @property
    def url(self) -> str:
        return f"/api/chat/files/{self.id}"

    @property
    def inline(self) -> bool:
        return self.mime in INLINE_IMAGE_TYPES

    def markdown(self) -> str:
        """An image for a picture, a download link for anything else."""
        label = re.sub(r"[\[\]()\r\n]+", " ", self.name).strip() or "file"
        return f"![{label}]({self.url})" if self.inline else f"[{label}]({self.url})"


def web_chat_thread() -> str:
    """The chat a web turn's tool is answering, or ``""`` when the running
    turn is not a web conversation (or names no chat)."""
    from kazma_core.safety.hitl import get_current_thread_id
    from kazma_core.tools.send_message import get_current_platform

    if get_current_platform() != "web":
        return ""
    return str(get_current_thread_id() or "").strip()


def share_file(path: Path, *, thread_id: str) -> SharedFile:
    """Copy *path* into the shared store for the chat *thread_id*.

    Raises ``ValueError`` for a missing chat, a missing or oversized file.
    """
    if not thread_id:
        raise ValueError("no chat to share into")
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"not a file: {src.name}")
    size = src.stat().st_size
    if size > MAX_SHARED_BYTES:
        raise ValueError(f"file too large ({size // 1024 // 1024} MB; max 50 MB)")
    with src.open("rb") as fh:
        mime = _sniff(fh.read(16))
    file_id = f"att_{uuid.uuid4().hex}"
    root = _shared_dir()
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, root / file_id)
    shared = SharedFile(id=file_id, name=src.name, mime=mime, size=size, thread_id=thread_id)
    record = {
        "name": shared.name,
        "mime": shared.mime,
        "size": shared.size,
        "thread_id": shared.thread_id,
        "created_at": time.time(),
    }
    (root / f"{file_id}.json").write_text(json.dumps(record), encoding="utf-8")
    return shared


def load_shared(file_id: str) -> tuple[SharedFile, Path] | None:
    """The shared file *file_id* and where its bytes are, or None.

    Only an id this module minted resolves, and only to a file directly in
    the shared store: the id is the whole name, never a path.
    """
    if not isinstance(file_id, str) or not _ID_RE.fullmatch(file_id):
        return None
    root = _shared_dir()
    blob, meta = root / file_id, root / f"{file_id}.json"
    try:
        record = json.loads(meta.read_text(encoding="utf-8"))
        if not blob.is_file():
            return None
        shared = SharedFile(
            id=file_id,
            name=str(record["name"]),
            mime=str(record["mime"]),
            size=int(record["size"]),
            thread_id=str(record["thread_id"]),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("[chat-files] unreadable record for %s: %s", file_id, exc)
        return None
    return shared, blob
