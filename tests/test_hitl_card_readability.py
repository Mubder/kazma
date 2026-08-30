"""An approval card must show what it is asking you to authorise.

2026-08-30, from a live Telegram session. Nine approval cards arrived in three
minutes, every one of them cut mid-command::

    shell_exec: 'cd ... && cp "a.jpg" "b.jpg" && cp "c.jpg" "d.jpg" && cp "phot…

The operator was being asked to authorise a chained shell command whose tail
was invisible. The dangerous half of a command is usually at the end.

Three separate defects produced that screen:

* ``str(args)[:300] + "…"`` in the card builder -- silent elision, and the
  ellipsis reads as formatting rather than as a warning;
* a 60s ``approval_timeout_seconds`` with ``auto_deny_on_timeout``, whose
  denial the model read as "that approach failed" and retried with a variant,
  raising a fresh card each time;
* nothing throttling repeated cards for one thread.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from kazma_gateway.agent_handler.hitl import (
    EXEC_TOOLS,
    _format_args_for_approval,
    approval_card_suppressed,
    clear_approval_throttle,
)

_CSS_DIR = (
    Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui" / "static" / "css"
)
#: base.html loads BOTH, kazma.v5.css second -- so a rule deduplicated in the
#: first file can still be redefined by the second, and the browser applies
#: the later one. Checking only kazma.css reported "one rule each" while the
#: real page had three definitions of .hitl-approval-actions.
CSS_FILES = (_CSS_DIR / "kazma.css", _CSS_DIR / "kazma.v5.css")
CSS = _CSS_DIR / "kazma.css"


def _all_css() -> str:
    return "\n".join(f.read_text(encoding="utf-8") for f in CSS_FILES if f.is_file())


# ── 1. nothing is hidden silently ──────────────────────────────────────────


def test_a_short_command_is_shown_whole():
    out = _format_args_for_approval("shell_exec", {"command": "ls -la /tmp"})
    assert "ls -la /tmp" in out
    assert "NOT SHOWN" not in out


def test_a_long_command_says_so_instead_of_trailing_off():
    """The regression: an ellipsis is not a warning."""
    cmd = "cd /x && " + " && ".join(f"cp a{i}.jpg b{i}.jpg" for i in range(400))
    out = _format_args_for_approval("shell_exec", {"command": cmd})

    assert "MORE CHARACTERS ARE NOT SHOWN" in out, (
        "a truncated command must announce that it is truncated"
    )
    assert re.search(r"\d+ MORE CHARACTERS", out), "say how much is hidden"
    assert not out.rstrip().endswith("…"), (
        "a bare ellipsis reads as formatting, not as 'you cannot see this'"
    )


@pytest.mark.parametrize("tool", sorted(EXEC_TOOLS))
def test_exec_tools_tell_you_not_to_approve_blind(tool):
    """These are the ones where a hidden suffix changes what happens."""
    payload = {"code": "x = 1\n" * 4000}
    out = _format_args_for_approval(tool, payload)
    assert "Do NOT approve this from chat" in out
    assert "web UI" in out


def test_a_non_exec_tool_is_advised_but_not_alarmed():
    out = _format_args_for_approval("http_request", {"body": "y" * 9000})
    assert "NOT SHOWN" in out
    assert "Do NOT approve this from chat" not in out


def test_args_are_readable_not_a_python_repr():
    out = _format_args_for_approval("shell_exec", {"command": "echo hi"})
    assert '"command"' in out, "JSON, so a human can read nested args"


def test_unserialisable_args_still_render():
    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    out = _format_args_for_approval("shell_exec", {"x": Weird()})
    assert "weird" in out


# ── 2 + 3. the retry loop is bounded ───────────────────────────────────────


def test_an_identical_request_is_not_repeated():
    tid = "thread-dup"
    clear_approval_throttle(tid)
    args = {"command": "cp a b"}

    assert approval_card_suppressed(tid, "shell_exec", args) is None
    again = approval_card_suppressed(tid, "shell_exec", args)
    assert again and "already sent" in again


def test_a_burst_of_variants_is_muted():
    """The exact shape of the incident: same intent, different arguments."""
    tid = "thread-burst"
    clear_approval_throttle(tid)

    sent = 0
    for i in range(9):
        if approval_card_suppressed(tid, "shell_exec", {"command": f"cp a{i} b{i}"}) is None:
            sent += 1

    assert sent <= 3, f"{sent} cards for one thread — that is the spam again"
    muted = approval_card_suppressed(tid, "shell_exec", {"command": "cp zzz b"})
    assert muted and "muting" in muted


def test_muting_never_implies_approval():
    """Withholding a notification must not withhold the gate."""
    tid = "thread-safety"
    clear_approval_throttle(tid)
    for i in range(6):
        approval_card_suppressed(tid, "shell_exec", {"command": f"x{i}"})

    reason = approval_card_suppressed(tid, "shell_exec", {"command": "final"})
    assert reason
    assert "Nothing has been approved or run" in reason


def test_separate_threads_do_not_mute_each_other():
    clear_approval_throttle("t-a")
    clear_approval_throttle("t-b")
    for i in range(5):
        approval_card_suppressed("t-a", "shell_exec", {"c": i})
    assert approval_card_suppressed("t-b", "shell_exec", {"c": 0}) is None


def test_engaging_clears_the_mute():
    tid = "thread-clear"
    clear_approval_throttle(tid)
    for i in range(5):
        approval_card_suppressed(tid, "shell_exec", {"c": i})
    assert approval_card_suppressed(tid, "shell_exec", {"c": 99}) is not None

    clear_approval_throttle(tid)
    assert approval_card_suppressed(tid, "shell_exec", {"c": 99}) is None


def test_a_timeout_is_not_reported_as_a_refusal():
    """The model retried variants because a timeout read like 'try again'."""
    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "hitl_timeout.py"
    ).read_text(encoding="utf-8")

    assert "did not answer" in src and "probably away" in src, (
        "the reason must say nobody is there, not merely that it was denied"
    )
    assert "NOT a refusal" in src
    assert "do not attempt a workaround" in src.lower()


# ── 4. the card cannot swallow the page ────────────────────────────────────


@pytest.mark.parametrize(
    "selector",
    [".hitl-approval-card", ".hitl-approval-header",
     ".hitl-approval-actions", ".hitl-approval-args"],
)
def test_each_hitl_rule_is_defined_once(selector):
    """Two competing blocks rendered the card as a hybrid of two designs.

    Counted across EVERY stylesheet base.html loads, not just the first one.
    """
    counts = {
        f.name: len(
            re.findall(
                rf"^{re.escape(selector)} \{{",
                f.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )
        for f in CSS_FILES
        if f.is_file()
    }
    total = sum(counts.values())
    assert total == 1, (
        f"{selector} is defined {total} times across {counts}; the last one "
        "silently wins and the card renders as a mixture of them"
    )


def test_the_args_block_is_bounded_and_scrollable():
    css = CSS.read_text(encoding="utf-8")
    block = re.search(r"^\.hitl-approval-args \{(.*?)\}", css, re.MULTILINE | re.DOTALL)
    assert block, ".hitl-approval-args rule missing"
    body = block.group(1)
    assert "max-height" in body, (
        "unbounded, a python_exec payload pushes Approve/Deny off-screen"
    )
    assert "overflow-y" in body, "bounded without scrolling would hide the rest"


def test_the_card_still_looks_like_a_warning():
    """It sits in a stream of reasoning output and must stand apart from it."""
    css = CSS.read_text(encoding="utf-8")
    block = re.search(r"^\.hitl-approval-card \{(.*?)\}", css, re.MULTILINE | re.DOTALL)
    assert block
    assert "--warning" in block.group(1), (
        "the neutral border let an authorisation prompt read as ordinary content"
    )


def test_the_panel_and_card_cannot_share_a_row():
    """The layout the operator actually reported, measured in a browser.

    With the message container as a flex row, the reasoning panel collapsed to
    51px -- "REAS...", phase chips stacked vertically, the activity list
    unreadable -- while the card took 493px beside it. Pinning a 100% basis was
    not enough on its own: without flex-wrap, two 100%-basis items simply
    shrink and share the line, which an inline display:flex reproduced at
    250px/270px.
    """
    css = _all_css()
    block = re.search(r"^\.message-content \{(.*?)\}", css, re.MULTILINE | re.DOTALL)
    assert block, ".message-content rule missing"
    assert "flex-wrap" in block.group(1), (
        "without flex-wrap a flex container shrinks both children onto one row"
    )

    for selector in (".agent-progress", ".hitl-approval-card"):
        rule = re.search(
            rf"^{re.escape(selector)} \{{(.*?)^\}}", css, re.MULTILINE | re.DOTALL
        )
        assert rule, f"{selector} rule missing"
        body = rule.group(1)
        assert "width: 100%" in body, f"{selector} is not pinned to full width"
        assert "flex: 1 1 100%" in body, (
            f"{selector} needs a 100% flex basis so a flex parent gives it a "
            "whole line instead of shrinking it beside its sibling"
        )


def test_the_web_card_does_not_truncate_the_command():
    """chat.js elided args at 300 chars — the Telegram fix missed this one."""
    js = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
    ).read_text(encoding="utf-8")

    assert "truncateStr(JSON.stringify(data.args" not in js, (
        "the browser card is truncating the command again; an operator cannot "
        "authorise what they cannot read"
    )
    assert "function formatApprovalArgs" in js
    assert "MORE CHARACTERS ARE NOT SHOWN" in js, (
        "if it must elide, it has to say so rather than trail off"
    )
