"""Native parsers for safe text-based document formats."""

from __future__ import annotations

import csv
import html
import io
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from ..errors import DocumentFormatError, DocumentLimitError
from ..models import BlockType, DocumentIR
from .common import IRBuilder, ParseContext, read_utf8

__all__ = ["CsvParser", "HtmlDocumentParser", "JsonParser", "RtfParser", "TextParser"]


class TextParser:
    def parse(self, path: Path, context: ParseContext) -> DocumentIR:
        text = read_utf8(path)
        builder = IRBuilder(path, context)
        blocks = []
        for index, section in enumerate(re.split(r"\n\s*\n", text)):
            if not section:
                continue
            kind = BlockType.TEXT
            if context.extension in {".md", ".markdown"} and section.lstrip().startswith("#"):
                kind = BlockType.HEADING
            blocks.append((kind, section, {"section": index + 1}))
        builder.add_page(blocks or [(BlockType.TEXT, "", None)], metadata={"kind": "text"})
        return builder.build()


class JsonParser:
    def parse(self, path: Path, context: ParseContext) -> DocumentIR:
        try:
            value = json.loads(read_utf8(path))
        except json.JSONDecodeError as exc:
            raise DocumentFormatError("JSON document is invalid") from exc
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        builder = IRBuilder(path, context)
        builder.add_page(
            [(BlockType.CODE, text, {"language": "json"})],
            metadata={"kind": "json"},
        )
        return builder.build()


class CsvParser:
    def __init__(self, delimiter: str) -> None:
        self.delimiter = delimiter

    def parse(self, path: Path, context: ParseContext) -> DocumentIR:
        builder = IRBuilder(path, context)
        rows: list[list[str]] = []
        try:
            reader = csv.reader(io.StringIO(read_utf8(path)), delimiter=self.delimiter)
            for row_index, row in enumerate(reader, start=1):
                if row_index > context.config.max_rows_per_sheet:
                    builder.warnings.append("Rows truncated at the configured per-sheet limit")
                    break
                builder.count_cells(len(row))
                rows.append(row)
        except csv.Error as exc:
            raise DocumentFormatError("Delimited document is malformed") from exc
        text = "\n".join(" | ".join(cell for cell in row) for row in rows)
        builder.add_page(
            [(BlockType.TABLE, text, {"rows": len(rows), "delimiter": self.delimiter})],
            metadata={"kind": "sheet", "sheet_name": path.stem, "row_count": len(rows)},
        )
        return builder.build(metadata={"sheet_count": 1})


class _TextHTMLParser(HTMLParser):
    """Text and tables of an HTML document, in document order.

    Tables were read as running text with nothing between cells: "North",
    "1,250" and "18,400.75" became "North1,25018,400.75" -- two numbers
    fused into a wrong one -- and no TABLE block came out (2026-10-13, F09).
    Cells are now kept apart and each top-level table is one TABLE block
    (`` | `` between cells, like every other parser); a nested table is
    flattened into the cell that holds it. ``<title>`` is metadata, not body
    text (it duplicated the first heading).
    """

    _IGNORED = {"script", "style", "noscript", "template", "title"}
    _BREAKS = {
        "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section",
        "article", "header", "footer", "blockquote", "pre", "ul", "ol", "dd", "dt",
        "figcaption", "caption", "hr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, object]] = []
        self._text: list[str] = []
        self._ignored = 0
        # One entry per open table: its rows and the cell being read (or None).
        self._tables: list[tuple[list[list[str]], list[str] | None]] = []

    def _sink(self) -> list[str] | None:
        if not self._tables:
            return self._text
        return self._tables[-1][1]  # None between cells: inter-cell whitespace

    def _flush_text(self) -> None:
        if "".join(self._text).strip():
            self.blocks.append(("text", "".join(self._text)))
        self._text = []

    def _close_cell(self) -> None:
        rows, cell = self._tables[-1]
        if cell is not None:
            if not rows:
                rows.append([])
            rows[-1].append(" ".join("".join(cell).split()))
            self._tables[-1] = (rows, None)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._IGNORED:
            self._ignored += 1
        elif tag == "table":
            if not self._tables:
                self._flush_text()
            self._tables.append(([], None))
        elif tag == "tr" and self._tables:
            self._close_cell()
            self._tables[-1][0].append([])
        elif tag in {"td", "th"} and self._tables:
            self._close_cell()
            self._tables[-1] = (self._tables[-1][0], [])
        elif tag in self._BREAKS:
            sink = self._sink()
            if sink is not None:
                sink.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._IGNORED:
            if self._ignored:
                self._ignored -= 1
        elif tag in {"td", "th"} and self._tables:
            self._close_cell()
        elif tag == "table" and self._tables:
            self._close_cell()
            rows, _cell = self._tables.pop()
            rows = [row for row in rows if any(cell for cell in row)]
            if self._tables:
                sink = self._sink()
                if sink is not None:
                    sink.append(" " + " ".join(cell for row in rows for cell in row if cell) + " ")
            elif rows:
                self.blocks.append(("table", rows))

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        sink = self._sink()
        if sink is not None:
            sink.append(data)

    def close(self) -> None:
        super().close()
        while self._tables:  # unclosed tables in malformed HTML
            self.handle_endtag("table")
        self._flush_text()


class HtmlDocumentParser:
    def parse(self, path: Path, context: ParseContext) -> DocumentIR:
        parser = _TextHTMLParser()
        parser.feed(read_utf8(path))
        parser.close()
        builder = IRBuilder(path, context)
        blocks: list[tuple[BlockType, str, dict[str, object] | None]] = []
        for kind, value in parser.blocks:
            if kind == "table":
                rows = value  # type: ignore[assignment]
                for row in rows:
                    builder.count_cells(len(row))
                blocks.append((
                    BlockType.TABLE,
                    "\n".join(" | ".join(row) for row in rows),
                    {"rows": len(rows)},
                ))
                continue
            text = html.unescape("\n".join(
                line.strip() for line in str(value).splitlines() if line.strip()
            ))
            if text:
                blocks.append((BlockType.TEXT, text, None))
        builder.add_page(blocks, metadata={"kind": "html"})
        return builder.build()


class RtfParser:
    _CONTROL = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'[0-9a-fA-F]{2}|[{}]")

    def parse(self, path: Path, context: ParseContext) -> DocumentIR:
        raw = path.read_bytes()
        if not raw.lstrip().startswith(b"{\\rtf"):
            raise DocumentFormatError("RTF signature is invalid")
        try:
            source = raw.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise DocumentFormatError("RTF encoding is invalid") from exc
        text = self._CONTROL.sub(" ", source)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > context.config.max_output_chars_total:
            raise DocumentLimitError("RTF output exceeds the configured limit")
        builder = IRBuilder(path, context)
        builder.add_page([(BlockType.TEXT, text, None)], metadata={"kind": "rtf"})
        return builder.build()

