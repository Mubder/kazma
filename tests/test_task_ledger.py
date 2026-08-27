"""Task Ledger — the durable intent-resolution surface.

Born from the 2026-08-27 incident: "proceed with next" resolved against a
truncated transcript and executed an unprompted git commit. The ledger is
the no-shortcuts answer: durable structured task state (SQLite), binding
for short continuations, STRUCTURAL clarify when unresolved (tools locked),
and git-write blast-radius limits on every execution path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.agent.task_ledger import (
    TaskLedger,
    TaskLedgerStore,
    extract_next_action,
    format_ledger_block,
    is_git_write_command,
    resolve_continuation,
)


@pytest.fixture()
def store(tmp_path: Path) -> TaskLedgerStore:
    return TaskLedgerStore(tmp_path / "ledgers.db")


# ── Store durability ──────────────────────────────────────────────────


def test_ledger_survives_store_reopen(store: TaskLedgerStore, tmp_path: Path) -> None:
    led = store.get_or_create("th1")
    led.goal = "Find green brand names for HypertFit"
    led.set_plan(["RDAP sweep all names", "Social sweep for greens", "Verdict table"])
    led.declare_next("social sweep for potensfit, acerfit, voimfit")
    led.mark_step(0, "done", "3 fully green found")
    led.add_finding("potensfit/acerfit/voimfit fully green")
    store.save(led)

    # "restart": a NEW store instance over the same file
    reopened = TaskLedgerStore(tmp_path / "ledgers.db")
    back = reopened.active_for("th1")
    assert back is not None
    assert back.goal.startswith("Find green")
    assert len(back.steps) == 3
    assert back.steps[0].status == "done"
    assert "potensfit" in back.next_action
    assert back.findings and "fully green" in back.findings[0]


def test_supersede_and_history(store: TaskLedgerStore) -> None:
    led = store.get_or_create("th2")
    led.goal = "old task"
    store.save(led)
    led.supersede(by="new topic")
    store.save(led)
    assert store.active_for("th2") is None  # no active ledger after supersede
    hist = store.history("th2")
    assert hist and hist[0].status == "superseded" and hist[0].superseded_by == "new topic"


# ── Deterministic next-action extraction (EN + AR) ────────────────────


def test_extract_next_action_english() -> None:
    reply = (
        "Batch 4 done. One last lookup — firmix.ai.\n"
        "All 40 domain lookups done. 3 fully green names found.\n"
        "Now the social sweep (X/IG/YT) for the three domain-clean candidates — "
        "potensfit, acerfit, voimfit"
    )
    nxt = extract_next_action(reply)
    assert "social sweep" in nxt
    assert "potensfit" in nxt


def test_extract_next_action_arabic_and_none() -> None:
    assert "تويتر" in extract_next_action(
        "أنهيت الفحص.\nالتالي: سأقوم بفحص أسماء تويتر وإنستغرام للحسابات الخضراء"
    )
    assert extract_next_action("Here are your results. Done.") == ""
    assert extract_next_action("") == ""


# ── Resolution: bind / clarify / pass ─────────────────────────────────


def test_continuation_binds_to_declared_next() -> None:
    led = TaskLedger(thread_id="t", goal="green names", next_action="social sweep for the 3 greens")
    res = resolve_continuation("proceed with next", led, is_continuation=True)
    assert res["mode"] == "bound"
    assert "social sweep" in res["binding"]
    block = format_ledger_block(led, binding=res["binding"])
    assert "NEXT STEP (declared): social sweep" in block
    assert "CONTINUATION BINDING" in block
    assert "NEW task" in block  # escape clause present


def test_continuation_without_target_clarifies() -> None:
    led = TaskLedger(thread_id="t", goal="green names", next_action="")
    res = resolve_continuation("next", led, is_continuation=True)
    assert res["mode"] == "clarify"
    assert "ONE short clarifying question" in res["question"]


def test_non_continuation_and_no_ledger_pass_through() -> None:
    led = TaskLedger(thread_id="t", goal="g", next_action="x")
    assert resolve_continuation("list me every green name", led, is_continuation=False)["mode"] == "pass"
    assert resolve_continuation("next", None, is_continuation=True)["mode"] == "pass"
    done = TaskLedger(thread_id="t", status="done")
    assert resolve_continuation("next", done, is_continuation=True)["mode"] == "pass"


# ── Blast radius: git writes ──────────────────────────────────────────


def test_git_write_classification() -> None:
    assert is_git_write_command("git commit -am 'x'")
    assert is_git_write_command("git push origin main")
    assert is_git_write_command("cd repo && git reset --hard HEAD~1")
    assert is_git_write_command("GIT MERGE feature")
    # Read-only git and non-git commands are exempt.
    assert not is_git_write_command("git status")
    assert not is_git_write_command("git log --oneline -5")
    assert not is_git_write_command("git diff HEAD")
    assert not is_git_write_command("grep -r foo .")


def test_tool_worker_git_write_helper() -> None:
    from kazma_core.agent.graph_tool_worker import _tc_is_git_write

    assert _tc_is_git_write({"name": "exec", "arguments": {"command": "git commit -am x"}})
    assert not _tc_is_git_write({"name": "exec", "arguments": {"command": "git status"}})
    assert not _tc_is_git_write({"name": "web_search", "arguments": {"query": "git commit"}})
    assert not _tc_is_git_write({"name": "exec", "arguments": {}})


def test_yolo_ttl_default_reduced() -> None:
    """4h → 1h: a stale YOLO window executed an unprompted git commit."""
    from kazma_core.safety.yolo import _DEFAULT_TTL_SECONDS

    assert _DEFAULT_TTL_SECONDS == 3600


# ── Supervisor wiring (structural pins) ───────────────────────────────


def test_supervisor_wires_ledger_end_to_end() -> None:
    src = (
        Path(__file__).resolve().parents[1]
        / "kazma-core" / "kazma_core" / "agent" / "graph_supervisor.py"
    ).read_text(encoding="utf-8")
    # load + bind + clarify-lock + persist
    assert "_resolve_ledger_continuation(" in src
    assert 'ledger binding' in src
    assert "_ledger_clarify" in src and "effective_tool_definitions = []" in src
    assert "_extract_next_action(_led_content)" in src
    assert "_ledger.set_plan(_items)" in src


def test_task_ledger_tool_registered() -> None:
    src = (
        Path(__file__).resolve().parents[1]
        / "kazma-core" / "kazma_core" / "agent" / "tool_builtins.py"
    ).read_text(encoding="utf-8")
    assert src.count('"task_ledger_update"') >= 1
    assert "DURABLE TASK LEDGER" in src


# ── Post-clarify unlock (2026-08-27 live report: clarify → "proceed" →
#    clarify-lock AGAIN → model narrated tool calls it couldn't run) ────


def test_continuation_after_clarify_unlocks_instead_of_looping() -> None:
    led = TaskLedger(thread_id="t", goal="naming sweep", next_action="",
                     clarify_pending=True)
    res = resolve_continuation("proceed", led, is_continuation=True)
    assert res["mode"] == "post_clarify"
    assert "recommended" in res["directive"] and "do not ask again" in res["directive"]


def test_clarify_marks_pending_and_store_roundtrip(store: TaskLedgerStore) -> None:
    led = TaskLedger(thread_id="th-c", goal="g", next_action="")
    res = resolve_continuation("go", led, is_continuation=True)
    assert res["mode"] == "clarify" and res.get("mark_pending") is True
    led.clarify_pending = True
    store.save(led)
    back = store.active_for("th-c")
    assert back is not None and back.clarify_pending is True


def test_supervisor_handles_post_clarify() -> None:
    src = (
        Path(__file__).resolve().parents[1]
        / "kazma-core" / "kazma_core" / "agent" / "graph_supervisor.py"
    ).read_text(encoding="utf-8")
    assert '"post_clarify"' in src
    assert "post-clarify unlock" in src
    assert "_ledger.clarify_pending = True" in src


def test_dsml_scrub_wired_in_client() -> None:
    js = (
        Path(__file__).resolve().parents[1]
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
    ).read_text(encoding="utf-8")
    assert "function _scrubDsml(text)" in js
    assert "_scrubDsml(stripPlanFenceForDisplay(tokenAccum))" in js
    assert "_scrubDsml(stripPlanFenceForDisplay(content))" in js
    assert "liveParts.prose = _scrubDsml(liveParts.prose);" in js
