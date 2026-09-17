"""X auto-reply: the gates, the rails, and the one that fails closed.

The property under test throughout is **no declared subject means no reply**.
Everything else here exists to stop a reply the operator did not sanction:
the summoner allowlist, the per-target cap, the thread cooldown, the small
account floor, the draft screen, and idempotency across a restart.
"""

from __future__ import annotations

import time

import pytest

from kazma_core.x_api import reply as reply_mod
from kazma_core.x_api import stance as stance_mod
from kazma_core.x_api.reply import (
    SummonResult,
    handle_summon,
    parse_tweet_url,
    screen_draft,
)
from kazma_core.x_api.reply_store import (
    STATUS_AWAITING,
    STATUS_POSTED,
    reset_reply_store,
)
from kazma_core.x_api.stance import MODE_AUTO, MODE_DRAFT, ReplyConfig, Subject, classify


# A deliberately mundane subject. It still exercises everything the fixture
# needs to: a strong stance, an ASCII keyword that is a substring of unrelated
# words ("var" inside "variable"), and a non-ASCII keyword.
VAR = Subject(
    id="var",
    match=("var", "offside", "تحكيم"),
    view="VAR has made football worse and the people defending it know it.",
    mood="roast",
    hard_lines=("never name or mock an individual referee",),
)
COFFEE = Subject(
    id="coffee", match=("espresso",), view="Dark roast is a cover-up.", mood="dry"
)


def _cfg(**over) -> ReplyConfig:
    base = dict(
        enabled=True,
        mode=MODE_DRAFT,
        summoners=("balfaris",),
        trigger="",
        max_replies_per_day=5,
        max_replies_per_target_per_day=1,
        cooldown_per_thread_s=3600,
        min_target_followers=500,
        poll_interval_s=600,
        subjects=(VAR, COFFEE),
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
    """Classification must not reach a model in these tests."""
    async def _never(*a, **k):
        raise AssertionError("LLM classifier should not have been called")

    monkeypatch.setattr(stance_mod, "_llm_pick", _never)


def _stub_draft(monkeypatch, text="Four minutes to draw a line through a knee. Riveting stuff."):
    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        return text

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)


# ── URL parsing ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expect_id,expect_handle",
    [
        ("https://x.com/someone/status/1234567890", "1234567890", "someone"),
        ("https://twitter.com/Other_1/status/999888777?s=20", "999888777", "other_1"),
        ("https://x.com/i/web/status/5555555555", "5555555555", ""),
        ("x.com/a/status/1212121212", "1212121212", "a"),
        ("1234567890", "1234567890", ""),
        ("not a link", "", ""),
    ],
)
def test_parse_tweet_url(raw, expect_id, expect_handle):
    assert parse_tweet_url(raw) == (expect_id, expect_handle)


# ── Classification fails closed ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_keyword_match_picks_subject(_no_llm):
    got = await classify("Thoughts on the VAR decision?", _cfg())
    assert got is not None and got.id == "var"


@pytest.mark.asyncio
async def test_substring_does_not_match(_no_llm):
    """`var` must not fire on `variable`. Whole-word only for ASCII keywords."""
    assert await classify("Declare the variable up top", _cfg(), allow_llm=False) is None


@pytest.mark.asyncio
async def test_non_ascii_keyword_matches(_no_llm):
    got = await classify("قرار التحكيم كان خاطئا", _cfg())
    assert got is not None and got.id == "var"


@pytest.mark.asyncio
async def test_no_subject_means_no_reply(monkeypatch):
    """THE property: an unmatched post produces no draft, not a generic one."""
    async def _none(*a, **k):
        return None

    monkeypatch.setattr(stance_mod, "_llm_pick", _none)
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m1", parent_id="p1",
        parent_text="Best shawarma in Kuwait City?",
        parent_handle="someone", summoner="balfaris",
        target_followers=10_000, cfg=_cfg(),
    )
    assert res.action == "skipped"
    assert "no declared subject" in res.reason
    assert res.draft == ""


@pytest.mark.asyncio
async def test_classifier_cannot_invent_a_subject(monkeypatch):
    """A hallucinated id is discarded rather than becoming a reply."""
    class _Resp:
        content = "geopolitics"

    class _Provider:
        async def chat(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: type("R", (), {"get_client": staticmethod(lambda *a, **k: _Provider())})(),
    )
    assert await classify("something unrelated entirely", _cfg()) is None


# ── Gates ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_non_summoner_is_refused(_no_llm, monkeypatch):
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m2", parent_id="p2", parent_text="VAR again",
        parent_handle="target", summoner="a_stranger",
        target_followers=10_000, cfg=_cfg(),
    )
    assert res.action == "skipped" and "summoners" in res.reason


