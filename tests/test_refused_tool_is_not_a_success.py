"""A refused tool call is not a completed one.

``docs/plans/UNIFIED_TURN_BLOCK.md`` §10, acceptance matrix:

    | Deny / expiry / tool execution failure | Correct distinct
    |                                        | decision/execution labels;
    |                                        | no false success.

and §3, which separates the decision from the execution precisely so
that "Approved" never implies "it worked".

Found 2026-09-20 while isolating the unified-turn harness. An approved
``file_write`` whose path the workspace policy refuses produced::

    Tool 'file_write' executed in 0ms
    Tool file_write [OK]: duration=32ms
    [ToolWorker] file_write → 32ms (error=False)

and wrote nothing. The turn recorded a completed execution, so the
approval row would have told the reader their approved write succeeded.

``tool_registry`` states the convention in its own comment — "Plain-string
tools report failures by returning an 'Error: …' string" — and checked
for ``Error:`` and ``⚠️``. ``workspace.path_policy.denied_message()``
never adopted it: it opens with "Safety: write/modify outside the active
workspace is not allowed". Five tools return that message
(``file_write``, ``file_read``, ``file_apply_patch``, ``tool_scope``,
``ide/service``), and all five were reporting a refusal as a success.
"""

from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════════════════
# The refusal really does look like this
# ══════════════════════════════════════════════════════════════════════


def test_the_denial_message_starts_with_safety() -> None:
    """The premise, asserted rather than assumed.

    If this message is ever reworded, the classifier below stops
    recognising it and the false success comes back silently. Pinning
    the prefix here is what makes that a red test instead of a
    regression.
    """
    from kazma_core.workspace.path_policy import denied_message

    msg = denied_message("/nowhere/at/all.txt", "write")
    assert msg.startswith("Safety:"), msg[:120]
    assert not msg.startswith("Error:"), (
        "the denial now uses the Error: convention — good, but the "
        "classifier's Safety: branch is then dead and should be revisited"
    )


# ══════════════════════════════════════════════════════════════════════
# ...and the registry must call it a failure
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "content, expected",
    [
        ("Error: Could not write to /x — denied", True),
        ("⚠️ something went wrong", True),
        ("Safety: write/modify outside the active workspace is not allowed.\n"
         "  path: /tmp/x\n  workspace: /repo\n", True),
        ("Wrote 12 bytes to README.md", False),
        # The substring guard the original comment called out: a
        # successful read of a file whose NAME contains the word must not
        # trip the check.
        ("document_error_handling.md\nsafety_notes.md", False),
    ],
)
def test_the_classifier_sorts_refusals_from_results(
    content: str, expected: bool
) -> None:
    """Exercised through the same expression the registry uses.

    Calling the real ``execute_tool`` needs a registered tool, an event
    loop and a procedural-outcome recorder; the thing under test is one
    prefix rule, so the rule is what is asserted — and
    ``test_the_registry_still_uses_this_rule`` below keeps the two from
    drifting apart.
    """
    is_err = (
        content.startswith("Error:")
        or content.startswith("⚠️")
        or content.startswith("Safety:")
    )
    assert is_err is expected, content[:60]


def test_the_registry_still_uses_this_rule() -> None:
    """The rule above is only worth anything if it is the shipped one."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-core" / "kazma_core" / "agent" / "tool_registry.py"
    ).read_text(encoding="utf-8")

    assert 'content.startswith("Safety:")' in src, (
        "tool_registry no longer treats a workspace refusal as a failure; "
        "an approved-then-refused danger tool will be recorded as a "
        "completed execution again"
    )
    assert 'content.startswith("Error:")' in src
    # One expression, so a future edit cannot fix one prefix and forget
    # the others. Sliced to the assignment's closing line rather than the
    # first ")" — each startswith() call ends with one of those.
    start = src.index("_is_err = ")
    block = src[start : src.index("\n                )", start)]
    for prefix in ('"Error:"', '"⚠️"', '"Safety:"'):
        assert prefix in block, f"{prefix} left the _is_err expression"
