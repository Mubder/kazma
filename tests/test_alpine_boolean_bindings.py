"""A boolean attribute bound by Alpine is bound to a boolean (2026-11-23).

Alpine's ``x-bind`` (the vendored 3.14.8, ``static/js/alpine.min.js``) sets a
boolean attribute -- ``disabled``, ``hidden``, ``required``, ``readonly``,
``open`` and the rest of its own list -- for every value but ``null``,
``undefined`` and ``false``, and before that it turns an ``undefined`` into
``""`` whenever the expression's text holds a ``.`` (for ``x-model``'s
sake). So ``:disabled="busy[p.provider]"`` or ``:disabled="e.protected"``
DISABLES the control while the value is missing, and ``:hidden="x.y"`` hides
it: found building S2-04, where "Use my own key" stayed disabled until
``!!`` was added. (The note in AGENTS.md said "``undefined`` leaves the
button disabled"; measured, a bare ``undefined`` enables it and a dotted one
disables it.)

The gate: every binding of an attribute on Alpine's boolean list (read from
the vendored file itself; ``checked`` and ``selected`` go to the element's
property too, which decides what is shown, and are left out) whose
expression holds a ``.`` is provably a boolean -- a literal, a negation, a
comparison, ``a || b`` with ``b`` one, ``a && b`` and ``a ?? b`` with both,
a conditional with both branches one -- or is declared in :data:`DECLARED`
with why. A name with no ``.`` cannot meet the rule: missing, it stays
``undefined`` and the attribute goes.

Negative controls: planted bindings of each shape are found; the vendored
Alpine still carries the rule (a version without it makes the gate's reason
false, and says so). The behaviour in a real browser is
``tests/e2e/test_alpine_boolean_binding_trap.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "kazma-ui" / "kazma_ui"
ALPINE = UI / "static" / "js" / "alpine.min.js"

#: ``(file, expression)`` -> why the value is always a boolean although the
#: gate cannot see it.
DECLARED: dict[tuple[str, str], str] = {}

#: Bound through the element's property as well as the attribute: the
#: property (``el.checked = ""`` is false) decides what is shown.
_PROPERTY_BOUND = frozenset({"checked", "selected"})

_BINDING = re.compile(r"""(?<![\w-])(?:x-bind)?:([a-zA-Z][\w-]*)((?:\.[\w-]+)*)=("([^"]*)"|'([^']*)')""")
_COMPARE = re.compile(r"===|!==|==|!=|<=|>=|<|>|\binstanceof\b|\bin\b")
_ENTITIES = {"&quot;": '"', "&#39;": "'", "&lt;": "<", "&gt;": ">", "&amp;": "&"}


def _boolean_attributes() -> frozenset[str]:
    """Alpine's own list of boolean attributes, from the vendored file."""
    text = ALPINE.read_text(encoding="utf-8")
    found = re.search(r'new Set\((\["allowfullscreen"[^\]]*\])\)', text)
    assert found, "the vendored Alpine no longer lists its boolean attributes where the gate reads them"
    return frozenset(json.loads(found.group(1))) - _PROPERTY_BOUND


