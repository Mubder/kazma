"""An English reply must not be read aloud in an Arabic voice.

Live, 2026-09-17. The operator opened the web UI and Kazma began speaking
unprompted, in Arabic, reciting what sounded like hashes and dashes:

    [TTS/edgetts] Synthesized 1424160 bytes (voice=ar-SA-HamedNeural)

1.4 MB is several minutes of speech. Two defects produced it.

`voice.tts_voice` was pinned to `ar-SA-HamedNeural`, and the route applied
that pin to EVERY reply regardless of the text — so English answers went to
an Arabic voice, which pronounces `ad03e222` and `|---|---|` character by
character.

The text that made it unbearable is also the text that breaks naive language
detection: a technical reply is mostly git SHAs, byte counts and table
pipes. Counting all characters would call that Arabic or Latin depending on
how many dashes it had, so detection counts LETTERS only.
"""

from __future__ import annotations

import pytest
from kazma_core.voice.tts import AUTO_VOICE, detect_script, pick_voice_for_text

AR = "مرحبا، كيف حالك اليوم؟"
EN = "Hello, how are you today?"
#: The live payload's shape: prose swamped by identifiers and punctuation.
TECHNICAL = (
    "Pushed as ad03e222. CI: 9,048 passed, 0 failed.\n"
    "| metric | value |\n|---|---|\n| commits | 3,436+ |\n"
    "See docs/KNOWN_GAPS.md — 415.37s vs 413.94s."
)


# ── detection ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "text", "expect"),
    [
        ("plain arabic", AR, "arabic"),
        ("plain english", EN, "latin"),
        ("technical english", TECHNICAL, "latin"),
        ("arabic with latin product name", "شكرا لك يا Kazma على المساعدة", "arabic"),
        ("arabic with hashes", "تم الإصلاح في ad03e222 والاختبارات خضراء", "arabic"),
        ("digits only", "12345 67890", "unknown"),
        ("punctuation only", "|---|---| --- ... !!!", "unknown"),
        ("empty", "", "unknown"),
        ("whitespace", "   \n\t ", "unknown"),
    ],
)
def test_script_detection(label, text, expect):
    assert detect_script(text) == expect, label


def test_digits_and_dashes_do_not_decide_the_language():
    """The exact trap: the live text was mostly non-letters."""
    assert detect_script(TECHNICAL) == "latin"
    assert detect_script("ad03e222 | 9,048 | --- | 415.37s") == "latin"
    # Arabic prose keeps winning even when identifiers outnumber the words.
    assert detect_script("راجع ad03e222 و 20e1645b و 69d2d57e الآن") == "arabic"


def test_arabic_does_not_need_a_majority_but_does_need_a_foothold():
    """Where the line sits, and why it is not 50%.

    The failure modes are asymmetric — Arabic read by an English voice is
    unintelligible, whereas Latin words read by an Arabic voice are merely
    accented — so Arabic wins well below half. But it is not *any* Arabic:
    an English sentence ending in one Arabic word is an English sentence,
    and speaking all of it in Arabic would be the same defect in reverse.

    The threshold is 15% of letters.
    """
    # One Arabic word on an English sentence: still English. (4 of 30 letters)
    assert detect_script("Please review the pull request. شكرا") == "latin"
    # A clause, not a word: Arabic. (~16 of 44 letters)
    assert detect_script("Please review the PR — شكرا جزيلا على المساعدة") == "arabic"
    # An Arabic sentence with an English technical term stays Arabic.
    assert detect_script("الرجاء مراجعة الـ pull request") == "arabic"


# ── voice selection ────────────────────────────────────────────────────────


@pytest.mark.parametrize("configured", [AUTO_VOICE, "default", "none", "", None, "  "])
def test_auto_follows_the_text(configured):
    assert pick_voice_for_text(EN, configured, "edgetts") == "en-US-AriaNeural"
    assert pick_voice_for_text(AR, configured, "edgetts") == "ar-SA-HamedNeural"


