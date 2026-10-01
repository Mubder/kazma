"""A script never reuses its translation helper's name (2026-10-02).

The Swarm page's active-task panel rendered a running task it had not
started (another tab, a chat, a page refresh) inside
``tasks.forEach(function(t) { ... t('swarm.workers_label_inline') ... })``:
the parameter ``t`` was the task, the call threw "t is not a function", and
the card never appeared. The Dashboard's ``tr()`` was lost the same way to
``var tr = createElement('tr')`` (2026-09-28).

A script that calls a translation helper does not declare that name as a
parameter or a variable anywhere, even where nothing inside calls it -- the
next edit that adds a translated word there is the crash. Helpers are found
as the English-words gate finds them (``tests/test_scripts_have_no_english``).
"""

from __future__ import annotations

from tests.test_scripts_have_no_english import Tok, all_sources, lex, matching, translation_helpers

_DECLARE = {"var", "let", "const"}


def _called(toks: list[Tok]) -> set[str]:
    """Names this script calls as functions (not as methods)."""
    out: set[str] = set()
    for i in range(len(toks) - 1):
        tok, nxt = toks[i], toks[i + 1]
        if tok.kind == "id" and nxt.kind == "punct" and nxt.text == "(":
            if i and toks[i - 1].kind == "punct" and toks[i - 1].text == ".":
                continue
            out.add(tok.text)
    return out


def _params(toks: list[Tok], open_idx: int) -> list[Tok]:
    """The names a parameter list declares (defaults and rest included)."""
    close = matching(toks, open_idx)
    names: list[Tok] = []
    depth = 0
    for j in range(open_idx + 1, close):
        tok = toks[j]
        if tok.kind == "punct" and tok.text in "([{":
            depth += 1
        elif tok.kind == "punct" and tok.text in ")]}":
            depth -= 1
        elif depth == 0 and tok.kind == "id":
            prev = toks[j - 1]
            if prev.kind == "punct" and prev.text in ("(", ",", "..."):
                names.append(tok)
    return names


def _arrow_at(toks: list[Tok], k: int) -> bool:
    """``=>`` starts at ``toks[k]`` (the lexer reads it as ``=`` then ``>``)."""
    return (
        k + 1 < len(toks)
        and toks[k].kind == "punct" and toks[k].text == "="
        and toks[k + 1].kind == "punct" and toks[k + 1].text == ">"
    )


def shadowing(src: str, helpers: set[str]) -> list[str]:
    """``line name how`` for each declaration of a helper name the script calls."""
    toks = lex(src)
    used = helpers & _called(toks)
    found: list[str] = []
    for i, tok in enumerate(toks):
        if tok.kind != "id":
            continue
        after = toks[i + 1] if i + 1 < len(toks) else None
        if tok.text in _DECLARE and after is not None and after.kind == "id" and after.text in used:
            found.append(f"{after.line} {after.text} variable")
            continue
        method = i and toks[i - 1].kind == "punct" and toks[i - 1].text == "."
        if tok.text in ("function", "catch") and not method:
            j = i + 1
            if tok.text == "function" and j < len(toks) and toks[j].kind == "id":
                j += 1  # a named function: its own name is its definition
            if j < len(toks) and toks[j].kind == "punct" and toks[j].text == "(":
                for name in _params(toks, j):
                    if name.text in used:
                        found.append(f"{name.line} {name.text} parameter")
        if tok.text in used and _arrow_at(toks, i + 1):
            found.append(f"{tok.line} {tok.text} parameter")
    # Parenthesised arrow parameters: ( ... ) =>
    for i, tok in enumerate(toks):
        if tok.kind == "punct" and tok.text == "(":
            close = matching(toks, i)
            if _arrow_at(toks, close + 1):
                for name in _params(toks, i):
                    if name.text in used:
                        found.append(f"{name.line} {name.text} parameter")
    return sorted(set(found), key=lambda s: (int(s.split()[0]), s))


def test_no_script_reuses_its_translators_name():
    sources = all_sources()
    helpers = translation_helpers(sources)
    problems = [
        f"{name}:{hit}" for name, src in sources for hit in shadowing(src, helpers)
    ]
    assert not problems, (
        "A script declares the name of a translation helper it calls; inside "
        "that scope the call reaches the variable, not the helper:\n  "
        + "\n  ".join(problems)
    )


def test_a_reused_translator_name_is_caught():
    """Negative control: the Swarm page's old loop, a for-of and an arrow."""
    src = (
        "function t(key, vars) { return window.kazmaT(key); }\n"
        "tasks.forEach(function(t) { return t('swarm.x'); });\n"
        "for (const t of xs) {}\n"
        "xs.map((a, t) => a);\n"
        "fetch(u).catch(function () { t('swarm.y'); });\n"
        "obj.items.forEach(function(task) { t('swarm.z'); });\n"
    )
    assert shadowing(src, {"t"}) == ["2 t parameter", "3 t variable", "4 t parameter"]


def test_a_script_that_never_calls_the_helper_may_use_the_name():
    """A file without translated words can name a loop variable ``t``."""
    assert shadowing("xs.forEach(function(t) { return t.id; });\n", {"t"}) == []
