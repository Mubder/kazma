"""Policy migration and bilingual routing must not expand posting permission."""

from __future__ import annotations

from dataclasses import replace

import pytest
from kazma_core.x_api.routing import AmbiguousSubjectError, route_subject
from kazma_core.x_api.stance import Subject, _parse_subjects, side_from_summon
from kazma_core.x_api.subject_policy import literal_match, normalize_cards


def card(**over):
    values = {"id": "coffee", "match": ["coffee"], "side": "against", "view": "Oppose poor sourcing."}
    values.update(over)
    return values


def test_legacy_card_keeps_draft_permission_but_does_not_gain_auto():
    cards, errors = normalize_cards([card()])
    assert not errors
    assert cards[0]["schema_version"] == 2 and cards[0]["target"] == "coffee"
    assert cards[0]["allow_draft"] and not cards[0]["allow_auto"]


@pytest.mark.parametrize("cards", [
    [card(), card(id="COFFEE")], [card(id="voice")], [card(id="post")],
    [card(schema_version=2, target="")], [card(allow_auto="yes")],
    [card(allow_auto=True, required_checks=["stance"])],
    [card(), card(id="other", side="support")],
    [card(exclusions=["coffee"])], [card(match=["*"]), card(id="other", match=["*"])],
    [card(view="a" * 4001)], [card(schema_version=99)],
])
def test_invalid_or_conflicting_cards_are_rejected(cards):
    assert normalize_cards(cards)[1]


@pytest.mark.parametrize("text,alias,expect", [
    ("VAR decisions", "var", True), ("variable", "var", False),
    ("التَّحْكِيم سيء", "تحكيم", True), ("تحكيمات", "تحكيم", False),
    ("الكــويت", "الكويت", True), ("الكويتية", "الكويت", False),
    ("أيد", "ايد", False), ("cafe\u0301", "café", True),
])
def test_safe_unicode_token_matching(text, alias, expect):
    assert literal_match(text, alias) is expect


def test_alias_exclusion_and_internal_id_do_not_become_targets():
    subject = Subject(id="card_17", target="Coffee suppliers", match=("coffee",), aliases=("espresso",), exclusions=("decaf",), side="against")
    assert route_subject("espresso quality", (subject,)).to_dict()["target"] == "Coffee suppliers"
    assert route_subject("decaf espresso", (subject,)).state == "needs_review"
    assert route_subject("card_17", (subject,)).state == "no_match"


def test_multiple_matches_need_review_in_either_order():
    first = Subject(id="coffee", match=("coffee",), side="against")
    second = Subject(id="tea", match=("tea",), side="support")
    for cards in ((first, second), (second, first)):
        decision = route_subject("Coffee and tea", cards)
        assert decision.state == "ambiguous" and decision.selected is None
        assert {c["subject_id"] for c in decision.candidates} == {"coffee", "tea"}


def test_specific_card_precedes_catchall_and_scope_needs_review():
    specific = Subject(id="coffee", match=("coffee",), side="against")
    fallback = Subject(id="general", match=("*",), view="General voice")
    assert route_subject("coffee", (fallback, specific)).selected == specific
    assert route_subject("coffee", (replace(specific, scope="Only commercial sourcing"),)).state == "needs_review"


@pytest.mark.parametrize("text", ["don't support this", "do not defend this", "لا تدافع عنه", "لا تؤيد", "support and roast this"])
def test_negated_or_conflicting_summons_are_reviewed(text):
    with pytest.raises(AmbiguousSubjectError):
        side_from_summon(text)


async def test_preview_and_live_share_ambiguous_routing(monkeypatch, tmp_path):
    from kazma_core.x_api import reply
    from kazma_core.x_api.reply_store import reset_reply_store
    from kazma_core.x_api.stance import ReplyConfig

    reset_reply_store(tmp_path / "replies.db")
    subjects = _parse_subjects([card(), card(id="tea", match=["tea"], side="support")])
    cfg = ReplyConfig(enabled=True, mode="auto", summoners=("owner",), trigger="", max_replies_per_day=5,
                      max_replies_per_target_per_day=1, cooldown_per_thread_s=0, min_target_followers=0,
                      poll_interval_s=60, subjects=subjects, unmatched="voice")
    async def forbidden(**kwargs):
        pytest.fail("Ambiguous routing must not draft or publish")
    monkeypatch.setattr(reply, "draft_reply", forbidden)
    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", forbidden)
    preview = await reply.preview_reply(parent_text="coffee and tea", cfg=cfg)
    live = await reply.handle_summon(summon_id="123", parent_id="456", parent_text="coffee and tea", parent_handle="target",
                                    summoner="owner", summon_text="😂", cfg=cfg)
    assert live.action == preview.action == "needs_review"
    assert live.routing["state"] == preview.routing["state"] == "ambiguous"


def test_settings_round_trip_preserves_policy_fields_and_increments_revision(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.config_store import get_config_store
    from kazma_ui.x_reply_api import protected_router, router

    async def no_loop():
        pass
    monkeypatch.setattr("kazma_core.x_api.mentions_fire.ensure_mentions_loop", no_loop)
    get_config_store().set("connectors.x.reply.subjects", [])
    app = FastAPI()
    app.include_router(router)
    app.include_router(protected_router)
    subject = card(schema_version=2, target="Coffee suppliers", aliases=["espresso"], exclusions=["decaf"],
                   scope="Commercial sourcing", allow_auto=True)
    with TestClient(app) as client:
        headers = {"X-Requested-With": "XMLHttpRequest"}
        first = client.put("/api/x/reply", json={"subjects": [subject]}, headers=headers)
        assert first.status_code == 200, first.text
        saved = first.json()["subjects"][0]
        assert saved["target"] == "Coffee suppliers" and saved["aliases"] == ["espresso"]
        assert saved["scope"] == "Commercial sourcing" and saved["allow_auto"]
        assert saved["revision"] == 1
        again = client.put("/api/x/reply", json={"subjects": [saved]}, headers=headers)
        assert again.json()["subjects"][0]["revision"] == 1
        saved["side"] = "support"
        changed = client.put("/api/x/reply", json={"subjects": [saved]}, headers=headers)
        assert changed.json()["subjects"][0]["revision"] == 2


async def test_legacy_card_in_global_auto_mode_stays_draft_only(monkeypatch, tmp_path):
    from kazma_core.x_api import reply
    from kazma_core.x_api.reply_store import reset_reply_store
    from kazma_core.x_api.stance import ReplyConfig

    reset_reply_store(tmp_path / "replies.db")
    cfg = ReplyConfig(enabled=True, mode="auto", summoners=("owner",), trigger="", max_replies_per_day=5,
                      max_replies_per_target_per_day=1, cooldown_per_thread_s=0, min_target_followers=0,
                      poll_interval_s=60, subjects=_parse_subjects([card()]), stance_check=False)
    async def draft(**kwargs):
        return "An opinion about coffee."
    async def forbidden(**kwargs):
        pytest.fail("Legacy cards must not acquire permission from global auto mode")
    monkeypatch.setattr(reply, "draft_reply", draft)
    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", forbidden)
    result = await reply.handle_summon(summon_id="123", parent_id="456", parent_text="coffee sourcing",
                                       parent_handle="target", summoner="owner", cfg=cfg)
    assert result.action == "awaiting_approval"
