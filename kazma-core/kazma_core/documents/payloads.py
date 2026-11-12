"""The records the document tools take, named key by key.

A tool parameter typed ``list[dict]`` told the model nothing about its keys,
and the engines read one name each: the model guessed, and a key it guessed
wrong was dropped without a word. Live 2026-11-12, a deck's every slide title
was lost -- the model sent ``"title"``, the engine reads ``"heading"``. The
tools type these parameters with the records below, so the schema the model
reads (``kazma_core.agent.tool_schema``) names each key, its type and what it
is. ``tests/test_document_tool_records.py`` holds each record's keys to what
its engine reads.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

__all__ = ["Chart", "Section", "Sheet", "Slide", "Table", "slide_heading"]

Cell = str | int | float | bool | None


class Section(TypedDict, total=False):
    """A section of the document: its heading and its body."""

    heading: Annotated[str, "the section's heading"]
    body: Annotated[str, (
        "the section's complete text, in Markdown (# headings, - lists, **bold**, "
        "tables); never a summary of it. A picture goes on a line of its own as "
        "![caption](path to a PNG, JPEG, GIF or WebP file) and is placed right there"
    )]


class Table(TypedDict, total=False):
    """A table: the header row, then the rows, each the same length."""

    heading: Annotated[str, "a title drawn above the table (optional)"]
    headers: Annotated[list[str], "the column names"]
    rows: Annotated[list[list[Cell]], "the rows, each a list of cell values in column order"]


class Slide(TypedDict, total=False):
    """A slide after the title slide."""

    heading: Annotated[str, "the slide's title, drawn in its title band"]
    bullets: Annotated[list[str], "the slide's points, one item per point"]
    body: Annotated[str, "the slide's text when it has no bullets; each line becomes a point"]
    image: Annotated[str, (
        "a picture for the slide: the path of a PNG, JPEG, GIF or WebP file (in the workspace, "
        "a generated image or a chat attachment; a web image is saved first with download_image). "
        "Drawn beside the text, or across the slide when it has no text"
    )]
    image_caption: Annotated[str, "a caption under the picture (optional)"]
    notes: Annotated[str, "speaker notes (not shown on the slide)"]


def slide_heading(slide: object) -> str:
    """A slide's title: its ``heading``, else ``title`` (what a caller that
    was never told the key sends; every slide title of a live deck was lost
    to it, 2026-11-12)."""
    if not isinstance(slide, dict):
        return ""
    return str(slide.get("heading") or slide.get("title") or "")


class Chart(TypedDict, total=False):
    """A chart over the sheet's rows (the first row is the header)."""

    type: Annotated[str, "bar, line, area, pie, doughnut or scatter"]
    category_col: Annotated[int, "the column of the labels, 1 = the first (default 1)"]
    value_col: Annotated[int | list[int], "the column of the values, or several for several series (default 2)"]
    title: Annotated[str, "the chart's title"]
    x_axis: Annotated[str, "the x axis title"]
    y_axis: Annotated[str, "the y axis title"]


class Sheet(TypedDict, total=False):
    """A worksheet: a title bar, then the rows, the first of them the header."""

    name: Annotated[str, "the sheet's tab name (at most 31 characters)"]
    title: Annotated[str, "the title bar above the table (default: the name)"]
    rows: Annotated[list[list[Cell]], "the rows; the first row is the header"]
    chart: Chart
