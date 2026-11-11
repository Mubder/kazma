"""Every font slot of every document Kazma generates names the house face.

Live 2026-11-10: the owner opened a deck that "has the font set to the Kazma
font but displays it as Arial". A DrawingML run has three slots and Arabic
is drawn from ``a:cs``; the PPTX engine wrote ``a:latin`` alone, so Arabic
fell through to the theme, whose complex-script slot was empty and whose
``script="Arab"`` entry named Times New Roman (major) and Arial (minor). The
same template theme -- Calibri -- sat under every Office format: the caret at
the end of a line, a new text box or cell, a heading style, the notes page,
the master's bullets (Arial) all left the house face, and nothing carried it
on a machine without it.

This gate generates English, Arabic and bilingual documents in every format
through Kazma's real engines (``renderer_worker._generate_*``), opens every
XML part of the Office files and resolves every face a slot can draw -- runs,
paragraph and list defaults, end-of-paragraph marks, bullets, styles, the
document defaults, the font table, masters, layouts, notes, themes (their
``latin``/``ea``/``cs`` and script entries), theme references (``+mn-cs``,
``minorBidi``, ``scheme="minor"``, ``fontRef``) -- and fails on any face but
the house face and the declared exception. PDF and HTML are read for the
faces they embed. Negative control: the engines with the house steps taken
out (what they were before 2026-11-11) fail it, slot by slot.
"""

from __future__ import annotations

import base64
import re
import zipfile
from pathlib import Path

import pytest
from lxml import etree

pytest.importorskip("pptx")
pytest.importorskip("openpyxl")
pytest.importorskip("docx")

from kazma_core.documents.arabic import has_rtl, script_runs  # noqa: E402
from kazma_core.documents.style_theme import THEME  # noqa: E402

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

HOUSE = {str(THEME["font_latin"]), str(THEME["font_arabic"])}
BULLET = "•"

EN = "Kazma reads every document in two languages."
AR = "منظومة كاظمة تقرأ كل مستند بلغتين."
MIX = "Kazma كاظمة reads both اللغتين together."


def _declared() -> dict[str, str]:
    """Faces a generated document may name besides the house face, and why."""

    from kazma_core.documents.fonts import direction_mark_family

    mark = direction_mark_family()
    return {mark: "DOCX direction marks (§19 J: the house face has no glyph for them)"} if mark else {}


# --------------------------------------------------------------------------- #
# the documents
# --------------------------------------------------------------------------- #

def _document(lang: str | None, title: str, body: str) -> dict:
    return {
        "title": title, "lang": lang,
        "sections": [
            {"heading": title, "body": (
                f"{body}\n\n- {body}\n- {EN}\n- {AR}\n\n> {AR}\n\n"
                f"| {EN} | {AR} |\n|---|---|\n| one | واحد |\n\n{MIX}")},
            {"heading": f"{title} 2", "body": f"{AR}\n\n{EN}"},
        ],
    }


def _deck(lang: str | None, title: str, body: str) -> dict:
    return {
        "title": title, "subtitle": body, "lang": lang, "notes": [body, EN, AR],
        "slides": [
            {"heading": title, "bullets": [body, EN, AR, MIX], "notes": body},
            {"heading": MIX, "body": f"{EN}\n{AR}"},
        ],
    }


def _workbook(lang: str | None, title: str, body: str) -> dict:
    return {
        "title": title, "lang": lang,
        "sheets": [{"name": "S", "title": title,
                    "rows": [[EN, AR], [body, 3], [MIX, 5], [AR, 7]],
                    "chart": {"type": "bar", "category_col": 1, "value_col": 2, "title": body}}],
    }


LANGS = (("en", "en", "Report", EN), ("ar", "ar", "تقرير", AR), ("mixed", None, "Kazma كاظمة", MIX))


