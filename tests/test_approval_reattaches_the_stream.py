"""After an approval, the browser must re-attach to the journal. Always.

THE INCIDENT (operator's install, 2026-09-14 15:44 UTC)

    15:44:26.783  SSE HITL interrupt: tool=python_exec - awaiting approval
    15:44:26.783  SSE turn complete ... interrupted=True     <- server closes
    15:44:28.825  HITL GRANT tool=python_exec actor=web:admin
    15:44:28.866  POST /api/approve/<thread> 200
    15:44:43.307  Reply persisted turn=96334c7ed410 chars=3725 interrupted=False

and between the approve and the reply, ZERO `/api/chat/stream` requests. The
approval was honoured, the tool ran, the answer was written -- into a journal
nobody was reading. The bubble kept its placeholder, "The agent paused to ask
for permission to run a tool", and the operator saw no response at all.

THE GUARD

Each of the four approve paths read `if (!activeStream)` before re-attaching.
That asks a variable a question it cannot answer: the handle `KS.sse` returns
exposes `abort()` and `lastEventId()` and nothing about liveness. A stream the
server closed on the HITL pause leaves `activeStream` holding a dead handle
unless a terminal callback nulls it -- and those callbacks are epoch-gated, so
a superseded stream's `done` frame nulls nothing.

The guard sat on the wrong side of an asymmetric trade. A redundant attach
costs one idempotent HTTP request (it resumes from `last_event_id`, and the
new epoch gates whatever it superseded). A missed attach costs the whole
response.

WHY THIS TEST IS SHAPED LIKE THIS

`chat.js` is ~7,700 lines wrapped in an IIFE around a live DOM; it cannot be
imported and exercised the way `turn_detail.js` can. So this reads the source
and pins the STRUCTURE. That is a weaker instrument than a behavioural test
and is worth saying out loud: it proves the guard is gone and cannot come
back by copy-paste, not that a real browser re-attaches. The behavioural proof
is the e2e suite plus the access log above.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_CHAT_JS = (
    Path(__file__).resolve().parents[1]
    / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
)


@pytest.fixture(scope="module")
def src() -> str:
    return _CHAT_JS.read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Strip block and line comments, so prose about the bug is not evidence.

    An earlier test in this repo passed by matching a string inside the very
    docstring that explained why that string was wrong.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


class TestTheGuardIsGone:
    def test_no_approval_path_is_gated_on_activestream(self, src: str) -> None:
        code = _code_only(src)
        offenders = [
            line.strip()
            for line in code.splitlines()
            if "activeStream" in line and "_reopenSseRef" in line
        ]
        assert not offenders, (
            "an approval re-attach is gated on activeStream again: " + repr(offenders)
        )

    def test_every_approve_reattach_goes_through_the_helper(self, src: str) -> None:
        code = _code_only(src)
        # Every mention of an approve attach reason is a helper call.
        for reason in ("approve-json", "approve-409"):
            for line in code.splitlines():
                if reason not in line:
                    continue
                if "_reopenSseRef(" in line or "_attachJournal(" in line:
                    assert "_reattachAfterApproval" in line, (
                        f"{reason} attaches without the helper: {line.strip()}"
                    )

    def test_the_helper_exists_and_is_called_from_all_four_paths(self, src: str) -> None:
        code = _code_only(src)
        assert "function _reattachAfterApproval(" in code
        calls = code.count("_reattachAfterApproval('")
        assert calls == 4, f"expected 4 approve paths, found {calls}"


class TestTheHelperAbortsFirst:
    def test_it_aborts_the_stale_handle_before_attaching(self, src: str) -> None:
        """Otherwise two live streams race on one journal cursor."""
        start = src.index("function _reattachAfterApproval(")
        body = src[start : src.index("\n  }", start)]
        abort_at = body.index("activeStream.abort()")
        attach_at = body.index("_reopenSseRef(reason)")
        assert abort_at < attach_at, "must abort the old stream before attaching"
        assert "activeStream = null" in body


class TestTheReasonSurvives:
    def test_the_attach_budget_still_resets_for_approvals(self, src: str) -> None:
        """`_attachJournal` resets its reopen budget for approve reasons.

        The helper passes the reason straight through, so that reset must keep
        matching the exact strings. A rename here silently reintroduces the
        2026-09-04 defect: budget exhausted, attach declined, CoT frozen.
        """
        assert "reason === 'approve-json' || reason === 'approve-409'" in src
