"""Structural guard: no reply-producing path may write history its own way.

The bug class this repo kept re-hitting (2026-08-21, -26, -27, -28) was never
one bad line — it was FIVE hand-rolled implementations of "store the reply",
each reconciling by message position, one per transport. New endpoints kept
being added without one (``/api/approve`` shipped delivering answers it never
saved), and fixing a symptom in one transport left the others intact.

These tests fail the build when that shape comes back, so the invariant is
enforced by CI rather than by remembering. They are deliberately structural:
they read the source of the delivery modules instead of exercising a route,
because the failure mode is *omission* — an endpoint that quietly does
nothing cannot be caught by testing the endpoints that exist today.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

_UI = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui"
_DELIVERY_MODULES = [
    _UI / "sse_chat.py",
    _UI / "routes_direct.py",
    _UI / "routes" / "ws_chat.py",
]


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 1. The sink is the only writer ────────────────────────────────────


def test_no_delivery_module_appends_assistant_rows_by_hand():
    """``messages.append({"role": "assistant" ...})`` is the old shape.

    Every one of those call sites decided for itself whether to append or
    overwrite, which is how two writers produced two rows for one reply and
    how a late writer overwrote the previous turn's answer. Replies go
    through ``reply_sink.upsert_reply``, which keys on the turn.
    """
    offenders: list[str] = []
    pattern = re.compile(
        r"messages\.append\(\s*\{[^}]*[\"']role[\"']\s*:\s*[\"']assistant[\"']",
        re.S,
    )
    for path in _DELIVERY_MODULES:
        for match in pattern.finditer(_src(path)):
            line = _src(path)[: match.start()].count("\n") + 1
            offenders.append(f"{path.name}:{line}")
    assert not offenders, (
        "Assistant rows must be written through reply_sink.upsert_reply so "
        "they carry a turn_id and cannot duplicate or clobber. Offenders: "
        + ", ".join(offenders)
    )


def test_no_delivery_module_overwrites_the_last_assistant_row_positionally():
    """``for m in reversed(messages): if role == 'assistant': m[...] = ...``

    On a turn that had not yet written a row, that loop's target was the
    PREVIOUS turn's answer — the 1,080 -> 151 char history clobber.
    """
    pattern = re.compile(
        r"for\s+\w+\s+in\s+reversed\([^)]*messages[^)]*\)\s*:"
        r"(?:(?!\ndef |\nclass ).){0,400}?"
        r"[\"']assistant[\"'](?:(?!\ndef |\nclass ).){0,200}?"
        r"\[\s*[\"']content[\"']\s*\]\s*=",
        re.S,
    )
    offenders: list[str] = []
    for path in _DELIVERY_MODULES:
        text = _src(path)
        for match in pattern.finditer(text):
            offenders.append(f"{path.name}:{text[: match.start()].count(chr(10)) + 1}")
    assert not offenders, (
        "Positional overwrite of 'the last assistant message' is the history "
        "clobber. Upsert on turn_id instead. Offenders: " + ", ".join(offenders)
    )


# ── 2. Every graph stream carries a session + a turn ──────────────────


def _stream_call_sites() -> list[tuple[str, int, ast.Call]]:
    sites: list[tuple[str, int, ast.Call]] = []
    for path in _DELIVERY_MODULES:
        tree = ast.parse(_src(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name == "_stream_langgraph_events":
                sites.append((path.name, node.lineno, node))
    return sites


def test_every_graph_stream_call_site_passes_session_and_turn():
    """A caller that streams a graph turn must say where to store the reply.

    ``/api/approve`` passed neither: it streamed the post-approval answer to
    the browser and persisted nothing, losing two finished replies on
    2026-08-28 (1,402 and 1,781 chars).
    """
    sites = _stream_call_sites()
    assert sites, "expected at least one production call site"
    missing: list[str] = []
    for module, lineno, call in sites:
        kwargs = {kw.arg for kw in call.keywords}
        if not {"session_id", "reply_turn_id"} <= kwargs:
            missing.append(f"{module}:{lineno} has {sorted(kwargs)}")
    assert not missing, (
        "_stream_langgraph_events must receive session_id and reply_turn_id "
        "or its reply cannot be persisted: " + "; ".join(missing)
    )


def test_streamer_persists_before_announcing_completion():
    """The durable write must precede the terminal ``done`` frame.

    Emitting ``done`` first leaves a window where a user who refreshes the
    instant the answer paints reloads a transcript that does not contain it.
    """
    from kazma_ui import sse_chat

    src = inspect.getsource(sse_chat._stream_langgraph_events)
    persist_at = src.index("_persist_turn_reply(")
    done_at = src.index('emit_j("done"')
    assert persist_at < done_at, (
        "the reply must be stored before the client is told the turn is over"
    )


# ── 3. The sink's own invariants ──────────────────────────────────────


def test_sink_refuses_to_write_without_a_turn_id():
    """No turn id means no way to tell rows apart — refuse rather than guess."""
    from kazma_ui.reply_sink import upsert_reply

    assert upsert_reply("session", "", "text") is False


def test_resume_paths_resolve_rather_than_mint_a_turn():
    """A HITL resume continues a turn; minting a new id splits one answer
    into two bubbles."""
    for path in (_UI / "routes_direct.py", _UI / "sse_chat.py"):
        text = _src(path)
        if "resume_cmd" in text or "resume_input" in text:
            assert "resolve_reply_turn" in text, (
                f"{path.name} builds a resume command but never resolves the "
                "turn it is continuing"
            )
