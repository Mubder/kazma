"""Every page of the docs site parses as MDX.

The site (Docusaurus, `docs/docs/`) reads markdown as MDX: outside code, "<"
opens a JSX tag and "{" a JavaScript expression. A tool description in the
generated tools catalog said ``proposal_id=<that item's id>``, and from
2026-09-25 the whole site stopped building ("Unexpected character `'` in
attribute name") until 2026-09-27, when a local build found it. Nothing ran
the build. This reads every page the way MDX would: outside fenced code,
inline code and HTML comments, a "<" must open a known HTML element, and
there is no "{" at all. The catalog generator escapes both
(``scripts/generate_tools_catalog.py``, ``_cell``).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "docs"

#: HTML elements the pages may use as such (MDX accepts them as JSX).
HTML_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "center", "cite", "code", "dd", "del",
    "details", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3",
    "h4", "h5", "h6", "hr", "i", "iframe", "img", "ins", "kbd", "li", "mark",
    "ol", "p", "picture", "pre", "q", "s", "samp", "small", "source", "span",
    "strong", "sub", "summary", "sup", "table", "tbody", "td", "th", "thead",
    "tr", "u", "ul", "var", "video",
}

_FENCE = re.compile(r"^(```|~~~)")
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TAG = re.compile(r"</?([A-Za-z][A-Za-z0-9-]*)")
_HEADING_ID = re.compile(r"\s*\{#[\w-]+\}\s*$")
_PARAGRAPH_BREAK = re.compile(r"(\n[ \t]*\n)")


def _blank(m: re.Match) -> str:
    """The match as spaces, its line breaks kept, so line numbers hold."""
    return re.sub(r"[^\n]", " ", m.group(0))


def mdx_hazards(text: str) -> list[str]:
    """``line: problem`` for each "<" MDX would read as an unknown JSX tag and
    each "{" it would read as an expression, outside code and comments."""
    text = _COMMENT.sub(_blank, text)
    kept: list[str] = []
    in_fence = False
    in_front_matter = False
    for n, line in enumerate(text.splitlines(), 1):
        if n == 1 and line.strip() == "---":
            in_front_matter = True
        elif in_front_matter:
            in_front_matter = line.strip() != "---"
        elif _FENCE.match(line.lstrip()):
            in_fence = not in_fence
        elif not in_fence:
            kept.append(line)
            continue
        kept.append("")
    # An inline code span may continue onto the next line of its paragraph,
    # never past a blank line.
    joined = "\n".join(kept).replace("\\{", "  ").replace("\\<", "  ")
    parts = _PARAGRAPH_BREAK.split(joined)  # separators kept at odd indices
    body = "".join(part if i % 2 else _INLINE_CODE.sub(_blank, part) for i, part in enumerate(parts))
    out: list[str] = []
    for n, bare in enumerate(body.split("\n"), 1):
        if bare.lstrip().startswith("#"):
            # Docusaurus reads a heading's explicit id before MDX does.
            bare = _HEADING_ID.sub("", bare)
        for m in _TAG.finditer(bare):
            if m.group(1).lower() not in HTML_TAGS:
                out.append(f"{n}: <{m.group(1)} would open a JSX tag")
        if "{" in bare:
            out.append(f"{n}: '{{' would open a JavaScript expression")
    return out


def test_every_docs_page_is_mdx_safe():
    pages = sorted(DOCS.rglob("*.md")) + sorted(DOCS.rglob("*.mdx"))
    assert len(pages) > 50, pages  # the enumeration itself is not blind
    found = {
        str(p.relative_to(REPO)): hz
        for p in pages
        if (hz := mdx_hazards(p.read_text(encoding="utf-8")))
    }
    assert found == {}, "the docs site would not build; escape or wrap in `code`:\n" + "\n".join(
        f"  {page} {h}" for page, hazards in found.items() for h in hazards[:5]
    )


def test_negative_control_the_line_that_broke_the_site_is_found():
    broke = "| `x_post` | social | **danger** | call this ONCE PER ITEM with proposal_id=<that item's id> |\n"
    assert mdx_hazards(broke) == ["1: <that would open a JSX tag"]
    assert mdx_hazards("Each item is {path, patch}.\n") == ["1: '{' would open a JavaScript expression"]
    fine = (
        "Use `proposal_id=<id>` and `{x}` in code.\n"
        "<details><summary>More</summary></details>\n"
        "```\n<anything goes {here}>\n```\n"
        "<!-- <not a tag> {nor this} -->\n"
        "a < b and 3 > 2\n"
    )
    assert mdx_hazards(fine) == []


def test_the_catalog_generator_escapes_what_mdx_reads():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_gen_catalog", REPO / "scripts" / "generate_tools_catalog.py")
    gen = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(gen)
    cell = gen._cell("proposal_id=<that item's id> and {path} | x")
    assert mdx_hazards(f"| {cell} |\n") == []
    assert "\\|" in cell and "&lt;" in cell and "&#123;" in cell
