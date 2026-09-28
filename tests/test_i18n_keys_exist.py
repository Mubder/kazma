"""Every catalog key a page names exists, in English and Arabic (2026-09-28).

A template's ``t('memory.pg.x')`` for a key the catalog does not have shows
the raw key in every language; a script's ``tOr('settings.x', 'English')`` or
``kazmaT('chat.x', 'English')`` shows English to an Arabic reader. Both
happened on the live install (``workspace.bookmark_title``,
``x_studio.refresh``, a Knowledge heading whose ``| default`` never applied),
and nothing caught them: the catalog tests (``tests/test_i18n.py``) check that
each KEY has both languages, not that each key a page USES is a key.

Held from the sources: every call in a template or a page script whose first
argument is a whole key of one of the catalog's namespaces --
``t('ns.key')``, ``tOr('ns.key', ...)``, ``_k('ns.key', ...)`` whatever the
helper is called -- names a key the catalog has, in English and Arabic. A key
built at run time (``'settings.category_' + name``) is not a whole literal
and is not checked. Negative control: a synthetic use of a missing key is
caught.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "kazma-ui" / "kazma_ui"

# helper('ns.key' ...) with the literal complete: a comma or ")" follows.
_CALL = re.compile(
    r"""(?<![\w$])([A-Za-z_$][\w$]*)\(\s*(['"])([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)\2\s*[,)]"""
)
# A plural's key is the base of its six CLDR forms (``chat.count_tools`` ->
# ``chat.count_tools_one`` ...); tests/test_chat_i18n_bridge.py holds those.
_PLURAL_HELPERS = {"plural_forms", "t_plural", "tiCount"}
_FILE_SUFFIXES = {"js", "css", "html", "json", "py", "md", "png", "svg", "txt", "yaml", "yml", "db"}


def _sources() -> list[tuple[str, str]]:
    out = []
    for path in sorted((UI / "templates").rglob("*.html")):
        out.append((str(path.relative_to(REPO)), path.read_text(encoding="utf-8")))
    for path in sorted((UI / "static" / "js").rglob("*.js")):
        if path.name.endswith(".min.js") or "vendor" in path.parts or "codemirror" in path.parts:
            continue
        out.append((str(path.relative_to(REPO)), path.read_text(encoding="utf-8")))
    return out


def used_keys(text: str, namespaces: set[str]) -> set[str]:
    keys = set()
    for m in _CALL.finditer(text):
        if m.group(1) in _PLURAL_HELPERS:
            continue
        key = m.group(3)
        if key.split(".", 1)[0] not in namespaces:
            continue
        if key.rsplit(".", 1)[-1] in _FILE_SUFFIXES or key.endswith("_"):
            continue
        keys.add(key)
    return keys


def missing(sources: list[tuple[str, str]], translations: dict) -> list[str]:
    namespaces = {k.split(".", 1)[0] for k in translations}
    out = []
    for name, text in sources:
        for key in sorted(used_keys(text, namespaces)):
            entry = translations.get(key)
            if not entry:
                out.append(f"{name}: {key} is not in the catalog")
            elif not entry.get("en") or not entry.get("ar"):
                out.append(f"{name}: {key} lacks English or Arabic")
    return out


def test_every_key_a_page_names_is_in_the_catalog() -> None:
    from kazma_ui.i18n import TRANSLATIONS

    sources = _sources()
    assert len(sources) > 40, [n for n, _ in sources]
    problems = missing(sources, TRANSLATIONS)
    assert not problems, f"{len(problems)} keys a page names are missing:\n  " + "\n  ".join(problems)


def test_the_scan_sees_the_helpers_pages_use() -> None:
    from kazma_ui.i18n import TRANSLATIONS

    namespaces = {k.split(".", 1)[0] for k in TRANSLATIONS}
    seen = set()
    for _name, text in _sources():
        seen |= used_keys(text, namespaces)
    # Keys named through different helpers in different places.
    for key in ("memory.console.expand_all", "settings.proxy_saved", "memory.pg.link_merge_hint"):
        assert key in seen, key


def test_negative_control_a_missing_key_is_caught() -> None:
    from kazma_ui.i18n import TRANSLATIONS

    sources = [
        ("synthetic.html", "<p>{{ t('memory.pg.no_such_key_2026') }}</p>"),
        ("synthetic.js", "showToast(_k('settings.no_such_key_2026', 'Saved'), 'success');"
                         " tOr('settings.category_' + name, name); _mtData('memory.console.ptype_', v);"
                         " load('memory.js'); t('chat.thinking'); plural_forms('chat.count_tools');"),
    ]
    assert missing(sources, TRANSLATIONS) == [
        "synthetic.html: memory.pg.no_such_key_2026 is not in the catalog",
        "synthetic.js: settings.no_such_key_2026 is not in the catalog",
    ]
