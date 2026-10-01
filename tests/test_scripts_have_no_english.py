"""The interface's scripts write no English a person reads (2026-10-01).

"Every interface word comes from the catalog" (AGENTS.md, UI conventions)
was held for templates (``tests/test_templates_have_no_english.py``) and for
rendered pages (``tests/e2e/test_pages_read_in_arabic.py``), but a toast, a
dialog or a title a script sets after a click is on no page the browser tour
reads. On 2026-10-01, 57 such strings were English in every language: the
chat's steer messages and its "Read aloud" title, the pinned/unpinned
toasts, "Rebuild started", Settings' profile saved/deleted, the Workspace's
pull-request viewer and GitHub dialogs, the voice status line...

The gate reads every script under ``static/js`` and every inline script of
a template with a small JavaScript lexer (strings, template literals,
comments, regex literals), finds what a person reads -- toasts, dialogs,
modals, and text properties (``title``, ``textContent``, ``placeholder``...)
-- and fails on a string in them that is English words and not inside a
translation helper's call. A helper is found from the source (a function
whose first parameter is the key and whose body reads the catalog), so a
page's own ``_k`` or ``tx`` counts. Allowed too: ``helper(key) || 'English'``
and ``S.key || 'English'`` (the catalog has no entry), a branch of a
language test (``isAr ? 'عربي' : 'English'``), and the dead fallback of
``window.t ? window.t(key) : 'English'`` (base.html defines ``window.t``
before any script). Names and acronyms (``tests/_ui_names.py``) are not
words of a language.

Held here too, from the same pass:

- every option a page passes to ``kazmaConfirm`` / ``kazmaPrompt`` /
  ``kazmaAlert`` is one the modal reads -- the Workspace's delete dialogs
  passed ``confirmLabel``, which it ignores, so "Delete files too" read
  "Confirm";
- no template puts ``{{ ... }}`` inside a JavaScript string literal: Jinja's
  HTML escaping is not JavaScript escaping (a newline in a value ended the
  script, ``&`` arrived as ``&amp;``), so a value goes in with ``| tojson``;
- the strings tables two pages pass to their scripts (``__KB_STRINGS``,
  ``__MEM_STRINGS``) hold every ``S.<key>`` the script reads.

Each rule has a negative control below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._ui_names import UI_NAMES

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "kazma-ui" / "kazma_ui"
JS_DIR = UI / "static" / "js"
TEMPLATES = UI / "templates"


# ── a small JavaScript lexer ─────────────────────────────────────────────


@dataclass
class Tok:
    kind: str  # 'str', 'tpl', 'id', 'punct', 'num', 'regex'
    text: str
    line: int


_ID = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUM = re.compile(r"0[xX][0-9a-fA-F]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?")
#: After these a '/' starts a regular expression, not a division.
_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {
    "return", "typeof", "case", "do", "else", "in", "of", "void", "yield", "await",
}


def lex(src: str) -> list[Tok]:
    toks: list[Tok] = []
    i, n, line = 0, len(src), 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += src.count("\n", i, j)
            i = j
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            toks.append(Tok("str", src[i:j + 1], line))
            i = j + 1
            continue
        if c == "`":
            j, depth = i + 1, 0
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if depth == 0 and ch == "`":
                    break
                if src.startswith("${", j):
                    depth += 1
                    j += 2
                    continue
                if depth and ch == "}":
                    depth -= 1
                elif depth and ch == "{":
                    depth += 1
                j += 1
            toks.append(Tok("tpl", src[i:j + 1], line))
            line += src.count("\n", i, j + 1)
            i = j + 1
            continue
        if c == "/":
            prev = toks[-1] if toks else None
            if prev is None or (prev.kind in ("punct", "id") and prev.text in _REGEX_AFTER):
                j, in_class = i + 1, False
                while j < n and src[j] != "\n":
                    ch = src[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    j += 1
                j += 1
                m = _ID.match(src, j)
                if m:
                    j = m.end()
                toks.append(Tok("regex", src[i:j], line))
                i = j
                continue
        m = _ID.match(src, i)
        if m:
            toks.append(Tok("id", m.group(), line))
            i = m.end()
            continue
        m = _NUM.match(src, i)
        if m:
            toks.append(Tok("num", m.group(), line))
            i = m.end()
            continue
        toks.append(Tok("punct", c, line))
        i += 1
    return toks


def matching(toks: list[Tok], k: int) -> int:
    """Index of the bracket that closes ``toks[k]``."""
    open_ = toks[k].text
    close = {"(": ")", "[": "]", "{": "}"}[open_]
    depth = 0
    for j in range(k, len(toks)):
        if toks[j].kind == "punct" and toks[j].text == open_:
            depth += 1
        elif toks[j].kind == "punct" and toks[j].text == close:
            depth -= 1
            if depth == 0:
                return j
    return len(toks) - 1


def value_end(toks: list[Tok], k: int) -> int:
    """Where the expression starting at ``toks[k]`` ends."""
    depth = 0
    for j in range(k, len(toks)):
        t = toks[j]
        if t.kind != "punct":
            continue
        if t.text in "([{":
            depth += 1
        elif t.text in ")]}":
            if depth == 0:
                return j
            depth -= 1
        elif t.text in ";," and depth == 0:
            return j
    return len(toks)


# ── the sources ──────────────────────────────────────────────────────────

_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)(?![^>]*\btype=\"(?:importmap|application/json)\")[^>]*>(.*?)</script>", re.S | re.I)


def page_scripts() -> list[Path]:
    return sorted(
        f for f in JS_DIR.rglob("*.js")
        if "vendor" not in f.parts and not f.name.endswith(".min.js")
    )


def template_script(path: Path) -> str:
    """A template's inline scripts, Jinja blanked, line numbers kept."""
    html = path.read_text(encoding="utf-8")
    text = ""
    for m in _INLINE_SCRIPT.finditer(html):
        body = m.group(1)
        body = re.sub(r"\{\{.*?\}\}", lambda x: "0" + "\n" * x.group().count("\n"), body, flags=re.S)
        body = re.sub(r"\{[%#].*?[%#]\}", lambda x: "\n" * x.group().count("\n"), body, flags=re.S)
        text += "\n" * max(0, html.count("\n", 0, m.start(1)) - text.count("\n")) + body + "\n;\n"
    return text