@pytest.mark.asyncio
async def test_empty_allowlist_means_nobody(_no_llm, monkeypatch):
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m3", parent_id="p3", parent_text="VAR",
        parent_handle="target", summoner="balfaris",
        target_followers=10_000, cfg=_cfg(summoners=()),
    )
    assert res.action == "skipped"


@pytest.mark.asyncio
async def test_disabled_config_drafts_nothing(_no_llm, monkeypatch):
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m4", parent_id="p4", parent_text="VAR",
        parent_handle="t", summoner="balfaris", cfg=_cfg(enabled=False),
    )
    assert res.action == "skipped" and "off" in res.reason


@pytest.mark.asyncio
async def test_small_account_floor(_no_llm, monkeypatch):
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m5", parent_id="p5", parent_text="VAR",
        parent_handle="tiny", summoner="balfaris",
        target_followers=40, cfg=_cfg(),
    )
    assert res.action == "skipped" and "followers" in res.reason


@pytest.mark.asyncio
async def test_unknown_follower_count_does_not_block(_no_llm, monkeypatch):
    """The paste path usually cannot know; unknown must not mean refuse."""
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m6", parent_id="p6", parent_text="VAR",
        parent_handle="t", summoner="balfaris",
        target_followers=None, cfg=_cfg(),
    )
    assert res.action == "awaiting_approval"


# ── Idempotency ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_summon_is_handled_once(_no_llm, monkeypatch):
    """A poller restart must not re-reply. Writes are never retried."""
    calls = {"n": 0}

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        calls["n"] += 1
        return "a draft"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    kw = dict(
        parent_id="p7", parent_text="VAR", parent_handle="t",
        summoner="balfaris", target_followers=9_000, cfg=_cfg(),
    )
    first = await handle_summon(summon_id="dupe", **kw)
    second = await handle_summon(summon_id="dupe", **kw)
    assert first.action == "awaiting_approval"
    assert second.action == "skipped" and second.reason == "already handled"
    assert calls["n"] == 1, "second summon must not spend a model call"


@pytest.mark.asyncio
async def test_result_carries_the_approvable_id(_no_llm, monkeypatch):
    """The approve prompt quotes summon_id; parent_id would never resolve."""
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m8", parent_id="p8", parent_text="VAR",
        parent_handle="t", summoner="balfaris",
        target_followers=9_000, cfg=_cfg(),
    )
    assert res.summon_id == "m8" and res.summon_id != res.parent_id


# ── Caps that policy.py cannot see ────────────────────────────────────────

@pytest.mark.asyncio
async def test_per_target_daily_cap(_no_llm, monkeypatch):
    from kazma_core.x_api.reply_store import get_reply_store

    store = get_reply_store()
    store.claim(summon_id="old", parent_id="other", target_handle="victim",
                summoner="balfaris")
    store.mark_posted("old", tweet_id="t1", draft="d", subject_id="var")

    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m9", parent_id="p9", parent_text="VAR",
        parent_handle="victim", summoner="balfaris",
        target_followers=9_000, cfg=_cfg(),
    )
    assert res.action == "skipped" and "already replied to @victim" in res.reason


@pytest.mark.asyncio
async def test_thread_cooldown(_no_llm, monkeypatch):
    from kazma_core.x_api.reply_store import get_reply_store

    store = get_reply_store()
    store.claim(summon_id="prev", parent_id="thread1", target_handle="a",
                summoner="balfaris")
    store.mark_posted("prev", tweet_id="t2", draft="d", subject_id="var")

    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m10", parent_id="thread1", parent_text="VAR",
        parent_handle="b", summoner="balfaris",
        target_followers=9_000, cfg=_cfg(),
    )
    assert res.action == "skipped" and "cooldown" in res.reason


@pytest.mark.asyncio
async def test_daily_cap(_no_llm, monkeypatch):
    from kazma_core.x_api.reply_store import get_reply_store

    store = get_reply_store()
    for i in range(2):
        store.claim(summon_id=f"s{i}", parent_id=f"p{i}", target_handle=f"h{i}",
                    summoner="balfaris")
        store.mark_posted(f"s{i}", tweet_id=f"t{i}", draft="d", subject_id="var")

    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m11", parent_id="p11", parent_text="VAR",
        parent_handle="new", summoner="balfaris",
        target_followers=9_000, cfg=_cfg(max_replies_per_day=2),
    )
    assert res.action == "skipped" and "daily auto-reply cap" in res.reason


# ── The draft screen ──────────────────────────────────────────────────────

def test_screen_blocks_violence():
    assert screen_draft("they should die honestly", VAR) is not None
    assert screen_draft("kill them all", VAR) is not None


def test_screen_blocks_overlong():
    assert screen_draft("x" * 281, VAR) is not None


def test_screen_blocks_empty():
    assert screen_draft("   ", VAR) is not None


