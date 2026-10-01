"""The kazma.ai sync plan finds every kind of difference -- and none when there is none.

``scripts/website_sync_plan.py`` is how an agent working on the website learns
what to add, update, remove and translate (docs/docs/ops/website-sync.md).
Each test builds a throwaway framework repository and site, changes one thing,
and checks the plan names it; the first shows a site in step gets an empty
plan, so the others are not passing on a plan that always complains.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "website_sync_plan_mod", REPO / "scripts" / "website_sync_plan.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # A dataclass looks its module up in sys.modules while it is built.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


wsp = _load()

MANIFEST = {
    "schema_version": 1,
    "site": {
        "repository": "example/site",
        "docs_dir": "src/content/docs",
        "arabic_dir": "src/content/docs/ar",
        "sidebar_file": "astro.config.mjs",
        "redirects_file": "public/_redirects",
        "sync_record": "src/data/docs-sync.json",
    },
    "pages": [
        {"source": "docs/docs/guide/alpha.md", "site": "alpha"},
        {"source": "docs/docs/ops/beta.md", "site": "ops/beta"},
        {"source": "docs/docs/ops/internal.md", "site": None, "why": "maintainer-only"},
        {"source": "docs/FEATURES.md", "site": None, "watch": "claims", "why": "claims"},
        {"source": "CHANGELOG.md", "site": None, "watch": "news", "why": "news"},
    ],
}

PAGE = (
    "---\ntitle: Alpha\n---\n\n# Alpha\n\n## Use\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
    "```bash\nkazma serve\n```\n"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _commit(fw: Path, message: str) -> str:
    _git(fw, "add", "-A")
    _git(fw, "commit", "-q", "-m", message)
    return _git(fw, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """A framework repo at its first commit, and a site that copied alpha."""
    fw, site = tmp_path / "kazma", tmp_path / "site"
    fw.mkdir()
    _git(fw, "init", "-q")
    _write(fw, "docs/docs/guide/alpha.md", PAGE)
    _write(fw, "docs/docs/ops/beta.md", PAGE.replace("Alpha", "Beta"))
    _write(fw, "docs/docs/ops/internal.md", "# Internal\n")
    _write(fw, "docs/FEATURES.md", "# Features\n")
    _write(fw, "CHANGELOG.md", "# CHANGELOG\n\n## First\n")
    _write(fw, "docs/website-pages.json", json.dumps(MANIFEST))
    first = _commit(fw, "one")
    _write(site, "src/content/docs/alpha.md", PAGE)
    _write(site, "src/content/docs/ar/alpha.md", PAGE.replace("# Alpha", "# ألفا"))
    _write(site, "astro.config.mjs", "sidebar: [{ label: 'Alpha', slug: 'docs/alpha' }]\n")
    return fw, site, first


def _add_beta(site: Path) -> None:
    beta = PAGE.replace("Alpha", "Beta")
    _write(site, "src/content/docs/ops/beta.md", beta)
    _write(site, "src/content/docs/ar/ops/beta.md", beta.replace("# Beta", "# بيتا"))
    _write(
        site, "astro.config.mjs",
        "sidebar: [{ slug: 'docs/alpha' }, { slug: 'docs/ops/beta' }]\n",
    )


def _plan(fw: Path, site: Path, **kw):
    return wsp.make_plan(fw, site, **kw)


# ── A site in step ──────────────────────────────────────────────────────────


def test_a_site_in_step_has_nothing_to_do(repos):
    fw, site, _ = repos
    _add_beta(site)
    wsp.mark_synced(fw, site, [], every=True)
    plan = _plan(fw, site)
    assert plan.work() == 0, plan
    assert "Nothing to do" in wsp.render(plan, fw, site)
    assert wsp.main(["--site", str(site), "--framework", str(fw), "--check"]) == 0


# ── Each kind of difference ─────────────────────────────────────────────────


def test_a_new_page_is_an_add(repos):
    fw, site, first = repos
    plan = _plan(fw, site, assume_at=first)
    assert [a["site"] for a in plan.add] == ["ops/beta"]
    assert wsp.main(["--site", str(site), "--framework", str(fw), "--check"]) == 1


def test_a_changed_source_is_an_update_carrying_its_diff(repos):
    fw, site, first = repos
    wsp.mark_synced(fw, site, ["alpha"])
    before = wsp.blob_at(fw, "HEAD", "docs/docs/guide/alpha.md")
    _write(fw, "docs/docs/guide/alpha.md", PAGE + "\nA new paragraph.\n")
    _commit(fw, "two")
    after = wsp.blob_at(fw, "HEAD", "docs/docs/guide/alpha.md")
    [update] = _plan(fw, site).update
    assert (update["site"], update["from"], update["to"]) == ("alpha", before, after)
    assert before in update["diff"] and after in update["diff"]
    assert "+A new paragraph." in _git(fw, "diff", update["from"], update["to"])


def test_assume_synced_at_stands_in_for_a_missing_record(repos):
    fw, site, first = repos
    assert _plan(fw, site, assume_at=first).update == []
    assert [u["site"] for u in _plan(fw, site).update] == ["alpha"]  # no record, no assumption


def test_unpublishing_or_deleting_a_source_is_a_removal(repos):
    fw, site, _ = repos
    wsp.mark_synced(fw, site, ["alpha"])
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["pages"][0] = {"source": "docs/docs/guide/alpha.md", "site": None, "why": "retired"}
    _write(fw, "docs/website-pages.json", json.dumps(manifest))
    _commit(fw, "unpublish")
    assert [(r["site"], r["why"]) for r in _plan(fw, site).remove] == [("alpha", "retired")]

    manifest["pages"].pop(0)
    _write(fw, "docs/website-pages.json", json.dumps(manifest))
    (fw / "docs/docs/guide/alpha.md").unlink()
    _commit(fw, "delete")
    [removal] = _plan(fw, site).remove
    assert removal["site"] == "alpha" and "removed or renamed" in removal["why"]


def test_a_page_with_no_source_is_site_only(repos):
    fw, site, first = repos
    _write(site, "src/content/docs/orphan.md", PAGE)
    assert _plan(fw, site, assume_at=first).site_only == ["orphan"]


def test_arabic_missing_orphaned_and_drifted(repos):
    fw, site, first = repos
    _add_beta(site)
    (site / "src/content/docs/ar/ops/beta.md").unlink()
    _write(site, "src/content/docs/ar/only-arabic.md", PAGE)
    _write(site, "src/content/docs/ar/alpha.md", PAGE.replace("## Use\n", ""))
    plan = _plan(fw, site, assume_at=first)
    assert plan.arabic_missing == ["ops/beta"]
    assert plan.arabic_orphans == ["only-arabic"]
    assert plan.arabic_drift == [{"site": "alpha", "differs": {"headings": {"en": 2, "ar": 1}}}]


def test_an_english_page_that_does_not_hold_its_source_is_english_drift(repos):
    """The record says what was copied; the structure says whether it was."""
    fw, site, first = repos
    _write(fw, "docs/docs/guide/alpha.md", PAGE + "\n## New section\n\n| x | y |\n|---|---|\n| 3 | 4 |\n")
    _commit(fw, "grown")
    [drift] = _plan(fw, site).english_drift
    assert drift == {
        "site": "alpha",
        "differs": {"headings": {"source": 3, "en": 2}, "table rows": {"source": 6, "en": 3}},
    }
    # Ported (both languages), the drift is gone and only the record is behind.
    ported = (fw / "docs/docs/guide/alpha.md").read_text(encoding="utf-8")
    _write(site, "src/content/docs/alpha.md", ported)
    _write(site, "src/content/docs/ar/alpha.md", ported.replace("# Alpha", "# ألفا"))
    plan = _plan(fw, site)
    assert plan.english_drift == [] and plan.arabic_drift == []
    assert [u["site"] for u in plan.update] == ["alpha"]


def test_marking_refuses_a_page_that_is_not_ported(repos):
    """No record may claim a page that visibly does not hold its source."""
    fw, site, _ = repos
    _add_beta(site)
    _write(fw, "docs/docs/guide/alpha.md", PAGE + "\n## New section\n")
    _commit(fw, "grown")
    with pytest.raises(wsp.PlanError, match=r"alpha: headings source 3 / en 2"):
        wsp.mark_synced(fw, site, ["alpha"])
    # All or nothing: the ported page is not recorded either.
    with pytest.raises(wsp.PlanError, match="do not match"):
        wsp.mark_synced(fw, site, [], every=True)
    assert not (site / "src/data/docs-sync.json").exists()
    # An Arabic page short of its English one is refused the same way.
    _write(site, "src/content/docs/ar/ops/beta.md", "---\ntitle: بيتا\n---\n\n# بيتا\n")
    with pytest.raises(wsp.PlanError, match=r"ops/beta: headings en 2 / ar 1"):
        wsp.mark_synced(fw, site, ["ops/beta"])


def test_an_adapted_page_is_compared_with_its_arabic_twin_only(repos):
    """The site writes its docs home itself: not a copy, so not held to the
    source's structure -- but English and Arabic still match."""
    fw, site, _ = repos
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["pages"][0]["adapted"] = True
    manifest["pages"][0]["why"] = "a landing page written from the source"
    _write(fw, "docs/website-pages.json", json.dumps(manifest))
    _commit(fw, "adapted")
    landing = "---\ntitle: Welcome\n---\n\n# Welcome\n\nCards.\n"
    _write(site, "src/content/docs/alpha.md", landing)
    _write(site, "src/content/docs/ar/alpha.md", landing.replace("Welcome", "أهلاً"))
    assert _plan(fw, site).english_drift == []
    wsp.mark_synced(fw, site, ["alpha"])  # not refused
    _write(site, "src/content/docs/ar/alpha.md", "---\ntitle: أهلاً\n---\n\nCards.\n")
    assert [d["site"] for d in _plan(fw, site).arabic_drift] == ["alpha"]
    with pytest.raises(wsp.PlanError, match="alpha: headings en 1 / ar 0"):
        wsp.mark_synced(fw, site, ["alpha"])


