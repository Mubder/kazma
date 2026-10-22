"""Embed the house fonts in a generated Word file.

A DOCX names its fonts; it does not carry them. Every Word file Kazma makes
names IBM Plex Sans Arabic, and wherever that font is not installed -- a
phone, another computer, a mail or Drive preview -- Word draws another face
and the house style is gone (2026-10-22: the owner's OCR'd booklet, after
the PDF route had been given its fonts twice). A PDF embeds its fonts at
render time; a DOCX has to be given them.

Word's own mechanism: each face is stored as an obfuscated font part
(``word/fonts/fontN.odttf``, ECMA-376 Part 1, 17.8.1) -- the first 32 bytes
XOR-ed with a key derived from a GUID -- related from ``word/fontTable.xml``,
whose ``w:font`` entry names it (``w:embedRegular`` / ``w:embedBold`` with
``w:fontKey``), and ``word/settings.xml`` says ``w:embedTrueTypeFonts``.
Word, LibreOffice and Pages read them. The IBM Plex fonts are under the SIL
Open Font License, which permits embedding.

Standard library only: the file is rewritten as a zip, the XML edited with
ElementTree-safe string operations on well-known elements.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

__all__ = ["embed_fonts", "deobfuscate"]

logger = logging.getLogger(__name__)

_FONT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"
_FONT_TYPE = "application/vnd.openxmlformats-officedocument.obfuscatedFont"
_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

#: The settings that come before w:embedTrueTypeFonts in CT_Settings: it goes
#: after the last of them that the file has.
_SETTINGS_BEFORE = (
    "writeProtection", "view", "zoom", "removePersonalInformation",
    "removeDateAndTime", "doNotDisplayPageBoundaries", "displayBackgroundShape",
    "printPostScriptOverText", "printFractionalCharacterWidth", "printFormsData",
)


def _key(guid: str) -> bytes:
    """The 16-byte key of a font GUID: its hex digits, bytes in reverse."""

    return bytes.fromhex(guid.strip("{}").replace("-", ""))[::-1]


def _obfuscate(font: bytes, guid: str) -> bytes:
    """A font file as Word stores it: the first 32 bytes XOR-ed with the key."""

    key = _key(guid)
    head = bytes(font[i] ^ key[i % 16] for i in range(min(32, len(font))))
    return head + font[32:]


#: The operation is its own inverse: what reads a stored font back.
deobfuscate = _obfuscate


def _add_settings_flag(xml: str) -> str:
    if "<w:embedTrueTypeFonts" in xml:
        return xml
    flag = "<w:embedTrueTypeFonts/><w:saveSubsetFonts w:val=\"0\"/>"
    last_end = None
    for name in _SETTINGS_BEFORE:
        for match in re.finditer(rf"<w:{name}\b[^>]*?(/>|>.*?</w:{name}>)", xml, flags=re.S):
            last_end = max(last_end or 0, match.end())
    if last_end is None:
        opening = re.search(r"<w:settings\b[^>]*>", xml)
        if opening is None:
            raise ValueError("settings.xml has no w:settings element")
        last_end = opening.end()
    return xml[:last_end] + flag + xml[last_end:]


def _font_entry(xml: str, family: str, embeds: str) -> str:
    """fontTable.xml with *family*'s entry carrying *embeds*."""

    name = re.escape(family)
    pattern = re.compile(rf"<w:font\s+w:name=\"{name}\"\s*(/>|>(.*?)</w:font>)", re.S)
    match = pattern.search(xml)
    if match is None:
        entry = f"<w:font w:name={quoteattr(family)}><w:charset w:val=\"00\"/>{embeds}</w:font>"
        return xml.replace("</w:fonts>", entry + "</w:fonts>", 1)
    inner = re.sub(r"<w:embed(Regular|Bold|Italic|BoldItalic)\b[^>]*/>", "", match.group(2) or "")
    return xml[: match.start()] + f"<w:font w:name={quoteattr(family)}>{inner}{embeds}</w:font>" + xml[match.end():]


def embed_fonts(docx: Path, faces: dict[str, dict[str, Path]]) -> int:
    """Embed *faces* (family -> {"regular"/"bold": file}) into *docx*.

    Returns the number of font files embedded. The file is rewritten in
    place; a failure leaves it as it was.
    """

    docx = Path(docx)
    with zipfile.ZipFile(docx) as source:
        names = source.namelist()
        parts = {name: source.read(name) for name in names}
    if "word/fontTable.xml" not in parts or "word/settings.xml" not in parts:
        raise ValueError("the document has no font table or settings part")

    font_table = parts["word/fontTable.xml"].decode("utf-8")
    if f'xmlns:r="{_R_NS}"' not in font_table:
        font_table = re.sub(r"<w:fonts\b", f'<w:fonts xmlns:r="{_R_NS}"', font_table, count=1)
    rels_name = "word/_rels/fontTable.xml.rels"
    rels = parts.get(rels_name, f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                f'<Relationships xmlns="{_RELS_NS}"></Relationships>'.encode()).decode("utf-8")
    added: dict[str, bytes] = {}
    count = 0
    for family, files in faces.items():
        embeds = ""
        for style, element in (("regular", "embedRegular"), ("bold", "embedBold")):
            path = files.get(style)
            if path is None or not Path(path).is_file():
                continue
            count += 1
            guid = "{" + str(uuid.uuid4()).upper() + "}"
            rel_id = f"rIdKazmaFont{count}"
            target = f"fonts/kazma-font{count}.odttf"
            added[f"word/{target}"] = _obfuscate(Path(path).read_bytes(), guid)
            rels = rels.replace(
                "</Relationships>",
                f'<Relationship Id="{rel_id}" Type="{_FONT_REL}" Target="{target}"/></Relationships>', 1)
            embeds += f'<w:{element} r:id="{rel_id}" w:fontKey="{guid}"/>'
        if embeds:
            font_table = _font_entry(font_table, family, embeds)
    if not count:
        return 0

    content_types = parts["[Content_Types].xml"].decode("utf-8")
    if 'Extension="odttf"' not in content_types:
        content_types = content_types.replace(
            "</Types>", f'<Default Extension="odttf" ContentType="{_FONT_TYPE}"/></Types>', 1)
    parts["[Content_Types].xml"] = content_types.encode("utf-8")
    parts["word/fontTable.xml"] = font_table.encode("utf-8")
    parts["word/settings.xml"] = _add_settings_flag(parts["word/settings.xml"].decode("utf-8")).encode("utf-8")
    parts[rels_name] = rels.encode("utf-8")
    parts.update(added)

    with tempfile.NamedTemporaryFile(dir=docx.parent, suffix=".docx", delete=False) as handle:
        temp = Path(handle.name)
    moved = False
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name in names + [n for n in parts if n not in names]:
                target.writestr(name, parts[name])
        shutil.move(str(temp), str(docx))
        moved = True
    finally:
        if not moved:
            temp.unlink(missing_ok=True)
    return count
