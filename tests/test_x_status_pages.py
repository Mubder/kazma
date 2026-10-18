"""``x_status`` reads every post in the ledger, a page at a time.

Live 2026-10-18: asked for an archive of every post, the agent could read the
newest 50 at most (``recent`` is capped there) and had no way further back,
while the ledger held 67. It concluded the tool "exposes no way to walk
backwards to older records".
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    import kazma_core.x_api.ledger as ledger_mod

    led = ledger_mod.XPostLedger(tmp_path / "x_posts.db")
    for n in range(7):
        led.record(tweet_id=f"id{n}", text=f"post number {n}")
    monkeypatch.setattr(ledger_mod, "_ledger", led)
    return led


async def _status(**kw) -> dict:
    from kazma_skills.native.x_publisher.tools import x_status

    return json.loads(await x_status(**kw))


@pytest.mark.anyio
async def test_every_post_is_reached_page_by_page(ledger):
    seen: list[str] = []
    offset: int | None = 0
    while offset is not None:
        page = await _status(recent=3, offset=offset)
        assert page["posts_total"] == 7
        seen += [p["tweet_id"] for p in page["recent_posts"]]
        offset = page["next_offset"]
    assert sorted(seen) == [f"id{n}" for n in range(7)]
    assert len(seen) == len(set(seen))


@pytest.mark.anyio
async def test_negative_control_the_first_page_alone_misses_posts(ledger):
    page = await _status(recent=3)
    assert len(page["recent_posts"]) == 3 and page["next_offset"] == 3
