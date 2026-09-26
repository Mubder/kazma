"""Every Alpine directive in every template is valid, and scoped where it runs.

Found on the live install 2026-09-26, touring each page for console errors:

* ``workspace.html`` put icon markup inside a double-quoted ``x-text``:
  ``x-text="item.is_dir ? '<span class="ki" ...`` -- the browser ends the
  attribute at ``class=``, so every file-tree row threw
  "SyntaxError: Invalid or unexpected token" and showed no icon (and
  ``x-text`` would have printed the markup as text anyway).
* ``ide.html`` bound ``:value="s.name"`` on the ``<template x-for="s in
  skills">`` element itself. Alpine evaluates a binding on the template
  element in the PARENT scope, where the loop variable does not exist:
  "ReferenceError: s is not defined" on every IDE load.

Neither shows up until a page renders the element -- the first only when the
workspace has files. These gates read every template instead: the directive
expressions are compiled by node exactly as Alpine compiles them (as an async
function body), and a ``<template x-for>`` may carry only ``x-for`` and its
key. Each has a negative control built from the shape that shipped.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "kazma-ui" / "kazma_ui" / "templates"

#: The attributes Alpine evaluates as JavaScript.
_DIRECTIVE = re.compile(
    r"^(x-(data|init|show|if|for|text|html|model|effect|bind:[\w.-]+|on:[\w.:-]+)"
    r"|:[\w.-]+|@[\w.:-]+)$"
)
#: Jinja is rendered before Alpine sees the page. An expression becomes an
#: identifier (valid wherever a value or a string's contents can go); a
#: statement tag disappears.
_JINJA_EXPR = re.compile(r"\{\{.*?\}\}", re.S)
_JINJA_STMT = re.compile(r"\{%.*?%\}|\{#.*?#\}", re.S)


class _Directives(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[int, str, str]] = []
        self.template_for: list[tuple[int, list[str]]] = []
        self.data_with_init: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line = self.getpos()[0]
        names = [name for name, _ in attrs]
        if tag == "template" and "x-for" in names:
            self.template_for.append((line, names))
        values = {name: (value or "") for name, value in attrs}
        if "x-data" in values and "x-init" in values:
            self.data_with_init.append((line, values["x-init"]))
        for name, value in attrs:
            if value is None or not value.strip() or not _DIRECTIVE.match(name):
                continue
            if name == "x-for":
                value = re.sub(r"^[\s\S]*?\s+(in|of)\s+", "", value)
            self.found.append((line, name, value))


def directives_of(markup: str) -> _Directives:
    parser = _Directives()
    parser.feed(_JINJA_STMT.sub("", _JINJA_EXPR.sub("__jinja__", markup)))
    return parser


_NODE_CHECK = r"""
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const items = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const bad = [];
for (const [i, value] of items.entries()) {
  try { new AsyncFunction('scope', 'with (scope) { return (' + value + '\n) }'); continue; } catch (e) {}
  try { new AsyncFunction('scope', 'with (scope) { ' + value + '\n }'); } catch (e) { bad.push([i, e.message]); }
}
process.stdout.write(JSON.stringify(bad));
"""


def uncompilable(values: list[str]) -> dict[int, str]:
    """Index -> error for each expression node cannot compile."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    proc = subprocess.run(
        [node, "-e", _NODE_CHECK],
        input=json.dumps(values),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=True,
    )
    return {i: msg for i, msg in json.loads(proc.stdout)}


def _templates() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def test_every_template_directive_compiles() -> None:
    sites: list[str] = []
    values: list[str] = []
    for path in _templates():
        rel = path.relative_to(REPO).as_posix()
        for line, name, value in directives_of(path.read_text(encoding="utf-8")).found:
            sites.append(f"{rel}:{line} {name}")
            values.append(value)
    assert len(values) > 1000, f"the scan found only {len(values)} directives"
    bad = uncompilable(values)
    assert not bad, "Alpine directives that do not compile:\n  " + "\n  ".join(
        f"{sites[i]}={values[i][:100]!r}: {msg}" for i, msg in sorted(bad.items())
    )


def test_negative_control_the_shape_that_shipped_is_caught() -> None:
    shipped = (
        """<span x-text="item.is_dir ? '<span class="ki" data-icon="folder"></span>'"""
        """ : fileIcon(item.name)"></span>"""
    )
    fine = """<span x-html="rowIcon(item)" :title="'{{ t('x') }}'"></span>"""
    found = directives_of(shipped).found + directives_of(fine).found
    bad = uncompilable([value for _, _, value in found])
    assert sorted(bad) == [0], (found, bad)


#: What a <template x-for> element may carry. Anything else is evaluated in the
#: parent scope, where the loop variable does not exist.
_TEMPLATE_FOR_ATTRS = {"x-for", ":key", "x-bind:key"}


def template_for_extras(markup: str) -> list[tuple[int, list[str]]]:
    return [
        (line, extra)
        for line, names in directives_of(markup).template_for
        if (extra := [n for n in names if n not in _TEMPLATE_FOR_ATTRS])
    ]


def test_a_template_x_for_carries_only_its_loop_and_key() -> None:
    offenders = []
    for path in _templates():
        for line, extra in template_for_extras(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO).as_posix()}:{line} {extra}")
    assert not offenders, (
        "a binding on a <template x-for> element runs OUTSIDE the loop -- move it "
        "to the element inside:\n  " + "\n  ".join(offenders)
    )


def test_negative_control_an_out_of_loop_binding_is_caught() -> None:
    shipped = """<template x-for="s in skills" :key="s.name" :value="s.name"><option></option></template>"""
    fixed = """<template x-for="s in skills" :key="s.name"><option :value="s.name"></option></template>"""
    assert template_for_extras(shipped) == [(1, [":value"])]
    assert template_for_extras(fixed) == []


#: Alpine 3 calls a component's init() by itself. An x-init that calls it
#: again beside x-data ran every page's init twice: eleven templates did,
#: base.html's app shell among them, so every load fetched twice and every
#: setInterval poller ran twice -- the workspace's GitHub poll too
#: (2026-09-26).
_OWN_INIT_CALL = re.compile(r"(?<![.\w$])init\s*\(\s*\)")


def duplicate_init_calls(markup: str) -> list[int]:
    return [
        line
        for line, expr in directives_of(markup).data_with_init
        if _OWN_INIT_CALL.search(expr)
    ]


def test_no_template_calls_the_init_alpine_already_calls() -> None:
    offenders = []
    for path in _templates():
        for line in duplicate_init_calls(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO).as_posix()}:{line}")
    assert not offenders, (
        'x-init="init()" beside x-data runs the component\'s init() a second '
        "time -- Alpine 3 already calls it:\n  " + "\n  ".join(offenders)
    )


def test_negative_control_a_second_init_call_is_caught() -> None:
    assert duplicate_init_calls('<div x-data="page()" x-init="init()"></div>') == [1]
    assert duplicate_init_calls('<div x-data="page()" x-init="$nextTick(() => init())"></div>') == [1]
    # A different method, or another object's init, is not the component's own.
    assert duplicate_init_calls('<div x-data="page()" x-init="loadAll()"></div>') == []
    assert duplicate_init_calls('<div x-data="page()" x-init="editor.init()"></div>') == []
    assert duplicate_init_calls('<div x-data="page()"></div><span x-init="init()"></span>') == []
