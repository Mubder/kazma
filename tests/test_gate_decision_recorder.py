"""Every gate decision is written down the same way (kazma_ui/hitl_decision.py).

Live 2026-09-26: an approval the watchdog auto-denied left its transcript part
``pending`` -- the turn reloaded with a dead Approve row and its answer
missing (Test chat, turn 9bdd89fd93cb). The watchdog claimed the registry row
but never stamped the transcript; platform buttons claimed the row but told
neither the transcript nor the journal; the WS approve path recorded nothing.
The approve route did all three, in the order that matters. That order now
lives in one function, and every site that decides a gate calls it.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from kazma_ui import hitl_decision as hd

ROOT = Path(__file__).resolve().parents[1]


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []


@pytest.fixture
def wired(monkeypatch):
    """Replace the three writers with recorders; keep the recorder real."""
    rec = _Recorder()
    import kazma_ui.delivery as delivery
    import kazma_ui.hitl_gate_bridge as bridge
    import kazma_ui.sse_chat._streaming as streaming

    def _stamp(session_id, turn_id, **kw):
        rec.calls.append(("stamp", (session_id, turn_id, kw["state"], kw["interrupt_id"])))

    async def _claimed(thread_id, iid, decision, actor, **kw):
        rec.calls.append(("claim", (thread_id, iid, decision, actor)))

    async def _claimed_for_thread(thread_id, decision, actor, **kw):
        rec.calls.append(("claim_thread", (thread_id, decision, actor)))

    async def _resuming(iid):
        rec.calls.append(("resuming", iid))

    class _Broker:
        async def emit(self, thread_id, frame):
            data = frame.get("data") or {}
            if frame["type"] == "hitl":
                rec.calls.append(("emit", (thread_id, frame["type"], data["state"],
                                           data["interrupt_id"])))
            else:
                rec.calls.append((frame["type"], data))
            return frame

    monkeypatch.setattr(streaming, "stamp_hitl_part_state", _stamp)
    monkeypatch.setattr(bridge, "gate_claimed", _claimed)
    monkeypatch.setattr(bridge, "gate_claimed_for_thread", _claimed_for_thread)
    monkeypatch.setattr(bridge, "gate_resuming", _resuming)
    monkeypatch.setattr(delivery, "get_turn_broker", lambda: _Broker())
    return rec


async def test_transcript_then_registry_then_journal(wired):
    """The broker stamps a frame's view from the registry, so the frame goes
    last: emitted before the CAS it painted "No longer pending" on an
    approved card (2026-09-20)."""
    await hd.record_gate_decision(
        "t1", decision="approved", actor="web:op", tool="file_write",
        payload={"tool": "file_write"}, interrupt_id="g1", session_id="s1", turn_id="u1",
    )
    assert [c[0] for c in wired.calls] == ["stamp", "claim", "resuming", "emit"]
    assert wired.calls[0][1] == ("s1", "u1", "approved", "g1")
    assert wired.calls[1][1] == ("t1", "g1", "approve", "web:op")
    assert wired.calls[3][1] == ("t1", "hitl", "approved", "g1")


@pytest.mark.parametrize("decision, stamped, registry", [
    ("approved", "approved", "approve"),
    ("denied", "denied", "deny"),
    ("timeout", "timeout", "deny"),
])
async def test_each_decision_is_stamped_and_claimed_as_itself(wired, decision, stamped, registry):
    await hd.record_gate_decision("t1", decision=decision, actor="a", interrupt_id="g1",
                                  session_id="s1", turn_id="u1")
    by = dict((k, v) for k, v in wired.calls)
    assert by["stamp"][2] == stamped and by["emit"][2] == stamped
    assert by["claim"][2] == registry


async def test_an_unknown_decision_is_a_programmer_error(wired):
    with pytest.raises(ValueError):
        await hd.record_gate_decision("t1", decision="maybe", actor="a")


async def test_a_platform_button_without_an_id_claims_the_threads_gate(wired, monkeypatch):
    """No id anywhere (no web turn, no registry row): the registry is still
    claimed for the thread, and nothing id-less is written -- an id-less part
    or frame is a stray row beside the real card."""
    monkeypatch.setattr(hd, "_resolve_turn", lambda t, s, u: ("", ""))
    monkeypatch.setattr(hd, "_open_gate_id", lambda t: "")
    await hd.record_gate_decision("gw-telegram-1", decision="denied", actor="telegram:9")
    assert [c[0] for c in wired.calls] == ["claim_thread"]


async def test_a_decision_without_an_id_uses_the_gate_the_card_shows(wired, monkeypatch):
    """The watchdog reads the interrupt from the checkpoint, which carries no
    id: recorded without one, the timeout was a second row next to the card
    (live 2026-09-26, "Approval timed out" + "No longer pending")."""
    import kazma_ui.hitl_status as hs

    monkeypatch.setattr(hs, "persisted_hitl_for_thread",
                        lambda t: {"state": "pending", "interrupt_id": "g-card"})
    used = await hd.record_gate_decision("t1", decision="timeout", actor="watchdog:timeout",
                                         session_id="s1", turn_id="u1")
    assert used == "g-card"
    by = dict((k, v) for k, v in wired.calls)
    assert by["stamp"][3] == "g-card" and by["emit"][3] == "g-card"
    assert by["claim"][1] == "g-card"


def test_the_registry_names_the_gate_when_the_transcript_cannot():
    from kazma_core.safety.hitl_gates import GateRow, register_gate

    register_gate(GateRow(gate_id="g-reg", thread_id="t-reg", tool="file_write"))
    assert hd._open_gate_id("t-reg") == "g-reg"
    # Negative control: another thread's gate is not this thread's.
    assert hd._open_gate_id("t-other") == ""


async def test_one_failing_writer_does_not_stop_the_others(wired, monkeypatch):
    import kazma_ui.sse_chat._streaming as streaming

    def _broken(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(streaming, "stamp_hitl_part_state", _broken)
    await hd.record_gate_decision("t1", decision="approved", actor="a", interrupt_id="g1",
                                  session_id="s1", turn_id="u1")
    assert [c[0] for c in wired.calls] == ["claim", "resuming", "emit"]


# ── no empty bubble for a thread with no open turn ────────────────────────


class _Session:
    def __init__(self, messages):
        self.session_id = "s-gw"
        self.messages = messages


def test_a_minted_turn_id_is_never_stamped(monkeypatch):
    """resolve_reply_turn mints an id when no turn is open; stamping under it
    added an assistant row holding nothing but the card."""
    import kazma_ui.reply_sink as rs
    import kazma_ui.session_manager as sm

    sess = _Session([{"role": "user", "content": "hi"}])
    monkeypatch.setattr(sm, "get_session_manager",
                        lambda: type("M", (), {"get_by_thread_id": lambda self, t: sess})())
    monkeypatch.setattr(rs, "resolve_reply_turn", lambda t, s: "fresh-id")
    assert hd._resolve_turn("gw-1", "", "") == ("s-gw", "")

    # Negative control: the same id on an existing assistant row is used.
    sess.messages.append({"role": "assistant", "turn_id": "fresh-id", "content": ""})
    assert hd._resolve_turn("gw-1", "", "") == ("s-gw", "fresh-id")


# ── the watchdog writes the timeout down (the 2026-09-26 turn) ─────────────


async def test_the_watchdog_stamps_the_transcript_timeout(wired, monkeypatch):
    import kazma_core.safety.commitment.resume as resume_mod
    import kazma_ui.hitl_status as hs
    import kazma_ui.hitl_timeout as wd
    import kazma_ui.reply_sink as reply_sink
    import kazma_ui.sse_chat._streaming as streaming
    import kazma_ui.turn_runtime as tr_mod

    payload = {"interrupt_id": "gwd9", "tool": "python_exec", "kind": "security"}

    async def _read_pending(graph, cfg):
        return dict(payload)

    async def _no_drive(*a, **k):
        return None

    monkeypatch.setattr(resume_mod, "read_pending_interrupt", _read_pending)
    monkeypatch.setattr(resume_mod, "build_resume_command", lambda *a, **k: object())
    monkeypatch.setattr(hs, "is_resume_claimed", lambda t: False)
    monkeypatch.setattr(tr_mod, "ensure_session_for_thread", lambda t: "sess-wd")
    monkeypatch.setattr(reply_sink, "resolve_reply_turn", lambda *a, **k: "turn-wd")
    monkeypatch.setattr(streaming, "_drive_graph_to_journal", _no_drive)
    monkeypatch.setattr(streaming, "mark_thread_unpaused", lambda t: None)

    await wd._auto_deny(object(), "t-wd-9", 300.0)

    by = dict((k, v) for k, v in wired.calls)
    assert by["stamp"] == ("sess-wd", "turn-wd", "timeout", "gwd9")
    assert by["claim"] == ("t-wd-9", "gwd9", "deny", "watchdog:timeout")
    assert by["emit"] == ("t-wd-9", "hitl", "timeout", "gwd9")
    # The toast frame names the same gate, so the page updates THAT card.
    assert by["approval_timeout"]["interrupt_id"] == "gwd9"


async def test_the_watchdog_names_the_card_when_the_checkpoint_does_not(wired, monkeypatch):
    import kazma_core.safety.commitment.resume as resume_mod
    import kazma_ui.hitl_status as hs
    import kazma_ui.hitl_timeout as wd
    import kazma_ui.reply_sink as reply_sink
    import kazma_ui.sse_chat._streaming as streaming
    import kazma_ui.turn_runtime as tr_mod

    async def _read_pending(graph, cfg):
        return {"tool": "python_exec", "kind": "security"}  # no interrupt_id

    async def _no_drive(*a, **k):
        return None

    monkeypatch.setattr(resume_mod, "read_pending_interrupt", _read_pending)
    monkeypatch.setattr(resume_mod, "build_resume_command", lambda *a, **k: object())
    monkeypatch.setattr(hs, "is_resume_claimed", lambda t: False)
    monkeypatch.setattr(hs, "persisted_hitl_for_thread",
                        lambda t: {"state": "pending", "interrupt_id": "23ecd31b"})
    monkeypatch.setattr(tr_mod, "ensure_session_for_thread", lambda t: "sess-wd")
    monkeypatch.setattr(reply_sink, "resolve_reply_turn", lambda *a, **k: "turn-wd")
    monkeypatch.setattr(streaming, "_drive_graph_to_journal", _no_drive)
    monkeypatch.setattr(streaming, "mark_thread_unpaused", lambda t: None)

    await wd._auto_deny(object(), "t-wd-10", 300.0)

    by = dict((k, v) for k, v in wired.calls)
    assert by["stamp"][3] == "23ecd31b" and by["emit"][3] == "23ecd31b"
    assert by["approval_timeout"]["interrupt_id"] == "23ecd31b"


# ── the class gate: every decision site goes through the recorder ─────────

#: Sites that decide a gate without the recorder, and why that is right.
EXEMPT: dict[str, str] = {
    "kazma-core/kazma_core/cli/ask.py":
        "kazma ask runs the graph in-process for a terminal: no web session, "
        "no gate registry, no journal to tell",
    "kazma-core/kazma_core/agent/hitl_supersede.py":
        "denies a checkpoint interrupt only when the registry says the gate is "
        "NOT pending (settled or aborted): the decision was already recorded",
}

PACKAGES = ("kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway", "kazma-core/kazma_core")


def _decision_sites_without_recorder(source: str) -> list[str]:
    """Functions that build a resume with ``approved=`` but never record it."""
    tree = ast.parse(source)
    missing = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        decides = any(
            (getattr(c.func, "id", None) or getattr(c.func, "attr", None)) == "build_resume_command"
            and any(k.arg == "approved" for k in c.keywords)
            for c in calls
        )
        if not decides:
            continue
        # Only the OUTERMOST function that decides is judged: an inner helper
        # is part of the function that calls it.
        records = any(
            (getattr(c.func, "id", None) or getattr(c.func, "attr", None)) == "record_gate_decision"
            for c in calls
        )
        if not records:
            missing.append(fn.name)
    return missing


def test_every_decision_site_records_through_the_recorder():
    offenders: dict[str, list[str]] = {}
    seen_sites = 0
    for pkg in PACKAGES:
        for path in (ROOT / pkg).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            src = path.read_text(encoding="utf-8")
            if "build_resume_command" not in src:
                continue
            if rel in EXEMPT:
                continue
            missing = _decision_sites_without_recorder(src)
            # A nested helper inside a recording function is covered by it.
            outer_ok = {
                n.name for n in ast.walk(ast.parse(src))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and "record_gate_decision" in ast.unparse(n)
            }
            missing = [m for m in missing if m not in outer_ok]
            if "approved=" in src:
                seen_sites += 1
            if missing:
                offenders[rel] = missing
    assert seen_sites >= 4, "the scan found fewer decision sites than exist"
    assert offenders == {}, (
        "these decide a gate without kazma_ui.hitl_decision.record_gate_decision: "
        f"{offenders}"
    )


def test_exemptions_are_still_decision_sites():
    for rel in EXEMPT:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "build_resume_command" in src and "approved=" in src, rel


def test_negative_control_an_unrecorded_decision_is_caught():
    src = (
        "async def approve(graph, payload, approved):\n"
        "    cmd = build_resume_command(payload, approved=approved)\n"
        "    await graph.ainvoke(cmd)\n"
    )
    assert _decision_sites_without_recorder(src) == ["approve"]
    recorded = src.replace(
        "    await graph.ainvoke(cmd)\n",
        "    await record_gate_decision('t', decision='approved', actor='x')\n"
        "    await graph.ainvoke(cmd)\n",
    )
    assert _decision_sites_without_recorder(recorded) == []