def test_code_that_is_not_copied_exactly_is_drift(repos):
    """Counts agree, the code does not: a dropped line in English, a
    translated comment in Arabic. Code is copied exactly into both."""
    fw, site, first = repos
    wsp.mark_synced(fw, site, ["alpha"])
    _write(site, "src/content/docs/alpha.md", PAGE.replace("kazma serve", "kazma serv"))
    _write(site, "src/content/docs/ar/alpha.md",
           PAGE.replace("# Alpha", "# ألفا").replace("kazma serve", "kazma serv  # شغّل"))
    plan = _plan(fw, site)
    assert plan.english_drift == [{"site": "alpha", "differs": {"code": {"blocks changed": 1}}}]
    assert plan.arabic_drift == [{"site": "alpha", "differs": {"code": {"blocks changed": 1}}}]
    with pytest.raises(wsp.PlanError, match="alpha: code blocks changed 1"):
        wsp.mark_synced(fw, site, ["alpha"])


def test_structure_ignores_what_is_inside_code_blocks():
    fenced = "# A\n\n```md\n# not a heading\n| not | a row |\n```\n"
    assert wsp.structure(fenced) == {"headings": 1, "code blocks": 1, "table rows": 0, "asides": 0}


def test_sidebar_missing_and_dead(repos):
    fw, site, first = repos
    _add_beta(site)
    _write(site, "astro.config.mjs", "sidebar: [{ slug: 'docs/alpha' }, { slug: 'docs/gone' }]\n")
    plan = _plan(fw, site, assume_at=first)
    assert plan.sidebar_missing == ["docs/ops/beta"]
    assert plan.sidebar_dead == ["docs/gone"]


