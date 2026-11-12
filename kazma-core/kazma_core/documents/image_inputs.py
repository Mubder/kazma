"""The images a document tool puts in a document, gathered before the worker runs.

A document names an image with a Markdown image line (``documents.image_refs``)
in a section body or its Markdown file, or with a slide's ``image``. Here, in
the Kazma process, each one is:

  * a file, never an address: a web image is refused with the tool that
    saves it first (``download_image``), so a fetch is never hidden inside a
    document call;
  * one the file tools may read (``resolve_in_scope(..., "reads")``: the
    workspace, granted folders, never a Kazma store or secret file), or one
    Kazma made or received itself: a generated image (``<data dir>/images``)
    or a chat attachment (``<data dir>/attachments``);
  * at most ``MAX_IMAGE_BYTES``, and starting like a PNG, JPEG, GIF or WebP
    (the first bytes only: no image is decoded in this process);
  * copied into a folder of the call's own under a name of the call's own
    (``image-01.png``), and the reference rewritten to that name.

The renderer worker then decodes, cleans and saves each one again in its
sandbox (``documents.image_clean``); the worker never sees a path.

Anything else refuses the call before anything is made
(``RefusedBeforeEffect``), naming the image and what to do.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.documents.image_refs import ImageRef, inline_images, parts, rewrite, without_images

__all__ = [
    "MAX_IMAGES",
    "MAX_IMAGE_BYTES",
    "MAX_TOTAL_BYTES",
    "DocumentImages",
    "image_type",
    "text_without_images",
]

#: Images in one document. The container takes 64 files a job.
MAX_IMAGES = 40
#: One image file.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
#: Every image of one document together (the container's scratch is 768 MB).
MAX_TOTAL_BYTES = 160 * 1024 * 1024

_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://")


def image_type(head: bytes) -> str | None:
    """The file extension a PNG, JPEG, GIF or WebP starts like, or None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def text_without_images(body: str) -> str:
    """A section body as a reader of the document sees its words: each image
    line its caption (what the PDF check compares against)."""
    return without_images(body)


def _is_address(source: str) -> bool:
    lowered = source.strip().lower()
    if lowered.startswith(("//", "data:", "file:", "blob:")):
        return True
    return bool(_SCHEME.match(lowered))  # http://, https://, ftp://, s3:// ...


def _kazma_folders() -> tuple[Path, ...]:
    """Folders of images Kazma made or received itself (blocking)."""
    from kazma_core.paths import data_dir

    root = data_dir()
    return tuple(p.resolve() for p in (root / "images", root / "attachments"))


def _in_kazma_folder(source: str) -> Path | None:
    """*source* when it is a file in one of Kazma's own image folders (blocking).

    Only an absolute path, checked on its text before it is resolved
    (``remote_path_reason``), and resolved strictly so a link cannot point out.
    """
    from kazma_core.store_registry import is_kazma_store
    from kazma_core.workspace.remote_paths import remote_path_reason

    if remote_path_reason(source) is not None:
        return None
    candidate = Path(source).expanduser()
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if is_kazma_store(resolved):
        return None
    if any(resolved.is_relative_to(folder) for folder in _kazma_folders()):
        return resolved
    return None


@dataclass
class _Approved:
    name: str
    source: str
    digest: str
    size: int


