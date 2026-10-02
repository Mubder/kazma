"""Find a template's Alpine text bindings that show text data outside content.

Shared by ``tests/test_text_follows_its_language.py`` (the gate) and the
sweep that marked the bindings it found (2026-10-02). A binding (``x-text`` /
``x-html``) shows data when what it can display -- every result of its
ternaries and ``||`` fallbacks -- reads a text field (a name, a title, a
model's words, an error, a path ...) rather than the catalog (``t('key')``),
a literal, a count or a flag. Data sits in content: ``translate="no"`` on the
element or an ancestor, the attribute the i18n rules give content, which also
gives it its direction from its own text (kazma.css). A number is left out on
purpose: content with no letters is laid out left-to-right, and a count in an
Arabic card belongs where the page puts it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

#: Calls that return the catalog's text in the page's language.
TRANSLATORS = ("t", "tOr", "i18n", "ti", "tiFmt", "tiCount", "tx", "_k", "kazmaT", "kazmaCount")

#: A field that holds text data.
TEXT_FIELD = re.compile(
    r"\.(?:model|models|name|display_name|display_name_ar|title|error|errors|message|detail|details|"
    r"summary|description|description_ar|desc|path|url|source_url|endpoint|reason|text|query|question|"
    r"prompt|content|author|username|email|address|command|source|output|result|snippet|preview|"
    r"subject|object|predicate|document_title|section_header|filename|original_filename|file|repo|"
    r"branch|worker|worker_name|agent_id|tool|tool_name|bot_name|exit_ip)\b(?!\s*\()"
)
#: A bare variable holding a server's status or error text.
BARE_TEXT = re.compile(
    r"^\s*!?\s*\$?[a-zA-Z_][\w$]*(?:Status|Error|Message|Detail|Text|Reason|Summary)\s*$"
    r"|^\s*(?:error|err|message)\s*$"
)

_SKIP_TAGS = {"script", "style", "code", "pre", "kbd", "samp", "option"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}
_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_COMMENTS = re.compile(r"\{#.*?#\}|<!--.*?-->", re.S)
_STRINGS = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`")


@dataclass(frozen=True)
class Binding:
    line: int
    col: int
    tag: str
    attr: str
    expr: str


def _split_top(expr: str, sep: str) -> list[str]:
    """Split *expr* at top-level *sep* (outside brackets; strings blanked)."""
    parts, depth, last = [], 0, 0
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and expr.startswith(sep, i):
            parts.append(expr[last:i])
            last = i + len(sep)
            i += len(sep)
            continue
        i += 1
    parts.append(expr[last:])
    return parts


def results(expr: str) -> list[str]:
    """What *expr* can display: the branches of its ternaries and fallbacks."""
    e = expr.strip()
    while e.startswith("(") and e.endswith(")") and _split_top(e[1:-1], ")") == [e[1:-1]]:
        inner = e[1:-1]
        if inner.count("(") != inner.count(")"):
            break
        e = inner.strip()
    q = _split_top(e, "?")
    if len(q) >= 2:
        rest = "?".join(q[1:])
        branches = _split_top(rest, ":")
        if len(branches) >= 2:
            return results(branches[0]) + results(":".join(branches[1:]))
    alts = _split_top(e, "||")
    if len(alts) >= 2:
        return [r for a in alts for r in results(a)]
    return [e]


def shows_text_data(expr: str) -> bool:
    """True when a result of *expr* reads a text field (not a count of one)."""
    blanked = _STRINGS.sub("''", expr)
    for leaf in results(blanked):
        leaf = leaf.strip()
        if not leaf or leaf == "''":
            continue
        if re.match(r"^(?:window\.)?(?:" + "|".join(TRANSLATORS) + r")\s*\(", leaf):
            continue
        readable = re.sub(r"(?:\([^()]*\)|[\w$.\]\[]+)\s*\.length\b", "0", leaf)
        if TEXT_FIELD.search(readable) or BARE_TEXT.match(readable):
            return True
    return False


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.found: list[Binding] = []

    def _content(self) -> bool:
        return any(c for _t, c in self.stack)

    def _check(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        amap = {k: v for k, v in attrs}
        content = (amap.get("translate") == "no" or ":translate" in amap
                   or "dir" in amap or ":dir" in amap or tag in _SKIP_TAGS)
        if not (self._content() or content):
            for name in ("x-text", "x-html"):
                expr = amap.get(name)
                if expr is not None and shows_text_data(expr):
                    line, col = self.getpos()
                    self.found.append(Binding(line, col, tag, name, expr))
        return content

    def handle_starttag(self, tag, attrs):
        content = self._check(tag, attrs)
        if tag not in _VOID:
            self.stack.append((tag, content))

    def handle_startendtag(self, tag, attrs):
        self._check(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def text_data_outside_content(source: str) -> list[Binding]:
    """Every binding showing text data outside content; positions are the source's."""
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))  # noqa: E731
    text = _COMMENTS.sub(blank, source)
    text = _JINJA.sub(blank, text)
    scan = _Scan()
    scan.feed(text)
    scan.close()
    return scan.found