def _generate(directory: Path, fmt: str) -> list[Path]:
    from kazma_core.documents import renderer_worker as rw

    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for name, lang, title, body in LANGS:
        path = directory / f"{name}.{fmt}"
        if fmt == "docx":
            rw._generate_docx(path, _document(lang, title, body))
        elif fmt == "pptx":
            rw._generate_pptx(path, _deck(lang, title, body))
        elif fmt == "xlsx":
            rw._generate_xlsx(path, _workbook(lang, title, body))
        elif fmt == "pdf":
            rw._generate_pdf(path, _document(lang, title, body), [])
        elif fmt == "html":
            rw._generate_html(path, _document(lang, title, body))
        out.append(path)
    return out


# --------------------------------------------------------------------------- #
# what an Office file names
# --------------------------------------------------------------------------- #

def _themes(parts: dict[str, etree._Element]) -> list[dict[str, dict[str, str]]]:
    themes = []
    for name, root in parts.items():
        scheme = root.find(f".//{{{A}}}fontScheme")
        if scheme is None or "/theme/" not in name:
            continue
        theme: dict[str, dict[str, str]] = {}
        for which in ("major", "minor"):
            collection = scheme.find(f"{{{A}}}{which}Font")
            slots = {}
            for slot in ("latin", "ea", "cs"):
                element = collection.find(f"{{{A}}}{slot}") if collection is not None else None
                slots[slot] = element.get("typeface", "") if element is not None else ""
            theme[which] = slots
        themes.append(theme)
    return themes


def _resolve(themes: list[dict[str, dict[str, str]]], which: str, slot: str) -> set[str]:
    return {theme[which][slot] for theme in themes} or {""}


_DML_REF = re.compile(r"^\+(mj|mn)-(lt|ea|cs)$")
_W_REF = {
    "majorHAnsi": ("major", "latin"), "majorAscii": ("major", "latin"),
    "minorHAnsi": ("minor", "latin"), "minorAscii": ("minor", "latin"),
    "majorEastAsia": ("major", "ea"), "minorEastAsia": ("minor", "ea"),
    "majorBidi": ("major", "cs"), "minorBidi": ("minor", "cs"),
}


def office_faces(path: Path) -> list[tuple[str, str, str]]:
    """``(part, slot, face)`` for every face any slot of an Office file can draw.

    Theme references are resolved through the package's themes; an empty
    slot is the face ``""`` (the application's default).
    """

    with zipfile.ZipFile(path) as package:
        parts = {name: etree.fromstring(package.read(name))
                 for name in package.namelist() if name.endswith(".xml")}
    themes = _themes(parts)
    found: list[tuple[str, str, str]] = []

    def add(part: str, slot: str, value: str | None) -> None:
        value = value or ""
        match = _DML_REF.match(value)
        if match:
            which = "major" if match.group(1) == "mj" else "minor"
            slot_name = {"lt": "latin", "ea": "ea", "cs": "cs"}[match.group(2)]
            for face in _resolve(themes, which, slot_name):
                found.append((part, f"{slot} {value}", face))
            return
        found.append((part, slot, value))

    for name, root in parts.items():
        for element in root.iter():
            if not isinstance(element.tag, str):
                continue
            qname = etree.QName(element)
            ns, local = qname.namespace, qname.localname
            if ns == A and local in ("latin", "ea", "cs", "sym", "buFont"):
                add(name, f"a:{local}", element.get("typeface"))
            elif ns == A and local == "font" and element.get("script"):
                add(name, f"a:font[{element.get('script')}]", element.get("typeface"))
            elif ns == A and local == "fontRef":
                for slot in ("latin", "ea", "cs"):
                    for face in _resolve(themes, element.get("idx", "minor"), slot):
                        found.append((name, f"a:fontRef[{element.get('idx')}] {slot}", face))
            elif ns == P and local == "font":
                add(name, "p:font", element.get("typeface"))
            elif ns == W and local == "rFonts":
                for attr, value in element.attrib.items():
                    slot = etree.QName(attr).localname
                    if slot.endswith("Theme") or slot == "cstheme":
                        if value not in _W_REF:
                            found.append((name, f"w:rFonts {slot}", value))
                            continue
                        for face in _resolve(themes, *_W_REF[value]):
                            found.append((name, f"w:rFonts {slot}={value}", face))
                    elif slot in ("ascii", "hAnsi", "eastAsia", "cs"):
                        add(name, f"w:rFonts {slot}", value)
            elif ns == W and local == "font":
                add(name, "w:font", element.get(f"{{{W}}}name"))
            elif ns == W and local == "sym":
                add(name, "w:sym", element.get(f"{{{W}}}font"))
            elif ns == S and local in ("font", "rPr"):
                face = element.find(f"{{{S}}}name") if local == "font" else element.find(f"{{{S}}}rFont")
                scheme = element.find(f"{{{S}}}scheme")
                if face is not None:
                    add(name, f"x:{local} name", face.get("val"))
                if scheme is not None and scheme.get("val") in ("major", "minor"):
                    for face_name in _resolve(themes, scheme.get("val"), "latin"):
                        found.append((name, f"x:{local} scheme", face_name))
    for theme in themes:
        for which, slots in theme.items():
            for slot, face in slots.items():
                found.append(("theme", f"{which}Font {slot}", face))
    return found


