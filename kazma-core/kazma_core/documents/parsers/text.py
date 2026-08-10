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
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored += 1
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)


class HtmlDocumentParser:
    def parse(self, path: Path, context: ParseContext) -> DocumentIR:
        parser = _TextHTMLParser()
        parser.feed(read_utf8(path))
        text = html.unescape("\n".join(
            line.strip() for line in "".join(parser.parts).splitlines() if line.strip()
        ))
        builder = IRBuilder(path, context)
        builder.add_page([(BlockType.TEXT, text, None)], metadata={"kind": "html"})
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

