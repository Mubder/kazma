"""One way to print a count, in both languages.

Live 2026-09-24 the turn header read "10 tools · 1 approvals"; the thoughts
header built "3 3 tools" (the number prepended to a template that already
had {n}); and one tool rendered as "1 step" through ti('step', 'tool'). Every
count label now goes through chat.js tiCount, which picks the catalog form
(chat.<base>.<category>) with t_plural's CLDR rule.

tests/js/test_count_labels.js reads the same fixture.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kazma_ui.i18n import PLURAL_CATEGORIES, TRANSLATIONS, plural_forms, t_plural

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "i18n" / "count_labels.json").read_text(encoding="utf-8"))
CHAT_JS = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
BASES = ("count_tools", "count_steps", "count_approvals", "count_requests")


@pytest.mark.parametrize("base", BASES)
def test_every_count_label_has_every_form_in_both_languages(base):
    for category in PLURAL_CATEGORIES:
        entry = TRANSLATIONS.get(f"chat.{base}.{category}")
        assert entry and entry.get("en") and entry.get("ar"), f"chat.{base}.{category}"


def test_the_fixture_forms_are_the_catalog_forms():
    for lang, by_base in FIXTURE["forms"].items():
        for base, forms in by_base.items():
            assert forms == plural_forms(f"chat.{base}", lang), (lang, base)


@pytest.mark.parametrize(
    "case", FIXTURE["cases"], ids=lambda c: f"{c['lang']}-{c['base']}-{c['n']}"
)
def test_t_plural_picks_the_same_form(case):
    got = t_plural(f"chat.{case['base']}", case["n"], case["lang"])
    assert got == case["expect"]


def test_the_chat_page_injects_the_forms():
    html = (ROOT / "kazma-ui" / "kazma_ui" / "templates" / "chat.html").read_text(encoding="utf-8")
    for base in BASES:
        assert f"{base}: {{{{ plural_forms('chat.{base}') | tojson }}}}" in html, base


def test_the_rendered_chat_page_carries_the_forms():
    """Through the real app: the template global exists and renders."""
    from fastapi.testclient import TestClient

    from kazma_ui.app import create_app

    resp = TestClient(create_app()).get("/chat")
    assert resp.status_code == 200
    text = resp.text
    assert "count_approvals: {" in text
    assert '"one": "{n} approval"' in text or '"one": "\\u0645\\u0648\\u0627\\u0641\\u0642\\u0629' in text or "موافقة واحدة" in text


# ── Gate: no hand-built count label comes back ──────────────────────────

_RETIRED = [
    (re.compile(r"ti\('step',\s*'tool'\)"), "the key for 'step' used to say 'tool'"),
    (re.compile(r"\+\s*' '\s*\+\s*tiFmt\('summary_tools'"), "the number prepended to '{n} tools'"),
    (re.compile(r"\+\s*' '\s*\+\s*ti\('approvals'"), "'1 approvals': no singular"),
    (re.compile(r"\.replace\(/\^\\d\+\\s\*/"), "a number stripped back off a label"),
    (re.compile(r"\.replace\('\{n\} ',\s*''\)"), "a placeholder stripped off a label"),
    (re.compile(r"ti\('steps?',"), "a step count outside tiCount"),
    (re.compile(r"'(?:one|n)_requests?'"), "a request count outside tiCount"),
]


def _retired_count_labels(src: str) -> list[str]:
    return [why for pattern, why in _RETIRED if pattern.search(src)]


def test_no_hand_built_count_labels():
    found = _retired_count_labels(CHAT_JS.read_text(encoding="utf-8"))
    assert not found, "count labels must go through tiCount: " + "; ".join(found)


def test_the_count_gate_catches_the_old_labels():
    """Negative control (AGENTS.md section 28): the shipped header builder."""
    old = (
        "bits.push(c.tools + ' ' + (c.tools === 1 ? ti('step', 'tool')\n"
        "  : tiFmt('summary_tools', '{n} tools', { n: c.tools }).replace(/^\\d+\\s*/, '')));\n"
        "bits.push(c.gates + ' ' + ti('approvals', 'approvals'));\n"
    )
    assert len(_retired_count_labels(old)) >= 3
