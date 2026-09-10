"""Per-hunk reverse-apply for IDE review."""

from __future__ import annotations

import difflib

from kazma_core.ide.hunks import apply_reverse_hunk, split_hunks


def test_split_and_reverse_one_hunk() -> None:
    before = "a\nb\nc\n"
    after = "a\nB\nc\n"
    diff = "\n".join(difflib.unified_diff(
        before.splitlines(), after.splitlines(), fromfile="a", tofile="b", lineterm="",
    ))
    hunks = split_hunks(diff)
    assert len(hunks) == 1
    header = hunks[0]["header"]
    body = hunks[0]["diff"].splitlines()[1:]
    restored = apply_reverse_hunk(after, header, body)
    assert restored.replace("\r", "") == before.replace("\r", "")


def test_two_hunks_reverse_second_only() -> None:
    after = "ONE\ntwo\nthree\nFOUR\n"
    diff = (
        "--- a\n+++ b\n"
        "@@ -1,2 +1,2 @@\n-one\n+ONE\n two\n"
        "@@ -4,1 +4,1 @@\n-four\n+FOUR\n"
    )
    hunks = split_hunks(diff)
    assert len(hunks) == 2
    h = hunks[-1]
    body = h["diff"].splitlines()[1:]
    out = apply_reverse_hunk(after, h["header"], body)
    assert "FOUR" not in out
    assert "ONE" in out
