"""Parse XML that came from somewhere we do not control.

Entity-expansion ("billion laughs", quadratic blowup) and external-entity
(XXE) attacks both live in the document type declaration. Nothing Kazma
fetches — sitemaps today — needs a DTD, so a document that declares one is
refused before the parser sees it. That is the whole of defusedxml's
protection for our use, without taking a dependency for one call site.

``tests/test_static_gates.py`` keeps the stdlib XML parsers out of every other
product module (audit 2026-09-22).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

__all__ = ["UnsafeXMLError", "parse_untrusted_xml"]

#: Anything larger is not a sitemap we want to hold in memory.
MAX_XML_BYTES = 10 * 1024 * 1024

_DECLARATION_RE = re.compile(r"<!\s*(DOCTYPE|ENTITY|ELEMENT|ATTLIST)\b", re.IGNORECASE)


class UnsafeXMLError(ValueError):
    """The document declares a DTD or entities, or is too large."""


def parse_untrusted_xml(text: str | bytes) -> ET.Element:
    """Parse *text* after refusing DTDs, entity declarations and oversize input.

    Raises :class:`UnsafeXMLError` for a refused document and
    ``xml.etree.ElementTree.ParseError`` for malformed XML.
    """
    raw = text.encode("utf-8", "replace") if isinstance(text, str) else text
    if len(raw) > MAX_XML_BYTES:
        raise UnsafeXMLError(f"XML document exceeds {MAX_XML_BYTES} bytes")
    head = raw.decode("utf-8", "replace")
    if _DECLARATION_RE.search(head):
        raise UnsafeXMLError("XML with a DTD or entity declarations is refused")
    return ET.fromstring(raw)  # the one sanctioned call site