def test_screen_passes_a_roast():
    assert screen_draft("Bold take from someone who just googled this.", VAR) is None


@pytest.mark.asyncio
async def test_screened_draft_never_posts(_no_llm, monkeypatch):
    published = {"n": 0}

    async def _bad(*, subject, parent_text, parent_handle="", mood=""):
        return "they should die"

    async def _publish(**kw):
        published["n"] += 1
        return True, {"tweet_id": "nope"}

    monkeypatch.setattr(reply_mod, "draft_reply", _bad)
    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", _publish)
    res = await handle_summon(
        summon_id="m12", parent_id="p12", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(mode=MODE_AUTO),
    )
    assert res.action == "failed"
    assert published["n"] == 0, "a screened draft must never reach publish"


# ── auto mode ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_mode_publishes_and_records(_no_llm, monkeypatch):
    from kazma_core.x_api.reply_store import get_reply_store

    async def _publish(*, text, reply_to_id=""):
        assert reply_to_id == "p13"
        return True, {"posted": True, "tweet_id": "9001",
                      "url": "https://x.com/i/web/status/9001"}

    _stub_draft(monkeypatch)
    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", _publish)
    # This test is about publish mechanics. The stance check is exercised in
    # its own section; here it must not fail closed for want of a provider.
    res = await handle_summon(
        summon_id="m13", parent_id="p13", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(mode=MODE_AUTO, stance_check=False),
    )
    assert res.action == "posted" and res.tweet_id == "9001"
    assert get_reply_store().get("m13").status == STATUS_POSTED


@pytest.mark.asyncio
async def test_draft_mode_holds(_no_llm, monkeypatch):
    from kazma_core.x_api.reply_store import get_reply_store

    async def _publish(**kw):
        raise AssertionError("draft mode must not publish")

    _stub_draft(monkeypatch)
    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", _publish)
    res = await handle_summon(
        summon_id="m14", parent_id="p14", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(mode=MODE_DRAFT),
    )
    assert res.action == "awaiting_approval"
    assert get_reply_store().get("m14").status == STATUS_AWAITING


@pytest.mark.asyncio
async def test_approve_posts_the_stored_draft(_no_llm, monkeypatch):
    """Approval resolves an id, not a memory — the STORED text is what posts."""
    from kazma_core.x_api.reply import approve_summon

    sent = {}

    async def _publish(*, text, reply_to_id=""):
        sent["text"] = text
        return True, {"tweet_id": "42", "url": "u"}

    _stub_draft(monkeypatch, text="the exact stored draft")
    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", _publish)
    await handle_summon(
        summon_id="m15", parent_id="p15", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(mode=MODE_DRAFT),
    )
    res = await approve_summon("m15")
    assert res.action == "posted"
    assert sent["text"] == "the exact stored draft"


@pytest.mark.asyncio
async def test_approve_is_not_replayable(_no_llm, monkeypatch):
    from kazma_core.x_api.reply import approve_summon

    calls = {"n": 0}

    async def _publish(*, text, reply_to_id=""):
        calls["n"] += 1
        return True, {"tweet_id": "43", "url": "u"}

    _stub_draft(monkeypatch)
    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", _publish)
    await handle_summon(
        summon_id="m16", parent_id="p16", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(mode=MODE_DRAFT),
    )
    assert (await approve_summon("m16")).action == "posted"
    assert (await approve_summon("m16")).action == "skipped"
    assert calls["n"] == 1


# ── Kill switch ───────────────────────────────────────────────────────────

def test_env_kill_switch_forces_off(monkeypatch):
    monkeypatch.setenv("KAZMA_X_REPLY", "0")
    cfg = stance_mod.get_reply_config()
    assert cfg.enabled is False and cfg.can_draft() is False


def test_post_kill_switch_also_stops_replies(monkeypatch):
    monkeypatch.setenv("KAZMA_X_POST", "0")
    assert stance_mod.get_reply_config().enabled is False


# ── Subject parsing ───────────────────────────────────────────────────────

def test_malformed_subject_is_skipped_not_fatal():
    subs = stance_mod._parse_subjects(
        [
            {"id": "ok", "match": ["a"], "view": "v"},
            {"id": "broken", "match": [], "view": "v"},
            {"no_id": True},
            "nonsense",
        ]
    )
    assert [s.id for s in subs] == ["ok"]


def test_universal_hard_lines_always_apply():
    s = Subject(id="a", match=("a",), view="v", hard_lines=("mine",))
    rules = s.all_hard_lines()
    assert "mine" in rules
    assert any("protected characteristic" in r for r in rules)
    assert any("never a people" in r for r in rules)


