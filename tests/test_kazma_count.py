"""A count a page script builds reads right in both languages (2026-10-02).

Settings -> MCP printed "1 أدوات": ``(s.tool_count || 0) + ' أدوات'``, a
number glued to a word in one form. Scripts now say a count through
``window.kazmaCount(key, n, vars)`` (``base.html``): the catalog's forms of
*key* (``<key>.zero`` ... ``<key>.other``, ``t_plural``'s convention), the
one ``KazmaFormat.pluralCategory`` picks.

Held here: the shared fixture is the catalog's forms and ``t_plural``'s
answers (node runs the real function over it, ``tests/js/test_kazma_count.js``),
and every key a template or script names through ``kazmaCount`` has all six
forms in English and Arabic. Negative control: a key with a missing form.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from kazma_ui.i18n import PLURAL_CATEGORIES, TRANSLATIONS, t_plural

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "kazma-ui" / "kazma_ui"
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "i18n" / "kazma_count.json").read_text(encoding="utf-8"))

_CALL = re.compile(r"""kazmaCount\(\s*(['"])([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)\1""")


def test_the_fixture_is_the_catalog():
    for key, entry in FIXTURE["catalog"].items():
        assert key in TRANSLATIONS, key
        assert entry == {"en": TRANSLATIONS[key]["en"], "ar": TRANSLATIONS[key]["ar"]}, key


def test_the_fixture_answers_are_t_plural_s():
    """The server's rule and the browser's give one answer."""
    for case in FIXTURE["cases"]:
        assert t_plural(case["key"], case["n"], case["lang"], **case["vars"]) == case["expect"], case


def _sources() -> list[tuple[str, str]]:
    out = [(str(p.relative_to(ROOT)), p.read_text(encoding="utf-8")) for p in sorted((UI / "templates").rglob("*.html"))]
    for path in sorted((UI / "static" / "js").rglob("*.js")):
        if path.name.endswith(".min.js") or "vendor" in path.parts or "codemirror" in path.parts:
            continue
        out.append((str(path.relative_to(ROOT)), path.read_text(encoding="utf-8")))
    return out


def missing_forms(sources: list[tuple[str, str]], translations: dict) -> list[str]:
    problems = []
    for name, text in sources:
        for m in _CALL.finditer(text):
            key = m.group(2)
            for category in PLURAL_CATEGORIES:
                entry = translations.get(f"{key}.{category}") or {}
                if not entry.get("en") or not entry.get("ar"):
                    problems.append(f"{name}: {key}.{category}")
    return problems


def test_every_count_a_page_names_has_every_form():
    sources = _sources()
    named = {m.group(2) for _n, text in sources for m in _CALL.finditer(text)}
    assert {"mcp.tool_count", "settings.int.mcp_tools_found"} <= named, named
    problems = missing_forms(sources, TRANSLATIONS)
    assert not problems, "kazmaCount keys without all six forms in both languages:\n  " + "\n  ".join(problems)


def test_negative_control_a_missing_form_is_caught():
    catalog = {f"x.widgets.{c}": {"en": "{n} widgets", "ar": "{n} أداة"} for c in PLURAL_CATEGORIES}
    del catalog["x.widgets.two"]
    assert missing_forms([("synthetic.js", "kazmaCount('x.widgets', n)")], catalog) == ["synthetic.js: x.widgets.two"]