def _scan(expr: str) -> list[tuple[int, int, str]]:
    """(index, depth, char) of each character outside string literals."""
    out: list[tuple[int, int, str]] = []
    depth = 0
    quote = ""
    i = 0
    while i < len(expr):
        char = expr[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = ""
            i += 1
            continue
        if char in "'\"`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        out.append((i, depth, char))
        i += 1
    return out


def _strip(expr: str) -> str:
    expr = expr.strip()
    while expr.startswith("("):
        closing = next((i for i, d, c in _scan(expr) if c == ")" and d == 0), -1)
        if closing != len(expr) - 1:
            break
        expr = expr[1:-1].strip()
    return expr


def _split(expr: str, op: str) -> list[str]:
    """*expr* split at *op* where it stands at the top level."""
    cuts = [i for i, d, _c in _scan(expr) if d == 0 and expr.startswith(op, i)]
    parts, last = [], 0
    for cut in cuts:
        if cut < last:
            continue
        parts.append(expr[last:cut])
        last = cut + len(op)
    parts.append(expr[last:])
    return parts


def _conditional(expr: str) -> tuple[str, str, str] | None:
    """``(test, then, else)`` of a top-level conditional, or None."""
    asks: list[int] = []
    for i, depth, char in _scan(expr):
        if depth != 0:
            continue
        if char == "?" and not expr.startswith(("??", "?."), i) and (i == 0 or expr[i - 1] != "?"):
            asks.append(i)
        elif char == ":" and asks:
            ask = asks.pop()
            if not asks:
                return expr[:ask], expr[ask + 1:i], expr[i + 1:]
    return None


def is_boolean(expr: str) -> bool:
    """Whether *expr* always evaluates to a boolean (or ``null``)."""
    expr = _strip(expr)
    if not expr:
        return False
    conditional = _conditional(expr)
    if conditional:
        return is_boolean(conditional[1]) and is_boolean(conditional[2])
    either = _split(expr, "||")
    if len(either) > 1:
        # A truthy left side is returned as it is, and sets the attribute as
        # it should; a falsy one hands over to the right.
        return is_boolean(either[-1])
    for op in ("??", "&&"):
        parts = _split(expr, op)
        if len(parts) > 1:
            return all(is_boolean(p) for p in parts)
    if expr in ("true", "false", "null"):
        return True
    if expr.startswith("!") and not expr.startswith("!="):
        return True
    top = "".join(c if d == 0 else " " for _i, d, c in _scan(expr)).replace("=>", "  ")
    return bool(_COMPARE.search(top))


def _decoded(expr: str) -> str:
    for entity, char in _ENTITIES.items():
        expr = expr.replace(entity, char)
    return expr


def traps(sources: dict[str, str], names: frozenset[str]) -> list[str]:
    """``file:line :name="expr"`` for each boolean-attribute binding whose
    expression holds a ``.`` and is not provably a boolean."""
    found: list[str] = []
    for rel, text in sources.items():
        for match in _BINDING.finditer(text):
            name = match.group(1).lower()
            if name not in names:
                continue
            expr = _decoded(match.group(4) if match.group(4) is not None else match.group(5))
            if "." not in expr or is_boolean(expr) or (rel, expr) in DECLARED:
                continue
            line = text.count("\n", 0, match.start()) + 1
            found.append(f'{rel}:{line} :{name}="{expr}"')
    return found


def _product_sources() -> dict[str, str]:
    """Every template, and every page script but the vendored ones."""
    found: dict[str, str] = {}
    for path in [*(UI / "templates").rglob("*.html"), *(UI / "static" / "js").rglob("*.js")]:
        if "vendor" in path.parts or path.name.endswith(".min.js"):
            continue
        found[path.relative_to(REPO).as_posix()] = path.read_text(encoding="utf-8")
    return found


def test_every_dotted_boolean_binding_is_a_boolean() -> None:
    found = traps(_product_sources(), _boolean_attributes())
    assert not found, (
        "A boolean attribute bound to a value that may be undefined: Alpine turns a dotted undefined "
        "into \"\" and sets the attribute (a missing value disables the control). Bind a boolean "
        "(!!value) or declare it in DECLARED with why:\n  " + "\n  ".join(found))


def test_every_declaration_still_names_a_binding() -> None:
    sources = _product_sources()
    stale = [f"{rel}: {expr}" for rel, expr in DECLARED
             if rel not in sources or expr not in _decoded(sources[rel])]
    assert not stale, stale


def test_the_vendored_alpine_still_carries_the_rule() -> None:
    """The gate's reason, read from the file the pages load: an undefined
    from a dotted expression becomes "", and a boolean attribute is removed
    only for null, undefined and false."""
    text = ALPINE.read_text(encoding="utf-8")
    assert re.search(r'===void 0&&typeof \w+=="string"&&\w+\.match\(/\\\./\)&&\(\w+=""\)', text)
    assert "[null,void 0,!1].includes(" in text


# ── negative controls ───────────────────────────────────────────────────

NAMES = frozenset({"disabled", "hidden", "required", "readonly", "open"})


def _found(binding: str) -> bool:
    return bool(traps({"planted.html": f"<button {binding}></button>"}, NAMES))


def test_control_planted_traps_are_found() -> None:
    for binding in (
        ':disabled="busy[p.provider]"',
        ':disabled="e.protected"',
        ':hidden="state.loaded"',
        ':disabled="a.ready && b"',
        ':disabled="(page + 1) * 20 >= list.length || busy"',
        ':disabled="x.y ?? false"',
        ':disabled="ok ? x.y : false"',
        'x-bind:disabled="row.busy"',
        ':disabled.camel="row.busy"',
        ":disabled='row.busy'",
    ):
        assert _found(binding), binding


def test_control_booleans_pass() -> None:
    for binding in (
        ':disabled="!!busy[p.provider]"',
        ':disabled="!e.protected"',
        ':disabled="x.count === 0"',
        ':disabled="a.ready || !b.c"',
        ':disabled="loadStates.x ? loadStates.x.loading === true : false"',
        ':disabled="busy"',
        ':disabled="fn(a)"',
        ':checked="row.on"',
        ':title="row.hint"',
        ':disabled="!!(a.b && c.d)"',
        ':disabled="(page + 1) * 20 >= list.length || !!busy"',
    ):
        assert not _found(binding), binding
