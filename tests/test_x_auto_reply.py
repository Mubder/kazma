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


IRAN = Subject(
    id="iran",
    match=("iran", "tehran", "طهران"),
    view="The regime and its people are not the same thing.",
    mood="roast",
    hard_lines=("never attack Iranians as a people",),
)
FOOTBALL = Subject(id="football", match=("offside",), view="VAR ruined it.", mood="dry")


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
        subjects=(IRAN, FOOTBALL),
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


def _stub_draft(monkeypatch, text="Tehran called, they want their talking points back."):
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
    got = await classify("Thoughts on Iran sanctions?", _cfg())
    assert got is not None and got.id == "iran"


@pytest.mark.asyncio
async def test_substring_does_not_match(_no_llm):
    """`iran` must not fire on `Tirana`. Whole-word only for ASCII keywords."""
    assert await classify("Landed in Tirana this morning", _cfg(), allow_llm=False) is None


@pytest.mark.asyncio
async def test_non_ascii_keyword_matches(_no_llm):
    got = await classify("الوضع في طهران", _cfg())
    assert got is not None and got.id == "iran"


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
        summon_id="m2", parent_id="p2", parent_text="Iran again",
        parent_handle="target", summoner="a_stranger",
        target_followers=10_000, cfg=_cfg(),
    )
    assert res.action == "skipped" and "summoners" in res.reason


@pytest.mark.asyncio
async def test_empty_allowlist_means_nobody(_no_llm, monkeypatch):
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m3", parent_id="p3", parent_text="Iran",
        parent_handle="target", summoner="balfaris",
        target_followers=10_000, cfg=_cfg(summoners=()),
    )
    assert res.action == "skipped"


@pytest.mark.asyncio
async def test_disabled_config_drafts_nothing(_no_llm, monkeypatch):
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m4", parent_id="p4", parent_text="Iran",
        parent_handle="t", summoner="balfaris", cfg=_cfg(enabled=False),
    )
    assert res.action == "skipped" and "off" in res.reason


@pytest.mark.asyncio
async def test_small_account_floor(_no_llm, monkeypatch):
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m5", parent_id="p5", parent_text="Iran",
        parent_handle="tiny", summoner="balfaris",
        target_followers=40, cfg=_cfg(),
    )
    assert res.action == "skipped" and "followers" in res.reason


@pytest.mark.asyncio
async def test_unknown_follower_count_does_not_block(_no_llm, monkeypatch):
    """The paste path usually cannot know; unknown must not mean refuse."""
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m6", parent_id="p6", parent_text="Iran",
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
        parent_id="p7", parent_text="Iran", parent_handle="t",
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
        summon_id="m8", parent_id="p8", parent_text="Iran",
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
    store.mark_posted("old", tweet_id="t1", draft="d", subject_id="iran")

    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m9", parent_id="p9", parent_text="Iran",
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
    store.mark_posted("prev", tweet_id="t2", draft="d", subject_id="iran")

    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m10", parent_id="thread1", parent_text="Iran",
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
        store.mark_posted(f"s{i}", tweet_id=f"t{i}", draft="d", subject_id="iran")

    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="m11", parent_id="p11", parent_text="Iran",
        parent_handle="new", summoner="balfaris",
        target_followers=9_000, cfg=_cfg(max_replies_per_day=2),
    )
    assert res.action == "skipped" and "daily auto-reply cap" in res.reason


# ── The draft screen ──────────────────────────────────────────────────────

def test_screen_blocks_violence():
    assert screen_draft("they should die honestly", IRAN) is not None
    assert screen_draft("kill them all", IRAN) is not None


def test_screen_blocks_overlong():
    assert screen_draft("x" * 281, IRAN) is not None


def test_screen_blocks_empty():
    assert screen_draft("   ", IRAN) is not None


def test_screen_passes_a_roast():
    assert screen_draft("Bold take from someone who just googled this.", IRAN) is None


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
        summon_id="m12", parent_id="p12", parent_text="Iran",
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
    res = await handle_summon(
        summon_id="m13", parent_id="p13", parent_text="Iran",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(mode=MODE_AUTO),
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
        summon_id="m14", parent_id="p14", parent_text="Iran",
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
        summon_id="m15", parent_id="p15", parent_text="Iran",
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
        summon_id="m16", parent_id="p16", parent_text="Iran",
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
        summon_id="e1", parent_id="p1", parent_text="Iran",
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
        summon_id="e2", parent_id="p2", parent_text="Iran",
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
        summon_id="e3", parent_id="p3", parent_text="Iran",
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
        summon_id="e4", parent_id="p4", parent_text="Iran",
        parent_handle="t", summoner="balfaris", target_followers=9_000,
        cfg=_cfg(allow_emoji_mood=False), summon_text="Kazma \U0001F92C",
    )
    assert seen["mood"] == ""


def test_mood_never_reaches_the_hard_lines():
    """Tone is a prompt line; the hard lines are not negotiable by emoji."""
    from kazma_core.x_api.reply import _build_prompt

    system = _build_prompt(IRAN, "a post", "someone", "angry")[0]["content"]
    assert "Blunt and indignant" in system, "the mood was applied"
    for rule in IRAN.all_hard_lines():
        assert rule in system, "a hard line went missing when the mood changed"


# ── Open to everyone ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_anyone_policy_lets_a_stranger_summon(_no_llm, monkeypatch):
    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="o1", parent_id="p1", parent_text="Iran",
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
        summon_id="o3", parent_id="p3", parent_text="Iran",
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
    store.mark_posted("prev", tweet_id="t1", draft="d", subject_id="iran")

    _stub_draft(monkeypatch)
    res = await handle_summon(
        summon_id="o4", parent_id="p4", parent_text="Iran",
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