def all_sources() -> list[tuple[str, str]]:
    out = [(f.relative_to(UI).as_posix(), f.read_text(encoding="utf-8")) for f in page_scripts()]
    out += [(f.relative_to(UI).as_posix(), template_script(f)) for f in sorted(TEMPLATES.rglob("*.html"))]
    return out


# ── translation helpers, found from the source ──────────────────────────

_HELPER_DEF = re.compile(
    r"function\s+([A-Za-z_$][\w$]*)\s*\(\s*(?:key|k|base)\s*,"
    r"|([A-Za-z_$][\w$]*)\s*[:=]\s*(?:function\s*)?\(\s*(?:key|k|base)\s*,"
    r"|^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\(\s*(?:key|k|base)\s*,",
    re.M,
)
#: A helper's body reads the catalog (or calls a helper that does).
_READS_CATALOG = re.compile(r"kazmaT|[A-Z_]*I18N\b|window\.t\b|\btOr\b|\bti\(|_STRINGS|\.tr\(")
#: Defined in base.html for every page.
_GLOBAL_TRANSLATORS = {"t", "tOr", "kazmaT"}
#: Strings tables a page hands its script (lookups fall back to English).
_TABLES = {"S", "I18N", "CHAT_I18N", "KAZMA_I18N", "DASH_I18N", "__KB_STRINGS", "__MEM_STRINGS", "__DASH_MEM_I18N"}


def translation_helpers(sources: list[tuple[str, str]]) -> set[str]:
    names = set(_GLOBAL_TRANSLATORS)
    for _name, src in sources:
        for m in _HELPER_DEF.finditer(src):
            helper = m.group(1) or m.group(2) or m.group(3)
            if _READS_CATALOG.search(src, m.end(), m.end() + 700):
                names.add(helper)
    return names


# ── what is English ──────────────────────────────────────────────────────

_WORD = re.compile(r"[A-Za-z]{2,}")


def _text_of(tok: Tok) -> str:
    body = tok.text[1:-1]
    if tok.kind == "tpl":
        body = re.sub(r"\$\{[^}]*\}", " ", body)
    body = re.sub(r"\{[A-Za-z_]+\}", " ", body)  # {name} placeholders
    body = re.sub(r"<[^>]*>|<[^>]*$|^[^<]*>", " ", body)  # markup, not text
    return body