# ── Emoji dials the tone ──────────────────────────────────────────────────
#
# "what do you think Kazma? 😂" and the same line with 🤬 must not produce
# the same reply — that emoji is how people actually summon a bot, and the
# first cut threw it away entirely (summon_text was not even a parameter).
#
# The invariant that makes it safe to honour text someone else wrote: the
# emoji reaches TONE only. It cannot touch the subject's view or its hard
# lines, so the worst a hostile summoner can do is pick which of the
# operator's own registers their reply arrives in.

import pytest

from kazma_core.x_api.stance import SUMMON_ANYONE, mood_from_text


@pytest.mark.parametrize(
    "text,expect",
    [
        ("what do you think Kazma? \U0001F602", "roast"),
        ("Kazma \U0001F92C this is outrageous", "angry"),
        ("\U0001F644 sure", "dry"),
        ("\U0001F44F well said", "supportive"),
        ("no emoji here", ""),
        ("\U0001F389 unrelated emoji", ""),
        ("", ""),
    ],
)
def test_mood_from_text(text, expect):
    assert mood_from_text(text) == expect


def test_first_emoji_wins():
    """'😂 but seriously 🤬' led with the joke."""
    assert mood_from_text("\U0001F602 but seriously \U0001F92C") == "roast"


@pytest.mark.asyncio
async def test_emoji_overrides_the_subject_mood(_no_llm, monkeypatch):
    seen = {}

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        seen["mood"] = mood
        return "drafted"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    await handle_summon(
        summon_id="e1", parent_id="p1", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(), summon_text="what do you think Kazma? \U0001F92C",
    )
    assert seen["mood"] == "angry", "the subject is saved as roast; 🤬 must win"


@pytest.mark.asyncio
async def test_no_emoji_keeps_the_subject_mood(_no_llm, monkeypatch):
    seen = {}

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        seen["mood"] = mood
        return "drafted"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    await handle_summon(
        summon_id="e2", parent_id="p2", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(), summon_text="what do you think Kazma?",
    )
    assert seen["mood"] == "", "no emoji means the subject's own mood applies"


@pytest.mark.asyncio
async def test_stranger_cannot_dial_the_tone(_no_llm, monkeypatch):
    """Under `anyone`, a stranger summons but does NOT pick the register.

    Letting someone else choose whether the operator answers angry is a small
    manipulation lever with no upside. Trusted summoners keep the emoji.
    """
    seen = {}

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        seen["mood"] = mood
        return "drafted"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    res = await handle_summon(
        summon_id="e3", parent_id="p3", parent_text="VAR",
        parent_handle="t", summoner="a_stranger", target_followers=9_000,
        cfg=_cfg(summoner_policy=SUMMON_ANYONE),
        summon_text="Kazma \U0001F92C",
    )
    assert res.action == "awaiting_approval", "the stranger may still summon"
    assert seen["mood"] == "", "but may not set the mood"


@pytest.mark.asyncio
async def test_emoji_can_be_switched_off(_no_llm, monkeypatch):
    seen = {}

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        seen["mood"] = mood
        return "drafted"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    await handle_summon(
        summon_id="e4", parent_id="p4", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(allow_emoji_mood=False), summon_text="Kazma \U0001F92C",
    )
    assert seen["mood"] == ""


def test_mood_never_reaches_the_hard_lines():
    """Tone is a prompt line; the hard lines are not negotiable by emoji."""
    from kazma_core.x_api.reply import _build_prompt

    system = _build_prompt(VAR, "a post", "someone", "angry")[0]["content"]
    assert "Blunt and indignant" in system, "the mood was applied"
    for rule in VAR.all_hard_lines():
        assert rule in system, "a hard line went missing when the mood changed"


# ── Open to everyone ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_anyone_policy_lets_a_stranger_summon(_no_llm, monkeypatch):
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="o1", parent_id="p1", parent_text="VAR",
        parent_handle="t", summoner="a_stranger", target_followers=9_000,
        cfg=_cfg(summoner_policy=SUMMON_ANYONE),
    )
    assert res.action == "awaiting_approval"


@pytest.mark.asyncio
async def test_anyone_still_needs_a_declared_subject(monkeypatch):
    """Opening the gate does not open the opinions."""
    async def _none(*a, **k):
        return None

    monkeypatch.setattr(stance_mod, "_llm_pick", _none)
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="o2", parent_id="p2", parent_text="best shawarma in Kuwait",
        parent_handle="t", summoner="a_stranger", target_followers=9_000,
        cfg=_cfg(summoner_policy=SUMMON_ANYONE),
    )
    assert res.action == "skipped" and "no declared subject" in res.reason


@pytest.mark.asyncio
async def test_anyone_still_honours_the_follower_floor(_no_llm, monkeypatch):
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="o3", parent_id="p3", parent_text="VAR",
        parent_handle="tiny", summoner="a_stranger", target_followers=40,
        cfg=_cfg(summoner_policy=SUMMON_ANYONE),
    )
    assert res.action == "skipped" and "followers" in res.reason


