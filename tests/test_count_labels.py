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
BASES = (
    "count_tools", "count_steps", "count_approvals", "count_requests", "count_results",
    "plan_locked", "preparing_n_tools", "hitl_allow_n", "hitl_allow_n_title", "session_msgs",
)


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


# ── Gate: a count is said in its plural form, in templates too (2026-10-02) ──

TEMPLATES = ROOT / "kazma-ui" / "kazma_ui" / "templates"
#: ``{{ <a count> }} {{ t('<a noun>') }}``: the number and a word glued in one
#: form. The MCP card read "1 أدوات" and the Dashboard "5 تتبع" that way.
_GLUED = re.compile(
    r"\{\{\s*(?P<expr>[^{}]*?(?:count|total|length|size|num)[^{}]*?)\s*\}\}"
    r"\s*\{\{\s*t\('(?P<key>[\w.]+)'\)\s*\}\}"
)


#: The same inside an Alpine expression: ``(s.tool_count || 0) + ' {{ t('<a
#: noun>') }}'``. Settings -> MCP read "1 أدوات" that way (2026-10-02); a
#: script says a count through ``kazmaCount`` (tests/test_kazma_count.py).
_GLUED_IN_EXPRESSION = re.compile(
    r"(?P<expr>[\w.()|\s]*?(?:count|total|length|size|num)[\w.()|\s]*?)\+\s*'\s*"
    r"\{\{\s*t\('(?P<key>[\w.]+)'\)\s*\}\}\s*'"
)


def glued_counts(html: str) -> list[str]:
    found = [f"{m.group('expr')} {m.group('key')}" for m in _GLUED.finditer(html)]
    found += [f"{m.group('expr').strip()} {m.group('key')}" for m in _GLUED_IN_EXPRESSION.finditer(html)]
    return found


def test_no_template_glues_a_count_to_a_word():
    found = [
        f"{path.relative_to(ROOT).as_posix()}: {hit}"
        for path in sorted(TEMPLATES.rglob("*.html"))
        for hit in glued_counts(path.read_text(encoding="utf-8"))
    ]
    assert not found, (
        "A count printed beside a translated word is right in one form only. "
        "Use t_plural('<key>', n) with <key>.zero/one/two/few/many/other in "
        "the catalog:\n  " + "\n  ".join(found)
    )


def test_negative_control_the_shipped_card_is_caught():
    shipped = "<span class=\"tool-count\">{{ server.tool_count }} {{ t('mcp.tools_suffix') }}</span>"
    assert glued_counts(shipped) == ["server.tool_count mcp.tools_suffix"]
    assert glued_counts("{{ t_plural('mcp.tool_count', server.tool_count) }}") == []


def test_negative_control_the_shipped_settings_row_is_caught():
    """Settings -> MCP's count before 2026-10-02, inside an Alpine expression."""
    shipped = (
        "<span x-text=\"(s.tool_count || 0) + ' {{ t('settings.tools_count') }}'\"></span>"
    )
    assert glued_counts(shipped) == ["(s.tool_count || 0) settings.tools_count"]
    assert glued_counts("<span x-text=\"kazmaCount('mcp.tool_count', s.tool_count || 0)\"></span>") == []


#: Catalog entries that are a count label in one form ("{n} sessions"): right
#: in English from 2 up and in Arabic from 11 up. None is left: 39 on the
#: morning of 2026-10-02, the last 34 converted that evening and two dead ones
#: removed. A count label is written as <key>.zero ... <key>.other and read
#: through t_plural, plural_forms + KazmaFormat.count, window.kazmaCount, or
#: chat.js tiCount.
_SINGLE_FORM = re.compile(r"\{(?:n|count)\}\s+(?:more\s+)?[a-z]+s\b")


def single_form_count_labels() -> list[str]:
    return sorted(
        key for key, entry in TRANSLATIONS.items()
        if key.rsplit(".", 1)[-1] not in PLURAL_CATEGORIES
        and _SINGLE_FORM.search((entry or {}).get("en") or "")
    )


def test_no_count_label_is_in_one_form():
    found = single_form_count_labels()
    assert not found, (
        "A count label in one form reads wrong for some numbers (\"1 sessions\", "
        "\"1 أدوات\"); write it as plural forms (<key>.zero ... <key>.other):\n  "
        + "\n  ".join(found)
    )


def test_negative_control_a_single_form_label_is_counted(monkeypatch):
    monkeypatch.setitem(TRANSLATIONS, "x.n_widgets", {"en": "{n} widgets", "ar": "{n} أداة"})
    assert "x.n_widgets" in single_form_count_labels()
    monkeypatch.setitem(TRANSLATIONS, "x.widgets.other", {"en": "{n} widgets", "ar": "{n} أداة"})
    assert "x.widgets.other" not in single_form_count_labels()
