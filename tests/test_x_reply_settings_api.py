"""The auto-reply settings panel: validation, round-trip, and the dry run.

Subjects are the one piece of config in the product where a malformed entry is
*silently inert* — ``stance._parse_subjects`` skips anything missing ``id``,
``match`` or ``view`` so one bad subject cannot disable the rest. Correct at
runtime, hostile to an author: a fat-fingered key becomes a subject that simply
never fires, with nothing to say why. So the save endpoint validates and names
the problem instead.

The dry run is the reason the panel exists. These tests pin the properties that
make it safe to hammer: it never publishes, never records, and does not skip
the two things being tuned (subject matching and the content screen).
"""

from __future__ import annotations

import pytest

from kazma_core.x_api import reply as reply_mod
from kazma_core.x_api import stance as stance_mod
from kazma_core.x_api.reply import preview_reply
from kazma_core.x_api.reply_store import reset_reply_store
from kazma_core.x_api.stance import ReplyConfig, Subject
from kazma_ui.x_reply_api import (
    ReplyConfigBody,
    SubjectBody,
    _validate_subjects,
)

IRAN = Subject(
    id="iran",
    match=("iran", "tehran"),
    view="The regime and the people are not the same thing.",
    mood="roast",
)


def _cfg(**over) -> ReplyConfig:
    base = dict(
        enabled=True, mode="draft", summoners=("balfaris",), trigger="",
        max_replies_per_day=5, max_replies_per_target_per_day=1,
        cooldown_per_thread_s=3600, min_target_followers=500,
        poll_interval_s=600, subjects=(IRAN,),
    )
    base.update(over)
    return ReplyConfig(**base)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path):
    reset_reply_store(tmp_path / "x_replies.db")
    yield
    reset_reply_store(tmp_path / "x_replies_after.db")


@pytest.fixture
def _no_llm(monkeypatch):
    async def _never(*a, **k):
        raise AssertionError("classifier should not have been called")

    monkeypatch.setattr(stance_mod, "_llm_pick", _never)


# ── Validation names the problem ──────────────────────────────────────────

def test_subject_without_view_is_rejected():
    problems = _validate_subjects([SubjectBody(id="iran", match=["iran"], view="")])
    assert problems and "needs a view" in problems[0]


def test_subject_without_keywords_is_rejected():
    problems = _validate_subjects([SubjectBody(id="iran", match=[], view="v")])
    assert problems and "can never match" in problems[0]


def test_subject_without_id_is_rejected():
    problems = _validate_subjects([SubjectBody(id="", match=["a"], view="v")])
    assert problems and "needs an id" in problems[0]


def test_duplicate_ids_are_rejected():
    problems = _validate_subjects([
        SubjectBody(id="iran", match=["iran"], view="v"),
        SubjectBody(id="iran", match=["tehran"], view="v"),
    ])
    assert any("duplicate id" in p for p in problems)


def test_unknown_mood_is_rejected():
    problems = _validate_subjects(
        [SubjectBody(id="a", match=["a"], view="v", mood="sarcastic")]
    )
    assert any("unknown mood" in p for p in problems)


def test_whitespace_only_keyword_does_not_count():
    problems = _validate_subjects(
        [SubjectBody(id="a", match=["   "], view="v")]
    )
    assert any("can never match" in p for p in problems)


def test_a_good_subject_passes():
    assert _validate_subjects(
        [SubjectBody(id="iran", match=["iran"], view="v", mood="roast")]
    ) == []


# ── The body shape the panel posts ────────────────────────────────────────

def test_body_defaults_are_the_safe_ones():
    """A panel that posts an empty form must not turn anything on."""
    body = ReplyConfigBody()
    assert body.enabled is False
    assert body.mode == "off"
    assert body.summoners == []
    assert body.subjects == []


# ── Dry run ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preview_publishes_nothing(monkeypatch, _no_llm):
    async def _publish(**kw):
        raise AssertionError("preview must never publish")

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        return "a draft"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", _publish)

    res = await preview_reply(parent_text="Iran sanctions again", cfg=_cfg())
    assert res.action == "preview" and res.draft == "a draft"


@pytest.mark.asyncio
async def test_preview_records_nothing(monkeypatch, _no_llm):
    """No store row — otherwise iterating would burn the idempotency key."""
    from kazma_core.x_api.reply_store import get_reply_store

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        return "a draft"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    await preview_reply(parent_text="Iran", cfg=_cfg())
    assert get_reply_store().recent(limit=5) == []


@pytest.mark.asyncio
async def test_preview_still_honours_no_subject(monkeypatch):
    """The rule being tuned is not suspended for the tuning tool."""
    async def _none(*a, **k):
        return None

    monkeypatch.setattr(stance_mod, "_llm_pick", _none)
    res = await preview_reply(parent_text="best shawarma in Kuwait", cfg=_cfg())
    assert res.action == "skipped" and "no declared subject" in res.reason


@pytest.mark.asyncio
async def test_preview_still_screens_the_draft(monkeypatch, _no_llm):
    async def _bad(*, subject, parent_text, parent_handle="", mood=""):
        return "they should die"

    monkeypatch.setattr(reply_mod, "draft_reply", _bad)
    res = await preview_reply(parent_text="Iran", cfg=_cfg())
    assert res.action == "failed" and "banned construction" in res.reason


@pytest.mark.asyncio
async def test_preview_ignores_caps(monkeypatch, _no_llm):
    """Rate caps guard publishing. A preview publishes nothing, so it runs."""
    from kazma_core.x_api.reply_store import get_reply_store

    store = get_reply_store()
    for i in range(5):
        store.claim(summon_id=f"s{i}", parent_id=f"p{i}",
                    target_handle="t", summoner="balfaris")
        store.mark_posted(f"s{i}", tweet_id=f"t{i}", draft="d", subject_id="iran")

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        return "still drafts"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    res = await preview_reply(parent_text="Iran", cfg=_cfg(max_replies_per_day=1))
    assert res.action == "preview"


@pytest.mark.asyncio
async def test_preview_ignores_the_summoner_allowlist(monkeypatch, _no_llm):
    """The caller is an authenticated operator in Settings, not a stranger."""
    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        return "drafted"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    res = await preview_reply(parent_text="Iran", cfg=_cfg(summoners=()))
    assert res.action == "preview"


@pytest.mark.asyncio
async def test_forced_subject_overrides_matching(monkeypatch, _no_llm):
    """Check how a view reads against a post its keywords would not match."""
    seen = {}

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        seen["id"] = subject.id
        return "drafted"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    res = await preview_reply(
        parent_text="completely unrelated text", cfg=_cfg(), subject_id="iran"
    )
    assert res.action == "preview" and seen["id"] == "iran"


@pytest.mark.asyncio
async def test_forced_unknown_subject_is_refused(_no_llm):
    res = await preview_reply(parent_text="x", cfg=_cfg(), subject_id="nope")
    assert res.action == "skipped" and "no subject with id" in res.reason


@pytest.mark.asyncio
async def test_preview_with_no_subjects_declines(_no_llm):
    res = await preview_reply(parent_text="Iran", cfg=_cfg(subjects=()))
    assert res.action == "skipped" and "no subjects declared" in res.reason
