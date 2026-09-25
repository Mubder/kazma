"""The security scan covers the whole tree, and every waiver says why.

Bandit's HIGH gate used to cover the product packages only; ``tests/`` and
``scripts/`` were in the JSON artifact, which nobody reads, while three HIGH
findings sat there for weeks (docs/KNOWN_GAPS.md). They were fixed on
2026-09-25 and both directories joined the gate. These tests keep them in it,
and keep every ``# nosec`` honest: a bare ``# nosec`` silences every check on
the line, forever, for reasons nobody wrote down.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CI = REPO / ".github" / "workflows" / "ci.yml"

_PACKAGES = (
    "kazma-core/kazma_core", "kazma-gateway/kazma_gateway", "kazma-ui/kazma_ui",
    "kazma-cli/kazma_cli", "kazma-skills/kazma_skills", "kazma-tui/kazma_tui",
)
#: `# nosec B402 - reason` -- one or more rule ids, then a dash and a reason.
_NOSEC = re.compile(r"#\s*nosec\b(?P<rest>.*)$")
_JUSTIFIED = re.compile(r"^\s+B\d{3}(?:\s*,\s*B\d{3})*\s+-\s+\S.{8,}")


def _bandit_gate_command(ci_text: str) -> str:
    """The `run:` block of the HIGH-severity bandit gate step."""
    step = ci_text.split("name: Bandit (high severity + high confidence — GATE)", 1)[1]
    run = step.split("run: |", 1)[1]
    return run.split("- name:", 1)[0]


def test_the_high_severity_gate_scans_tests_and_scripts():
    cmd = _bandit_gate_command(CI.read_text(encoding="utf-8"))
    assert "-lll" in cmd and "-ii" in cmd
    assert "|| true" not in cmd, "a gate that cannot fail is decoration"
    for target in (*_PACKAGES, "tests", "scripts"):
        assert re.search(rf"(?<![\w/]){re.escape(target)}(?![\w/])", cmd), f"{target} left the gate"


def test_the_gate_parser_sees_a_narrowed_scan():
    """Negative control: dropping a directory is reported."""
    narrowed = (
        "      - name: Bandit (high severity + high confidence — GATE)\n"
        "        run: |\n"
        "          bandit -r kazma-core/kazma_core tests -lll -ii\n"
        "      - name: next\n"
    )
    cmd = _bandit_gate_command(narrowed)
    assert "scripts" not in cmd and "kazma-ui/kazma_ui" not in cmd


def _nosec_comments(rel: str, source: str) -> list[tuple[str, int, str]]:
    """Real ``# nosec`` COMMENTS only -- a docstring that mentions one is not a waiver."""
    out: list[tuple[str, int, str]] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return out
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            m = _NOSEC.search(tok.string)
            if m:
                out.append((rel, tok.start[0], m.group("rest")))
    return out


def _nosec_lines() -> list[tuple[str, int, str]]:
    roots = [REPO / p for p in _PACKAGES] + [REPO / "tests", REPO / "scripts"]
    out: list[tuple[str, int, str]] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            out.extend(_nosec_comments(path.relative_to(REPO).as_posix(), text))
    return out


def _unjustified(entries: list[tuple[str, int, str]]) -> list[str]:
    return [f"{rel}:{n}" for rel, n, rest in entries if not _JUSTIFIED.match(rest)]


def test_every_nosec_names_its_rule_and_a_reason():
    entries = _nosec_lines()
    assert entries, "no nosec found at all -- the scanner is not reading the tree"
    bad = _unjustified(entries)
    assert not bad, (
        "A `# nosec` must name the rule(s) and say why: `# nosec B402 - <reason>`. "
        "A bare one waives every check on the line, including ones added later:\n  "
        + "\n  ".join(bad)
    )


def test_the_nosec_check_rejects_bare_and_reasonless_waivers():
    entries = [
        ("a.py", 1, ""),
        ("a.py", 2, " B602"),
        ("a.py", 3, " B602 -"),
        ("a.py", 4, " - because"),
        ("a.py", 5, " B402 - tests the FTP backend against a fake"),
        ("a.py", 6, " B321, B402 - explicit, warned-about fallback"),
    ]
    assert _unjustified(entries) == ["a.py:1", "a.py:2", "a.py:3", "a.py:4"]


def test_only_comments_are_waivers():
    source = 'x = 1  # nosec\ndoc = "text about # nosec in a string"\n'
    assert _nosec_comments("b.py", source) == [("b.py", 1, "")]
