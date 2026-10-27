"""A `"` in a Markdown link's URL no longer breaks Telegram's HTML (audit
2026-10-26 AUD-051).

``md_to_tg_html`` escapes the text without quotes, then put the URL into
``href="..."`` as it was: a `"` ended the attribute, Telegram refused the
message's HTML, and the whole message fell back to plain text. The href is
now escaped with quotes, from its unescaped form so ``&`` is not doubled.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str | None] = []
        self.attrs_seen: list[list[tuple[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.append(dict(attrs).get("href"))
            self.attrs_seen.append(attrs)


def _parse(markup: str) -> _Links:
    parser = _Links()
    parser.feed(markup)
    return parser


def test_a_quote_in_the_url_stays_inside_the_href() -> None:
    from kazma_gateway.telegram_format import md_to_tg_html

    out = md_to_tg_html('See [the page](https://example.com/a"b) now')
    links = _parse(out)
    assert links.hrefs == ['https://example.com/a"b']
    assert links.attrs_seen == [[("href", 'https://example.com/a"b')]]
    assert "&quot;" in out


def test_an_ampersand_is_escaped_once() -> None:
    from kazma_gateway.telegram_format import md_to_tg_html

    out = md_to_tg_html("[q](https://example.com/?a=1&b=2)")
    assert 'href="https://example.com/?a=1&amp;b=2"' in out
    assert _parse(out).hrefs == ["https://example.com/?a=1&b=2"]


def _old_link(text: str) -> str:
    """The link step before AUD-051 (on the quote-less escaped text)."""
    s = html.escape(text, quote=False)
    return re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
        s,
    )


def test_negative_control_the_old_link_breaks_the_attribute() -> None:
    links = _parse(_old_link('See [the page](https://example.com/a"b) now'))
    assert links.hrefs == ["https://example.com/a"]
    assert len(links.attrs_seen[0]) > 1  # the rest of the URL became attributes
