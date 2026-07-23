"""Language lock detection tests."""

from kazma_core.language_lock import detect_user_language, language_lock_message


def test_detect_english():
    assert detect_user_language("Hello, please list my log files.") == "en"


def test_detect_arabic():
    assert detect_user_language("مرحبا كيف حالك") == "ar"


def test_lock_english_forbids_arabic():
    msg = language_lock_message("What is the status of memory?")
    assert "ENGLISH" in msg
    assert "MUST reply in English" in msg
    assert "Kazma" in msg


def test_lock_arabic_enforces_brand_name():
    msg = language_lock_message("مرحبا كيف حالك")
    assert "ARABIC" in msg
    assert "كاظمه" in msg
    assert "كازما" in msg  # forbidden form must be named so model avoids it
