"""Every link a page builds into the chat names a parameter the chat reads.

(audit 2026-10-26 AUD-031) The Dashboard's session table and its mobile
cards linked ``/chat?session=<id>``; ``chat.js`` reads only ``?s=``, so a
click opened the last chat, not the one clicked. The gate reads the query
parameters the chat page's script reads and every ``/chat?<name>=`` a page
script or template builds.
"""

from __future__ import annotations

import re
from pathlib import Path

_UI = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui"
_CHAT_JS = _UI / "static" / "js" / "chat.js"

# A parameter some page builds that the chat page does not read, with why it
# is left. Never add a session identifier here.
_NOT_READ = {
    "file": "workspace.html's 'open in chat' link; not a chat identifier "
    "(the chat page does not read it -- reported separately)",
}

_LINK = re.compile(r"/chat\?([A-Za-z_]+)=")


def _params_the_chat_reads(src: str) -> set[str]:
    names = set(re.findall(r"""\.get\(\s*['"]([A-Za-z_]+)['"]\s*\)""", src))
    names |= set(re.findall(r"\[\?&\]\)([A-Za-z_]+)=", src))
    return names


def _built_params() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    roots = [_UI / "static" / "js", _UI / "templates"]
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in {".js", ".html"} or "vendor" in path.parts:
                continue
            for name in _LINK.findall(path.read_text(encoding="utf-8")):
                found.setdefault(name, set()).add(path.name)
    return found


def test_the_chat_reads_s_and_not_session() -> None:
    reads = _params_the_chat_reads(_CHAT_JS.read_text(encoding="utf-8"))
    assert "s" in reads and "session" not in reads, reads


def test_every_chat_link_names_a_parameter_the_chat_reads() -> None:
    reads = _params_the_chat_reads(_CHAT_JS.read_text(encoding="utf-8"))
    bad = {
        name: sorted(files)
        for name, files in _built_params().items()
        if name not in reads and name not in _NOT_READ
    }
    assert not bad, f"links into the chat with a parameter it never reads: {bad}"


def test_the_gate_catches_the_old_dashboard_link() -> None:
    """Negative control: the link the Dashboard built before the fix."""
    reads = _params_the_chat_reads(_CHAT_JS.read_text(encoding="utf-8"))
    old = "titleEl.href = '/chat?session=' + encodeURIComponent(s.session_id);"
    assert [n for n in _LINK.findall(old) if n not in reads] == ["session"]
