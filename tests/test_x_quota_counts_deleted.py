"""A deleted post still counts against the X quota (2026-09-28).

The daily and monthly caps are "a Kazma fail-safe well under X Free-tier
quota" (x_api/policy.py). X counts every post it created; deleting one does
not give the request back. The ledger counted only posts still up, so the
live end-to-end test -- one post, then its delete -- showed "0/16 today"
where X had spent one. Re-posting a deleted text stays allowed.
"""

from __future__ import annotations

import sqlite3

from kazma_core.x_api.ledger import XPostLedger


def test_a_deleted_post_still_counts_toward_the_caps(tmp_path) -> None:
    ledger = XPostLedger(tmp_path / "x_posts.db")
    ledger.record(tweet_id="1", text="Testing Kazma's posting pipeline end to end.")
    ledger.record(tweet_id="2", text="A second post.")
    assert ledger.mark_deleted("1") is True
    assert ledger.count_since(0) == 2, "X counted both creates"


def test_its_text_may_be_posted_again(tmp_path) -> None:
    ledger = XPostLedger(tmp_path / "x_posts.db")
    ledger.record(tweet_id="1", text="Testing Kazma's posting pipeline end to end.")
    ledger.mark_deleted("1")
    assert ledger.has_duplicate("Testing Kazma's posting pipeline end to end.", window_days=30) is False


def test_negative_control_the_old_count_freed_the_slot(tmp_path) -> None:
    ledger = XPostLedger(tmp_path / "x_posts.db")
    ledger.record(tweet_id="1", text="one")
    ledger.mark_deleted("1")
    conn = sqlite3.connect(tmp_path / "x_posts.db")
    try:
        old = conn.execute(
            "SELECT COUNT(*) FROM x_posts WHERE created_at >= ? AND deleted_at IS NULL", (0,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert old == 0 and ledger.count_since(0) == 1
