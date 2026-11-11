"""Embed the house fonts in a generated PowerPoint file.

A deck names its fonts; opened where IBM Plex Sans Arabic is not installed,
PowerPoint draws another face (the same reason DOCX embeds them,
``engines/docx_fonts.py``). PowerPoint's own mechanism (ECMA-376 Part 1,
19.2.1.9-10): ``p:embeddedFontLst`` in ``presentation.xml`` names the
family and points, per style, at a font part (``ppt/fonts/fontN.fntdata``,
content type ``application/x-fontdata``) holding the face as Embedded
OpenType -- the format Windows' font-embedding library (T2Embed) reads.
EOT is a header (the face's names, OS/2 fields and the head checksum) over
the TrueType data; Kazma writes version 0x00020001 with no compression and
no obfuscation, which T2Embed loads as it is. Checked with PowerPoint
(Microsoft 365, 2026-11-11): a renamed copy of the face that no machine had
installed was listed as embedded and drew the deck's text in the PDF export.
IBM Plex Sans Arabic is under the SIL Open Font License (fsType 0), which
permits embedding.

Standard library only for the format; python-pptx for the package.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any

__all__ = ["embed_fonts"]

logger = logging.getLogger(__name__)

_EOT_VERSION = 0x00020001
_EOT_MAGIC = 0x504C
_DEFAULT_CHARSET = 1
_FONT_DATA_TYPE = "application/x-fontdata"

#: What follows p:embeddedFontLst in CT_Presentation (19.2.1.26).
_AFTER_FONT_LIST = (
    "custShowLst", "photoAlbum", "custDataLst", "kinsoku", "defaultTextStyle",
    "modifyVerifier", "extLst",
)


def _tables(data: bytes) -> dict[bytes, tuple[int, int]]:
    count = struct.unpack(">H", data[4:6])[0]
    tables: dict[bytes, tuple[int, int]] = {}
    for index in range(count):
        tag, _checksum, offset, length = struct.unpack(">4sIII", data[12 + 16 * index: 28 + 16 * index])
        tables[tag] = (offset, length)
    return tables


def _name(data: bytes, tables: dict[bytes, tuple[int, int]], name_id: int) -> str:
    """A Windows (UTF-16) name record, US English when there is one."""

    offset, _ = tables[b"name"]
    _format, count, strings = struct.unpack(">HHH", data[offset: offset + 6])
    fallback = ""
    for index in range(count):
        platform, encoding, language, nid, length, start = struct.unpack(
            ">HHHHHH", data[offset + 6 + 12 * index: offset + 18 + 12 * index])
        if nid != name_id or platform != 3 or encoding not in (0, 1, 10):
            continue
        text = data[offset + strings + start: offset + strings + start + length].decode("utf-16-be")
        if language == 0x409:
            return text
        fallback = fallback or text
    return fallback


def _font_facts(data: bytes) -> dict[str, Any]:
    """The facts an EOT header and a ``p:font`` entry carry, read from a TrueType file."""

    tables = _tables(data)
    if b"OS/2" not in tables or b"head" not in tables or b"name" not in tables:
        raise ValueError("not a TrueType font with OS/2, head and name tables")
    os2, _ = tables[b"OS/2"]
    head, _ = tables[b"head"]
    weight = struct.unpack(">H", data[os2 + 4: os2 + 6])[0]
    fs_type = struct.unpack(">H", data[os2 + 8: os2 + 10])[0]
    return {
        "weight": weight,
        "fs_type": fs_type,
        "panose": data[os2 + 32: os2 + 42],
        "unicode_ranges": struct.unpack(">IIII", data[os2 + 42: os2 + 58]),
        "italic": bool(struct.unpack(">H", data[os2 + 62: os2 + 64])[0] & 1),
        "code_pages": struct.unpack(">II", data[os2 + 78: os2 + 86]),
        "checksum_adjustment": struct.unpack(">I", data[head + 8: head + 12])[0],
        "family": _name(data, tables, 1),
        "style": _name(data, tables, 2),
        "version": _name(data, tables, 5),
        "full": _name(data, tables, 4),
    }


def _eot_from_ttf(data: bytes) -> bytes:
    """A TrueType font as uncompressed Embedded OpenType (version 0x00020001)."""

    facts = _font_facts(data)
    if facts["fs_type"] & 0x0002:
        raise ValueError("the font's licence forbids embedding (fsType restricted)")

    def text(value: str) -> bytes:
        raw = value.encode("utf-16-le")
        return struct.pack("<HH", 0, len(raw)) + raw  # padding, size, name

    body = b"".join((
        facts["panose"],
        bytes((_DEFAULT_CHARSET, 1 if facts["italic"] else 0)),
        struct.pack("<IHH", facts["weight"], facts["fs_type"], _EOT_MAGIC),
        struct.pack("<IIII", *facts["unicode_ranges"]),
        struct.pack("<II", *facts["code_pages"]),
        struct.pack("<IIIII", facts["checksum_adjustment"], 0, 0, 0, 0),
        text(facts["family"]),
        text(facts["style"]),
        text(facts["version"]),
        text(facts["full"]),
        struct.pack("<HH", 0, 0),  # padding, an empty root string
    ))
    header_size = 16 + len(body)
    return struct.pack("<IIII", header_size + len(data), len(data), _EOT_VERSION, 0) + body + data


def _ttf_from_eot(eot: bytes) -> tuple[str, bytes]:
    """``(family, TrueType data)`` of an uncompressed EOT (what Kazma writes)."""

    size, data_size, version, flags = struct.unpack("<IIII", eot[:16])
    if size != len(eot) or flags != 0 or version not in (0x00010000, 0x00020001, 0x00020002):
        raise ValueError("not an uncompressed Embedded OpenType font")
    if struct.unpack("<H", eot[34:36])[0] != _EOT_MAGIC:
        raise ValueError("bad Embedded OpenType magic number")
    family_size = struct.unpack("<H", eot[82:84])[0]
    family = eot[84: 84 + family_size].decode("utf-16-le")
    return family, eot[len(eot) - data_size:]


def embed_fonts(presentation: Any, faces: dict[str, dict[str, Path]]) -> int:
    """Embed *faces* (family -> {"regular"/"bold": file}) in a python-pptx deck.

    Call before saving. Every file is read and converted first, so a font
    that cannot be embedded raises before the deck is changed. Returns the
    number of font files embedded.
    """

    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.opc.package import Part
    from pptx.opc.packuri import PackURI
    from pptx.oxml.ns import qn

    prepared: list[tuple[str, str, bytes, bytes]] = []  # family, style, ttf, eot
    for family, files in faces.items():
        for style in ("regular", "bold"):
            path = files.get(style)
            if path is not None and Path(path).is_file():
                data = Path(path).read_bytes()
                prepared.append((family, style, data, _eot_from_ttf(data)))
    if not prepared:
        return 0

    part = presentation.part
    root = part._element
    font_list = root.find(qn("p:embeddedFontLst"))
    if font_list is None:
        font_list = root.makeelement(qn("p:embeddedFontLst"), {})
        anchor = next((root.find(qn(f"p:{name}")) for name in _AFTER_FONT_LIST
                       if root.find(qn(f"p:{name}")) is not None), None)
        if anchor is None:
            root.append(font_list)
        else:
            anchor.addprevious(font_list)
    taken = {str(p.partname) for p in part.package.iter_parts()}
    entries: dict[str, Any] = {}
    index = 0
    for family, style, data, eot in prepared:
        entry = entries.get(family)
        if entry is None:
            entry = font_list.makeelement(qn("p:embeddedFont"), {})
            font_list.append(entry)
            entry.append(entry.makeelement(qn("p:font"), {"typeface": family}))
            entries[family] = entry
        if style == "regular":
            face = entry.find(qn("p:font"))
            face.set("panose", _font_facts(data)["panose"].hex().upper())
            face.set("pitchFamily", "34")  # variable pitch, sans serif (FF_SWISS)
            face.set("charset", "0")
        index += 1
        while f"/ppt/fonts/font{index}.fntdata" in taken:
            index += 1
        name = f"/ppt/fonts/font{index}.fntdata"
        taken.add(name)
        rel_id = part.relate_to(Part(PackURI(name), _FONT_DATA_TYPE, part.package, eot), RT.FONT)
        entry.append(entry.makeelement(qn(f"p:{style}"), {qn("r:id"): rel_id}))
    root.set("embedTrueTypeFonts", "1")
    root.set("saveSubsetFonts", "0")
    return len(prepared)
