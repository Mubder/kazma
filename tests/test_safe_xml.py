"""XML fetched from the web is parsed without DTDs or entities (audit 2026-09-22)."""

from __future__ import annotations

import time

import pytest
from kazma_core.security.safe_xml import MAX_XML_BYTES, UnsafeXMLError, parse_untrusted_xml
from kazma_core.stores.knowledge_ingest import _extract_urls_from_sitemap

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc></url>
  <url><loc>https://example.com/b</loc></url>
</urlset>"""

BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<urlset><url><loc>&lol3;</loc></url></urlset>"""

EXTERNAL_ENTITY = """<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<urlset><url><loc>&xxe;</loc></url></urlset>"""


def test_a_real_sitemap_still_parses():
    assert _extract_urls_from_sitemap(SITEMAP) == ["https://example.com/a", "https://example.com/b"]


@pytest.mark.parametrize("hostile", [BILLION_LAUGHS, EXTERNAL_ENTITY])
def test_dtds_and_entities_are_refused(hostile: str):
    with pytest.raises(UnsafeXMLError):
        parse_untrusted_xml(hostile)
    start = time.monotonic()
    assert _extract_urls_from_sitemap(hostile) == []
    assert time.monotonic() - start < 1.0


def test_declaration_keyword_case_and_spacing_do_not_slip_through():
    with pytest.raises(UnsafeXMLError):
        parse_untrusted_xml("<?xml version='1.0'?><! doctype x [<!entity a 'b'>]><x>&a;</x>")


def test_oversize_documents_are_refused():
    with pytest.raises(UnsafeXMLError):
        parse_untrusted_xml(b"<x>" + b"a" * (MAX_XML_BYTES + 1) + b"</x>")
