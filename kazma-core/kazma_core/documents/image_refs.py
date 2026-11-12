"""How a document's text names an image: a Markdown image on a line of its own.

``![A caption](path/to/picture.png)`` on its own line, in a section body or a
Markdown source, is a figure placed right there, the alt text its caption. A
path with spaces or parentheses goes in angle brackets:
``![Caption](<my pictures/a (1).png>)``. Lines inside a fenced code block are
text, as an image inside a sentence is: the document tools refuse the latter
rather than drop it (``inline_images``).

One grammar for the Kazma process (``documents.image_inputs``, which finds
and approves the images) and the renderer worker (which places them): the
standard library only, so the worker image needs nothing more.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["ImageRef", "inline_images", "parts", "rewrite", "without_images"]

_IMAGE_LINE = re.compile(
    r"^[ \t]{0,3}!\[(?P<alt>(?:[^\]\\\n]|\\.)*)\]\([ \t]*"
    r"(?:<(?P<angle>[^<>\n]+)>|(?P<bare>[^\s()<>]+))"
    r"(?:[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'))?[ \t]*\)[ \t]*$"
)
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_ANY_IMAGE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
_ESCAPE = re.compile(r"\\(.)")


@dataclass(frozen=True, slots=True)
class ImageRef:
    """One image line: its caption, what it names, and the line as written."""

    caption: str
    source: str
    line: str


def _image(line: str) -> ImageRef | None:
    match = _IMAGE_LINE.match(line)
    if not match:
        return None
    source = (match.group("angle") or match.group("bare") or "").strip()
    if not source:
        return None
    caption = _ESCAPE.sub(r"\1", match.group("alt")).strip()
    return ImageRef(caption=caption, source=source, line=line)


def _lines(body: str) -> list[tuple[str, bool]]:
    """Each line of *body* and whether it is inside a fenced code block."""
    out: list[tuple[str, bool]] = []
    fence: str | None = None
    for line in str(body or "").splitlines():
        opener = _FENCE.match(line)
        if fence is None and opener:
            fence = opener.group(1)[0] * len(opener.group(1))
            out.append((line, True))
            continue
        if fence is not None:
            closing = _FENCE.match(line)
            if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= len(fence):
                fence = None
            out.append((line, True))
            continue
        out.append((line, False))
    return out


def parts(body: str) -> list[str | ImageRef]:
    """*body* split at its image lines: text runs and images, in order."""
    result: list[str | ImageRef] = []
    run: list[str] = []
    for line, fenced in _lines(body):
        ref = None if fenced else _image(line)
        if ref is None:
            run.append(line)
            continue
        if run:
            result.append("\n".join(run))
            run = []
        result.append(ref)
    if run:
        result.append("\n".join(run))
    return result


def inline_images(body: str) -> list[str]:
    """Image markup inside a line of text (outside code), which is no figure."""
    found: list[str] = []
    for line, fenced in _lines(body):
        if fenced or _image(line) is not None:
            continue
        found.extend(_ANY_IMAGE.findall(_CODE_SPAN.sub("", line)))
    return found


def rewrite(body: str, name_for: Callable[[ImageRef], str]) -> str:
    """*body* with each image line naming ``name_for(ref)`` instead."""
    out: list[str] = []
    for part in parts(body):
        if isinstance(part, ImageRef):
            caption = part.caption.replace("\\", "\\\\").replace("]", "\\]")
            out.append(f"![{caption}]({name_for(part)})")
        else:
            out.append(part)
    return "\n".join(out)


def without_images(body: str) -> str:
    """*body* with each image line replaced by its caption (what a reader of
    the document's text sees)."""
    return "\n".join(part.caption if isinstance(part, ImageRef) else part for part in parts(body))
