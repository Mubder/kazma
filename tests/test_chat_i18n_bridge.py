"""Every translation key the chat page's scripts use reaches the page, in
English and Arabic (2026-09-27).

chat.js and agentStore.js read their strings from ``window.CHAT_I18N``, which
``chat.html`` fills from the catalog key by key. A key with no line there shows
its English fallback in every language; on 2026-09-27, 15 did ("Reconnecting",
"Thoughts", "Waiting for the server", "Planning", the retired memory panel's
labels ...). A count read through ``tiCount`` needs a line in the ``plural``
block and all six CLDR forms in the catalog, or Arabic prints the wrong form.

Held from the sources: every ``ti()``/``_ti()``/``tiFmt()`` key the scripts use
is a bridge line; every ``tiCount()`` key a plural line; every catalog key a
line names exists in English and Arabic (a plural key in all six forms).
Negative control: a synthetic key the page never gets is caught.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "kazma-ui" / "kazma_ui" / "static" / "js"
SCRIPTS = (STATIC / "chat.js", STATIC / "stores" / "agentStore.js")
TEMPLATE = REPO / "kazma-ui" / "kazma_ui" / "templates" / "chat.html"

_PLAIN = re.compile(r"\b_?ti(?:Fmt)?\(\s*'([a-z0-9_]+)'")
_COUNT = re.compile(r"\btiCount\(\s*'([a-z0-9_]+)'")
_LINE = re.compile(r"^\s*([a-z0-9_]+):\s*\{\{\s*(t|plural_forms)\('([a-z0-9_.]+)'\)", re.M)


# chat.js's turn header reads its phase label as ti(pair[0], pair[1]) from
# this table; each name is a bridge line like any literal ti() key. Until
# 2026-09-28 they were not, and an Arabic turn ended under "Completed".
_PHASE_TABLE = re.compile(r"var _HEADER_PHASE_LABELS = \{(.*?)\};", re.S)
_PHASE_NAME = re.compile(r"\[\s*'([a-z0-9_]+)'\s*,")


def used_keys(sources: list[str]) -> tuple[set[str], set[str]]:
    plain: set[str] = set()
    counts: set[str] = set()
    for src in sources:
        plain |= set(_PLAIN.findall(src))
        counts |= set(_COUNT.findall(src))
        for table in _PHASE_TABLE.findall(src):
            plain |= set(_PHASE_NAME.findall(table))
    return plain, counts


def bridge(template: str) -> tuple[dict[str, str], dict[str, str]]:
    """``window.CHAT_I18N``'s lines: name -> catalog key, plain and plural."""
    block = template[template.index("window.CHAT_I18N = {"):]
    block = block[:block.index("\n  };")]
    plain: dict[str, str] = {}
    plural: dict[str, str] = {}
    for name, fn, key in _LINE.findall(block):
        (plural if fn == "plural_forms" else plain)[name] = key
    return plain, plural


def missing(sources: list[str], template: str) -> list[str]:
    plain_used, counts_used = used_keys(sources)
    plain, plural = bridge(template)
    out = [f"ti('{k}') has no CHAT_I18N line" for k in sorted(plain_used - set(plain))]
    out += [f"tiCount('{k}') has no plural line" for k in sorted(counts_used - set(plural))]
    return out


def test_every_key_the_scripts_use_reaches_the_page():
    sources = [p.read_text(encoding="utf-8") for p in SCRIPTS]
    assert missing(sources, TEMPLATE.read_text(encoding="utf-8")) == []


def test_every_line_names_a_catalog_key_in_both_languages():
    from kazma_ui.i18n import PLURAL_CATEGORIES, TRANSLATIONS

    plain, plural = bridge(TEMPLATE.read_text(encoding="utf-8"))
    assert len(plain) > 50 and len(plural) >= 8  # the instrument reads the block
    bad = [
        f"{name}: {key}" for name, key in plain.items()
        if not (TRANSLATIONS.get(key, {}).get("en") and TRANSLATIONS.get(key, {}).get("ar"))
    ]
    bad += [
        f"{name}: {key}.{cat}" for name, key in plural.items() for cat in PLURAL_CATEGORIES
        if not (TRANSLATIONS.get(f"{key}.{cat}", {}).get("en") and TRANSLATIONS.get(f"{key}.{cat}", {}).get("ar"))
    ]
    assert bad == [], "a bridge line names a catalog entry that is missing or English-only"


def test_a_key_the_page_never_gets_is_caught():
    """Negative control."""
    template = TEMPLATE.read_text(encoding="utf-8")
    assert missing(["x = ti('never_bridged_key', 'x'); y = tiCount('never_counted', 2, 'a', 'b');",
                    "var _HEADER_PHASE_LABELS = {\n  gone: ['never_bridged_phase', 'Gone'],\n};"],
                   template) == ["ti('never_bridged_key') has no CHAT_I18N line",
                                 "ti('never_bridged_phase') has no CHAT_I18N line",
                                 "tiCount('never_counted') has no plural line"]


def test_the_phase_table_is_read():
    """The scan finds the header's phase names in the real chat.js."""
    plain, _counts = used_keys([(STATIC / "chat.js").read_text(encoding="utf-8")])
    assert {"completed", "approval_required", "interrupted"} <= plain
