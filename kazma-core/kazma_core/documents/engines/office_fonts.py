"""The house face in every font slot of the Office files Kazma makes.

An Office file names a typeface in many more places than its runs, and each
place is read for some text:

* a DrawingML run (PowerPoint, chart text) has three slots, ``a:latin``,
  ``a:ea`` and ``a:cs``; Arabic is drawn from ``a:cs``. python-pptx's
  ``font.name`` writes ``a:latin`` alone, so every Arabic run of a Kazma deck
  fell through to the theme -- whose complex-script slot was empty and whose
  ``script="Arab"`` entry named Times New Roman (major) or Arial (minor). The
  owner saw "the Kazma font, displayed as Arial" (2026-11-10);
* the theme (every Office format has one) names the faces of everything that
  inherits: the caret at the end of a line, a new text box, a new cell, a
  heading style, the notes page. The three templates Kazma builds on named
  Calibri or Cambria there;
* paragraph and list defaults (``a:defRPr``, ``a:endParaRPr``, the master's
  ``p:txStyles``, ``p:defaultTextStyle``, the notes master's
  ``p:notesStyle``), Word's ``w:docDefaults`` and every style's ``w:rFonts``,
  Excel's first font (the Normal style, which new cells take);
* bullets (``a:buFont``): the slide master drew them in Arial.

One rule for all of them (AGENTS.md §19 J): the face of ``THEME`` -- IBM Plex
Sans Arabic for Latin, East Asian and complex script alike -- in every slot,
written by the helpers below. A bullet is "•" in that face, as in DOCX
(``DocxEngine._house_bullets``). Gate:
``tests/test_office_files_name_the_house_face.py``.

Nothing here parses XML: the engines hand over elements their own libraries
parsed (python-docx, python-pptx: entity resolution off), and a theme --
always the library's own template -- is changed as text, its one
``a:fontScheme`` replaced whole.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from xml.sax.saxutils import quoteattr

from lxml import etree

logger = logging.getLogger(__name__)

__all__ = [
    "BULLET",
    "house_faces",
    "house_text_properties",
    "house_bullet",
    "house_drawingml_tree",
    "house_theme",
    "house_word_styles",
    "house_font_table",
    "word_faces_named",
]

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

#: The house bullet, in every format (DOCX numbering, PPTX paragraphs and levels).
BULLET = "•"


def house_faces() -> tuple[str, str]:
    """``(latin, complex script)`` -- the THEME's faces (both IBM Plex Sans Arabic)."""

    from kazma_core.documents.style_theme import theme_fonts

    fonts = theme_fonts(rtl=False)
    return fonts["latin"], fonts["arabic"]


def _local(element: Any) -> str:
    tag = element.tag
    if not isinstance(tag, str):  # comments, processing instructions
        return ""
    return etree.QName(tag).localname


def _a(name: str) -> str:
    return f"{{{A_NS}}}{name}"


