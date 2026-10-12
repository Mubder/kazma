"""Bounded local text extraction that retains hidden documentation panels."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser


class _TextLimit(Exception):
    pass


@dataclass(frozen=True)
class KnowledgeDocument:
    text: str
    truncated: bool
    dynamic_tabs: bool


class _KnowledgeText(HTMLParser):
    _SKIP = {"head", "script", "style", "svg", "iframe", "object", "template", "nav", "footer"}
    _BLOCK = {"p", "div", "section", "article", "main", "ul", "ol", "table", "blockquote"}

    def __init__(self, limit):
        super().__init__(convert_charrefs=True)
        self.limit, self.length = limit, 0
        self.parts: list[str] = []
        self.skip = ""
        self.skip_depth = 0
        self.pre: list[str] | None = None
        self.has_script = self.has_tabs = False

    def _append(self, text):
        remaining = self.limit - self.length
        (self.pre if self.pre is not None else self.parts).append(text[:remaining])
        self.length += min(len(text), remaining)
        if len(text) > remaining:
            raise _TextLimit

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.has_script |= tag == "script"
        self.has_tabs |= (attributes.get("role") == "tab" or "data-tab" in attributes or
                          "tab-button" in (attributes.get("class") or "").split() or
                          (tag == "button" and "aria-selected" in attributes))
        if self.skip:
            self.skip_depth += tag == self.skip
            return
        if tag in self._SKIP:
            self.skip, self.skip_depth = tag, 1
            return
        if tag == "pre":
            self._append("\n\n")
            self.pre = []
        elif self.pre is None:
            if tag in self._BLOCK:
                self._append("\n\n")
            elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self._append("\n\n" + "#" * int(tag[1]) + " ")
            elif tag == "li":
                self._append("\n- ")
            elif tag in {"br", "tr"}:
                self._append("\n")
            elif tag in {"td", "th"}:
                self._append("\t")

    def _flush_pre(self):
        if self.pre is not None:
            text = "".join(self.pre)
            self.pre = None
            self.length -= len(text)
            fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", text)), default=3))
            self._append(fence + "\n" + text + "\n" + fence + "\n\n")

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip:
                self.skip_depth -= 1
                if not self.skip_depth:
                    self.skip = ""
            return
        if tag == "pre":
            self._flush_pre()
        elif self.pre is None and (tag in self._BLOCK or tag in {"h1", "h2", "h3", "h4", "h5", "h6"}):
            self._append("\n\n")

    def handle_data(self, data):
        if not self.skip:
            self._append(data if self.pre is not None else re.sub(r"[\t\r\n ]+", " ", data))


def extract_knowledge_document(html: str, limit: int = 100_000) -> KnowledgeDocument:
    """Keep code/tables/headings/hidden panels; exclude executable/site chrome.

    Parsing and retained output are bounded. The caller runs parsing off-loop.
    This is an explicit documentation profile, not a replacement for normal
    article extraction. Dynamic tab controls with scripts require rendering.
    """
    if limit < 1:
        raise ValueError("Text limit must be positive")
    parser = _KnowledgeText(limit)
    truncated = False
    try:
        parser.feed(html)
        parser.close()
        parser._flush_pre()
    except _TextLimit:
        truncated = True
    if parser.pre is not None:
        # A clipped code block is partial, never presented as complete code.
        parser.parts.extend(parser.pre)
    return KnowledgeDocument("".join(parser.parts).strip(), truncated, parser.has_script and parser.has_tabs)