def is_english(tok: Tok) -> bool:
    text = _text_of(tok).strip()
    words = [w for w in _WORD.findall(text) if w not in UI_NAMES and not w.isupper()]
    if not any(len(w) >= 3 for w in words):
        return False
    return " " in text or bool(re.match(r"[A-Z][a-z]", text))


def _is_catalog_fallback(toks: list[Tok], j: int, helpers: set[str]) -> bool:
    """``helper(key) || 'English'`` or ``S.key || 'English'``."""
    if j < 2 or not (toks[j - 1].text == "|" and toks[j - 2].text == "|"):
        return False
    k = j - 3
    if k >= 0 and toks[k].text == ")":
        depth = 0
        while k >= 0:
            if toks[k].text == ")":
                depth += 1
            elif toks[k].text == "(":
                depth -= 1
                if depth == 0:
                    break
            k -= 1
        ids = {tok.text for tok in toks[max(0, k - 2):j - 2] if tok.kind == "id"}
        return bool(ids & helpers)
    ids = {tok.text for tok in toks[max(0, j - 8):j - 2] if tok.kind == "id"}
    return bool(ids & _TABLES)


def _is_language_branch(toks: list[Tok], j: int) -> bool:
    """A branch of a language test, or the dead fallback of ``window.t ?``."""
    window = toks[max(0, j - 12):j]
    ids = {tok.text for tok in window if tok.kind == "id"}
    if ids & {"isAr", "isArabic"}:
        return True
    if ids & {"KAZMA_LANG", "lang"} and any(tok.kind == "str" and tok.text[1:-1] == "ar" for tok in window):
        return True
    if j and toks[j - 1].text == ":":
        depth = 0
        for k in range(j - 2, max(0, j - 40), -1):
            tx = toks[k].text
            if tx in ")]}":
                depth += 1
            elif tx in "([{":
                if depth == 0:
                    return False
                depth -= 1
            elif tx == "?" and depth == 0:
                test = toks[max(0, k - 8):k]
                return any(tok.kind == "id" and tok.text in _GLOBAL_TRANSLATORS for tok in test)
    return False


def english_in(toks: list[Tok], lo: int, hi: int, helpers: set[str]) -> list[Tok]:
    out: list[Tok] = []
    j = lo
    while j < hi:
        t = toks[j]
        if t.kind == "id" and t.text in helpers and j + 1 < hi and toks[j + 1].text == "(":
            j = matching(toks, j + 1) + 1
            continue
        if t.kind in ("str", "tpl") and is_english(t):
            nxt = toks[j + 1] if j + 1 < len(toks) else None
            prv = toks[j - 1] if j else None
            is_key = nxt is not None and nxt.text == ":" and (prv is None or prv.text in "{,")
            if not is_key and not _is_catalog_fallback(toks, j, helpers) and not _is_language_branch(toks, j):
                out.append(t)
        j += 1
    return out


# ── where a person reads it ──────────────────────────────────────────────

#: Calls whose argument a person reads.
_CALL_SINKS = {"showToast", "toast", "kazmaAlert", "kazmaConfirm", "kazmaPrompt", "showModal", "alert", "confirm", "prompt"}
#: Calls whose whole argument (an options object) a person reads.
_WHOLE_ARGUMENT = {"kazmaAlert", "kazmaConfirm", "kazmaPrompt", "showModal"}
#: Properties a person reads.
_TEXT_PROPS = {"title", "textContent", "innerText", "placeholder", "ariaLabel", "alt"}
_TEXT_ATTRS = {"title", "aria-label", "placeholder", "alt"}


