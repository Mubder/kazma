"""Every documentation page can be reached from the docs sidebar.

Docusaurus builds a page that no sidebar lists, but nothing links to it: on
2026-09-28 the task ledger, the ``kazma update`` operator page and the smoke
matrix had no sidebar entry, so the docs site had them and no reader could
find them. Only per-page checks existed (the environment-variables index,
OpenTelemetry); this is the whole class.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "docs"
SIDEBARS = REPO / "docs" / "sidebars.js"


def doc_ids(docs: Path) -> set[str]:
    """Docusaurus ids: the page's folder plus its frontmatter ``id`` (else its name)."""
    ids = set()
    for page in docs.rglob("*"):
        if page.suffix not in (".md", ".mdx") or not page.is_file():
            continue
        text = page.read_text(encoding="utf-8")
        match = re.match(r"---\n(.*?)\n---", text.replace("\r\n", "\n"), re.S)
        own = re.search(r"^id:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", match.group(1), re.M) if match else None
        name = own.group(1) if own else page.stem
        folder = page.parent.relative_to(docs).as_posix()
        ids.add(name if folder == "." else f"{folder}/{name}")
    return ids


def sidebar_ids(text: str) -> set[str]:
    return set(re.findall(r"""['"]([a-z0-9][a-z0-9_/-]*)['"]""", text))


def test_every_documentation_page_is_in_the_sidebar():
    missing = sorted(doc_ids(DOCS) - sidebar_ids(SIDEBARS.read_text(encoding="utf-8")))
    assert missing == [], f"pages no reader can reach (add them to docs/sidebars.js): {missing}"


def test_the_check_sees_a_page_left_out(tmp_path):
    """Negative control, including a page whose frontmatter id is not its file name."""
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / "listed.md").write_text("---\nid: listed\n---\n# L\n", encoding="utf-8")
    (tmp_path / "ops" / "file-name.md").write_text("---\nid: own-id\n---\n# O\n", encoding="utf-8")
    (tmp_path / "intro.md").write_text("# Intro\n", encoding="utf-8")
    sidebar = "module.exports = { docs: ['intro', { items: ['ops/listed'] }] };"
    assert doc_ids(tmp_path) - sidebar_ids(sidebar) == {"ops/own-id"}
