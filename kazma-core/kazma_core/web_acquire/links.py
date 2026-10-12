"""Bounded links from the exact document used for content acquisition."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


def resolve_document_url(base: str, href: str) -> str:
    """Resolve a document reference without erasing an explicit empty query."""
    literal = href.partition('#')[0]
    resolved = urljoin(base, href).partition('#')[0]
    if literal.endswith('?') or not literal and base.partition('#')[0].endswith('?'):
        resolved = resolved.partition('?')[0] + '?'
    return resolved


class _LinkLimit(Exception):
    pass


class _Links(HTMLParser):
    def __init__(self, url: str, limit: int):
        super().__init__(convert_charrefs=True)
        self.base = url
        self.base_seen = False
        self.limit = limit
        self.links: dict[str, None] = {}

    def handle_starttag(self, tag, attrs):
        href = next((value for name, value in attrs if name == "href"), None)
        if not href:
            return
        if tag == "base" and not self.base_seen:
            self.base_seen = True
            try:
                base = resolve_document_url(self.base, href)
                if urlsplit(base).scheme in ("http", "https") and urlsplit(base).hostname:
                    self.base = base
            except ValueError:
                # Browsers ignore an invalid base; keep the document URL.
                return
            return
        if tag not in ("a", "area"):
            return
        try:
            url = resolve_document_url(self.base, href)
            if urlsplit(url).scheme not in ("http", "https"):
                return
        except ValueError:
            return
        if url not in self.links:
            if len(self.links) == self.limit:
                raise _LinkLimit
            self.links[url] = None


def extract_document_links(html: str, url: str, limit: int = 2000) -> tuple[tuple[str, ...], bool]:
    if limit < 1:
        raise ValueError("Link limit must be positive")
    parser = _Links(url, limit)
    try:
        parser.feed(html)
        parser.close()
    except _LinkLimit:
        return tuple(parser.links), False
    return tuple(parser.links), True
