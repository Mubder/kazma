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


def test_empty_input() -> None:
    assert extract_post_body("") == ""
    assert extract_post_body("   ") == ""
    assert shorten_outcome("") == ""
    assert text_dir("") == "ltr"