def test_the_live_regression_english_no_longer_gets_an_arabic_voice():
    assert pick_voice_for_text(TECHNICAL, AUTO_VOICE, "edgetts") == "en-US-AriaNeural"


def test_an_explicitly_pinned_voice_still_wins():
    """Someone who typed a voice name meant it; auto must not override."""
    assert pick_voice_for_text(EN, "ar-SA-HamedNeural", "edgetts") == "ar-SA-HamedNeural"
    assert pick_voice_for_text(AR, "en-GB-RyanNeural", "edgetts") == "en-GB-RyanNeural"


def test_unknown_script_falls_back_to_latin_not_to_silence():
    assert pick_voice_for_text("12345", AUTO_VOICE, "edgetts") == "en-US-AriaNeural"


@pytest.mark.parametrize("provider", ["openai", "kokoro", "coqui", "nvidia", "unknown"])
def test_providers_with_multilingual_voices_are_left_alone(provider):
    """`alloy` is not an English voice — re-picking by script is meaningless."""
    assert pick_voice_for_text(AR, AUTO_VOICE, provider) == "default"
    assert pick_voice_for_text(EN, AUTO_VOICE, provider) == "default"
    # ...but an explicit pin is still honoured.
    assert pick_voice_for_text(AR, "nova", provider) == "nova"


# ── the route must ask, not pin ────────────────────────────────────────────


def test_the_route_resolves_the_voice_from_the_text():
    """Wiring guard: the fix is void if the route reinstates the blunt pin."""
    import inspect

    from kazma_ui import routes_voice

    src = inspect.getsource(routes_voice.text_to_speech)
    assert "pick_voice_for_text(" in src, "the route must resolve per-text"
    assert "voice = str(db_voice)" not in src, (
        "this is the unconditional pin that spoke English replies in Arabic"
    )


# ── where the control lives ────────────────────────────────────────────────
#
# The first attempt put a speak toggle in the composer, beside the live-voice
# button. Both are speaker glyphs and nobody could tell which was which, so
# the control moved onto each assistant message — next to copy and the
# thumbs, where "read THIS" is unambiguous.


def _read(rel: str) -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent / rel).read_text(encoding="utf-8")


def test_the_composer_has_only_one_speaker_icon():
    html = _read("kazma-ui/kazma_ui/templates/chat.html")
    assert "voice-speak-btn" not in html, (
        "a second speaker button in the composer is indistinguishable from "
        "the live-voice one beside it"
    )
    assert "voice-live-btn" in html, "live voice must stay in the composer"
    assert "voice-btn" in html, "the mic must stay in the composer"


def test_every_assistant_message_gets_a_speak_button():
    js = _read("kazma-ui/kazma_ui/static/js/chat.js")
    assert 'data-action="speak"' in js
    assert "speak-action" in js
    assert "toggleSpeakMessage(" in js


def test_the_speak_button_is_visible_without_hover():
    """The rest of the row may hide; this one may not.

    On a touch screen there is no hover at all, and someone trying to STOP
    audio should not have to discover the control first.
    """
    css = _read("kazma-ui/kazma_ui/static/css/kazma.css")
    assert ".message-actions .speak-action { opacity: 1; }" in css
    # The old rule faded the whole ROW, which makes an always-visible child
    # impossible: opacity composites the entire subtree.
    assert ".message:hover .message-actions { opacity: 1; }" not in css


def test_playback_is_owned_so_one_message_can_stop_itself():
    js = _read("kazma-ui/kazma_ui/static/js/voice.js")
    assert "_currentOwner" in js
    assert "function isSpeaking(owner)" in js
    assert "onSpeakStateChange" in js, "the UI needs a start/stop signal"
    # The clip must be reachable — a bare `new Audio()` with no handle is
    # what made the 2026-09-17 playback unstoppable.
    assert "_currentAudio = audio;" in js


