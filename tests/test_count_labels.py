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
import unicodedata
from pathlib import Path

import pytest

from kazma_ui.i18n import PLURAL_CATEGORIES, TRANSLATIONS, plural_forms, t_plural

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "i18n" / "count_labels.json").read_text(encoding="utf-8"))
CHAT_JS = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
BASES = (
    "count_tools", "count_steps", "count_approvals", "count_requests", "count_results",
    "plan_locked", "preparing_n_tools", "hitl_allow_n", "hitl_allow_n_title", "session_msgs",
    "hitl_wants_to_run_n",
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
#: chat.js tiCount. Any placeholder name counts: "{nodes} nodes · {links}
#: beliefs" read "200 عقد · 295 معتقدات" on the live memory page after the
#: first pass, which looked only for {n} and {count}. Nor only a plural right
#: after it: "{count} worker(s)", "{n} danger tools", "{chunks} new chunks" and
#: "{dbs} DBs" were twenty more on 2026-10-03, their Arabic in one form too
#: ("{count} عامل نشط"). Up to two words may stand between the count and its
#: noun ("{n} failed queue tasks"); the first word after it that ends in "s"
#: is taken as the noun.
_SINGLE_FORM = re.compile(
    r"\{([a-z_]+)\}\s+((?:[A-Za-z-]+(?<!s)\s+){0,2})([A-Za-z]+(?:\(s\)|s))(?![A-Za-z(])"
)
#: Words after a placeholder that are not a counted noun ("{name} is off").
_NOT_A_NOUN = frozenset({"is", "was", "as", "has", "does", "its", "this", "us"})
#: Placeholders that hold a name, an id, a path, a size, a sum of money, a
#: position or a duration: what follows them is not a counted noun, in either
#: language ("{name} uses tools", "{name} مُعطَّل").
_NOT_A_COUNT = frozenset({
    "amount", "boost", "d", "end", "error", "id", "library", "mb", "member", "model",
    "name", "names", "number", "path", "protocol", "provider", "root", "s", "scanner",
    "shown", "size", "source", "start", "target", "tool", "v", "x", "y",
})


def _counted_noun(word: str) -> bool:
    """A plural or a hedged plural; "progress", "status" and "analysis" end in s."""
    word = word.lower().replace("(s)", "s")
    return word not in _NOT_A_NOUN and not word.endswith(("ss", "us", "is"))


def single_form_count_labels() -> list[str]:
    return sorted(
        key for key, entry in TRANSLATIONS.items()
        if key.rsplit(".", 1)[-1] not in PLURAL_CATEGORIES
        and any(
            m.group(1) not in _NOT_A_COUNT and _counted_noun(m.group(3))
            for m in _SINGLE_FORM.finditer((entry or {}).get("en") or "")
        )
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
    # The shipped memory-graph header, under other placeholder names.
    monkeypatch.setitem(TRANSLATIONS, "x.stats", {"en": "{nodes} nodes · {links} beliefs", "ar": "{nodes} عقد"})
    assert "x.stats" in single_form_count_labels()
    monkeypatch.setitem(TRANSLATIONS, "x.verb", {"en": "{name} is off", "ar": "{name} مُعطَّل"})
    assert "x.verb" not in single_form_count_labels()
    # The shapes the first two passes missed, as they shipped.
    for key, en in {
        "x.hedge": "Dry-run found {n} item(s) to delete (~{size} reclaimable).",
        "x.adjective": "Ingested 1 page — {chunks} new chunks.",
        "x.two_words": "{n} failed queue task(s)",
        "x.capitals": "Backup complete: {dbs} DBs, {mb} MB",
    }.items():
        monkeypatch.setitem(TRANSLATIONS, key, {"en": en, "ar": ""})
        assert key in single_form_count_labels(), key
    # A singular noun ending in s, and anything after a name, are not counts.
    for key, en in {
        "x.progress": "{n} in progress",
        "x.named": "{name} uses tools",
    }.items():
        monkeypatch.setitem(TRANSLATIONS, key, {"en": en, "ar": ""})
        assert key not in single_form_count_labels(), key
    monkeypatch.setitem(TRANSLATIONS, "x.new_uses", {"en": "{n} new uses", "ar": ""})
    assert "x.new_uses" in single_form_count_labels()


# ── Gate: the Arabic of a count is in plural forms too (2026-10-03) ────────

#: A number followed by an Arabic noun changes with the number, and the English
#: does not always show it: "Cleared {n} failed" was "مُسحت {n} مهام فاشلة"
#: ("مُسحت 1 مهام"), "{n} err" was "{n} خطأ" ("3 خطأ"), "{seconds}s" was
#: "{seconds} ثانية" ("5 ثانية").
_AFTER_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}\s+(\S+)")
#: Arabic words a count may stand before in one form: prepositions and
#: conjunctions, the adjectives of a stat line that names its noun elsewhere
#: ("المهارات: 5 نشطة"), and unit abbreviations ("12 ث"). Compared without
#: harakat.
_AR_SAME_FOR_ANY_COUNT = frozenset({
    "من", "في", "و", "على", "عن", "إلى", "حتى", "تحت", "تحتها", "مع", "أو", "دون",
    "بانتظار", "متوقفة", "سليمة", "معزولة", "مستبدلة", "عاملة", "نشطة", "مؤرشفة",
    "حلقية", "أخرى", "غيرها", "راسب", "ناجح", "متبقية",
    "ث", "م",
})


def _arabic_word(token: str) -> str:
    """The Arabic letters a token starts with, without its harakat."""
    letters = []
    for ch in token:
        if not unicodedata.name(ch, "").startswith("ARABIC"):
            break
        if unicodedata.category(ch) == "Lo":
            letters.append(ch)
    return "".join(letters)


def arabic_single_form_counts() -> list[str]:
    found = []
    for key, entry in TRANSLATIONS.items():
        if key.rsplit(".", 1)[-1] in PLURAL_CATEGORIES:
            continue
        for m in _AFTER_PLACEHOLDER.finditer((entry or {}).get("ar") or ""):
            word = _arabic_word(m.group(2))
            if word and m.group(1) not in _NOT_A_COUNT and word not in _AR_SAME_FOR_ANY_COUNT:
                found.append(f"{key}: {{{m.group(1)}}} {word}")
    return sorted(found)


def test_no_arabic_count_is_in_one_form():
    found = arabic_single_form_counts()
    assert not found, (
        "An Arabic noun after a count changes with the count (\"1 مهام\", "
        "\"3 خطأ\"); write the entry as plural forms (<key>.zero ... <key>.other). "
        "A word that does not change belongs in _AR_SAME_FOR_ANY_COUNT, a "
        "placeholder that is not a count in _NOT_A_COUNT:\n  " + "\n  ".join(found)
    )


def test_negative_control_an_arabic_single_form_count_is_caught(monkeypatch):
    for key, ar in {
        "x.cleared": "مُسحت {n} مهام فاشلة",
        "x.errors": "{n} خطأ",
        "x.seconds": "تجاوز الميزانية — {seconds} ثانية حتى الإيقاف",
        "x.workers": "● السرب يعمل — {count} عامل نشط",
    }.items():
        monkeypatch.setitem(TRANSLATIONS, key, {"en": "", "ar": ar})
        assert any(hit.startswith(key + ":") for hit in arabic_single_form_counts()), key
    for key, ar in {
        "x.stat": "{n} نشطة",
        "x.name": "{name} مُعطَّل",
        "x.of": "صفحة {x} من {y}",
        "x.forms.few": "{n} مهام",
    }.items():
        monkeypatch.setitem(TRANSLATIONS, key, {"en": "", "ar": ar})
        assert not any(hit.startswith(key + ":") for hit in arabic_single_form_counts()), key


# ── Gate: no script glues an English word after a value (2026-10-02) ──────

STATIC_JS = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js"
#: ``+ ' chat models discovered'``: an English word appended to a value, out
#: of reach of the catalog (settings_hub.js said it on Arabic pages, and
#: kb.js's crawl toast " · 3 unchanged"). Lines that build markup, classes or
#: log text are not interface words.
_GLUED_WORD = re.compile(r"""\+\s*(['"])\s[a-z]{3,}[a-z ]*\1""")
_NOT_INTERFACE = ("console.", "logger.", "className", "class=", "querySelector", "addEventListener")


def glued_script_words(src: str) -> list[str]:
    return [
        line.strip() for line in src.splitlines()
        if _GLUED_WORD.search(line) and not any(skip in line for skip in _NOT_INTERFACE)
    ]


def test_no_script_glues_an_english_word_to_a_value():
    found = [
        f"{path.relative_to(ROOT).as_posix()}: {hit}"
        for path in sorted(STATIC_JS.rglob("*.js"))
        if not (path.name.endswith(".min.js") or "vendor" in path.parts or "codemirror" in path.parts)
        for hit in glued_script_words(path.read_text(encoding="utf-8"))
    ]
    assert not found, (
        "A value glued to an English word is English on every page and in one "
        "form; use window.kazmaCount('<key>', n) or kazmaT:\n  " + "\n  ".join(found)
    )


def test_negative_control_the_shipped_glued_words_are_caught():
    shipped = (
        "var msg = count + ' chat models discovered';\n"
        "(unchanged ? \" · \" + unchanged + \" unchanged\" : \"\") +\n"
        "console.log('x' + ' count');\n"
    )
    assert glued_script_words(shipped) == [
        "var msg = count + ' chat models discovered';",
        "(unchanged ? \" · \" + unchanged + \" unchanged\" : \"\") +",
    ]