def test_broken_and_unconverted_links(repos):
    fw, site, first = repos
    body = PAGE + (
        "\n[a](/docs/nowhere/) [b](../guide/alpha.md) [ok](/docs/alpha/#use) [ar](/ar/docs/alpha/)\n"
        "[c](https://github.com/Mubder/kazma/blob/main/docs/docs/guide/alpha.md)\n"
        "[d](https://github.com/Mubder/kazma/blob/main/docs/nope.md)\n"
        "```md\n[e](/docs/also-nowhere/)\n```\n"
    )
    _write(site, "src/content/docs/alpha.md", body)
    _write(site, "src/content/docs/ar/alpha.md", body)
    found = {(l["page"], l["link"]) for l in _plan(fw, site, assume_at=first).links}
    for lang in ("en", "ar"):
        assert (f"{lang}:alpha", "/docs/nowhere/") in found
        assert (f"{lang}:alpha", "../guide/alpha.md") in found
        assert (f"{lang}:alpha", "https://github.com/Mubder/kazma/blob/main/docs/nope.md") in found
    # the same body on the Arabic page leads to the English alpha
    assert ("ar:alpha", "/docs/alpha/#use") in found
    assert len(found) == 7, found  # the valid links and the one in a code block are not flagged


def test_an_arabic_page_that_leads_to_an_english_one_is_flagged(repos):
    """Translations copied the English links: on 2026-10-01, 140 links in
    kazma.ai's Arabic pages led to English pages that have Arabic ones."""
    fw, site, first = repos
    _write(site, "src/content/docs/ops/beta.md", PAGE.replace("Alpha", "Beta"))  # English only
    links = "\n[a](/docs/alpha/) [b](/docs/alpha/#use) [c](/ar/docs/alpha/) [d](/docs/ops/beta/)\n"
    _write(site, "src/content/docs/alpha.md", PAGE + links)
    _write(site, "src/content/docs/ar/alpha.md", PAGE.replace("# Alpha", "# ألفا") + links)
    flagged = {(l["page"], l["link"]): l["problem"] for l in _plan(fw, site, assume_at=first).links}
    assert flagged == {
        ("ar:alpha", "/docs/alpha/"): "an Arabic page leads to the English page: link /ar/docs/alpha/",
        ("ar:alpha", "/docs/alpha/#use"): "an Arabic page leads to the English page: link /ar/docs/alpha/#use",
    }, flagged  # an English page may link English; beta has no Arabic page to go to


