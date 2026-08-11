"""Format-agnostic content model.

Every input path (generate, convert, ingest-reprocess, edit, export) produces a
list of :class:`Block` instances; every format engine (DOCX, PDF, XLSX, PPTX,
HTML) consumes the same blocks under a :class:`~kazma_core.documents.profile.DocProfile`.
That is what makes "the same output regardless of file extension" real: a
generated DOCX, a converted markdown→DOCX, and a re-processed ingested file all
become the same :class:`ContentModel`, then the chosen engine renders it.

``BodyBlock.text`` carries Markdown and the engine runs the shared parser
(:func:`kazma_core.documents.rich_render.parse_rich_blocks`) on it, so the rich
body (headings, lists, tables, quotes, code) is identical across formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Block",
    "TitleBlock",
    "HeadingBlock",
    "BodyBlock",
    "TableBlock",
    "TOCBlock",
    "CitationBlock",
    "SpacerBlock",
    "ContentModel",
]


@dataclass
class Block:
    """Base class for all content blocks."""


@dataclass
class TitleBlock(Block):
    """The document title — rendered as a full-width filled bar.

    ``level=0`` is the document title; ``level>=1`` is a section heading depth
    (used when a title block doubles as a heading bar).
    """

    text: str
    level: int = 0
    fill: str | None = None  # override bar fill (hex, no #)


@dataclass
class HeadingBlock(Block):
    """A section/sub-section heading rendered as a filled bar."""

    text: str
    level: int = 1  # 1, 2, 3
    fill: str | None = None


@dataclass
class BodyBlock(Block):
    """Rich Markdown body (headings/lists/tables/quotes/code inline)."""

    text: str


@dataclass
class TableBlock(Block):
    """A structured table with an optional heading bar above it."""

    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    heading: str | None = None
    heading_level: int = 2


@dataclass
class TOCBlock(Block):
    """A numbered table-of-contents list."""

    entries: list[str] = field(default_factory=list)


@dataclass
class CitationBlock(Block):
    """A numbered references/citations list."""

    items: list[str] = field(default_factory=list)


@dataclass
class SpacerBlock(Block):
    """An empty paragraph spacer."""

    text: str = ""


@dataclass
class ContentModel:
    """An ordered document body: header/footer chrome + the block list."""

    blocks: list[Block] = field(default_factory=list)
    header: str | None = None
    footer: str | None = None
    page_numbers: bool = False
    # True when the source payload requested images that generation must omit
    # (engines warn rather than render unapproved filesystem resources).
    images_present: bool = False
    # Document metadata → file core properties (title/author/subject/keywords),
    # shown in Explorer/Finder/search. Title comes from the first TitleBlock.
    author: str = ""
    subject: str = ""
    keywords: str = ""

    def add(self, block: Block) -> "ContentModel":
        self.blocks.append(block)
        return self
