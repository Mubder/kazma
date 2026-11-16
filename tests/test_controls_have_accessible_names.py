"""Every form control on every page has a name a screen reader can read.

SonarCloud's first accessibility pass over the web templates (2026-11-16) found
479 issues; read against the code and measured in Chromium's accessibility
tree, 383 were real. Nearly all were one shape, written by hand field after
field: ``<div class="form-group"><label>Name</label><input ...></div>``. The
label is shown, but nothing ties it to the control, so a screen reader hears
"edit text" -- or the placeholder's example value ("us-east-1",
"my-server") -- and every select is an unnamed "combo box". Toggles nested a
second ``<label>`` inside the first (invalid HTML), the offsite backup radios had
no name, the chat apps' switches all read "Enable this platform adapter", and the
Settings skill toggle had no name at all while its twin on the Skills page was
already fixed.

This gate renders every page the app's own navigation links to (the list the
browser tour in ``tests/e2e/test_pages_load_clean.py`` walks), through the
real app, and reads each page's HTML:

* every ``input`` / ``select`` / ``textarea`` a person can reach has a name
  from something other than a placeholder: an ``aria-label``, an
  ``aria-labelledby`` that resolves, a ``title``, a ``<label for>`` naming its
  id (``:for`` / ``:id`` bound to the same expression inside a loop), or a
  wrapping ``<label>`` with text (static, or an ``x-text`` / ``x-html``);
* every ``<label>`` names a control: it has ``for`` / ``:for`` or wraps one;
* no ``<label>`` sits inside another ``<label>``;
* a ``<label for>`` names an id that is on the page once, and not inside an
  ``x-for`` loop (a static id there repeats at run time);
* every heading has text, static or bound.

A placeholder never counts: it vanishes once text is typed, and on most
fields it is an example value, not the field's name. A page added to the
navigation is read the day it is linked, and must be at zero. The browser tour
checks the same thing in Chromium's own accessibility tree, after Alpine has
run (``name_problems``).
"""

from __future__ import annotations

import re
from functools import lru_cache
from html.parser import HTMLParser

import pytest

CONTROLS = {"input", "select", "textarea"}
LABELABLE = CONTROLS | {"button", "meter", "output", "progress"}
SKIP_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class _Node:
    __slots__ = ("tag", "attrs", "line", "parent", "children", "text")

    def __init__(self, tag: str, attrs: list, line: int, parent: _Node | None) -> None:
        self.tag = tag
        self.attrs = {k: (v if v is not None else "") for k, v in attrs}
        self.line = line
        self.parent = parent
        self.children: list[_Node] = []
        self.text: list[str] = []

    def ancestors(self):
        p = self.parent
        while p is not None:
            yield p
            p = p.parent

    def descendants(self):
        for c in self.children:
            yield c
            yield from c.descendants()


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", [], 0, None)
        self.cur = self.root
        self.nodes: list[_Node] = []

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.getpos()[0], self.cur)
        self.cur.children.append(node)
        self.nodes.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        node = _Node(tag, attrs, self.getpos()[0], self.cur)
        self.cur.children.append(node)
        self.nodes.append(node)

    def handle_endtag(self, tag):
        p = self.cur
        while p is not None and p.tag != tag:
            p = p.parent
        if p is not None and p.parent is not None:
            self.cur = p.parent

    def handle_data(self, data):
        self.cur.text.append(data)


def _bound(node: _Node, name: str) -> str:
    """The value of ``name``, ``:name`` or ``x-bind:name`` (bound ones marked)."""
    for key in (name, f":{name}", f"x-bind:{name}"):
        value = node.attrs.get(key)
        if value is not None and value.strip():
            return value.strip() if key == name else ":" + value.strip()
    return ""


def _hidden(node: _Node) -> bool:
    if "hidden" in node.attrs:
        return True
    style = node.attrs.get("style", "").replace(" ", "").lower()
    return "display:none" in style


def _text_of(node: _Node) -> bool:
    """Whether ``node`` shows text: its own, or a bound ``x-text`` / ``x-html``.

    A control's own subtree (a select's options) is not the label's text, and
    neither is anything marked ``aria-hidden``.
    """
    stack = [node]
    while stack:
        n = stack.pop()
        if n is not node and (n.tag in CONTROLS or n.attrs.get("aria-hidden") == "true"):
            continue
        if any(t.replace("\xa0", " ").strip() for t in n.text):
            return True
        if n.attrs.get("x-text", "").strip() or n.attrs.get("x-html", "").strip():
            return True
        stack.extend(n.children)
    return False


