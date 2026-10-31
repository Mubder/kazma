"""Characters the house face lacks, drawn in a face that has them (ReportLab PDF).

IBM Plex Sans Arabic, the house face, has no Latin Extended-A or Additional:
no a-macron, h/s/z/d/t with a dot below, no ayn or hamza modifier letters --
every letter of a scholarly transliteration of Arabic ("Kāẓima", "Ṣabāḥ",
"ʿAbd"). LibreOffice and Word fall back to another face on their own;
ReportLab has no fallback and draws such a letter blank. Live 2026-10-31 a
Kuwait history paper rendered "Kāẓima" as "K ima", and the agent stripped the
transliteration from its own paper to get a readable PDF.

:class:`GlyphFallback` wraps each run of such characters in a ``<font>`` tag
naming a face whose cmap has them: a sans with wide Latin coverage the
machine has (it sits beside Plex), else the bundled faces (Amiri covers all of
it, so every install has one). ReportLab's ``<font>`` tag resets bold to the
face it names, so a run inside ``<b>`` (or a bold paragraph) names the bold
face itself. A character no face has is left as it was.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from kazma_core.documents.fonts import _FONT_FILE_ERRORS, bundled_font_dir

logger = logging.getLogger(__name__)

__all__ = ["GlyphFallback"]

#: Faces that may draw a character the house face lacks, in order, each
#: (regular, bold). Sans faces with wide Latin first; the bundle follows.
_SYSTEM_FACES: tuple[tuple[str, str], ...] = (
    ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
     "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
)

#: A tag or an entity in ReportLab paragraph markup: copied as it is.
_MARKUP = re.compile(r"<[^>]*>|&[#A-Za-z0-9]+;")
_BOLD_OPEN = re.compile(r"<(b|strong)(\s[^>]*)?>", re.IGNORECASE)
_BOLD_CLOSE = re.compile(r"</(b|strong)\s*>", re.IGNORECASE)


@lru_cache(maxsize=32)
def _cmap(path: str) -> frozenset[int]:
    """The codepoints *path* has glyphs for (empty when it cannot be read)."""
    try:
        from reportlab.pdfbase.ttfonts import TTFError, TTFont
    except ImportError:
        return frozenset()
    try:
        return frozenset(TTFont("kazma-glyph-probe", path).face.charToGlyph)
    except _FONT_FILE_ERRORS + (TTFError,):
        logger.debug("[glyph_fallback] cmap probe failed for %s", path, exc_info=True)
        return frozenset()


def _candidate_faces(main: Path) -> list[tuple[Path, Path | None]]:
    faces: list[tuple[Path, Path | None]] = []
    for regular, bold in _SYSTEM_FACES:
        if Path(regular).is_file():
            faces.append((Path(regular), Path(bold) if Path(bold).is_file() else None))
    directory = bundled_font_dir()
    if directory is not None:
        found = sorted(directory.glob("*.ttf"))
        for regular in (f for f in found if "bold" not in f.name.lower()):
            if regular.resolve() == main.resolve():
                continue
            stem = regular.stem.lower().replace("-regular", "")
            bold = next((b for b in found if "bold" in b.name.lower() and stem in b.stem.lower()), None)
            faces.append((regular, bold))
    return faces


class GlyphFallback:
    """Draw what the main face lacks in a face that has it."""

    def __init__(self, main: Path, pdfmetrics: Any, TTFont: Any) -> None:
        self._main = _cmap(str(main))
        self._faces = _candidate_faces(main)
        self._pdfmetrics = pdfmetrics
        self._TTFont = TTFont
        self._names: dict[int, str] = {}
        self.used: list[str] = []  # files of the faces this render drew in

    def covers(self, text: str) -> bool:
        """True when the main face has every visible character of *text*."""
        return all(ord(ch) in self._main or ch.isspace() for ch in text)

    def _face_for(self, ch: str) -> int | None:
        code = ord(ch)
        for index, (regular, _bold) in enumerate(self._faces):
            if code in _cmap(str(regular)):
                return index
        return None

    def _name(self, index: int) -> str:
        name = self._names.get(index)
        if name is None:
            from reportlab.lib.fonts import addMapping

            regular, bold = self._faces[index]
            # Named by the file: ReportLab keeps the first font registered
            # under a name for the life of the process.
            name = "KazmaGlyph" + hashlib.sha1(str(regular.resolve()).encode(), usedforsecurity=False).hexdigest()[:10]
            self._pdfmetrics.registerFont(self._TTFont(name, str(regular)))
            bold_name = name
            if bold is not None:
                bold_name = f"{name}Bold"
                self._pdfmetrics.registerFont(self._TTFont(bold_name, str(bold)))
            # One family. The italic mappings first: the last mapping of a
            # face is what ReportLab reads back for it (regular: not bold).
            addMapping(name, 0, 1, name)
            addMapping(name, 1, 1, bold_name)
            addMapping(name, 0, 0, name)
            addMapping(name, 1, 0, bold_name)
            self._names[index] = name
            self.used.append(str(regular))
        return name

    def markup(self, markup: str, *, bold: bool = False) -> str:
        """*markup* (ReportLab paragraph markup) with each run the main face
        lacks wrapped in a face that has it; tags and entities kept as they are."""
        if not markup or self.covers(_MARKUP.sub("", markup)):
            return markup
        out: list[str] = []
        pos = 0
        depth = 0  # inside how many <b>
        for match in _MARKUP.finditer(markup):
            out.append(self._wrap(markup[pos:match.start()], bold or depth > 0))
            tag = match.group(0)
            if _BOLD_OPEN.fullmatch(tag):
                depth += 1
            elif _BOLD_CLOSE.fullmatch(tag):
                depth = max(0, depth - 1)
            out.append(tag)
            pos = match.end()
        out.append(self._wrap(markup[pos:], bold or depth > 0))
        return "".join(out)

    def _wrap(self, text: str, bold: bool) -> str:
        out: list[str] = []
        run: list[str] = []
        current: int | None = None

        def flush() -> None:
            if run:
                face = self._name(current) + ("Bold" if bold and self._faces[current][1] else "")
                out.append(f'<font name="{face}">{"".join(run)}</font>')
                run.clear()

        for ch in text:
            index = None if ord(ch) in self._main or ch.isspace() else self._face_for(ch)
            if index is not None and index == current:
                run.append(ch)
                continue
            flush()
            current = index
            if index is None:
                out.append(ch)
            else:
                run.append(ch)
        flush()
        return "".join(out)

    def cell(self, value: str, *, Paragraph: Any, font_name: str, size: float,
             color: Any = None, bold: bool = False) -> Any:
        """A table cell: the text itself when the main face draws all of it,
        else a paragraph in the cell's font that draws the rest in a face
        that has it (a plain cell string cannot change face)."""
        if self.covers(value):
            return value
        from reportlab.lib.styles import ParagraphStyle

        style = ParagraphStyle(
            f"glyph-cell-{font_name}-{size}", fontName=font_name, fontSize=size,
            leading=size * 1.2, **({"textColor": color} if color is not None else {}),
        )
        text = html.escape(value)
        if getattr(Paragraph, "glyph_fallback", None) is not self:  # else the paragraph does it
            text = self.markup(text, bold=bold)
        return Paragraph(text, style)

    def paragraph_class(self, base: Any, bold_font: str) -> Any:
        """*base* (ReportLab's Paragraph) drawing what the main face lacks in a
        face that has it; a paragraph set in *bold_font* takes the bold."""
        fallback = self

        class _Paragraph(base):  # type: ignore[misc,valid-type]
            glyph_fallback = fallback

            def __init__(self, text: str, style: Any = None, *args: Any, **kwargs: Any) -> None:
                is_bold = getattr(style, "fontName", "") == bold_font
                super().__init__(fallback.markup(text, bold=is_bold), style, *args, **kwargs)

        return _Paragraph