@pytest.mark.asyncio
async def test_anyone_still_honours_the_daily_cap(_no_llm, monkeypatch):
    from kazma_core.x_api.reply_store import get_reply_store

    store = get_reply_store()
    store.claim(summon_id="prev", parent_id="other", target_handle="x",
                summoner="whoever")
    store.mark_posted("prev", tweet_id="t1", draft="d", subject_id="var")

    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="o4", parent_id="p4", parent_text="VAR",
        parent_handle="t", summoner="a_stranger", target_followers=9_000,
        cfg=_cfg(summoner_policy=SUMMON_ANYONE, max_replies_per_day=1),
    )
    assert res.action == "skipped" and "daily auto-reply cap" in res.reason


def test_allowlist_is_the_default_and_empty_means_nobody():
    """A config mistake must never open the account to the world."""
    cfg = _cfg(summoners=())
    assert cfg.summoner_policy == "allowlist"
    assert cfg.is_summoner("anyone_at_all") is False
    assert cfg.is_summoner("") is False


def test_anyone_still_refuses_a_blank_handle():
    cfg = _cfg(summoner_policy=SUMMON_ANYONE)
    assert cfg.is_summoner("someone") is True
    assert cfg.is_summoner("") is False


def test_trusted_is_independent_of_policy():
    cfg = _cfg(summoner_policy=SUMMON_ANYONE)
    assert cfg.is_trusted_summoner("balfaris") is True
    assert cfg.is_trusted_summoner("a_stranger") is False
    assert cfg.mood_override_allowed("balfaris") is True
    assert cfg.mood_override_allowed("a_stranger") is False


# ── The conversation log keeps both sides ─────────────────────────────────
#
# The store originally kept handles, Kazma's draft and the outcome — but not
# what the other person actually said. That log shows Kazma talking to itself:
# a reply with no idea what provoked it, which is unreadable and makes "why
# did it say that?" unanswerable without opening X and rebuilding the thread.
#
# Both texts are captured at CLAIM time, not later: the poller has them in
# hand, re-fetching costs read quota, and once a tweet is deleted it is simply
# gone.

@pytest.mark.asyncio
async def test_both_sides_are_recorded(_no_llm, monkeypatch):
    from kazma_core.x_api.reply_store import get_reply_store

    _stub_draft(monkeypatch, text="Bold take from a fresh account.")
    await handle_summon(
        summon_id="c1", parent_id="p1",
        parent_text="VAR is obviously working fine",
        parent_handle="someone", summoner="balfaris",
        target_followers=9_000, cfg=_cfg(),
        summon_text="what do you think Kazma? \U0001F602",
    )
    rec = get_reply_store().get("c1")
    assert rec.parent_text == "VAR is obviously working fine"
    assert "\U0001F602" in rec.summon_text
    assert rec.draft_text == "Bold take from a fresh account."
    assert rec.summoner == "balfaris"
    assert rec.target_handle == "someone"


@pytest.mark.asyncio
async def test_a_skipped_summon_still_records_what_was_said(monkeypatch):
    """The unanswered question is 'why didn't it reply?' — so the incoming
    post has to survive even when nothing was drafted."""
    from kazma_core.x_api.reply_store import get_reply_store

    async def _none(*a, **k):
        return None

    monkeypatch.setattr(stance_mod, "_llm_pick", _none)
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="c2", parent_id="p2",
        parent_text="best shawarma in Kuwait City",
        parent_handle="someone", summoner="balfaris",
        target_followers=9_000, cfg=_cfg(),
        summon_text="Kazma?",
    )
    assert res.action == "skipped"
    rec = get_reply_store().get("c2")
    assert rec.parent_text == "best shawarma in Kuwait City"
    assert rec.draft_text == ""
    assert "no declared subject" in rec.reason


def test_long_texts_are_bounded():
    """A 20k-character quote-tweet chain must not become a 20k DB row."""
    from kazma_core.x_api.reply_store import get_reply_store

    store = get_reply_store()
    store.claim(
        summon_id="c3", parent_id="p3", target_handle="t", summoner="s",
        parent_text="x" * 9000, summon_text="y" * 9000,
    )
    rec = store.get("c3")
    assert len(rec.parent_text) == 2000
    assert len(rec.summon_text) == 500