def _w(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def _insert(parent: Any, child: Any, before: frozenset[str]) -> None:
    """Put *child* before the first child of *parent* named in *before*, else last."""

    anchor = next((c for c in parent if _local(c) in before), None)
    if anchor is None:
        parent.append(child)
    else:
        anchor.addprevious(child)


# --------------------------------------------------------------------------- #
# DrawingML (PowerPoint, chart text, the theme)
# --------------------------------------------------------------------------- #

#: What follows the typefaces in CT_TextCharacterProperties (ECMA-376 21.1.2.3.2):
#: ln, fill, effect, highlight, underline, then latin, ea, cs, then these.
_AFTER_RUN_FACES = frozenset({"sym", "hlinkClick", "hlinkMouseOver", "rtl", "extLst"})
#: Every element of that type: a run's, a paragraph's or a level's defaults,
#: and the end-of-paragraph mark new text takes.
_TEXT_PROPERTIES = frozenset({"rPr", "defRPr", "endParaRPr"})


def house_text_properties(props: Any) -> None:
    """Name the house face in a text-properties element's three slots.

    *props* is an ``a:rPr``, ``a:defRPr`` or ``a:endParaRPr``. Any existing
    ``a:latin`` / ``a:ea`` / ``a:cs`` (a theme reference such as ``+mn-cs``
    included) is replaced; the new ones go where the schema puts them.
    """

    latin, arabic = house_faces()
    for child in list(props):
        if _local(child) in ("latin", "ea", "cs"):
            props.remove(child)
    for name, face in (("latin", latin), ("ea", latin), ("cs", arabic)):
        _insert(props, props.makeelement(_a(name), {"typeface": face}), _AFTER_RUN_FACES)


#: CT_TextParagraphProperties order (21.1.2.2.7): lnSpc, spcBef, spcAft,
#: bullet colour, bullet size, bullet font, bullet type, tabLst, defRPr, extLst.
_BULLET_FONT_BEFORE = frozenset({"buNone", "buAutoNum", "buChar", "buBlip", "tabLst", "defRPr", "extLst"})
_BULLET_TYPE_BEFORE = frozenset({"tabLst", "defRPr", "extLst"})
_BULLET_PARTS = frozenset({"buFont", "buFontTx", "buNone", "buAutoNum", "buChar", "buBlip"})


def house_bullet(ppr: Any) -> None:
    """Make a paragraph (or list level) draw the house bullet in the house face."""

    latin, _ = house_faces()
    for child in list(ppr):
        if _local(child) in _BULLET_PARTS:
            ppr.remove(child)
    _insert(ppr, ppr.makeelement(_a("buFont"), {"typeface": latin}), _BULLET_FONT_BEFORE)
    _insert(ppr, ppr.makeelement(_a("buChar"), {"char": BULLET}), _BULLET_TYPE_BEFORE)


def house_drawingml_tree(root: Any) -> int:
    """Every DrawingML font slot under *root* in the house face.

    Text properties (runs, paragraph and level defaults, end-of-paragraph
    marks) name the house face; a bulleted level or paragraph draws "•" in
    it; a numbered one keeps its numbering and takes the house face. Returns
    the number of elements changed.
    """

    latin, _ = house_faces()
    wanted = _TEXT_PROPERTIES | {"buChar", "buFont"}
    targets = [el for el in root.iter(_a("*")) if _local(el) in wanted]
    changed = 0
    for element in targets:
        name = _local(element)
        parent = element.getparent()
        if name in _TEXT_PROPERTIES:
            house_text_properties(element)
        elif parent is None:  # a bullet already replaced with its paragraph's
            continue
        elif name == "buChar":
            house_bullet(parent)
        elif parent.find(_a("buChar")) is None:
            # A numbered (or picture) bullet's font: the face only.
            element.attrib.clear()
            element.set("typeface", latin)
        else:
            continue  # its buChar rewrites both
        changed += 1
    return changed


_FONT_SCHEME_RE = re.compile(rb"<a:fontScheme\b[^>]*?(?:/>|>.*?</a:fontScheme>)", re.S)


def house_theme(blob: bytes) -> bytes:
    """A theme part whose font scheme is the house face.

    Major and minor fonts name the house face in ``latin``, ``ea`` and
    ``cs``, and ``script="Arab"`` names it too. The other script entries
    (MS Mincho for Japanese, Angsana New for Thai...) are dropped: they name
    faces Kazma does not use, and with ``ea`` and ``cs`` named a script entry
    is a fallback the application already has. The theme is the library's
    own template, with the DrawingML namespace on ``a:``; one that is not
    raises, rather than leaving the old faces in.
    """

    latin, arabic = house_faces()
    slots = (
        f"<a:latin typeface={quoteattr(latin)}/><a:ea typeface={quoteattr(latin)}/>"
        f"<a:cs typeface={quoteattr(arabic)}/><a:font script=\"Arab\" typeface={quoteattr(arabic)}/>"
    )
    scheme = (f'<a:fontScheme name="Kazma"><a:majorFont>{slots}</a:majorFont>'
              f"<a:minorFont>{slots}</a:minorFont></a:fontScheme>").encode()
    new, count = _FONT_SCHEME_RE.subn(lambda _match: scheme, blob, count=1)
    if count != 1 or b'xmlns:a="' + A_NS.encode() + b'"' not in new:
        raise ValueError("the theme has no a:fontScheme in the DrawingML namespace")
    return new


# --------------------------------------------------------------------------- #
# WordprocessingML
# --------------------------------------------------------------------------- #

_W_FACE_SLOTS = ("ascii", "hAnsi", "eastAsia", "cs")
_W_THEME_SLOTS = ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme")


def _house_rfonts(r_fonts: Any) -> None:
    """A ``w:rFonts`` naming the house face in all four slots, no theme reference."""

    latin, arabic = house_faces()
    for slot in _W_THEME_SLOTS:
        r_fonts.attrib.pop(_w(slot), None)
    for slot in _W_FACE_SLOTS:
        r_fonts.set(_w(slot), arabic if slot == "cs" else latin)


def house_word_styles(root: Any) -> int:
    """``w:docDefaults`` and every style of a styles part in the house face.

    The default run properties are what every style and run without fonts
    of its own resolves to; the template's pointed at the theme (Cambria,
    and Times New Roman for Arabic) and its heading styles at the major
    theme font. Returns the number of ``w:rFonts`` written.
    """

    defaults = root.find(_w("docDefaults"))
    if defaults is None:
        defaults = root.makeelement(_w("docDefaults"), {})
        root.insert(0, defaults)
    r_default = defaults.find(_w("rPrDefault"))
    if r_default is None:
        r_default = defaults.makeelement(_w("rPrDefault"), {})
        defaults.insert(0, r_default)
    r_pr = r_default.find(_w("rPr"))
    if r_pr is None:
        r_pr = r_default.makeelement(_w("rPr"), {})
        r_default.append(r_pr)
    if r_pr.find(_w("rFonts")) is None:
        r_pr.insert(0, r_pr.makeelement(_w("rFonts"), {}))
    written = 0
    for r_fonts in list(root.iter(_w("rFonts"))):
        _house_rfonts(r_fonts)
        written += 1
    return written


def word_faces_named(roots: list[Any]) -> set[str]:
    """Every face a ``w:rFonts`` or ``w:sym`` names under *roots*."""

    faces: set[str] = set()
    for root in roots:
        for r_fonts in root.iter(_w("rFonts")):
            for slot in _W_FACE_SLOTS:
                value = r_fonts.get(_w(slot))
                if value:
                    faces.add(value)
        for sym in root.iter(_w("sym")):
            value = sym.get(_w("font"))
            if value:
                faces.add(value)
    return faces


def house_font_table(root: Any, used: set[str]) -> None:
    """A font table that lists the faces the document names, and no other.

    Word writes the table from the faces in use; the template's listed
    Calibri, Cambria, Times New Roman, Arial, Courier, Symbol and two
    Japanese faces the document never used. A face in use without an entry
    gets one (the house face's says sans serif, variable pitch).
    """

    latin, arabic = house_faces()
    present: set[str] = set()
    for entry in list(root.findall(_w("font"))):
        name = entry.get(_w("name")) or ""
        if name not in used:
            root.remove(entry)
        else:
            present.add(name)
    for name in sorted(used - present):
        entry = root.makeelement(_w("font"), {_w("name"): name})
        root.append(entry)
        entry.append(entry.makeelement(_w("charset"), {_w("val"): "00"}))
        if name in (latin, arabic):
            entry.append(entry.makeelement(_w("family"), {_w("val"): "swiss"}))
            entry.append(entry.makeelement(_w("pitch"), {_w("val"): "variable"}))
