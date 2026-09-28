"""Negative + positive controls for tweet/prompt display extraction."""

from kazma_core.text_display import (
    display_kicker,
    extract_post_body,
    is_arabic_dominant,
    shorten_outcome,
    text_dir,
)

_WRAP = (
    "Rescheduled batch job 2/8 — POST ONE TWEET ONLY. Call x_post with "
    'EXACTLY this text (do not alter, translate, or add hashtags): '
    '"كاظمه لا تجيب فقط — بل تنفّذ وفق جدول. حدد مهمة في التاسعة صباحًا وستعم'
)
_TWEET = "كاظمه لا تجيب فقط — بل تنفّذ وفق جدول. حدد مهمة في التاسعة صباحًا وستعم"
_STATUS = (
    "Status for batch job 2/8 — tweet posting: 1. **x_post attempted** with "
    "the exact locked text (unchanged, no extra hashtags): > "
    "كاظمه لا تجيب فقط — بل تنفّذ وفق جدول. حدد مهمة في التاسعة صباحًا وست"
)


def test_extracts_arabic_from_unclosed_english_wrapper() -> None:
    body = extract_post_body(_WRAP)
    assert body.startswith("كاظمه")
    assert "POST ONE TWEET" not in body
    assert "x_post" not in body


def test_extract_is_noop_on_plain_tweet() -> None:
    assert extract_post_body(_TWEET) == _TWEET


def test_extract_from_markdown_blockquote_status() -> None:
    body = extract_post_body(_STATUS)
    assert body.startswith("كاظمه")
    assert "x_post attempted" not in body


def test_extracted_body_is_rtl() -> None:
    body = extract_post_body(_WRAP)
    assert text_dir(body) == "rtl"
    assert is_arabic_dominant(body)


def test_english_tweet_stays_ltr() -> None:
    assert text_dir("hello from kazma") == "ltr"
    assert extract_post_body("hello from kazma") == "hello from kazma"


def test_arabic_with_english_prefix_is_rtl_not_auto() -> None:
    """dir=auto first-strong of 'See https://…' painted the tweet LTR on /x."""
    mixed = (
        "See https://example.com/a/very/long/article-path and more English "
        "words about the launch: مرحبا بالعالم"
    )
    assert not is_arabic_dominant(mixed)
    assert text_dir(mixed) == "rtl"


def test_kicker_names_the_batch() -> None:
    assert display_kicker(_WRAP) == "Batch 2/8"


def test_kicker_empty_when_text_is_the_tweet() -> None:
    assert display_kicker(_TWEET) == ""


def test_outcome_is_the_bold_status_not_the_essay() -> None:
    assert shorten_outcome(_STATUS) == "x_post attempted"


# The owner's reminder on the live install (2026-09-28): the Scheduled page
# listed it as "done", the word it asks for as a reply.
_REMINDER = (
    "⏰ CANCELLATION REMINDER — CoPilot Pro+ renews on October 1, 2026. "
    "If you want to avoid the charge, cancel before the renewal date. "
    'Reply "done" to stop these daily reminders.'
)


def test_a_quoted_reply_word_is_not_the_reminder() -> None:
    from kazma_core.text_display import _CLOSED_QUOTE_RE

    # Negative control: the input does carry a quoted candidate -- the path
    # that used to return it is exercised, not skipped.
    assert _CLOSED_QUOTE_RE.findall(_REMINDER) == ["done"]
    assert extract_post_body(_REMINDER) == _REMINDER
    assert display_kicker(_REMINDER) == ""


def test_a_tweet_quoting_a_phrase_is_the_tweet() -> None:
    tweet = 'Our motto is "ship it" and we mean it: every fix lands with a test.'
    assert extract_post_body(tweet) == tweet
    after_colon = 'The rule: "ship it" -- and every fix lands with a test, always.'
    assert extract_post_body(after_colon) == after_colon, "introduced, but not the end of the text"


def test_an_arrow_is_not_a_blockquote() -> None:
    line = "Pipeline researcher -> writer finished in 12 seconds with no errors at all."
    assert extract_post_body(line) == line


def test_a_short_tweet_the_wrapper_hands_over_is_the_post() -> None:
    wrapped = (
        "Rescheduled batch job 2/8 — POST ONE TWEET ONLY. Call x_post with "
        'EXACTLY this text: "كاظمه لا تجيب فقط — بل تنفّذ وفق جدول."'
    )
    assert extract_post_body(wrapped) == "كاظمه لا تجيب فقط — بل تنفّذ وفق جدول."


def test_a_short_post_that_is_most_of_the_prompt_is_still_the_post() -> None:
    prompt = 'Post on X: "Kazma 0.12 is out, faster recall"'
    assert extract_post_body(prompt) == "Kazma 0.12 is out, faster recall"


def test_the_shared_cases_hold_for_the_server_copy() -> None:
    """The same file tests/js/test_post_body_cases.js holds bidi.js to."""
    import json
    from pathlib import Path

    cases = json.loads(
        (Path(__file__).parent / "fixtures" / "post_body_cases.json").read_text(encoding="utf-8")
    )["cases"]
    assert len(cases) >= 8
    wrong = {c["name"]: extract_post_body(c["text"]) for c in cases if extract_post_body(c["text"]) != c["body"]}
    assert not wrong, wrong


def test_empty_input() -> None:
    assert extract_post_body("") == ""
    assert extract_post_body("   ") == ""
    assert shorten_outcome("") == ""
    assert text_dir("") == "ltr"