PROSE_EN = "\nKazma answers in the chat and keeps what it learns for the next question. " * 3
PROSE_AR = "\nيجيب كازما في المحادثة ويحتفظ بما يتعلمه للسؤال التالي دون أن ينسى شيئا. " * 3


def test_an_arabic_page_that_is_the_english_one_is_untranslated(repos):
    """Eight Arabic pages on kazma.ai were the English page, copied: built
    the same, so no drift check saw them (2026-10-01)."""
    fw, site, first = repos
    _add_beta(site)
    _write(site, "src/content/docs/alpha.md", PAGE + PROSE_EN)
    _write(site, "src/content/docs/ar/alpha.md", PAGE.replace("# Alpha", "# ألفا") + PROSE_EN)
    _write(site, "src/content/docs/ops/beta.md", PAGE.replace("Alpha", "Beta") + PROSE_EN)
    _write(site, "src/content/docs/ar/ops/beta.md", PAGE.replace("# Alpha", "# بيتا") + PROSE_AR)

    plan = _plan(fw, site, assume_at=first)
    assert [(u["site"], round(u["share"], 2)) for u in plan.arabic_untranslated] == [("alpha", 0.02)]
    assert "ARABIC UNTRANSLATED" in wsp.render(plan, fw, site)
    with pytest.raises(wsp.PlanError, match=r"alpha: the Arabic page is 2% Arabic"):
        wsp.mark_synced(fw, site, ["alpha"])
    wsp.mark_synced(fw, site, ["ops/beta"])  # the translated one is recorded


