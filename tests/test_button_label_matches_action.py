"""A button reaches the provider its label and its section name (2026-09-28).

The Settings calendar card was titled "Calendar (Google / Outlook)" and had
one "Connect Calendar" button, which only ever started Google's sign-in.
Asked to connect Outlook, the owner pressed it and landed on Google. Two days
of touring the live install had not caught it: nothing compared what a button
says with what it calls.

This gate does, for every ``<button>`` with a click handler in every
template. It follows the handler into the scripts the page loads (its own
``<script src>`` tags, the base layout's and its inline scripts, two calls
deep) and collects the ``/api/...`` routes it reaches; a route naming a
provider (Google, Microsoft, X, Telegram, ...) is that provider's. It fails:

- when the button's label (text, title or aria-label, translated) names a
  provider and the routes reach none of that provider's, only another's;
- when the label names no provider, the section heading above it names
  several, and the routes reach exactly one -- the calendar card's shape.

Negative control: the old calendar card, run through the same checker with
the real Settings scripts, is flagged.
"""

from __future__ import annotations

import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "kazma-ui" / "kazma_ui"
TEMPLATES = UI / "templates"
STATIC_JS = UI / "static" / "js"

#: Words that name a provider in a label, a heading or a route.
PROVIDERS = {
    "google": ("google", "gmail"),
    "microsoft": ("microsoft", "outlook", "msn", "azure", "onedrive"),
    "x": ("twitter", "tweet", "/api/x/", " x studio", " x account", " x connector"),
    "telegram": ("telegram",),
    "discord": ("discord",),
    "slack": ("slack",),
    "github": ("github",),
}


def providers_in(text: str) -> set[str]:
    low = " " + re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "").lower() + " "
    return {p for p, words in PROVIDERS.items() if any(w in low for w in words)}


@lru_cache(maxsize=1)
def _english() -> dict[str, str]:
    from kazma_ui.i18n import TRANSLATIONS

    return {k: (v.get("en") or "") for k, v in TRANSLATIONS.items() if isinstance(v, dict)}


_T_KEY = re.compile(r"""\bt(?:Or)?\(\s*['"]([A-Za-z0-9_.]+)['"]""")


def _words(fragment: str) -> str:
    """A template fragment as the reader sees it: catalog keys translated."""
    en = _english()
    keys = " ".join(en.get(k, "") for k in _T_KEY.findall(fragment or ""))
    return keys + " " + re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", fragment or "")