def english_at_sinks(src: str, helpers: set[str]) -> list[Tok]:
    toks = lex(src)
    found: list[Tok] = []
    for k, t in enumerate(toks):
        span = None
        nxt = toks[k + 1].text if k + 1 < len(toks) else ""
        prv = toks[k - 1].text if k else ""
        if t.kind == "id" and t.text in _CALL_SINKS and nxt == "(":
            if prv == "." and t.text in ("alert", "confirm", "prompt") and toks[k - 2].text != "window":
                continue
            if prv == "function":
                continue
            close = matching(toks, k + 1)
            span = (k + 2, close if t.text in _WHOLE_ARGUMENT else min(close, value_end(toks, k + 2)))
        elif t.kind == "id" and t.text in _TEXT_PROPS and prv == "." and nxt == "=" and (k + 2 >= len(toks) or toks[k + 2].text != "="):
            span = (k + 2, value_end(toks, k + 2))
        elif (
            t.kind == "id" and t.text == "setAttribute" and nxt == "("
            and k + 3 < len(toks) and toks[k + 2].kind == "str"
            and toks[k + 2].text[1:-1] in _TEXT_ATTRS and toks[k + 3].text == ","
        ):
            span = (k + 4, value_end(toks, k + 4))
        if span:
            found.extend(english_in(toks, span[0], span[1], helpers))
    return found


# ── the gates ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def sources() -> list[tuple[str, str]]:
    return all_sources()


@pytest.fixture(scope="module")
def helpers(sources) -> set[str]:
    return translation_helpers(sources)


def test_the_scan_reads_the_scripts(sources, helpers) -> None:
    names = {name for name, _src in sources}
    assert "static/js/chat.js" in names and "templates/workspace.html" in names
    # The helpers the pages use, found from their definitions.
    assert {"ti", "tiFmt", "_k", "_mt", "tx", "_tx", "_tr"} <= helpers, sorted(helpers)


def test_no_script_writes_english_a_person_reads(sources, helpers) -> None:
    english = [
        f"{name}:{tok.line}: {tok.text[:90]}"
        for name, src in sources
        for tok in english_at_sinks(src, helpers)
    ]
    assert not english, (
        "Text a person reads is written in English here, whatever the page "
        "language. Put it in the catalog (kazma_ui/i18n/catalog, both "
        "languages) and read it through the page's helper -- ti()/tiFmt() on "
        "the chat page, _k()/kazmaT() elsewhere:\n" + "\n".join(english)
    )


@pytest.mark.parametrize("snippet", [
    "showToast('Profile saved', 'success');",
    "KS.toast(pinned ? 'Session pinned' : 'Session unpinned', 'success');",
    "btn.title = mine ? 'Stop reading' : 'Read aloud';",
    "showToast(`Profile \"${name}\" saved`, 'success');",
    "await window.kazmaConfirm({ title: 'Merge pull request', message: m });",
    "if (!await confirm('Delete this research result?')) return;",
    "el.setAttribute('aria-label', 'Close panel');",
    "showModal({ title: 'Pull Request #' + n, body: '<p>' + x + '</p>' });",
])
def test_negative_control_english_is_caught(snippet, helpers) -> None:
    assert english_at_sinks(snippet, helpers), snippet


@pytest.mark.parametrize("snippet", [
    "showToast(_k('settings.saved', 'Profile saved'), 'success');",
    "KS.toast(ti('session_pinned', 'Session pinned'), 'success');",
    "showToast(window.t ? window.t('settings.agent_saved') : 'Agent settings saved');",
    "toast((S.page_ingested || 'Ingested 1 page.').replace('{n}', n));",
    "var t1 = isAr ? 'تبديل اللغة؟' : 'Switch language?'; kazmaConfirm({ title: isAr ? 'تبديل' : 'Switch language?' });",
    "showToast(data.error, 'error');",
    "showToast('STT: ' + stt, 'info');",
    "btn.title = 'Kazma';",
])
def test_negative_control_translated_text_passes(snippet, helpers) -> None:
    assert not english_at_sinks(snippet, helpers), snippet


# ── dialog options ───────────────────────────────────────────────────────

#: What modules/stores.js reads from each dialog's options.
DIALOG_OPTIONS = {
    "kazmaConfirm": {"title", "message", "confirmText", "cancelText", "danger", "checkbox"},
    "kazmaPrompt": {"title", "message", "label", "placeholder", "defaultValue", "confirmText", "cancelText"},
    "kazmaAlert": {"title", "message", "size", "okText", "variant"},
}


