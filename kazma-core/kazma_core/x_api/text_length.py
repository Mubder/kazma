"""One authoritative v3 weighted counter for previews, drafts and publication."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextLength:
    weighted: int
    valid: bool


def validate_text(text: str, *, maximum: int = 280) -> TextLength:
    """Count NFC text, transformed URLs and recognized emoji sequences.

    Parser assets are pinned. Newer unrecognized emoji may conservatively
    overcount; a valid count never grants account capability or approval.
    """
    from kazma_core.x_api._text.config import config
    from kazma_core.x_api._text.parse_tweet import parse_tweet

    if len(text) > 32000:
        return TextLength(len(text) * 2, False)
    if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        return TextLength(len(text) * 2, False)
    result = parse_tweet(text, {**config["defaults"], "max_weighted_tweet_length": maximum})
    return TextLength(result.weightedLength, result.valid)