def test_older_stores_gain_the_columns(tmp_path):
    """An install that predates the log must not silently keep the old shape.

    CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so without the
    additive migration an upgraded store would keep working and quietly never
    record a conversation.
    """
    import sqlite3

    from kazma_core.x_api.reply_store import XReplyStore

    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """CREATE TABLE x_replies (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               summon_id TEXT NOT NULL UNIQUE,
               parent_id TEXT NOT NULL DEFAULT '',
               target_handle TEXT NOT NULL DEFAULT '',
               summoner TEXT NOT NULL DEFAULT '',
               subject_id TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'drafting',
               draft_text TEXT NOT NULL DEFAULT '',
               proposal_id TEXT NOT NULL DEFAULT '',
               tweet_id TEXT NOT NULL DEFAULT '',
               reason TEXT NOT NULL DEFAULT '',
               tenant_id TEXT NOT NULL DEFAULT 'default',
               created_at REAL NOT NULL,
               updated_at REAL NOT NULL
           );"""
    )
    conn.execute(
        "INSERT INTO x_replies (summon_id, created_at, updated_at) "
        "VALUES ('legacy', 1.0, 1.0)"
    )
    conn.commit()
    conn.close()

    store = XReplyStore(db)
    rec = store.get("legacy")
    assert rec is not None, "the pre-existing row must survive the migration"
    assert rec.parent_text == ""
    assert store.claim(
        summon_id="fresh", parent_id="p", target_handle="t", summoner="s",
        parent_text="now recorded",
    )
    assert store.get("fresh").parent_text == "now recorded"


# ── The stance check ──────────────────────────────────────────────────────
#
# screen_draft checks rules that are identical for every subject — violence,
# length, emptiness. It has no idea what the operator's position IS, so a
# reply that quietly argues the opposite side passes it cleanly. For a feature
# whose whole premise is "argue the view I wrote", that is the failure that
# matters: not a rude reply, but Kazma agreeing with the person it was
# summoned to answer.

def _verdict(monkeypatch, word):
    """Stub the classifier to return one verdict."""
    class _Resp:
        content = word

    class _Provider:
        async def chat(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: type("R", (), {"get_client": staticmethod(lambda *a, **k: _Provider())})(),
    )


def _no_provider(monkeypatch):
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: type("R", (), {"get_client": staticmethod(lambda *a, **k: None)})(),
    )


@pytest.mark.asyncio
async def test_argues_passes(monkeypatch):
    from kazma_core.x_api.reply import check_stance

    _verdict(monkeypatch, "argues")
    assert await check_stance("a sharp reply", VAR, unattended=True) is None


@pytest.mark.asyncio
async def test_contradicting_draft_is_blocked(monkeypatch):
    from kazma_core.x_api.reply import check_stance

    _verdict(monkeypatch, "contradicts")
    reason = await check_stance("actually they had a point", VAR, unattended=False)
    assert reason and "AGAINST the declared view" in reason


@pytest.mark.asyncio
async def test_fence_sitting_is_blocked(monkeypatch):
    """Both-sides is a failure mode too, not a safe middle."""
    from kazma_core.x_api.reply import check_stance

    _verdict(monkeypatch, "fence")
    reason = await check_stance("there are points on both sides", VAR, unattended=False)
    assert reason and "fence" in reason


@pytest.mark.asyncio
async def test_unknown_verdict_blocks_when_unattended(monkeypatch):
    """A classifier that cannot answer must not become a silent approval."""
    from kazma_core.x_api.reply import check_stance

    _verdict(monkeypatch, "banana")
    reason = await check_stance("something", VAR, unattended=True)
    assert reason and "could not run" in reason


@pytest.mark.asyncio
async def test_unknown_verdict_allows_when_attended(monkeypatch):
    """In draft mode the operator IS the check; refusing to show them a draft
    because a classifier hiccuped is worse than useless."""
    from kazma_core.x_api.reply import check_stance

    _verdict(monkeypatch, "banana")
    assert await check_stance("something", VAR, unattended=False) is None


@pytest.mark.asyncio
async def test_no_provider_blocks_when_unattended(monkeypatch):
    from kazma_core.x_api.reply import check_stance

    _no_provider(monkeypatch)
    reason = await check_stance("something", VAR, unattended=True)
    assert reason and "could not run" in reason


@pytest.mark.asyncio
async def test_a_raising_classifier_does_not_raise_out(monkeypatch):
    from kazma_core.x_api.reply import check_stance

    class _Provider:
        async def chat(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: type("R", (), {"get_client": staticmethod(lambda *a, **k: _Provider())})(),
    )
    assert await check_stance("x", VAR, unattended=False) is None
    assert await check_stance("x", VAR, unattended=True) is not None


# ── end to end ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_mode_will_not_publish_a_contradicting_draft(_no_llm, monkeypatch):
    """The answer to 'so it never starts cheering for the other side'."""
    published = {"n": 0}

    async def _publish(**kw):
        published["n"] += 1
        return True, {"tweet_id": "nope"}

    _stub_draft(monkeypatch, text="Honestly VAR has been a huge success.")
    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", _publish)
    _verdict(monkeypatch, "contradicts")

    res = await handle_summon(
        summon_id="s1", parent_id="p1", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(mode=MODE_AUTO),
    )
    assert res.action == "failed"
    assert published["n"] == 0, "a contradicting draft must never reach publish"


