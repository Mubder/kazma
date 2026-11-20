"""A page asks a Development route only where Development is offered.

Spaces S1-20 refuses the Development routes (``route_scopes.DEVELOPMENT_ROUTE_RULES``)
in a space that has no Development (403 ``host_only``). The chat page asked
``/api/workspaces`` on every load, so in a sandboxed space every chat page
logged two or three 403s, and the two-tabs browser test failed on Linux CI
from 2026-11-20 (it was not run before the push). The chat page now reads
``data-development`` on ``#chat-root`` (``development_here()``) first.

The rule, read from the source: a script or template that names a Development
route is a Development page's own (``workspace.html``, ``ide.html`` and the
script only it loads), or reads ``data-development`` before it asks.
Negative control: the chat page as it was.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "kazma-ui" / "kazma_ui"

#: The Development pages and the scripts only they load.
DEVELOPMENT_PAGES = {
    "kazma-ui/kazma_ui/templates/workspace.html",
    "kazma-ui/kazma_ui/templates/ide.html",
    "kazma-ui/kazma_ui/static/js/ide.js",
}

_ROUTE = re.compile(r"""['"`]/api/(?:workspaces?|git|bookmarks|ide)(?:[/'"`?]|$)|['"`]/api/github/repos/clone""")


def _naming(text: str) -> bool:
    return bool(_ROUTE.search(text))


def _breaks_the_rule(rel: str, text: str) -> bool:
    return _naming(text) and rel not in DEVELOPMENT_PAGES and "data-development" not in text


def _ui_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "kazma-ui/kazma_ui/static/js", "kazma-ui/kazma_ui/templates"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.endswith((".js", ".html"))]


def test_every_development_route_is_asked_only_where_offered() -> None:
    wrong = [rel for rel in _ui_files()
             if _breaks_the_rule(rel, (ROOT / rel).read_text(encoding="utf-8", errors="replace"))]
    assert wrong == [], wrong


def test_the_route_list_matches_the_development_rules() -> None:
    from kazma_ui.route_scopes import DEVELOPMENT_ROUTE_RULES

    for path in ("/api/workspaces", "/api/workspace/files", "/api/git/status", "/api/bookmarks",
                 "/api/ide/files", "/api/github/repos/clone"):
        assert _naming(f"'{path}'"), path
        assert any(re.match(rule, path) for rule in DEVELOPMENT_ROUTE_RULES), path


def test_control_the_chat_page_as_it_was() -> None:
    old = "  function refreshWorkspaceId() {\n    fetch('/api/workspaces')\n"
    assert _breaks_the_rule("kazma-ui/kazma_ui/static/js/chat.js", old)


def test_the_chat_page_is_drawn_with_its_development_flag() -> None:
    template = (UI / "templates" / "chat.html").read_text(encoding="utf-8")
    assert 'data-development="{{ \'1\' if development_here is defined and development_here() else \'\' }}"' in template
