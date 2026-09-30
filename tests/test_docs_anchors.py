"""Every anchored link in the docs lands on its section.

Docusaurus only warns about a broken anchor (``onBrokenAnchors: 'warn'`` in
docs/docusaurus.config.js) and CI does not build the docs, so a link whose
section was renamed or removed kept pointing at nothing. On 2026-10-01 five of
the docs' 101 anchored links did -- among them "Memory & RAG -> Honest status",
a section gone since the V2 memory cutover, behind a sentence that still said
memory recall needs the model to call a tool -- and kazma.ai copied them.

This reads every link with a "#" in docs/docs and checks that the page it
names has that id: a heading's ``{#id}``, the id Docusaurus derives from a
heading's text, or an HTML ``id``. The derivation is github-slugger's, which
Docusaurus uses: lower case; letters, marks, digits, "_", "-" and spaces
kept; spaces to "-"; a repeat numbered "-1", "-2". It matched all 2,708
heading ids of the built kazma.ai site (Astro uses the same slugger, plus
smartypants, which Docusaurus does not).
"""

from __future__ import annotations

import html
import posixpath
import re
import unicodedata
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "docs"

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_EXPLICIT = re.compile(r"\s*\{#([A-Za-z0-9_-]+)\}\s*$")
_HTML_ID = re.compile(r"""\sid=["']([^"']+)["']""")
_LINK = re.compile(r"\]\(([^)\s]*#[^)\s]*)\)")


def prose_lines(text: str):
    """The lines outside fenced code. A backtick fence's info string holds no
    backtick (a line opening with ```` ```x ```` is inline code), and a fence
    closes on its own character, at least as long, with nothing after it."""
    fence = None
    for line in text.splitlines():
        m = _FENCE.match(line)
        if fence is None:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = (m.group(1)[0], len(m.group(1)))
                continue
            yield line
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] \
                and not m.group(2).strip():
            fence = None


def heading_text(markdown: str) -> str:
    """What a heading shows: inline markdown reduced to its text."""
    out = []
    parts = re.split(r"(`+)(.+?)\1", markdown)
    for k in range(0, len(parts), 3):
        text = parts[k]
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
        text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)
        text = re.sub(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?![\w])", r"\1", text)
        text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|<>~])", r"\1", text)
        out.append(html.unescape(text))
        if k + 2 < len(parts):
            out.append(parts[k + 2])  # a code span's text is literal
    return "".join(out)


def slug(text: str) -> str:
    kept = [ch for ch in text.lower()
            if ch in " -" or unicodedata.category(ch)[0] in "LMN"
            or unicodedata.category(ch) == "Pc"]
    return "".join(kept).replace(" ", "-")


def anchors_of(text: str) -> set[str]:
    """Every id a page offers a link."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        text = text[end + 4:] if end != -1 else text
    ids: set[str] = set()
    seen: dict[str, int] = {}
    for line in prose_lines(text):
        ids.update(_HTML_ID.findall(line))
        m = _HEADING.match(line)
        if not m:
            continue
        explicit = _EXPLICIT.search(m.group(2))
        if explicit:
            ids.add(explicit.group(1))
            continue
        base = candidate = slug(heading_text(m.group(2)))
        while candidate in seen:
            seen[base] += 1
            candidate = f"{base}-{seen[base]}"
        seen[candidate] = 0
        ids.add(candidate)
    return ids


def _page_for(docs: Path, page: Path, target: str) -> Path | None:
    if target == "":
        return page
    if target.startswith("/docs/"):
        base, rel = docs, target[len("/docs/"):]
    else:
        base, rel = page.parent, target
    path = Path(posixpath.normpath((base / rel).as_posix()))
    for candidate in (path, path.with_suffix(".md"), path.with_suffix(".mdx"), path / "index.md"):
        if candidate.is_file():
            return candidate
    return None


def broken_anchors(docs: Path) -> list[str]:
    """``page: link`` for each anchored link whose page has no such id."""
    cache: dict[Path, set[str]] = {}
    broken = []
    for page in sorted(docs.rglob("*.md*")):
        for line in prose_lines(page.read_text(encoding="utf-8")):
            for target in _LINK.findall(line):
                path, _, anchor = target.partition("#")
                if path.startswith(("http://", "https://", "mailto:")):
                    continue
                dest = _page_for(docs, page, path)
                if dest is None:
                    broken.append(f"{page.relative_to(docs).as_posix()}: {target} (no such page)")
                    continue
                if dest not in cache:
                    cache[dest] = anchors_of(dest.read_text(encoding="utf-8"))
                if anchor not in cache[dest]:
                    broken.append(f"{page.relative_to(docs).as_posix()}: {target}")
    return broken


def test_every_anchor_in_the_docs_names_a_section():
    assert broken_anchors(DOCS) == []


def test_a_broken_anchor_is_found(tmp_path):
    """The negative control: the check finds what it is for, and only that."""
    (tmp_path / "guide").mkdir()
    (tmp_path / "guide" / "a.md").write_text(
        "---\ntitle: A\n---\n\n# A\n\n"
        "## 5.6 Tool hooks (PreToolUse / PostToolUse)\n\n"
        "## 3. SSE event contract {#sse-event-contract}\n\n"
        "## Notes\n\n## Notes\n\n"
        "### `notifications.lifecycle`\n\n"
        "```` ```plan ```` is inline code, not a fence\n\n"
        "## After the inline code\n\n"
        "```md\n## Not a heading\n[in code](b#nowhere)\n```\n\n"
        "[same page](#notes-1)\n",
        encoding="utf-8")
    (tmp_path / "guide" / "b.md").write_text(
        "# B\n\n"
        "[1](a#56-tool-hooks-pretooluse--posttooluse) [2](./a#sse-event-contract)\n"
        "[3](a.md#notificationslifecycle) [4](/docs/guide/a#after-the-inline-code)\n"
        "[5](a#56-tool-hooks) [6](a#not-a-heading) [7](#b)\n"
        "[8](https://example.com/#anything) [9](missing#x)\n",
        encoding="utf-8")
    assert broken_anchors(tmp_path) == [
        "guide/b.md: a#56-tool-hooks",
        "guide/b.md: a#not-a-heading",
        "guide/b.md: missing#x (no such page)",
    ]


@pytest.mark.parametrize("heading, expected", [
    ("5.6 Tool hooks (PreToolUse / PostToolUse)", "56-tool-hooks-pretooluse--posttooluse"),
    ("`notifications.lifecycle`", "notificationslifecycle"),
    ("3.3 Cryptographic signing (HMAC-SHA256) — VERIFIED", "33-cryptographic-signing-hmac-sha256--verified"),
    ("2. أنماط الإرسال الستة",
     "2-أنماط-الإرسال-الستة"),
    ("5.1 `CircuitBreaker` (`reliability.py:238-389`)", "51-circuitbreaker-reliabilitypy238-389"),
    ("**Bold** and [a link](x.md) and _em_", "bold-and-a-link-and-em"),
])
def test_ids_are_derived_the_way_the_site_and_docusaurus_do(heading, expected):
    """The first five are the ids the built kazma.ai site gave those headings;
    the last shows inline markdown reduced to its text."""
    assert slug(heading_text(heading)) == expected
