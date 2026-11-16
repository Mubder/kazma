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
_BACKTICKS = re.compile(r"`+")
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


def _without_code_spans(line: str) -> str:
    """*line* without its code spans: a run of backticks opens one, and the
    next run of the same length closes it (CommonMark).

    One pass over the runs. The pattern this replaces retried every opener,
    and every shorter piece of one, over the rest of the line: a line of
    backticks in a ``generate_pdf`` body took seconds on the event loop
    (``DocumentImages.in_body``; Sonar S8786, 2026-11-16). It also opened and
    closed spans on pieces of longer runs, which CommonMark does not.
    """
    runs = [match.span() for match in _BACKTICKS.finditer(line)]
    closer: list[int | None] = [None] * len(runs)
    latest: dict[int, int] = {}
    for index in range(len(runs) - 1, -1, -1):
        width = runs[index][1] - runs[index][0]
        closer[index] = latest.get(width)
        latest[width] = index
    kept: list[str] = []
    cursor = index = 0
    while index < len(runs):
        end = closer[index]
        if end is None:
            index += 1
            continue
        kept.append(line[cursor:runs[index][0]])
        cursor = runs[end][1]
        index = end + 1
    kept.append(line[cursor:])
    return "".join(kept)


def _images_in(text: str) -> list[str]:
    """Each ``![alt](address)`` in *text* (alt and address on one line), as
    the pattern it replaces found them with ``findall``, read once.

    From a "![" the alt ends at the first "]" and the address at the first
    ")" after it, so every "![" before the same "]" ends the same way: once
    one fails, the next start worth trying is past that "]". The pattern
    tried each "![" over the rest of the line (quadratic on a run of them).
    """
    found: list[str] = []
    for line in text.split("\n"):  # nothing in the pattern crosses a newline
        cursor = 0
        while True:
            start = line.find("![", cursor)
            if start < 0:
                break
            close = line.find("]", start + 2)
            if close < 0:
                break
            if not line.startswith("(", close + 1):
                cursor = close + 1
                continue
            end = line.find(")", close + 2)
            if end < 0:
                break
            found.append(line[start:end + 1])
            cursor = end + 1
    return found


def inline_images(body: str) -> list[str]:
    """Image markup inside a line of text (outside code), which is no figure."""
    found: list[str] = []
    for line, fenced in _lines(body):
        if fenced or _image(line) is not None:
            continue
        found.extend(_images_in(_without_code_spans(line)))
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
