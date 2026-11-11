"""No sign-in runs in a popup that talks to its opener (2026-11-11).

The GitHub sign-in opened ``/api/github/oauth/start`` in a new window, and the
callback page answered ``window.opener.postMessage`` to the Workspace page,
which listened for window ``message`` events (AUD-106 checked the origin of
that message). Every Kazma page now sends ``Cross-Origin-Opener-Policy:
same-origin`` (``kazma_ui.security_headers``), which cuts the link between a
page and a window that visits another site -- the provider's sign-in page --
so a popup sign-in would never hear back. Every sign-in now runs in the same
tab: the start is a POST that answers the provider's URL
(``window.kazmaStartSignIn``), and the callback sends the browser back.

This gate reads every product script, template and server-rendered page and
fails on:

* ``window.open`` of a sign-in (an OAuth path, an ``authorize`` /
  ``authorization`` URL, a login page);
* reading ``window.opener``, or posting to an ``opener`` / ``parent`` /
  ``top`` window;
* a page listening for window ``message`` events.

A use with a reason it does not depend on its opener is declared in
:data:`DECLARED`. A declaration that matches nothing fails, as does each
planted shape (the negative controls).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: (file, the line's text) -> why it is not a popup sign-in that needs its opener.
DECLARED: dict[tuple[str, str], str] = {
    ("kazma-ui/kazma_ui/static/js/mcp.js",
     "window.open(result.authorization_url, '_blank', 'noopener');"): (
        "an MCP server's own authorization page: its callback is a loopback "
        "listener on another port that Kazma runs (kazma_core.mcp.oauth, the "
        "RFC 8252 native-app shape), the tab is opened noopener, and nothing "
        "reads or posts to an opener"
    ),
}

_SIGN_IN_TARGET = re.compile(r"oauth|authori[sz]|/login\b|sign-?in", re.IGNORECASE)
_WINDOW_OPEN = re.compile(r"\bwindow\.open\s*\(([^;\n]*)")
#: Prose that names it in a code span (``window.opener``) is not a use.
_OPENER = re.compile(r"(?<!`)\bwindow\s*\.\s*opener\b")
_POST_UP = re.compile(r"\b(?:opener|parent|top)\s*\.\s*postMessage\s*\(")
_MESSAGE_LISTENER = re.compile(
    r"\bwindow\s*\.\s*(?:addEventListener\s*\(\s*['\"]message['\"]|onmessage\s*=)"
)


def _sources() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    keep = []
    for rel in out:
        if not rel.startswith(("kazma-ui/", "kazma-gateway/", "kazma-core/", "kazma-skills/",
                               "kazma-tui/", "kazma-cli/")):
            continue
        if "/tests/" in rel or "/vendor/" in rel or rel.endswith(".min.js"):
            continue
        if rel.endswith((".js", ".html", ".py")):
            keep.append(rel)
    return keep


def findings(sources: dict[str, str]) -> list[str]:
    """``file: line`` for each popup sign-in, opener read or message listener."""
    found: list[str] = []
    for rel, text in sources.items():
        for raw in text.splitlines():
            line = raw.strip()
            if (rel, line) in DECLARED:
                continue
            opened = _WINDOW_OPEN.search(line)
            if opened and _SIGN_IN_TARGET.search(opened.group(1)):
                found.append(f"{rel}: a sign-in in another window: {line[:120]}")
            if (_OPENER.search(line) or _POST_UP.search(line)) \
                    and not line.startswith(("#", "//", "*", "/*")):
                found.append(f"{rel}: talks to its opener: {line[:120]}")
            elif _POST_UP.search(line):
                found.append(f"{rel}: posts to another window: {line[:120]}")
            if _MESSAGE_LISTENER.search(line):
                found.append(f"{rel}: listens for window messages: {line[:120]}")
    return found


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def test_no_product_page_signs_in_through_a_popup() -> None:
    sources = {rel: _read(rel) for rel in _sources()}
    assert len(sources) > 200, "the instrument found the product's sources"
    assert findings(sources) == []


def test_every_declaration_still_matches_a_line() -> None:
    for (rel, line), why in DECLARED.items():
        assert why
        assert line in _read(rel), f"{rel}: the declared line is gone: {line}"


def test_negative_control_each_planted_shape_is_found() -> None:
    planted = {
        "kazma-ui/kazma_ui/templates/planted.html": "\n".join([
            "      window.open('/api/github/oauth/start', '_blank');",
            "      window.addEventListener('message', onMessage);",
        ]),
        "kazma-gateway/kazma_gateway/routers/planted.py": "\n".join([
            "  if (window.opener) {{ try {{ window.opener.postMessage({{ type: 'done' }}, '*'); }} catch(e) {{}} }}",
        ]),
        "kazma-ui/kazma_ui/static/js/planted.js": "\n".join([
            "    window.open(data.authorize_url);",
            "    parent.postMessage({ ok: true }, '*');",
            "    window.onmessage = handler;",
        ]),
    }
    got = findings(planted)
    assert len(got) == 6, got
    # The old GitHub sign-in, as it shipped, is found.
    old = {"kazma-ui/kazma_ui/templates/workspace.html":
           "      window.open('/api/github/oauth/start', '_blank');"}
    assert findings(old)
    # What is not a sign-in in a popup passes: a link opened noopener, a
    # download, a socket's own message events.
    fine = {"kazma-ui/kazma_ui/static/js/fine.js": "\n".join([
        "    window.open(pr.html_url, '_blank');",
        "    window.open('/api/backup/x/download', '_blank');",
        "    socket.addEventListener('message', onFrame);",
        "    // window.opener is gone: the sign-in runs in this tab",
    ])}
    assert findings(fine) == []
