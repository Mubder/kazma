"""No template holds English interface text of its own (2026-09-28).

The interface is English and Arabic; a template's words come from the
catalog (``{{ t('key') }}``). Translating the pages on 2026-09-28 found
English literals in views the page tour could not see because they only
open on interaction -- the login page, the MCP add-server dialog, the API
tokens card, the voice and LiveKit settings, a crawl's progress counters.
``tests/e2e/test_pages_read_in_arabic.py`` measures rendered pages in a
browser; this reads every template's source, so a new English literal fails
the fast suite before any browser runs.

Counted: a text node, and a static ``title`` / ``placeholder`` /
``aria-label`` / ``alt``, holding a word that is not a name
(``tests/_ui_names.py``) or an acronym. Not counted: anything inside
``translate="no"`` -- the attribute for content, such as the user's own data
or an example of a value -- or inside ``code``, ``pre``, ``kbd``, ``samp``,
``script`` and ``style``; Jinja's own expressions and statements; bound
attributes (``:title``). Negative control: a synthetic template with English
in each counted place is caught, and every exempt place is not.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from tests._ui_names import UI_NAMES

TEMPLATES = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "templates"

_SKIP_TAGS = {"script", "style", "code", "pre", "kbd", "samp"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}
_ATTRS = ("title", "placeholder", "aria-label", "alt")
_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_COMMENTS = re.compile(r"\{#.*?#\}|<!--.*?-->", re.S)


def english(text: str) -> bool:
    """A word that is interface text: letters only, not an acronym, not a name."""
    for raw in text.split():
        tok = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", raw)
        if not re.fullmatch(r"[A-Za-z]{2,}", tok):
            continue
        if tok == tok.upper() or tok in UI_NAMES:
            continue
        return True
    return False


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []  # (tag, exempt)
        self.found: list[tuple[int, str]] = []

    def _exempt(self) -> bool:
        return any(exempt for _tag, exempt in self.stack)

    def handle_starttag(self, tag, attrs):
        amap = dict(attrs)
        exempt = amap.get("translate") == "no" or tag in _SKIP_TAGS
        if not (self._exempt() or exempt):
            for name in _ATTRS:
                value = amap.get(name)
                if value and english(_JINJA.sub(" ", value)):
                    self.found.append((self.getpos()[0], f'{name}="{value.strip()[:80]}"'))
        if tag not in _VOID:
            self.stack.append((tag, exempt))

    def handle_startendtag(self, tag, attrs):
        amap = dict(attrs)
        if not (self._exempt() or amap.get("translate") == "no"):
            for name in _ATTRS:
                value = amap.get(name)
                if value and english(_JINJA.sub(" ", value)):
                    self.found.append((self.getpos()[0], f'{name}="{value.strip()[:80]}"'))

    def handle_endtag(self, tag):
        # Jinja branches can open a tag twice and close it once: pop to the
        # nearest match, never past it.
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        text = " ".join(data.split())
        if text and not self._exempt() and english(text):
            self.found.append((self.getpos()[0], text[:80]))


def english_in(source: str) -> list[tuple[int, str]]:
    # Keep line numbers: blank out comments and Jinja, newlines kept.
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))  # noqa: E731
    text = _COMMENTS.sub(blank, source)
    text = _JINJA.sub(blank, text)
    scan = _Scan()
    scan.feed(text)
    scan.close()
    return scan.found


def test_no_template_holds_english_interface_text() -> None:
    templates = sorted(TEMPLATES.rglob("*.html"))
    assert len(templates) >= 20, templates
    problems = [
        f"{path.relative_to(TEMPLATES)}:{line}: {text}"
        for path in templates
        for line, text in english_in(path.read_text(encoding="utf-8"))
    ]
    assert not problems, (
        f"{len(problems)} English literals in templates -- give each a catalog key "
        "({{ t('...') }}), or mark content translate=\"no\":\n  " + "\n  ".join(problems)
    )


def test_negative_control_the_scan_tells_interface_from_content() -> None:
    found = english_in(
        "<div>Save changes</div>\n"
        '<input placeholder="Search libraries">\n'
        '<button title="Close this">x</button>\n'
        '<img alt="Company logo" src="x.png">\n'
        "<p>{{ t('common.save') }}</p>\n"
        "{# A Jinja comment in English #}\n"
        "<!-- An HTML comment in English -->\n"
        '<span translate="no">What the user wrote</span>\n'
        '<div translate="no"><p>Nested user content</p></div>\n'
        "<code>file_read</code><pre>some output</pre>\n"
        '<input placeholder="my-server" translate="no">\n'
        '<span :title="dynamic">JSON MCP Kazma</span>\n'
        "<script>var s = 'Not interface';</script>\n"
        "{% if x %}<div>{% else %}<div>{% endif %}</div>\n"
        "<p>Still counted</p>\n"
    )
    assert [text for _line, text in found] == [
        "Save changes",
        'placeholder="Search libraries"',
        'title="Close this"',
        'alt="Company logo"',
        "Still counted",
    ], found