def office_problems(path: Path) -> list[str]:
    """Every slot naming something other than the house face (or the declared)."""

    allowed = HOUSE | set(_declared())
    problems = [f"{part}: {slot} = {face!r}"
                for part, slot, face in office_faces(path) if face not in allowed]
    if path.suffix == ".pptx":
        problems += _pptx_text_problems(path)
    return problems


def _pptx_text_problems(path: Path) -> list[str]:
    """Each slide and notes run names the house face itself, each paragraph
    has its end mark, and each bullet is "•" in the house face."""

    problems = []
    with zipfile.ZipFile(path) as package:
        names = [n for n in package.namelist()
                 if re.fullmatch(r"ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml", n)]
        for name in names:
            root = etree.fromstring(package.read(name))
            for run in root.iter(f"{{{A}}}r"):
                props = run.find(f"{{{A}}}rPr")
                faces = {slot: (props.find(f"{{{A}}}{slot}").get("typeface")
                                if props is not None and props.find(f"{{{A}}}{slot}") is not None else None)
                         for slot in ("latin", "ea", "cs")}
                if any(face not in HOUSE for face in faces.values()):
                    problems.append(f"{name}: run {run.findtext(f'{{{A}}}t')!r} names {faces}")
            for paragraph in root.iter(f"{{{A}}}p"):
                if paragraph.find(f"{{{A}}}r") is not None and paragraph.find(f"{{{A}}}endParaRPr") is None:
                    problems.append(f"{name}: a paragraph with no end mark (new text takes the default)")
                text = "".join(t.text or "" for t in paragraph.iter(f"{{{A}}}t"))
                if text.lstrip().startswith(BULLET):
                    problems.append(f"{name}: a bullet typed into the text: {text[:30]!r}")
            for run in root.iter(f"{{{A}}}r"):
                # PowerPoint lays a run out by its language: one run holding
                # both scripts misplaced spaces or the full stop (measured).
                text = run.findtext(f"{{{A}}}t") or ""
                if has_rtl(text) and re.search(r"[A-Za-z]", text):
                    problems.append(f"{name}: one run holds both scripts: {text[:30]!r}")
            for char in root.iter(f"{{{A}}}buChar"):
                font = char.getparent().find(f"{{{A}}}buFont")
                if char.get("char") != BULLET or font is None or font.get("typeface") not in HOUSE:
                    problems.append(f"{name}: bullet {char.get('char')!r} in {font.get('typeface') if font is not None else None}")
    return problems


def _pdf_faces(path: Path) -> set[str]:
    import pymupdf

    with pymupdf.open(str(path)) as pdf:
        return {font[3].split("+")[-1] for page in pdf for font in page.get_fonts(full=True)}


