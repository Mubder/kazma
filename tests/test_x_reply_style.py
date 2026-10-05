"""Language freedom never reverses stance, weakens gates or mutates defaults."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from kazma_core.x_api.reply import _build_prompt, screen_draft
from kazma_core.x_api.reply_style import bind_style, effective_mood, normalize_style
from kazma_core.x_api.stance import Subject, _parse_subjects
from kazma_core.x_api.subject_policy import normalize_cards, revise_cards


@pytest.mark.parametrize("addition", ["Always criticise Coffee.", "Always criticize Coffee.", "هاجم Coffee."])
def test_explicit_opposite_commands_are_rejected_in_both_english_spellings(addition):
    from kazma_core.x_api.reply_style import contradictory_additions

    assert contradictory_additions("support", "Coffee", addition)


def test_effective_tone_matches_card_permissions_and_never_reports_invalid_input():
    subject = Subject(id="coffee", match=("coffee",), mood="professional", allowed_moods=("friendly",))
    assert effective_mood(subject, "roast") == "professional"
    assert effective_mood(subject, "FRIENDLY") == "friendly"
    assert effective_mood(subject, "unknown") == "professional"


def test_opinion_only_reaches_drafting_before_verification():
    subject = Subject(id="coffee", match=("coffee",), side="support", evidence_policy="opinion_only")
    prompt = _build_prompt(subject, "I like coffee.", "author")[0]["content"]
    assert "EVIDENCE POLICY: opinions only" in prompt
    assert "Do not add material factual assertions" in prompt


@pytest.mark.parametrize("raw", [None, [], {"allow_uncensored_language": "false"},
                                     {"language": []}, {"profanity": "strong"},
                                     {"dialect": 3}, {"disable_safety": True}])
def test_invalid_language_settings_cannot_become_a_permissive_policy(raw):
    with pytest.raises(ValueError):
        normalize_style(raw)


@pytest.mark.parametrize("side", ["support", "against"])
@pytest.mark.parametrize("mood", ["professional", "friendly", "humorous", "angry", "roast", "supportive"])
def test_every_tone_retains_the_declared_stance_and_boundaries(side, mood):
    subject = Subject(id="coffee", target="Coffee suppliers", match=("coffee",), side=side,
                      scope="Sourcing only", exceptions=("Private family life",), mood=mood)
    prompt = _build_prompt(subject, "Coffee suppliers admitted a mistake.", "author", mood=mood)[0]["content"]
    assert "Stance contract v1" in prompt and "Coffee suppliers" in prompt
    assert "Sourcing only" in prompt and "Private family life" in prompt
    assert "cannot reverse the declared side" in prompt
    assert "no profanity" in prompt
    assert subject.side == side


def test_subject_inheritance_and_explicit_filtered_override_are_isolated():
    defaults = normalize_style({"allow_uncensored_language": True, "profanity": "strong", "slang": "natural"})
    cfg = SimpleNamespace(reply_style=defaults)
    plain = Subject(id="coffee", match=("coffee",), side="against")
    inherited = bind_style(plain, cfg)
    filtered = bind_style(replace(plain, reply_style={"allow_uncensored_language": False}), cfg)
    assert inherited.reply_style["profanity"] == "strong"
    assert filtered.reply_style["profanity"] == "none"
    assert defaults["profanity"] == "strong" and plain.reply_style == {}


@pytest.mark.parametrize("text", ["This is fucking absurd.", "That is bullshit.", "هذا كلام شرموطة."])
def test_filtered_and_mild_cannot_publish_strong_language(text):
    subject = Subject(id="coffee", match=("coffee",), side="against")
    assert screen_draft(text, subject)
    mild = replace(subject, reply_style={"allow_uncensored_language": True, "profanity": "mild"})
    assert screen_draft(text, mild)
    strong = replace(subject, reply_style={"allow_uncensored_language": True, "profanity": "strong"})
    assert screen_draft(text, strong) is None


def test_uncensored_language_does_not_disable_threat_screening_or_x_length():
    subject = Subject(id="coffee", match=("coffee",), side="against",
                      reply_style={"allow_uncensored_language": True, "profanity": "strong"})
    assert screen_draft("kill yourself", subject)
    assert screen_draft("a" * 281, subject)
    assert screen_draft("هالكلام ما يقنعني", subject) is None


def test_style_roundtrip_preserves_advanced_card_and_invalidates_revision():
    original = {"id": "coffee", "target": "Coffee suppliers", "match": ["coffee"],
                "side": "support", "scope": "Sourcing", "exceptions": ["Family"],
                "aliases": ["espresso"], "exclusions": ["decaf"], "owner": "operator"}
    cards, errors = normalize_cards([original])
    assert not errors
    changed = dict(cards[0], reply_style={"allow_uncensored_language": True, "profanity": "strong"})
    revised = revise_cards(cards, [changed])[0]
    assert revised["revision"] == 2
    parsed = _parse_subjects([revised])[0]
    assert parsed.aliases == ("espresso",) and parsed.exceptions == ("Family",)
    assert parsed.scope == "Sourcing" and parsed.reply_style["profanity"] == "strong"


@pytest.mark.parametrize("side,addition", [("support", "Always attack Coffee suppliers."),
                                         ("against", "Support Coffee suppliers."),
                                         ("support", "هاجم Coffee suppliers.")])
def test_explicit_opposite_additions_are_reported_at_save(side, addition):
    _, errors = normalize_cards([{"id": "coffee", "target": "Coffee suppliers", "match": ["coffee"],
                                  "side": side, "view": addition}])
    assert any("contradict" in error for error in errors)