@pytest.mark.asyncio
async def test_draft_mode_does_not_offer_a_contradicting_draft(_no_llm, monkeypatch):
    _stub_draft(monkeypatch, text="Honestly VAR has been a huge success.")
    _verdict(monkeypatch, "contradicts")

    res = await handle_summon(
        summon_id="s2", parent_id="p2", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(mode=MODE_DRAFT),
    )
    assert res.action == "failed"


@pytest.mark.asyncio
async def test_the_check_can_be_switched_off(_no_llm, monkeypatch):
    """An operator who does not want the extra call per reply can opt out."""
    calls = {"n": 0}

    class _Provider:
        async def chat(self, *a, **k):
            calls["n"] += 1
            raise AssertionError("stance check should not have run")

    _stub_draft(monkeypatch)
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: type("R", (), {"get_client": staticmethod(lambda *a, **k: _Provider())})(),
    )
    res = await handle_summon(
        summon_id="s3", parent_id="p3", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(stance_check=False),
    )
    assert res.action == "awaiting_approval"
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_rule_screen_still_runs_first(_no_llm, monkeypatch):
    """A violent draft is refused without spending a classifier call."""
    calls = {"n": 0}

    class _Provider:
        async def chat(self, *a, **k):
            calls["n"] += 1
            raise AssertionError("should not reach the stance check")

    async def _bad(*, subject, parent_text, parent_handle="", mood=""):
        return "they should die"

    monkeypatch.setattr(reply_mod, "draft_reply", _bad)
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: type("R", (), {"get_client": staticmethod(lambda *a, **k: _Provider())})(),
    )
    res = await handle_summon(
        summon_id="s4", parent_id="p4", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(),
    )
    assert res.action == "failed" and "banned construction" in res.reason
    assert calls["n"] == 0


# ── Gateway identity is not an X handle ───────────────────────────────────

@pytest.mark.asyncio
async def test_trusted_caller_skips_the_x_allowlist(_no_llm, monkeypatch):
    """`/x roast` passes a GATEWAY identity, not a handle on X.

    Telegram sends `telegram:12345`. Matching that against a list of X handles
    is a category error: it can never match, so the command was refused for
    everyone including the operator, and no allowlist entry could fix it —
    putting your X handle in there does nothing for a Telegram id. The
    gateway's own auth is the authorization for that path.
    """
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="t1", parent_id="p1", parent_text="VAR",
        parent_handle="someone", summoner="telegram:12345",
        target_followers=9_000, cfg=_cfg(), force_mode="draft", trusted=True,
    )
    assert res.action == "awaiting_approval"


@pytest.mark.asyncio
async def test_untrusted_caller_is_still_gated(_no_llm, monkeypatch):
    """The bypass is for authenticated gateway callers only."""
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="t2", parent_id="p2", parent_text="VAR",
        parent_handle="someone", summoner="telegram:12345",
        target_followers=9_000, cfg=_cfg(),
    )
    assert res.action == "skipped" and "summoners" in res.reason


@pytest.mark.asyncio
async def test_a_trusted_caller_can_set_the_tone(_no_llm, monkeypatch):
    """They are the operator; the emoji is theirs to use."""
    seen = {}

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        seen["mood"] = mood
        return "drafted"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    await handle_summon(
        summon_id="t3", parent_id="p3", parent_text="VAR",
        parent_handle="someone", summoner="telegram:12345",
        target_followers=9_000, cfg=_cfg(), force_mode="draft", trusted=True,
        summon_text="roast him 🤬",
    )
    assert seen["mood"] == "angry"


@pytest.mark.asyncio
async def test_trusted_does_not_lift_the_other_rails(_no_llm, monkeypatch):
    """Only the allowlist is bypassed — caps and subjects still hold."""
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="t4", parent_id="p4", parent_text="VAR",
        parent_handle="tiny", summoner="telegram:12345",
        target_followers=40, cfg=_cfg(), force_mode="draft", trusted=True,
    )
    assert res.action == "skipped" and "followers" in res.reason


def test_the_command_passes_trusted():
    """Grep-proof: the /x path must keep sending trusted=True."""
    import inspect

    from kazma_gateway.agent_handler.commands import _try_x_command

    src = inspect.getsource(_try_x_command)
    assert "trusted=True" in src, (
        "/x roast must mark itself a gateway caller, or the X allowlist "
        "refuses the operator's own command"
    )


# ── A failed draft must name its cause ────────────────────────────────────