def _house_postscript(name: str) -> bool:
    return any(name.split(",")[0].split("-")[0] == face.replace(" ", "") for face in HOUSE)


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fmt", ["docx", "pptx", "xlsx"])
def test_every_slot_of_an_office_file_names_the_house_face(tmp_path, fmt):
    for path in _generate(tmp_path, fmt):
        problems = office_problems(path)
        assert not problems, f"{path.name}:\n" + "\n".join(problems[:40])
        assert any(face in HOUSE for _, _, face in office_faces(path))


def test_a_deck_carries_the_house_face(tmp_path):
    """The face travels with the deck: ``p:embeddedFontLst`` names it, and each
    font part is Embedded OpenType holding the bundled regular and bold."""

    from kazma_core.documents.engines.pptx_fonts import _ttf_from_eot
    from kazma_core.documents.fonts import _bundled_pair

    pair = _bundled_pair(arabic=True)
    if pair is None or "plex" not in pair[0].name.lower():
        pytest.skip("the bundled house fonts are not installed with this build")
    deck = _generate(tmp_path, "pptx")[1]
    with zipfile.ZipFile(deck) as package:
        presentation = etree.fromstring(package.read("ppt/presentation.xml"))
        rels = etree.fromstring(package.read("ppt/_rels/presentation.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        assert presentation.get("embedTrueTypeFonts") == "1"
        entries = presentation.findall(f"{{{P}}}embeddedFontLst/{{{P}}}embeddedFont")
        assert [e.find(f"{{{P}}}font").get("typeface") for e in entries] == [str(THEME["font_arabic"])]
        carried = {}
        for style in ("regular", "bold"):
            rel_id = entries[0].find(f"{{{P}}}{style}").get(f"{{{R}}}id")
            family, data = _ttf_from_eot(package.read("ppt/" + targets[rel_id]))
            assert family == str(THEME["font_arabic"])
            carried[style] = data
        types = package.read("[Content_Types].xml").decode()
    assert carried == {"regular": pair[0].read_bytes(), "bold": pair[1].read_bytes()}
    assert 'Extension="fntdata" ContentType="application/x-fontdata"' in types


def test_a_new_cell_takes_the_house_face(tmp_path):
    """Excel's Normal style is the stylesheet's first font: what a cell a person
    adds is drawn in."""

    book = _generate(tmp_path, "xlsx")[1]
    with zipfile.ZipFile(book) as package:
        styles = etree.fromstring(package.read("xl/styles.xml"))
    normal = next(s for s in styles.iter(f"{{{S}}}cellStyle") if s.get("name") == "Normal")
    xf = styles.find(f"{{{S}}}cellStyleXfs")[int(normal.get("xfId"))]
    font = styles.find(f"{{{S}}}fonts")[int(xf.get("fontId"))]
    assert font.find(f"{{{S}}}name").get("val") in HOUSE
    assert font.find(f"{{{S}}}scheme") is None


def test_pdf_and_html_carry_only_the_house_face(tmp_path):
    from kazma_core.documents.fonts import resolve_fonts

    for pdf in _generate(tmp_path / "pdf", "pdf"):
        faces = _pdf_faces(pdf)
        assert faces and all(_house_postscript(face) for face in faces), (pdf.name, faces)
    choice = resolve_fonts(arabic=True)
    for html in _generate(tmp_path / "html", "html"):
        text = html.read_text(encoding="utf-8")
        body = re.search(r"\bbody\s*\{[^}]*?font-family:\s*([^;]+);", text)
        first = body.group(1).split(",")[0].strip().strip("'\"")
        if choice.source == "bundled" and choice.regular is not None:
            face = re.search(rf"font-family:\s*'{re.escape(first)}';\s*font-weight:\s*400;.*?"
                             r"base64,([A-Za-z0-9+/=]+)\)", text, re.S)
            assert face, (html.name, first)
            assert base64.b64decode(face.group(1)) == choice.regular.read_bytes()
        else:
            assert first in HOUSE, (html.name, first)


def _soffice() -> str | None:
    try:
        import pymupdf  # noqa: F401
        from kazma_core.documents.binaries import find_soffice

        return find_soffice()
    except Exception:
        return None


def _faces_on_the_page(tmp_path: Path, fmt: str) -> dict[str, set[str]]:
    from kazma_core.documents.binaries import run_soffice_cli
    from kazma_core.documents.fonts import give_soffice_the_bundled_fonts

    profile = tmp_path / "profile"
    give_soffice_the_bundled_fonts(profile)
    faces = {}
    for path in _generate(tmp_path / "in", fmt):
        run_soffice_cli(("--headless", "--nologo", "--norestore",
                         f"-env:UserInstallation={profile.as_uri()}",
                         "--convert-to", "pdf", "--outdir", str(tmp_path), str(path)),
                        timeout=180, cwd=tmp_path)
        faces[path.name] = _pdf_faces(tmp_path / f"{path.stem}.pdf")
    return faces


def _opens(tmp_path: Path, fmt: str) -> bool:
    """Whether this LibreOffice opens *fmt* at all. CI installs Writer only,
    which cannot open a deck or a workbook (Impress and Calc are packages of
    their own), and there no PDF is written. Probed on a plain file made with
    python-pptx or openpyxl, never on Kazma's output, so a file of Kazma's
    that LibreOffice refuses still fails the test. Tried twice: a fresh
    profile's first conversion can write nothing (seen in WSL, 2026-11-11)."""
    from kazma_core.documents.binaries import run_soffice_cli

    folder = tmp_path / "probe"
    folder.mkdir()
    plain = folder / f"plain.{fmt}"
    if fmt == "pptx":
        from pptx import Presentation

        deck = Presentation()
        deck.slides.add_slide(deck.slide_layouts[1]).shapes.title.text = "Plain"
        deck.save(plain)
    else:
        from openpyxl import Workbook

        book = Workbook()
        book.active["A1"] = "Plain"
        book.save(plain)
    for _ in range(2):
        run_soffice_cli(("--headless", "--nologo", "--norestore",
                         f"-env:UserInstallation={(folder / 'profile').as_uri()}",
                         "--convert-to", "pdf", "--outdir", str(folder), str(plain)),
                        timeout=180, cwd=folder)
        if (folder / "plain.pdf").exists():
            return True
    return False


@pytest.mark.skipif(not _soffice(), reason="needs LibreOffice and PyMuPDF")
@pytest.mark.parametrize("fmt", ["pptx", "xlsx"])
def test_on_the_page_only_the_house_face_is_drawn(tmp_path, monkeypatch, fmt):
    """LibreOffice, given the bundled faces as the worker gives them, draws a
    generated deck and workbook in the house face only; the old engines' were
    drawn partly in Arial or Calibri (Arabic, chart text)."""

    if not _opens(tmp_path, fmt):
        pytest.skip(f"this LibreOffice cannot open .{fmt} (Impress or Calc is not installed)")
    for name, faces in _faces_on_the_page(tmp_path / "new", fmt).items():
        assert faces and all(_house_postscript(face) for face in faces), (name, faces)
    _old_engines(monkeypatch)
    old = _faces_on_the_page(tmp_path / "old", fmt)
    assert any(not _house_postscript(face) for faces in old.values() for face in faces), old


def test_a_bilingual_line_is_one_run_per_script():
    """PowerPoint lays a run out by its language (measured 2026-11-11): one run
    holding both scripts drew its spaces on the wrong side of the Arabic words
    (``en-US``) or moved the full stop (``ar-SA``). A neutral takes the script
    on both sides when they agree, else the paragraph's."""

    line = "Kazma كاظمة reads اللغتين together 2026."
    assert script_runs(line, rtl=False) == [
        ("Kazma ", False), ("كاظمة", True), (" reads ", False), ("اللغتين", True), (" together 2026.", False)]
    assert script_runs("منظومة Kazma تقرأ المستند in English 2026.", rtl=True) == [
        ("منظومة ", True), ("Kazma", False), (" تقرأ المستند ", True), ("in English", False), (" 2026.", True)]
    assert script_runs("", rtl=True) == []
    assert script_runs("2026", rtl=True) == [("2026", True)]


# --------------------------------------------------------------------------- #
# negative controls
# --------------------------------------------------------------------------- #

def _old_engines(monkeypatch) -> None:
    """The engines as they were: runs named in ``a:latin`` only, the template
    themes, master, defaults and styles untouched, nothing embedded."""

    from kazma_core.documents.engines import office_fonts
    from kazma_core.documents.engines.docx import DocxEngine
    from kazma_core.documents.engines.pptx import PptxEngine
    from kazma_core.documents.engines.xlsx import XlsxEngine

    def latin_only(props) -> None:  # python-pptx's ``run.font.name``
        for child in list(props):
            if isinstance(child.tag, str) and etree.QName(child).localname in ("latin", "ea", "cs"):
                props.remove(child)
        etree.SubElement(props, f"{{{A}}}latin").set("typeface", str(THEME["font_latin"]))

    monkeypatch.setattr(office_fonts, "house_text_properties", latin_only)
    monkeypatch.setattr(PptxEngine, "_house_package", staticmethod(lambda presentation: None))
    monkeypatch.setattr(PptxEngine, "_embed_house_fonts", lambda self, presentation: None)
    monkeypatch.setattr(XlsxEngine, "_house_workbook", staticmethod(lambda workbook: None))
    monkeypatch.setattr(DocxEngine, "_house_package", staticmethod(lambda document: None))


@pytest.mark.parametrize("fmt", ["docx", "pptx", "xlsx"])
def test_negative_control_the_old_engines_fail_the_gate(tmp_path, monkeypatch, fmt):
    _old_engines(monkeypatch)
    for path in _generate(tmp_path, fmt):
        problems = "\n".join(office_problems(path))
        assert problems, path.name
        # The template themes: Calibri (Cambria in Word's) with no
        # complex-script face, so Arabic took the script table's.
        for slot in ("theme: minorFont latin = '", "theme: minorFont cs = ''"):
            assert slot in problems, (path.name, slot)
        if fmt == "pptx":
            assert "a:buFont = 'Arial'" in problems  # the master's bullets
            assert "a:font[Arab] = 'Times New Roman'" in problems
            assert "names {'latin': 'IBM Plex Sans Arabic', 'ea': None, 'cs': None}" in problems
        if fmt == "xlsx":
            assert "x:font name = 'Calibri'" in problems  # what a new cell takes
        if fmt == "docx":
            assert "w:font = 'Times New Roman'" in problems
            assert "w:rFonts hAnsi = 'Courier'" in problems


def test_negative_control_a_planted_face_is_found(tmp_path):
    """The owner's deck named Segoe UI in its runs; a part naming any face but
    the house face fails, wherever it is."""

    deck = _generate(tmp_path, "pptx")[2]  # the bilingual one
    planted = tmp_path / "planted.pptx"
    with zipfile.ZipFile(deck) as source, zipfile.ZipFile(planted, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "ppt/slides/slide2.xml":
                data = data.replace(b'<a:cs typeface="IBM Plex Sans Arabic"/>', b'<a:cs typeface="Segoe UI"/>', 1)
                # The old engine's paragraph.text: one run for both scripts.
                data = data.replace(b"<a:t>Kazma </a:t>", "<a:t>Kazma كاظمة</a:t>".encode(), 1)
            target.writestr(item, data)
    problems = office_problems(planted)
    assert any("'Segoe UI'" in problem for problem in problems)
    assert any("one run holds both scripts" in problem for problem in problems)