def test_arabic_share_reads_prose_only():
    """Code, inline code, link targets and tags are English on every page."""
    page = (
        "---\ntitle: English title in the frontmatter\n---\n\n" + PROSE_AR
        + "\n\n```bash\n" + "kazma serve --host 0.0.0.0 " * 20 + "\n```\n"
        + "\n`an inline code span in English` [رابط](/docs/a-long-english-page-path/) <br />\n"
    )
    assert wsp.arabic_share(page) == 1.0
    assert wsp.arabic_share(PAGE) is None  # too little prose to judge
    assert wsp.untranslated(page) is None and wsp.untranslated(PAGE + PROSE_EN) == 0.0


def test_inline_code_opening_a_line_is_not_a_fence():
    """``` ```` ```plan ```` ``` opening a line is inline code: the naive rule
    took it for a fence and skipped the rest of the page (task-ledger's links
    were never checked). A fence closes on its own character, as long."""
    page = (
        "# A\n\n```` ```plan ```` fence becomes the ledger's steps\n\n"
        "## After\n\n[x](/docs/after/)\n\n"
        "````md\n```bash\nkazma serve\n```\n````\n"
    )
    assert wsp.structure(page) == {"headings": 2, "code blocks": 1, "table rows": 0, "asides": 0}
    assert wsp.links(page) == ["/docs/after/"]
    assert wsp.code_blocks(page) == ["```bash\nkazma serve\n```"]


def test_new_changelog_entries_and_changed_claims_are_listed(repos):
    fw, site, _ = repos
    wsp.mark_synced(fw, site, ["CHANGELOG.md", "docs/FEATURES.md"])
    _write(fw, "CHANGELOG.md", "# CHANGELOG\n\n## Second\n\n## First\n")
    _write(fw, "docs/FEATURES.md", "# Features\n\n| Web chat | Shipped |\n")
    _commit(fw, "news")
    watch = {w["watch"]: w for w in _plan(fw, site).watch}
    assert watch["news"]["new_entries"] == ["## Second"]
    assert watch["claims"]["from"] and watch["claims"]["to"] != watch["claims"]["from"]


# ── Recording a sync ────────────────────────────────────────────────────────


def test_marking_refuses_a_page_missing_a_language(repos):
    fw, site, _ = repos
    _write(site, "src/content/docs/ops/beta.md", PAGE)
    with pytest.raises(wsp.PlanError, match="both languages"):
        wsp.mark_synced(fw, site, ["ops/beta"])
    with pytest.raises(wsp.PlanError, match="not in"):
        wsp.mark_synced(fw, site, ["no-such-page"])


def test_marking_writes_the_record_and_drops_what_left(repos):
    fw, site, _ = repos
    record = site / "src/data/docs-sync.json"
    _write(site, "src/data/docs-sync.json", json.dumps(
        {"schema_version": 1, "framework_commit": None,
         "pages": {"docs/docs/guide/retired.md": {"site": "retired", "blob": "0" * 40}}}
    ))
    marked, head = wsp.mark_synced(fw, site, ["alpha"])
    data = json.loads(record.read_text(encoding="utf-8"))
    assert marked == ["docs/docs/guide/alpha.md"] and data["framework_commit"] == head
    assert set(data["pages"]) == {"docs/docs/guide/alpha.md"}
    assert data["pages"]["docs/docs/guide/alpha.md"]["blob"] == wsp.blob_at(fw, "HEAD", "docs/docs/guide/alpha.md")
    assert b"\r\n" not in record.read_bytes()