def unread_dialog_options(src: str) -> list[str]:
    toks = lex(src)
    bad: list[str] = []
    for k, t in enumerate(toks):
        if not (t.kind == "id" and t.text in DIALOG_OPTIONS and k + 2 < len(toks)
                and toks[k + 1].text == "(" and toks[k + 2].text == "{"):
            continue
        close = matching(toks, k + 2)
        depth = 0
        for j in range(k + 3, close):
            tj = toks[j]
            if tj.text in "([{":
                depth += 1
            elif tj.text in ")]}":
                depth -= 1
            elif (depth == 0 and tj.kind in ("id", "str") and toks[j + 1].text == ":"
                  and toks[j - 1].text in "{,"):
                key = tj.text.strip("'\"")
                if key not in DIALOG_OPTIONS[t.text]:
                    bad.append(f"{t.text}({{{key}: ...}}) line {tj.line}")
    return bad


def test_dialog_options_are_ones_the_modal_reads(sources) -> None:
    stores = (JS_DIR / "modules" / "stores.js").read_text(encoding="utf-8")
    for name, keys in DIALOG_OPTIONS.items():
        for key in keys - {"danger"}:
            assert re.search(r"opts\." + key + r"\b", stores), f"stores.js no longer reads {name} {key}"
    bad = [f"{name}: {item}" for name, src in sources for item in unread_dialog_options(src)]
    assert not bad, (
        "the modal ignores these options (modules/stores.js), so the dialog "
        "shows its default instead: " + "; ".join(bad)
    )


def test_negative_control_an_ignored_option_is_caught() -> None:
    src = "await window.kazmaConfirm({ title: t('a'), confirmLabel: t('b'), danger: true });"
    assert unread_dialog_options(src) == ["kazmaConfirm({confirmLabel: ...}) line 1"]


# ── template values in scripts ───────────────────────────────────────────

_QUOTED_EXPR = re.compile(r"""(["'`])\{\{(.*?)\}\}\1""", re.S)


def quoted_template_values(html: str) -> list[str]:
    return [
        m.group(0)[:80]
        for block in _INLINE_SCRIPT.findall(html)
        for m in _QUOTED_EXPR.finditer(block)
    ]


def test_no_template_quotes_a_value_inside_a_script() -> None:
    bad = {
        f.name: found
        for f in sorted(TEMPLATES.rglob("*.html"))
        if (found := quoted_template_values(f.read_text(encoding="utf-8")))
    }
    assert not bad, (
        "a template value inside a JavaScript string: Jinja's HTML escaping is "
        "not JavaScript escaping. Write {{ value | tojson }} with no quotes: "
        f"{bad}"
    )


def test_negative_control_a_quoted_value_is_caught() -> None:
    html = "<script>var I = { x: \"{{ t('a.b') }}\", y: {{ t('a.c') | tojson }} };</script>"
    assert quoted_template_values(html) == ["\"{{ t('a.b') }}\""]


# ── strings tables ───────────────────────────────────────────────────────

#: Script -> (template, table) for the pages that hand their script a table.
STRINGS_TABLES = {
    "kb.js": ("knowledge_base.html", "__KB_STRINGS"),
    "memory.js": ("memory.html", "__MEM_STRINGS"),
}


def table_keys(html: str, table: str) -> set[str]:
    start = html.index(f"window.{table}")
    block = html[start:html.index("};", start)]
    return set(re.findall(r"^\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s*:", block, re.M))


def keys_read(src: str) -> set[str]:
    return set(re.findall(r"\bS\.([A-Za-z_][A-Za-z0-9_]*)", src))


@pytest.mark.parametrize("script", sorted(STRINGS_TABLES))
def test_a_strings_table_holds_every_key_its_script_reads(script) -> None:
    template, table = STRINGS_TABLES[script]
    src = (JS_DIR / script).read_text(encoding="utf-8")
    assert re.search(r"window\." + table, src), f"{script} no longer reads window.{table}"
    held = table_keys((TEMPLATES / template).read_text(encoding="utf-8"), table)
    missing = sorted(keys_read(src) - held)
    assert not missing, f"{script} reads S.{missing} that {template} does not hand it: English in Arabic"


def test_negative_control_a_missing_table_key_is_caught() -> None:
    html = "<script>window.__KB_STRINGS = {\n  a: {{ t('x') | tojson }},\n};</script>"
    assert keys_read("toast(S.a || 'A'); toast(S.b || 'B');") - table_keys(html, "__KB_STRINGS") == {"b"}
