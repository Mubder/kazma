"""Kazma's security policy says one thing, in every copy and both languages.

2026-09-27: the security guide said a hardening runner "runs the checks at
startup" with ``run_on_startup: true`` in ``kazma-security.yaml``, and told
readers to verify runtime enforcement against that file. No code read it.
2026-11-27: the file was removed (its ``scanning`` and ``hardening`` keys
still read like a schedule that never ran), and the policy was rewritten. The
rewrite found the policy's copies had drifted: the website mirror still listed
0.6.x as supported, ``SECURITY.md`` listed 0.20.x (never a GitHub Release),
"private reporting when enabled" though it is on, and live-model injection
numbers without the threat model's note that one row predates the
2026-09-12b hardening.

So these gates hold the class, not the instances: no product code names the
removed file and a page that names it says it is gone; and every copy of the
policy (``SECURITY.md``, ``SECURITY.ar.md``, the two website pages,
CONTRIBUTING's section) names the same channels, no version line, no
"when enabled", and -- where it carries them -- the same response targets and
the live-model caveat. Each gate is shown failing on the old text.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGES = ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-skills", "kazma-tui")
REMOVED = "kazma-security.yaml"

FORM = "https://github.com/Mubder/kazma/security/advisories/new"
EMAIL = "admin@kazma.ai"

#: Every copy of the policy, and whether it carries the response targets.
POLICY_PAGES = {
    "SECURITY.md": "en",
    "SECURITY.ar.md": "ar",
    "docs/docs/security/security-policy.md": "en",
    "docs/docs/security/vulnerability-reporting.md": "en",
}
#: Copies that only point at the policy: the channels, and nothing stale.
POINTER_PAGES = ("CONTRIBUTING.md",)

#: The owner's response targets (2026-11-27), as each language writes them.
TARGETS = {
    "en": ("within 3 days", "within 7 days", "within 30 days", "at most 90 days"),
    "ar": ("خلال 3 أيام", "خلال 7 أيام", "خلال 30 يومًا", "خلال 90 يومًا"),
}

#: A supported-versions line naming a release series (``0.20.x``): the
#: policy supports "the latest release and main", which cannot go stale.
_VERSION_LINE = re.compile(r"\b\d+\.\d+\.x\b")
_WHEN_ENABLED = re.compile(r"\bwhen (?:it is |it's )?enabled\b", re.I)


# ── the removed declaration ─────────────────────────────────────────────


def readers(sources: dict[str, str]) -> list[str]:
    return sorted(path for path, text in sources.items() if REMOVED in text)


def test_no_code_reads_the_removed_declaration():
    assert not (REPO / REMOVED).exists(), (
        f"{REMOVED} is back: no code reads it, and SECURITY.md is the policy"
    )
    sources = {
        str(p.relative_to(REPO)): p.read_text(encoding="utf-8", errors="replace")
        for pkg in PACKAGES for p in (REPO / pkg).rglob("*.py")
        if "tests" not in p.parts and "_tests" not in p.parent.name
    }
    assert readers(sources) == [], f"product code names {REMOVED}, which was removed on 2026-11-27"
    # Negative control: a reader is seen.
    assert readers({"k.py": f"yaml.safe_load(open('{REMOVED}'))"}) == ["k.py"]


def stale_mentions(pages: dict[str, str]) -> list[str]:
    """``page:line`` for each line that names the removed file without saying it is gone."""
    out: list[str] = []
    for page, text in pages.items():
        for n, line in enumerate(text.splitlines(), 1):
            if REMOVED in line and "removed" not in line.lower():
                out.append(f"{page}:{n}")
    return out


def _docs_pages() -> dict[str, str]:
    files = [*(REPO / "docs" / "docs").rglob("*.md"), *(REPO / "docs").glob("*.md"),
             *(REPO / p for p in (*POLICY_PAGES, *POINTER_PAGES, "README.md", "README.ar.md"))]
    return {str(p.relative_to(REPO)): p.read_text(encoding="utf-8") for p in files if p.is_file()}


def test_a_page_naming_the_removed_file_says_it_was_removed():
    assert stale_mentions(_docs_pages()) == []
    # Negative control: the old checklist line and the old guide sentence.
    old = {
        "SECURITY.md": "- [ ] Periodic review of `kazma-security.yaml` / `kazma-permissions.yaml` posture",
        "guide.md": "## 7.2\n`kazma-security.yaml` (removed)\nNo code reads `kazma-security.yaml`.",
    }
    assert stale_mentions(old) == ["SECURITY.md:1", "guide.md:3"]


def test_the_guide_does_not_claim_the_checks_run_at_startup():
    guide = (REPO / "docs/docs/guide/security-and-safety.md").read_text(encoding="utf-8")
    assert "runs the checks at startup" not in guide
    assert "It does not run at startup" in guide


# ── the policy's copies agree ───────────────────────────────────────────


def policy_problems(page: str, text: str, lang: str | None) -> list[str]:
    """What a copy of the policy says that the policy does not.

    ``lang`` is None for a page that only points at the policy.
    """
    problems: list[str] = []
    if FORM not in text:
        problems.append(f"{page}: does not name the private form")
    if EMAIL not in text:
        problems.append(f"{page}: does not name {EMAIL}")
    if _WHEN_ENABLED.search(text):
        problems.append(f"{page}: says private reporting is used 'when enabled' (it is on)")
    for m in _VERSION_LINE.finditer(text):
        problems.append(f"{page}: names a release series {m.group(0)!r} (supported: the latest release and main)")
    if "tenant isolation" in text.lower():
        problems.append(f"{page}: lists tenant isolation (THREAT_MODEL section 6: not a boundary)")
    if lang is None:
        return problems
    for target in TARGETS[lang]:
        if target not in text:
            problems.append(f"{page}: does not carry the target {target!r}")
    if "compound-mini" in text and "2026-09-12b" not in text:
        problems.append(f"{page}: quotes the compound-mini row without its re-run caveat")
    if FORM in text and EMAIL in text and text.index(FORM) > text.index(EMAIL):
        problems.append(f"{page}: names the email before the private form (the form comes first)")
    return problems


def test_every_copy_of_the_policy_says_the_same():
    problems: list[str] = []
    for page, lang in POLICY_PAGES.items():
        problems += policy_problems(page, (REPO / page).read_text(encoding="utf-8"), lang)
    for page in POINTER_PAGES:
        text = (REPO / page).read_text(encoding="utf-8")
        section = text[text.index("## Security Policy"):]
        problems += policy_problems(page, section.split("\n## ", 1)[0], None)
    assert problems == []


def test_the_gate_reads_the_old_policy_as_wrong():
    """Negative control: the text the 2026-11-27 rewrite replaced."""
    old = (
        "| 0.21.x | yes |\n| 0.20.x | critical fixes only |\n"
        "| Email | **admin@kazma.ai** | Primary |\n"
        f"| GitHub | [Private vulnerability reporting]({FORM}) | Use when enabled on the repository |\n"
        "| RBAC / permissions | Role-based access control, tenant isolation |\n"
        "| Acknowledgment | 48 hours |\n"
        "injection compliance on `groq/compound-mini` drops 42% -> 8%\n"
    )
    found = policy_problems("SECURITY.md", old, "en")
    assert any("'0.21.x'" in p for p in found) and any("'0.20.x'" in p for p in found)
    assert any("when enabled" in p for p in found)
    assert any("tenant isolation" in p for p in found)
    assert any("within 3 days" in p for p in found)
    assert any("re-run caveat" in p for p in found)
    assert any("form comes first" in p for p in found)
    # The mirrors' old text.
    assert any("'0.6.x'" in p for p in policy_problems("mirror", f"{FORM} {EMAIL}\n| 0.6.x | Full support |", None))
    assert any("when enabled" in p for p in policy_problems(
        "mirror", f"Email {EMAIL}, or use [GitHub private reporting]({FORM}) when it is enabled on the repo", None))


def test_each_language_opens_with_a_link_to_the_other():
    en = (REPO / "SECURITY.md").read_text(encoding="utf-8").splitlines()[:4]
    ar = (REPO / "SECURITY.ar.md").read_text(encoding="utf-8").splitlines()[:4]
    assert any("(SECURITY.ar.md)" in line for line in en), "SECURITY.md does not open with the Arabic link"
    assert any("(SECURITY.md)" in line for line in ar), "SECURITY.ar.md does not open with the English link"
    assert "كاظمه" in "\n".join(ar) and "كازما" not in (REPO / "SECURITY.ar.md").read_text(encoding="utf-8")
