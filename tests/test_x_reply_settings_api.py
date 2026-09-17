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

VAR = Subject(
    id="var",
    match=("var", "offside"),
    view="VAR has made football worse and the people defending it know it.",
    mood="roast",
)


def _cfg(**over) -> ReplyConfig:
    base = dict(
        enabled=True, mode="draft", summoners=("balfaris",), trigger="",
        max_replies_per_day=5, max_replies_per_target_per_day=1,
        cooldown_per_thread_s=3600, min_target_followers=500,
        poll_interval_s=600, subjects=(VAR,),
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
    problems = _validate_subjects([SubjectBody(id="var", match=["var"], view="")])
    assert problems and "needs a view" in problems[0]


def test_subject_without_keywords_is_rejected():
    problems = _validate_subjects([SubjectBody(id="var", match=[], view="v")])
    assert problems and "can never match" in problems[0]


def test_subject_without_id_is_rejected():
    problems = _validate_subjects([SubjectBody(id="", match=["a"], view="v")])
    assert problems and "needs an id" in problems[0]


def test_duplicate_ids_are_rejected():
    problems = _validate_subjects([
        SubjectBody(id="var", match=["var"], view="v"),
        SubjectBody(id="var", match=["offside"], view="v"),
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
        [SubjectBody(id="var", match=["var"], view="v", mood="roast")]
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

    res = await preview_reply(parent_text="VAR ruined that match", cfg=_cfg())
    assert res.action == "preview" and res.draft == "a draft"


@pytest.mark.asyncio
async def test_preview_records_nothing(monkeypatch, _no_llm):
    """No store row — otherwise iterating would burn the idempotency key."""
    from kazma_core.x_api.reply_store import get_reply_store

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        return "a draft"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    await preview_reply(parent_text="VAR", cfg=_cfg())
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
    res = await preview_reply(parent_text="VAR", cfg=_cfg())
    assert res.action == "failed" and "banned construction" in res.reason


@pytest.mark.asyncio
async def test_preview_ignores_caps(monkeypatch, _no_llm):
    """Rate caps guard publishing. A preview publishes nothing, so it runs."""
    from kazma_core.x_api.reply_store import get_reply_store

    store = get_reply_store()
    for i in range(5):
        store.claim(summon_id=f"s{i}", parent_id=f"p{i}",
                    target_handle="t", summoner="balfaris")
        store.mark_posted(f"s{i}", tweet_id=f"t{i}", draft="d", subject_id="var")

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        return "still drafts"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    res = await preview_reply(parent_text="VAR", cfg=_cfg(max_replies_per_day=1))
    assert res.action == "preview"


@pytest.mark.asyncio
async def test_preview_ignores_the_summoner_allowlist(monkeypatch, _no_llm):
    """The caller is an authenticated operator in Settings, not a stranger."""
    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        return "drafted"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    res = await preview_reply(parent_text="VAR", cfg=_cfg(summoners=()))
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
        parent_text="completely unrelated text", cfg=_cfg(), subject_id="var"
    )
    assert res.action == "preview" and seen["id"] == "var"


@pytest.mark.asyncio
async def test_forced_unknown_subject_is_refused(_no_llm):
    res = await preview_reply(parent_text="x", cfg=_cfg(), subject_id="nope")
    assert res.action == "skipped" and "no subject with id" in res.reason


@pytest.mark.asyncio
async def test_preview_with_no_subjects_declines(_no_llm):
    res = await preview_reply(parent_text="VAR", cfg=_cfg(subjects=()))
    assert res.action == "skipped" and "no subjects declared" in res.reason


# ── Tuning an UNSAVED subject ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preview_uses_an_unsaved_subject(monkeypatch, _no_llm):
    """The dry run must test the card being edited, not only stored config.

    Reading saved config only made the loop: type a view, save it, try it,
    hate it, retype, save again — committing half-finished subjects to live
    config just to see what they produce. The first thing an operator hit was
    "no subjects declared" while looking at a subject they had just typed.
    """
    seen = {}

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        seen["view"] = subject.view
        seen["id"] = subject.id
        return "drafted"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    scratch = Subject(id="scratch", match=("x",), view="an unsaved position")

    # cfg has NO subjects — the exact state that produced the message.
    res = await preview_reply(
        parent_text="anything", cfg=_cfg(subjects=()), subject_override=scratch
    )
    assert res.action == "preview"
    assert seen["view"] == "an unsaved position"
    assert seen["id"] == "scratch"


@pytest.mark.asyncio
async def test_preview_without_an_override_still_needs_a_subject(_no_llm):
    """The message stays correct when nothing is genuinely declared."""
    res = await preview_reply(parent_text="anything", cfg=_cfg(subjects=()))
    assert res.action == "skipped"
    assert "no subjects declared" in res.reason


@pytest.mark.asyncio
async def test_an_unsaved_subject_still_gets_the_universal_hard_lines(
    monkeypatch, _no_llm
):
    """A scratch subject is not a way around the rules that always apply."""
    seen = {}

    async def _draft(*, subject, parent_text, parent_handle="", mood=""):
        seen["rules"] = subject.all_hard_lines()
        return "drafted"

    monkeypatch.setattr(reply_mod, "draft_reply", _draft)
    await preview_reply(
        parent_text="x", cfg=_cfg(subjects=()),
        subject_override=Subject(id="scratch", match=("x",), view="v"),
    )
    assert any("protected characteristic" in r for r in seen["rules"])
    assert any("never a people" in r for r in seen["rules"])


def test_the_panel_sends_the_open_card():
    """Grep-proof: the JS must post the expanded subject, or this is dead."""
    import pathlib as _pl

    js = _pl.Path(
        "kazma-ui/kazma_ui/static/js/settings_integrations.js"
    ).read_text(encoding="utf-8")
    assert "subject: (this.xReplyOpen !== null" in js, (
        "runXReplyPreview must send the open subject card, or the dry run "
        "silently falls back to saved config"
    )


# ── "Is this thing actually on?" ──────────────────────────────────────────
#
# The dry run reads the card in the EDITOR; everything else reads SAVED
# config. So an operator can have a preview drafting happily and a completely
# inert feature at the same moment. That is exactly what happened on the first
# live test: subject typed, preview working, `enabled` never saved, and a real
# mention on X went nowhere with nothing on screen to explain it.

def _reason(**over):
    from kazma_ui.x_reply_api import _live_reason

    class _X:
        def __init__(self, ok): self._ok = ok
        def can_post(self): return self._ok

    return _live_reason(_cfg(**over), _X(over.pop("_connector", True)))


def test_live_reason_flags_a_disabled_connector(monkeypatch):
    from kazma_ui.x_reply_api import _live_reason

    class _X:
        def can_post(self): return False

    assert "not posting-ready" in _live_reason(_cfg(), _X())


def test_live_reason_flags_unsaved_enable():
    """The exact first-run state: everything typed, nothing saved."""
    assert "Auto-reply is OFF" in _reason(enabled=False)


def test_live_reason_flags_mode_off():
    assert "Mode is 'off'" in _reason(mode="off")


def test_live_reason_flags_no_saved_subjects():
    r = _reason(subjects=())
    assert "No subjects are SAVED" in r
    assert "not saved until you press Save" in r


def test_live_reason_flags_empty_allowlist():
    assert "nobody can summon" in _reason(summoners=())


def test_live_reason_flags_a_stopped_poller(monkeypatch):
    """Saved and correct, but the loop only starts at boot."""
    monkeypatch.setattr("kazma_ui.x_reply_api._poller_running", lambda: False)
    r = _reason()
    assert "poller is not running" in r and "restart" in r


def test_live_reason_says_live_when_it_is(monkeypatch):
    monkeypatch.setattr("kazma_ui.x_reply_api._poller_running", lambda: True)
    assert _reason().startswith("Live in draft mode")
