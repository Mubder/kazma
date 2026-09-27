"""Every documentation page declares its fate on kazma.ai.

``docs/website-pages.json`` maps each page of this repository's documentation
to its place on the website, or says why it is not published. It is what
``scripts/website_sync_plan.py`` reads to tell a website agent what to add,
update and remove. A page missing from it is a page the site never learns
about: the website went two months without ``kazma update``'s page, and kept
pages whose source had been deleted (2026-09-28). So a page added, moved or
deleted here must change the map in the same commit.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "docs" / "website-pages.json"

#: The site layout keys the plan script reads.
SITE_KEYS = {"repository", "docs_dir", "arabic_dir", "sidebar_file", "redirects_file", "sync_record"}

#: What an entry may say. ``adapted``: the site writes its own page from the
#: source instead of copying it, so the plan does not compare their structure.
ENTRY_KEYS = {"source", "site", "why", "watch", "adapted"}


def documentation_pages(repo: Path) -> set[str]:
    """Every page the map must cover, from the files on disk (new ones too).

    ``docs/docs/`` in full, the pages directly in ``docs/`` (``docs/plans``,
    ``docs/audits`` and the other folders there are working notes, not
    documentation), and the CHANGELOG.
    """
    docs = repo / "docs"
    pages = {
        p.relative_to(repo).as_posix()
        for p in (docs / "docs").rglob("*")
        if p.suffix in (".md", ".mdx") and p.is_file()
    }
    pages |= {p.relative_to(repo).as_posix() for p in docs.glob("*.md") if p.is_file()}
    if (repo / "CHANGELOG.md").is_file():
        pages.add("CHANGELOG.md")
    return pages


def map_problems(pages: set[str], manifest: dict) -> list[str]:
    entries = manifest.get("pages") or []
    sources = [e.get("source") for e in entries]
    problems = [f"{s}: listed {n} times" for s, n in Counter(sources).items() if n > 1]
    problems += [f"{p}: not in docs/website-pages.json" for p in sorted(pages - set(sources))]
    problems += [f"{s}: in the map but no such page" for s in sorted(set(sources) - pages)]
    sites = [e["site"] for e in entries if e.get("site")]
    problems += [f"site path {s}: used by {n} sources" for s, n in Counter(sites).items() if n > 1]
    for e in entries:
        unknown = sorted(set(e) - ENTRY_KEYS)
        if unknown:
            problems.append(f"{e.get('source')}: unknown keys {unknown}")
        if not e.get("site") and not (e.get("why") or "").strip():
            problems.append(f"{e.get('source')}: not published, and no `why`")
        if e.get("adapted") and not e.get("site"):
            problems.append(f"{e.get('source')}: adapted, but not published")
        if e.get("adapted") and not (e.get("why") or "").strip():
            problems.append(f"{e.get('source')}: adapted, and no `why`")
        if e.get("watch") not in (None, "claims", "news"):
            problems.append(f"{e.get('source')}: unknown watch {e.get('watch')!r}")
        if e.get("site") and e.get("watch"):
            problems.append(f"{e.get('source')}: a published page is not also watched")
    return problems


def test_every_documentation_page_has_its_website_fate():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert set(manifest["site"]) == SITE_KEYS
    assert map_problems(documentation_pages(REPO), manifest) == []


def test_the_map_check_sees_every_kind_of_mistake():
    """Negative control: a new page, a stale entry, a double, a silent skip."""
    pages = {"docs/docs/a.md", "docs/docs/b.md", "CHANGELOG.md"}
    manifest = {
        "pages": [
            {"source": "docs/docs/a.md", "site": "a"},
            {"source": "docs/docs/gone.md", "site": "gone"},
            {"source": "CHANGELOG.md", "site": None},
            {"source": "docs/docs/a.md", "site": "a"},
            {"source": "docs/docs/b.md", "sites": "b", "adapted": True},
        ]
    }
    problems = map_problems(pages | {"docs/docs/c.md"}, manifest)
    assert "docs/docs/c.md: not in docs/website-pages.json" in problems
    assert "docs/docs/gone.md: in the map but no such page" in problems
    assert "docs/docs/a.md: listed 2 times" in problems
    assert "site path a: used by 2 sources" in problems
    assert "CHANGELOG.md: not published, and no `why`" in problems
    assert "docs/docs/b.md: unknown keys ['sites']" in problems
    assert "docs/docs/b.md: adapted, but not published" in problems
    assert "docs/docs/b.md: adapted, and no `why`" in problems


def test_the_page_scan_includes_a_file_not_yet_committed(tmp_path):
    """The scan reads the disk, not git: a page added locally fails here, not in CI."""
    (tmp_path / "docs" / "docs" / "guide").mkdir(parents=True)
    (tmp_path / "docs" / "docs" / "guide" / "new.md").write_text("# New\n", encoding="utf-8")
    (tmp_path / "docs" / "plans").mkdir()
    (tmp_path / "docs" / "plans" / "note.md").write_text("# Note\n", encoding="utf-8")
    (tmp_path / "docs" / "FEATURES.md").write_text("# F\n", encoding="utf-8")
    assert documentation_pages(tmp_path) == {"docs/docs/guide/new.md", "docs/FEATURES.md"}
