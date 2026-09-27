"""`kazma-security.yaml` is a declaration, and the docs say so.

The security guide said a hardening runner "runs the checks at startup" with
`run_on_startup: true`, and told readers to "verify runtime enforcement"
against it: no code read the file, and the runner ran only when its API route
was called (found 2026-09-27). The docs now say the file is read by no code.
If code ever starts reading it, this fails, so the docs change with it.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGES = ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-skills", "kazma-tui")


def readers(sources: dict[str, str]) -> list[str]:
    return sorted(path for path, text in sources.items() if "kazma-security.yaml" in text)


def test_no_code_reads_the_security_policy_file():
    sources = {
        str(p.relative_to(REPO)): p.read_text(encoding="utf-8", errors="replace")
        for pkg in PACKAGES for p in (REPO / pkg).rglob("*.py")
        if "tests" not in p.parts and "_tests" not in p.parent.name
    }
    assert readers(sources) == [], (
        "code now reads kazma-security.yaml: update the docs that say no code does "
        "(kazma-security.yaml's header, guide/security-and-safety.md §8, guide/configuration.md §7.2)"
    )
    # Negative control: a reader is seen.
    assert readers({"k.py": "yaml.safe_load(open('kazma-security.yaml'))"}) == ["k.py"]


def test_the_docs_do_not_claim_it_is_enforced():
    guide = (REPO / "docs/docs/guide/security-and-safety.md").read_text(encoding="utf-8")
    assert "runs the checks at startup" not in guide
    assert "No Kazma code reads that file" in guide
    header = (REPO / "kazma-security.yaml").read_text(encoding="utf-8")[:1200]
    assert "NO KAZMA CODE READS THIS FILE" in header