def test_no_second_hidden_autospeak_switch():
    """Auto-speak is `voice.tts_reply` on the server, and only there."""
    js = _read("kazma-ui/kazma_ui/static/js/voice.js")
    assert "autoSpeakEnabled" not in js, (
        "a localStorage auto-speak gate is a second source of truth that no "
        "settings screen shows"
    )
    # Settings → Voice must not overwrite the browser `/voice on|off` pref.
    # That copy is what re-armed web speech after every refresh.
    assert "TTS_ENABLED_KEY, settings.enabled" not in js
    assert "setItem(TTS_ENABLED_KEY, settings.enabled" not in js


def test_web_typed_chat_does_not_autospeak():
    """SSE completion must not call playTTS. Speak is the 🔊 on the message."""
    js = _read("kazma-ui/kazma_ui/static/js/chat.js")
    assert js.count("playTTS(") == 1
    assert "toggleSpeakMessage(" in js
    assert "window.KazmaVoice.playTTS(tokenAccum)" not in js
    assert "KazmaVoice.playTTS(tokenAccum)" not in js


def test_explicit_speak_is_not_blocked_by_stale_tts_pref():
    """A leftover kazma.ttsEnabled=false must not brick the 🔊 button."""
    js = _read("kazma-ui/kazma_ui/static/js/voice.js")
    assert "if (!owner && !isTtsEnabled()) return;" in js
    assert "if (!isTtsEnabled() || _ttsUnavailable) return;" not in js


# ── the settings page must cover every voice key ───────────────────────────


def test_per_language_voices_are_used_when_auto():
    over = {"latin": "en-GB-RyanNeural", "arabic": "ar-EG-SalmaNeural"}
    assert pick_voice_for_text(EN, AUTO_VOICE, "edgetts", over) == "en-GB-RyanNeural"
    assert pick_voice_for_text(AR, AUTO_VOICE, "edgetts", over) == "ar-EG-SalmaNeural"


def test_an_unset_per_language_voice_falls_back_to_the_builtin():
    over = {"latin": "", "arabic": "   "}
    assert pick_voice_for_text(EN, AUTO_VOICE, "edgetts", over) == "en-US-AriaNeural"
    assert pick_voice_for_text(AR, AUTO_VOICE, "edgetts", over) == "ar-SA-HamedNeural"


def test_a_pinned_voice_still_beats_per_language_choices():
    over = {"latin": "en-GB-RyanNeural", "arabic": "ar-EG-SalmaNeural"}
    assert pick_voice_for_text(EN, "en-US-GuyNeural", "edgetts", over) == "en-US-GuyNeural"


def test_every_persisted_voice_key_is_settable():
    """A key the code READS but no form can WRITE is a dead setting.

    `voice.stt_api_key` was exactly that: `/api/voice/stt` had read it since
    it shipped, and the only field an operator could not configure was the
    credential.
    """
    from kazma_ui.models import VoiceSettingsUpdate

    fields = set(VoiceSettingsUpdate.model_fields)
    for required in (
        "stt_api_key", "tts_voice_en", "tts_voice_ar",
        "livekit_url", "livekit_api_key", "livekit_api_secret",
    ):
        assert required in fields, f"{required} is read by the app but not settable"


def test_secrets_are_masked_on_read_and_a_masked_save_is_a_no_op():
    """Round-tripping the form must not overwrite a key with asterisks.

    This is the shape of the incident where pressing Test blanked every
    provider API key: a read that cannot see the secret, written straight
    back.
    """
    import inspect

    from kazma_ui import settings as settings_mod

    src = inspect.getsource(settings_mod)
    assert '"stt_api_key": _mask(' in src, "a stored key must never be echoed"
    assert "is_masked_secret_placeholder(val)" in src, (
        "saving the mask back must be ignored, not written through"
    )
