"""Model failover has its own visible switch in Settings.

Failover is read on its own (``agent.nonstop.failover.enabled`` and its
chain): the chat supervisor, ``resilient_chat``, research and the monthly
failover drill use it whether Non-Stop is on or not. The Settings page showed
its switch and chain only inside ``x-show="nonstop.enabled"``, so turning on a
backup model meant turning on Non-Stop's watchdog too (2026-11-06, setting up
the owner's backup model). The gate: no failover control has an ancestor
shown only when Non-Stop is on. The template before the fix is the negative
control.
"""

from __future__ import annotations

import subprocess
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"
FAILOVER_MODELS = ("nonstop.failover_enabled", "nonstop.failover_chain", "nonstop.failover_cooldown_seconds")
_VOID = {"input", "br", "img", "hr", "meta", "link", "source", "area", "col", "embed", "wbr"}


class _Finder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict[str, str | None]] = []
        self.hidden_under_nonstop: list[str] = []
        self.seen: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        model = attrs_d.get("x-model") or attrs_d.get("x-model.number") or ""
        if model in FAILOVER_MODELS:
            self.seen.append(model)
            if any(a.get("x-show") == "nonstop.enabled" for a in self.stack):
                self.hidden_under_nonstop.append(model)
        if tag not in _VOID:
            self.stack.append(attrs_d)

    def handle_endtag(self, tag):
        if tag not in _VOID and self.stack:
            self.stack.pop()


def _hidden(source: str) -> tuple[list[str], list[str]]:
    finder = _Finder()
    finder.feed(source)
    return finder.seen, finder.hidden_under_nonstop


def test_failover_controls_are_not_behind_the_non_stop_switch():
    seen, hidden = _hidden(TEMPLATE.read_text(encoding="utf-8"))
    assert sorted(seen) == sorted(FAILOVER_MODELS)
    assert hidden == []


def test_negative_control_the_old_template_hid_them():
    old = subprocess.run(["git", "show", "25c80a16d:kazma-ui/kazma_ui/templates/settings.html"],
                         capture_output=True, text=True, encoding="utf-8", cwd=REPO, check=True).stdout
    _, hidden = _hidden(old)
    assert sorted(hidden) == sorted(FAILOVER_MODELS)