@dataclass
class DocumentImages:
    """The images of one document call, staged in a folder of its own.

    Use as ``async with DocumentImages() as images``: the folder is removed
    when the call ends, whatever happened.
    """

    _folder: Path | None = None
    _by_file: dict[Path, _Approved] = field(default_factory=dict)
    _order: list[_Approved] = field(default_factory=list)
    _total: int = 0

    async def __aenter__(self) -> DocumentImages:
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._folder is not None:
            await asyncio.to_thread(shutil.rmtree, self._folder, True)
            self._folder = None

    # ------------------------------------------------------------------ #
    @property
    def paths(self) -> tuple[Path, ...]:
        """The staged copies, for ``approved_assets``."""
        folder = self._folder
        return tuple(folder / item.name for item in self._order) if folder is not None else ()

    @property
    def digests(self) -> tuple[str, ...]:
        """Each image's SHA-256, in order (what makes two calls the same document)."""
        return tuple(item.digest for item in self._order)

    def explain(self, message: str) -> str:
        """*message* with each staged name it mentions followed by the image's source."""
        for item in self._order:
            if item.name in message or Path(item.name).stem in message:
                message += f" ({item.name} is {item.source})"
        return message

    # ------------------------------------------------------------------ #
    async def take(self, source: str, base: Path | None = None) -> str:
        """Approve and stage the image *source* names; its staged name.

        A relative path from a Markdown file (*base*) means that file's folder
        first, as in any Markdown viewer, then the workspace.
        """
        source = str(source or "").strip()
        if not source:
            raise RefusedBeforeEffect("Error: an image names no file. Give the image's path.")
        if _is_address(source):
            raise RefusedBeforeEffect(
                f"Error: the image {source[:200]} is an address, not a file. Save it first with "
                "download_image(url=...), then name the path it returns; nothing was made."
            )
        path = await asyncio.to_thread(_in_kazma_folder, source)
        if path is None:
            from kazma_core.agent.tool_scope import resolve_in_scope

            candidates = [source]
            if base is not None and not Path(source).is_absolute():
                candidates.insert(0, str(Path(base).parent / source))
            for candidate in candidates:
                resolved, refusal = await resolve_in_scope(candidate, "reads")
                if refusal:
                    raise RefusedBeforeEffect(f"Error: the image {source[:200]} cannot be read: {refusal}")
                found = await asyncio.to_thread(resolved.is_file)
                if path is None or found:
                    path = resolved
                if found:
                    break
        if path is None:  # pragma: no cover - the loop always sets it
            raise RefusedBeforeEffect(f"Error: the image {source[:200]} was not found; nothing was made.")
        return await asyncio.to_thread(self._stage, path, source)

    def _stage(self, path: Path, source: str) -> str:
        if not path.is_file():
            raise RefusedBeforeEffect(
                f"Error: the image {source[:200]} was not found (resolved: {path}); nothing was made."
            )
        key = path.resolve()
        if key in self._by_file:
            return self._by_file[key].name
        if len(self._order) >= MAX_IMAGES:
            raise RefusedBeforeEffect(
                f"Error: a document takes at most {MAX_IMAGES} images; nothing was made."
            )
        size = path.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise RefusedBeforeEffect(
                f"Error: the image {source[:200]} is {size // (1024 * 1024)} MB; an image may be at most "
                f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB. Nothing was made."
            )
        if self._total + size > MAX_TOTAL_BYTES:
            raise RefusedBeforeEffect(
                f"Error: the document's images come to more than {MAX_TOTAL_BYTES // (1024 * 1024)} MB "
                "together; use fewer or smaller images. Nothing was made."
            )
        with path.open("rb") as handle:
            kind = image_type(handle.read(16))
        if kind is None:
            raise RefusedBeforeEffect(
                f"Error: {source[:200]} is not a PNG, JPEG, GIF or WebP image; a document takes only "
                "those. Nothing was made."
            )
        if self._folder is None:
            self._folder = Path(tempfile.mkdtemp(prefix="kazma-doc-images-"))
        name = f"image-{len(self._order) + 1:02d}.{kind}"
        target = self._folder / name
        shutil.copyfile(path, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if target.stat().st_size != size:  # changed while it was copied
            raise RefusedBeforeEffect(
                f"Error: the image {source[:200]} changed while it was read; call again. Nothing was made."
            )
        item = _Approved(name=name, source=source, digest=digest, size=size)
        self._by_file[key] = item
        self._order.append(item)
        self._total += size
        return name

    # ------------------------------------------------------------------ #
    async def in_body(self, body: str, where: str, base: Path | None = None) -> str:
        """*body* with every image it names approved and renamed."""
        stray = inline_images(body)
        if stray:
            raise RefusedBeforeEffect(
                f"Error: {where} has an image inside a line of text ({stray[0][:120]}). Put each "
                "image on a line of its own, as ![caption](path); nothing was made."
            )
        names: dict[str, str] = {}
        for part in parts(body):
            if isinstance(part, ImageRef) and part.source not in names:
                names[part.source] = await self.take(part.source, base)
        if not names:
            return body
        return rewrite(body, lambda ref: names[ref.source])

    async def in_sections(self, sections: list[dict[str, str]],
                          base: Path | None = None) -> list[dict[str, str]]:
        """The sections with their images approved and renamed (*base*: the
        Markdown file they came from, for its relative paths)."""
        out: list[dict[str, str]] = []
        for index, section in enumerate(sections, start=1):
            heading = str(section.get("heading", ""))
            where = f"section {index}" + (f" ({heading.lstrip('#').strip()[:60]})" if heading.strip() else "")
            out.append({"heading": heading,
                        "body": await self.in_body(str(section.get("body", "")), where, base)})
        return out

    async def in_slides(self, slides: list[Any]) -> list[Any]:
        """The slides with each ``image`` approved and renamed."""
        out: list[Any] = []
        for slide in slides:
            if isinstance(slide, dict) and str(slide.get("image") or "").strip():
                slide = {**slide, "image": await self.take(str(slide["image"]))}
            elif isinstance(slide, dict) and "image" in slide:
                slide = {key: value for key, value in slide.items() if key != "image"}
            out.append(slide)
        return out
