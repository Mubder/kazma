"""A journal gap resumes from the server's head, on every cursor a page keeps.

Live 2026-10-16 11:16-11:22 UTC: the owner sent a task, opened Memory and came
back. The server ran the turn on and paused it for an approval, but the page
showed it dead -- "Reconnecting..." with nothing arriving -- and a refresh did
not help; no approval reached the server and the watchdog auto-denied it.

The turn journal restarts at seq 1 on every server restart (and after an hour
idle), so the cursor a browser saved in localStorage is AHEAD of it. The page
attached with it, the server answered GAP and named its head, and the gap
handler cleared only ``_lastSeqSeen``; the next attach took
``max(_lastSeqSeen, _pageDeliveryCursor())`` -- the saved cursor again -- until
the reopen budget ran out (while paused, with no budget, it looped). The
socket's gap branch never corrected the saved cursor either, so every
reconnect sent the same ``?last_seq=``.

``KazmaDeliveryCursor.adoptHead`` is the one answer both mouths use. Browser
proof: ``tests/e2e/test_return_to_running_turn.py``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_JS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js"
_CURSOR_JS = _JS / "modules" / "delivery_cursor.js"
_CHAT_JS = _JS / "chat.js"
_AGENT_JS = _JS / "stores" / "agentStore.js"

_NODE_PRELUDE = (
    "global.window = { localStorage: { _m:{}, getItem(k){return this._m[k] ?? null;}, "
    "setItem(k,v){this._m[k]=String(v);} } };\n"
    "const fs = require('fs');\n"
    "eval(fs.readFileSync(process.argv[2], 'utf8'));\n"
    "const K = window.KazmaDeliveryCursor;\n"
)

# The cursor chat.js's `_attachJournal` attaches with:
# max(_lastSeqSeen, _pageDeliveryCursor()), where _pageDeliveryCursor() is the
# socket tracker's last seq when > 0, else the saved cursor when > 0, else 0.
_NEXT_ATTACH = (
    "function pageCursor(t, sid){ const live = Number(t.last()); if (live > 0) return live;"
    " const kept = Number(K.loadPersisted(sid)); return kept > 0 ? kept : 0; }\n"
    "function nextAttach(lastSeqSeen, t, sid){ return Math.max(lastSeqSeen || 0, pageCursor(t, sid)); }\n"
)


def _node(tmp_path: Path, body: str) -> list:
    if shutil.which("node") is None:
        pytest.skip("node not available")
    harness = tmp_path / "cursor_gap_harness.js"
    harness.write_text(_NODE_PRELUDE + _NEXT_ATTACH + body, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), str(_CURSOR_JS)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_adopt_head_saves_the_head_and_moves_the_tracker(tmp_path):
    out = _node(tmp_path, (
        "K.persist('s', 5000);\n"
        "const t = K.createTracker(); t.observeSeq(4999);\n"
        "const r = K.adoptHead('s', 7, t);\n"
        "const after = [r, K.loadPersisted('s'), t.last(), t.observeSeq(8)];\n"
        "const t0 = K.createTracker(); t0.observeSeq(12);\n"
        "const z = [K.adoptHead('s', 0, t0), K.loadPersisted('s'), t0.last()];\n"
        "const bad = [K.adoptHead('s', 'nonsense'), K.adoptHead('s', -3)];\n"
        "console.log(JSON.stringify([after, z, bad]));\n"
    ))
    assert out == [[7, 7, 7, "ok"], [0, 0, None], [0, 0]]


def test_after_a_gap_the_next_attach_uses_the_head_not_the_saved_cursor(tmp_path):
    """The page saved 5000 before a server restart; the journal's head is 7."""
    out = _node(tmp_path, (
        "K.persist('s', 5000);\n"
        "// fixed gap handler: _adoptJournalHead -> adoptHead, _lastSeqSeen = head\n"
        "const t = K.createTracker();\n"
        "const lastSeqSeen = K.adoptHead('s', 7, t);\n"
        "const fixed = nextAttach(lastSeqSeen, t, 's');\n"
        "// the old gap handler, as the negative control: `_lastSeqSeen = 0` only\n"
        "K.persist('s2', 5000);\n"
        "const old = nextAttach(0, K.createTracker(), 's2');\n"
        "console.log(JSON.stringify([fixed, old]));\n"
    ))
    fixed, old = out
    assert fixed == 7
    # The control reproduces the incident: the stale cursor wins the max,
    # is ahead of the head, and the server answers GAP again.
    assert old == 5000 and old > 7


def _branch(src: str, opener: str, length: int = 900) -> str:
    at = src.find(opener)
    assert at != -1, f"branch not found: {opener!r}"
    return src[at: at + length]


def _gap_branches_adopt_the_head(chat_src: str, agent_src: str) -> list[str]:
    """Every place a page handles a server gap must adopt the named head."""
    problems: list[str] = []
    resync = _branch(chat_src, "if (status === 'resync') {")
    if "_adoptJournalHead(" not in resync:
        problems.append("chat.js: the SSE `resync` branch does not adopt the head")
    m = re.search(r"function _adoptJournalHead\(head\) \{(.*?)\n  \}", chat_src, re.S)
    if not m or "KazmaDeliveryCursor.adoptHead(" not in m.group(1):
        problems.append("chat.js: _adoptJournalHead does not go through KazmaDeliveryCursor.adoptHead")
    gap = _branch(agent_src, "if (r.gap) {")
    if "KazmaDeliveryCursor.adoptHead(" not in gap:
        problems.append("agentStore.js: the socket's `r.gap` branch does not adopt the head")
    return problems


def test_every_gap_branch_adopts_the_head():
    chat = _CHAT_JS.read_text(encoding="utf-8")
    agent = _AGENT_JS.read_text(encoding="utf-8")
    assert _gap_branches_adopt_the_head(chat, agent) == []


def test_the_gate_fails_on_the_old_gap_handlers():
    """Negative control: the handlers as they were before 2026-10-16."""
    old_chat = (
        "        if (status === 'resync') {\n"
        "          _lastSeqSeen = 0;\n"
        "          _resyncDelivery('sse-gap');\n"
        "          return;\n"
        "        }\n"
    )
    old_agent = (
        "        if (r.gap) {\n"
        "          try {\n"
        "            window.KazmaChat.resync('resume-gap');\n"
        "          } catch (e) { /* ignore */ }\n"
        "        }\n"
    )
    problems = _gap_branches_adopt_the_head(old_chat, old_agent)
    assert len(problems) == 3, problems