def _in_loop(node: _Node) -> bool:
    return any(a.tag == "template" and "x-for" in a.attrs for a in node.ancestors())


def _describe(node: _Node) -> str:
    keys = ("id", ":id", "x-model", "name", "type", ":value", "class")
    parts = [f'{k}="{node.attrs[k][:60]}"' for k in keys if node.attrs.get(k)]
    return f"<{node.tag} {' '.join(parts)}> (line {node.line})"


def problems(html: str) -> list[str]:
    """Every reason a control, label or heading in ``html`` fails the rules."""
    tree = _Tree()
    tree.feed(html)
    tree.close()
    nodes = tree.nodes
    ids: dict[str, list[_Node]] = {}
    for n in nodes:
        if n.attrs.get("id"):
            ids.setdefault(n.attrs["id"], []).append(n)
    labels = [n for n in nodes if n.tag == "label"]
    static_for = {lab.attrs["for"]: lab for lab in labels if lab.attrs.get("for")}
    bound_for = {
        _bound(lab, "for")[1:]: lab for lab in labels if _bound(lab, "for").startswith(":")
    }
    found: list[str] = []

    for n in nodes:
        if n.tag not in CONTROLS:
            continue
        if n.tag == "input" and n.attrs.get("type", "text").lower() in SKIP_INPUT_TYPES:
            continue
        if _hidden(n):
            continue
        if _bound(n, "aria-label") or _bound(n, "title"):
            continue
        labelled_by = _bound(n, "aria-labelledby")
        if labelled_by.startswith(":"):
            continue
        if labelled_by:
            missing = [i for i in labelled_by.split() if i not in ids]
            if not missing:
                continue
            found.append(f"{_describe(n)}: aria-labelledby names ids not on the page: {missing}")
            continue
        own_id = n.attrs.get("id", "")
        if own_id and own_id in static_for and _text_of(static_for[own_id]):
            continue
        bound_id = _bound(n, "id")
        if bound_id.startswith(":") and bound_id[1:] in bound_for:
            continue
        wrapper = next((a for a in n.ancestors() if a.tag == "label"), None)
        if wrapper is not None and _text_of(wrapper):
            continue
        why = "named only by its placeholder" if _bound(n, "placeholder") else "has no name"
        found.append(f"{_describe(n)}: {why}")

    for lab in labels:
        if any(a.tag == "label" for a in lab.ancestors()):
            found.append(f"{_describe(lab)}: a <label> inside another <label>")
            continue
        target = lab.attrs.get("for", "")
        if target:
            holders = ids.get(target, [])
            if not holders:
                found.append(f"{_describe(lab)}: for=\"{target}\" names no element on the page")
            elif len(holders) > 1:
                found.append(f"{_describe(lab)}: for=\"{target}\" names an id the page holds {len(holders)} times")
            elif holders[0].tag not in LABELABLE:
                found.append(f"{_describe(lab)}: for=\"{target}\" names a <{holders[0].tag}>, not a control")
            elif _in_loop(holders[0]):
                found.append(f"{_describe(lab)}: for=\"{target}\" names a static id inside an x-for loop")
            continue
        if _bound(lab, "for"):
            continue
        if not any(d.tag in LABELABLE for d in lab.descendants()):
            found.append(f"{_describe(lab)}: a <label> that names no control")

    for n in nodes:
        if n.tag in HEADINGS and not _text_of(n) and not _bound(n, "aria-label"):
            found.append(f"{_describe(n)}: a heading with no text")
    return found


# ── The pages, rendered through the real app ───────────────────────────────

_HREF = re.compile(r'<a\b[^>]*\bhref="(/[^"]*)"')


@lru_cache(maxsize=1)
def _rendered_pages() -> dict[str, str]:
    from fastapi.testclient import TestClient
    from kazma_ui.app import create_app

    client = TestClient(create_app(), client=("127.0.0.1", 40000), base_url="http://127.0.0.1:9090")
    chat = client.get("/chat")
    assert chat.status_code == 200, chat.status_code
    paths = sorted({
        h.split("?")[0].split("#")[0] for h in _HREF.findall(chat.text)
    })
    paths = [p for p in paths if len(p) > 1 and not p.startswith(("/static", "/api"))]
    pages: dict[str, str] = {}
    for path in paths:
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 200, (path, resp.status_code)
        pages[path] = resp.text
    return pages


