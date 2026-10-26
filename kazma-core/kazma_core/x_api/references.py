"""Reference documents for X post drafting.

The operator names up to three text files in Settings → X
(``connectors.x.drafting.reference_files``): what the account's posts are
about, in the operator's own words. X Studio's Generate reads them whole every
time it drafts. The drafter is given them as the facts its posts may state,
and the fact check as evidence a claim may cite, so a post about the product
says what the product's own page says and no more.

A named file that cannot be read stops the draft: posts written without the
facts the operator chose would look grounded and not be.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SETTING_KEY = "connectors.x.drafting.reference_files"
MAX_FILES = 3
MAX_PATH_CHARS = 500
#: All reference text together. The drafter and each fact-check call read
#: it, so this is a cost bound as much as a size one.
MAX_CHARS = 40_000


class ReferenceUnreadable(ValueError):
    """A configured reference document could not be used; nothing was drafted."""


@dataclass(frozen=True)
class ReferenceDocument:
    path: str
    text: str
    modified_at: float

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def evidence(self) -> dict[str, Any]:
        """The fact check's source record; ``evidence.source_hold`` reads it.

        Dated by the file's last change: a reference nobody has touched in
        longer than the subject's evidence age is held for review like any
        other old source.
        """
        digest = self.content_hash
        return {"source_id": f"reference:{digest[:12]}", "kind": "reference_file",
                "title": self.name, "path": self.path, "content": self.text,
                "content_hash": digest, "truncated": False,
                "published_at": self.modified_at, "retrieved_at": time.time()}

    def summary(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "chars": len(self.text)}


def normalize_paths(raw: Any) -> tuple[str, ...]:
    """The saved value as paths: strings, stripped, no blanks or repeats."""
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, (list, tuple)):
        return ()
    paths: list[str] = []
    for item in raw:
        text = item.strip() if isinstance(item, str) else ""
        if text and text not in paths:
            paths.append(text)
    return tuple(paths)


def validate_paths(raw: Any) -> tuple[str, ...]:
    """The paths to save, or ValueError naming what is wrong with them."""
    paths = normalize_paths(raw)
    if len(paths) > MAX_FILES:
        raise ValueError(f"Name at most {MAX_FILES} reference documents for X drafting.")
    for path in paths:
        if len(path) > MAX_PATH_CHARS:
            raise ValueError(f"A reference document path is longer than {MAX_PATH_CHARS} characters.")
    return paths


def load_references(paths: tuple[str, ...]) -> tuple[ReferenceDocument, ...]:
    """Read every reference whole, or raise ReferenceUnreadable.

    A relative path is read under the active workspace, like a path the
    agent's file tools are given. Blocking (workspace lookup and file reads):
    call it in a thread.
    """
    from kazma_core.store_registry import is_kazma_store
    from kazma_core.workspace.binding import resolve_tool_path

    documents: list[ReferenceDocument] = []
    total = 0
    for raw in validate_paths(paths):
        path = resolve_tool_path(raw)
        if is_kazma_store(path):
            raise ReferenceUnreadable(f"Reference document {raw} is one of Kazma's own databases, not a text file.")
        try:
            stat = path.stat()
            if not path.is_file():
                raise ReferenceUnreadable(f"Reference document {raw} is not a file.")
            if stat.st_size > MAX_CHARS * 4:
                raise ReferenceUnreadable(f"Reference document {raw} is larger than X drafting reads.")
            data = path.read_bytes()
        except OSError as exc:
            raise ReferenceUnreadable(
                f"Reference document {raw} cannot be read ({exc.strerror or type(exc).__name__}).") from exc
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ReferenceUnreadable(f"Reference document {raw} is not UTF-8 text.") from exc
        text = text.replace("\r\n", "\n").strip()
        if not text or "\x00" in text:
            raise ReferenceUnreadable(f"Reference document {raw} holds no text.")
        total += len(text)
        if total > MAX_CHARS:
            raise ReferenceUnreadable(
                f"The reference documents hold more than {MAX_CHARS:,} characters, the most X drafting "
                "reads. Name fewer or shorter files.")
        documents.append(ReferenceDocument(str(path), text, stat.st_mtime))
    return tuple(documents)