@pytest.mark.asyncio
async def test_drafting_failure_names_the_cause(_no_llm, monkeypatch):
    """"model returned an empty draft" was true, useless, and identical for
    an unconfigured provider, a rejected key, an unknown model and a timeout.

    The real error exists at the point of failure; it was just swallowed. An
    operator testing the feature for the first time got one sentence that
    named none of the four things they might need to fix.
    """
    from kazma_core.x_api.reply import DraftFailed

    async def _boom(*, subject, parent_text, parent_handle="", mood=""):
        raise DraftFailed("the model provider rejected the credentials (401)")

    monkeypatch.setattr(reply_mod, "draft_reply", _boom)
    res = await handle_summon(
        summon_id="d1", parent_id="p1", parent_text="VAR",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(),
    )
    assert res.action == "failed"
    assert "rejected the credentials" in res.reason
    assert "empty draft" not in res.reason


@pytest.mark.asyncio
async def test_preview_failure_names_the_cause(_no_llm, monkeypatch):
    from kazma_core.x_api.reply import DraftFailed, preview_reply

    async def _boom(*, subject, parent_text, parent_handle="", mood=""):
        raise DraftFailed("the configured model is not available")

    monkeypatch.setattr(reply_mod, "draft_reply", _boom)
    res = await preview_reply(parent_text="VAR", cfg=_cfg(), subject_id="var")
    assert res.action == "failed" and "not available" in res.reason


@pytest.mark.asyncio
async def test_no_provider_says_so(monkeypatch, _no_llm):
    """The commonest first-run cause gets its own sentence."""
    from kazma_core.x_api.reply import DraftFailed, draft_reply

    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: type("R", (), {"get_client": staticmethod(lambda *a, **k: None)})(),
    )
    with pytest.raises(DraftFailed) as err:
        await draft_reply(subject=VAR, parent_text="VAR")
    assert "no LLM provider" in str(err.value)


@pytest.mark.parametrize(
    "raw,expect",
    [
        ("LLM call failed (HTTP 401): no usable API key", "rejected the credentials"),
        ("Model 'x' not found in any configured provider", "not available"),
        ("Request timed out after 60s", "timed out"),
        ("something else entirely", "the model call failed"),
    ],
)
def test_error_text_points_at_the_thing_to_fix(raw, expect):
    from kazma_core.x_api.reply import _draft_error_text

    assert expect in _draft_error_text(RuntimeError(raw))


# ── A successful call that returns nothing ────────────────────────────────
#
# Live, 2026-09-17: the operator's first dry run logged
#   "Response truncated at max_tokens=200 -- retrying once with max_tokens=400"
#   "Still truncated at max_tokens=400 -- returning truncated response"
# and produced "model returned an empty draft". The provider was healthy and
# the key was fine: deepseek-flash is a REASONING model, and the ceiling was
# set for the length of a tweet rather than for how such a model gets there.
# The same mistake was in the stance check (8 tokens) and the subject
# classifier (16) -- neither could ever have worked on that model.

@pytest.mark.asyncio
async def test_empty_content_names_the_reasoning_model(monkeypatch, _no_llm):
    from kazma_core.x_api.reply import DraftFailed, draft_reply

    class _Resp:
        content = ""
        finish_reason = "length"
        model = "deepseek-flash"

    class _Provider:
        async def chat(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: type("R", (), {"get_client": staticmethod(lambda *a, **k: _Provider())})(),
    )
    with pytest.raises(DraftFailed) as err:
        await draft_reply(subject=VAR, parent_text="VAR")
    reason = str(err.value)
    assert "deepseek-flash" in reason
    assert "reasoning model" in reason
    assert "empty draft" not in reason


@pytest.mark.asyncio
async def test_empty_without_truncation_still_explains(monkeypatch, _no_llm):
    from kazma_core.x_api.reply import DraftFailed, draft_reply

    class _Resp:
        content = "   "
        finish_reason = "stop"
        model = "some-model"

    class _Provider:
        async def chat(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: type("R", (), {"get_client": staticmethod(lambda *a, **k: _Provider())})(),
    )
    with pytest.raises(DraftFailed) as err:
        await draft_reply(subject=VAR, parent_text="VAR")
    assert "empty reply" in str(err.value)


def test_ceilings_leave_room_for_reasoning_tokens():
    """Guard the numbers themselves.

    A ceiling sized for the OUTPUT (a tweet is ~80 tokens) is one a reasoning
    model never reaches. These are ceilings, not budgets: they cost nothing on
    a model that does not use them, and they are the difference between the
    feature working and returning nothing on an entire class of model.
    """
    from kazma_core.x_api import reply as r
    from kazma_core.x_api import stance as st
    import inspect

    assert r._DRAFT_MAX_TOKENS >= 1000
    assert r._VERDICT_MAX_TOKENS >= 400
    src = inspect.getsource(st._llm_pick)
    assert "max_tokens=600" in src, "the subject classifier ceiling regressed"