def test_the_page_list_is_the_navigation():
    pages = _rendered_pages()
    assert len(pages) >= 10 and "/settings" in pages and "/chat" in pages, sorted(pages)


def test_every_control_on_every_page_has_a_name():
    found = []
    for path, html in _rendered_pages().items():
        found += [f"{path}: {p}" for p in problems(html)]
    assert not found, (
        f"{len(found)} control(s), label(s) or heading(s) a screen reader cannot name:\n  "
        + "\n  ".join(found)
    )


# ── Negative controls: each shape the gate exists for fails it ─────────────

_FAILS = {
    "a label beside its control, no for/id": (
        '<div class="form-group"><label>Region</label>'
        '<input type="text" x-model="p.region" placeholder="us-east-1"></div>'
    ),
    "a select with a label beside it": (
        '<div class="form-group"><label>Transport</label>'
        '<select x-model="s.transport"><option>stdio</option></select></div>'
    ),
    "a placeholder alone": '<input type="text" placeholder="Search sessions">',
    "a toggle label with nothing in it": (
        '<label class="toggle"><input type="checkbox" x-model="on"><span class="toggle-slider"></span></label>'
    ),
    "a toggle label nested in a text label": (
        '<label style="display:flex"><span>Enable</span><label class="toggle">'
        '<input type="checkbox" x-model="on"><span class="toggle-slider"></span></label></label>'
    ),
    "a radio named by a sibling <strong>": (
        '<div><input type="radio" name="p" value="a"><strong>Google Drive</strong></div>'
    ),
    "a label for an id that is not there": '<label for="nope">Name</label><input id="other" aria-label="x">',
    "a static id inside a loop": (
        '<template x-for="r in rows"><div><label for="row-val">Value</label>'
        '<input id="row-val" x-model="r.v"></div></template>'
    ),
    "a label for an id the page holds twice": (
        '<label for="dup">A</label><input id="dup"><input id="dup" aria-label="B">'
    ),
    "a caption label over a group": (
        '<div class="form-group"><label class="form-label">Workers</label>'
        '<label><input type="checkbox" value="a"> Auto</label></div>'
    ),
    "a heading filled by nothing": "<h3></h3>",
}

_PASSES = {
    "label for/id": '<label for="f-a">Name</label><input id="f-a" type="text">',
    "bound for/id in a loop": (
        "<template x-for=\"(r, i) in rows\"><div><label :for=\"'row-' + i\">Value</label>"
        "<input :id=\"'row-' + i\"></div></template>"
    ),
    "a wrapping label with bound text": (
        '<label><input type="checkbox"><span x-text="S.archived"></span></label>'
    ),
    "aria-label": '<input type="text" aria-label="Search sessions" placeholder="Search sessions">',
    "a bound aria-label": "<select :aria-label=\"t('x.role')\"><option>a</option></select>",
    "aria-labelledby": '<h2 id="t">Title</h2><input aria-labelledby="t">',
    "a file input the page hides": '<input type="file" style="display:none"><button>Choose</button>',
    "a fieldset legend over named controls": (
        '<fieldset><legend>Workers</legend><label><input type="checkbox"> Auto</label></fieldset>'
    ),
    "a heading with bound text": "<h3 x-text=\"t('a.b')\"></h3>",
}


@pytest.mark.parametrize("name", sorted(_FAILS))
def test_negative_control_each_shape_fails(name):
    assert problems(_FAILS[name]), f"the gate passed {name!r}"


@pytest.mark.parametrize("name", sorted(_PASSES))
def test_each_named_shape_passes(name):
    assert problems(_PASSES[name]) == [], problems(_PASSES[name])


def test_negative_control_the_old_mcp_form_fails():
    """The MCP add-server form as it was before 2026-11-16."""
    old = """
    <div class="form-group">
        <label>Name</label>
        <input type="text" x-model="newServer.name" class="form-input" placeholder="my-server">
    </div>
    <div class="form-group">
        <label>Transport</label>
        <select x-model="newServer.transport" class="form-select"><option value="stdio">stdio</option></select>
    </div>"""
    assert len(problems(old)) == 4  # two unnamed controls, two labels naming nothing