def _methods(js: str) -> dict[str, list[str]]:
    """``name -> [body, ...]`` for every ``name(...) {`` in *js*."""
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\{", js):
        name = m.group(1)
        if name in ("if", "for", "while", "switch", "catch", "function", "return"):
            continue
        i, depth = m.end(), 1
        while i < len(js) and depth:
            depth += {"{": 1, "}": -1}.get(js[i], 0)
            i += 1
        out.setdefault(name, []).append(js[m.end():i])
    return out


_API = re.compile(r"""['"`](/api/[^'"`]*)""")


def routes_of(handler: str, methods: dict[str, list[str]]) -> set[str]:
    """The /api routes a click handler reaches, following calls two deep."""
    found = set(_API.findall(handler))
    seen: set[str] = set()
    frontier = [handler]
    for _depth in range(3):
        nxt = []
        for code in frontier:
            for call in re.findall(r"([A-Za-z_$][\w$]*)\s*\(", code):
                if call in seen or call not in methods:
                    continue
                seen.add(call)
                for body in methods[call]:
                    found |= set(_API.findall(body))
                    nxt.append(body)
        frontier = nxt
    return found


class _Buttons(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.heading = ""
        self._head: str | None = None
        self._head_buf: list[str] = []
        self._btn: tuple | None = None
        self._btn_buf: list[str] = []
        self.buttons: list[tuple[int, str, str, str]] = []
        self.scripts: list[str] = []
        self._in_script = False
        self._script_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "script":
            if a.get("src"):
                self.scripts.append(a["src"])
            else:
                self._in_script, self._script_buf = True, []
        if tag in ("h2", "h3", "h4", "h5", "summary"):
            self._head, self._head_buf = tag, []
        if tag == "button":
            click = next((v for k, v in a.items() if k.startswith(("@click", "x-on:click"))), "")
            label = " ".join(a.get(k, "") for k in ("title", "aria-label", ":title", ":aria-label", "x-text"))
            self._btn = (self.getpos()[0], click, label, self.heading)
            self._btn_buf = []

    def handle_endtag(self, tag):
        if tag == "script" and self._in_script:
            self.scripts.append("inline:" + "".join(self._script_buf))
            self._in_script = False
        if self._head == tag:
            self.heading = _words(" ".join(self._head_buf))
            self._head = None
        if tag == "button" and self._btn:
            line, click, label, heading = self._btn
            self.buttons.append((line, click, _words(" ".join(self._btn_buf) + " " + label), heading))
            self._btn = None

    def handle_data(self, data):
        if self._in_script:
            self._script_buf.append(data)
        if self._head:
            self._head_buf.append(data)
        if self._btn:
            self._btn_buf.append(data)


def _page_js(parsed: _Buttons, src: str) -> str:
    """The scripts a page runs: its own tags, its inline scripts, and the
    base layout's when it extends it."""
    parts = []
    tags = list(parsed.scripts)
    if "{% extends" in src:
        base = _Buttons()
        base.feed((TEMPLATES / "base.html").read_text(encoding="utf-8"))
        tags += base.scripts
    for tag in tags:
        if tag.startswith("inline:"):
            parts.append(tag[len("inline:"):])
            continue
        name = tag.split("?")[0].rsplit("/static/js/", 1)[-1]
        path = STATIC_JS / name
        if path.is_file() and path.suffix == ".js" and not name.endswith(".min.js"):
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def mismatches(src: str, *, js: str | None = None) -> list[str]:
    """Every button in template *src* whose action and words disagree."""
    parsed = _Buttons()
    parsed.feed(src)
    methods = _methods(js if js is not None else _page_js(parsed, src))
    problems = []
    for line, click, label, heading in parsed.buttons:
        if not click:
            continue
        reached = set().union(*(providers_in(r) for r in routes_of(click, methods))) if methods else set()
        reached |= set().union(*(providers_in(r) for r in _API.findall(click)))
        if not reached:
            continue
        named, section = providers_in(label), providers_in(heading)
        if named and not (named & reached):
            problems.append(f"line {line}: says {sorted(named)}, calls {sorted(reached)} ({click})")
        elif not named and len(section) >= 2 and len(reached) == 1:
            problems.append(
                f"line {line}: in a section for {sorted(section)}, serves only "
                f"{sorted(reached)} and does not say so ({click})"
            )
    return problems


def test_every_button_reaches_what_it_names() -> None:
    found = {}
    for tpl in sorted(TEMPLATES.rglob("*.html")):
        bad = mismatches(tpl.read_text(encoding="utf-8"))
        if bad:
            found[tpl.name] = bad
    assert not found, found


def test_negative_control_the_old_calendar_card_is_caught() -> None:
    settings_js = "\n".join(
        (STATIC_JS / f).read_text(encoding="utf-8") for f in ("settings_integrations.js",)
    )
    old_card = """
      <h4>Calendar (Google / Outlook)</h4>
      <button class="btn btn-primary btn-sm" @click="connectCalendarOAuth()">Connect Calendar</button>
    """
    assert mismatches(old_card, js=settings_js), "the old card must be flagged"
    # The fixed shape passes: the button names its calendar.
    fixed = """
      <h4>Calendar (Google / Outlook)</h4>
      <button class="btn btn-sm" @click="connectCalendarOAuth()">Connect Google Calendar</button>
      <button class="btn btn-sm" @click="connectOutlookCalendar()">Connect Outlook Calendar</button>
    """
    assert mismatches(fixed, js=settings_js) == []
    # And a label naming the wrong provider is caught however the section reads.
    wrong = '<h4>Calendar</h4><button @click="connectCalendarOAuth()">Connect Outlook Calendar</button>'
    assert mismatches(wrong, js=settings_js)
