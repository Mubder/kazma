"""Credentials recognised by their VALUE: the one table every masker reads.

A rule that decides by a key's name cannot see a token pasted into a command
line, quoted in an error or logged inside a request: ``curl -H
"Authorization: Bearer ..."`` in a ``shell_exec`` call, a Slack ``xapp-``
token in a log line. Until 2026-11-15 the shared redactor
(``errors.redact_secrets``) knew a handful of shapes (OpenAI, Slack ``xox``,
GitHub, Google API keys, Telegram) and the configuration and approval-card
maskers knew none. :data:`TOKEN_SHAPES` is the one table; :func:`mask_tokens`
applies it. The redactor (logs, the problem report, ``read_system_logs``),
the URL-password maskers (``url_credentials.mask_urls_in_text``,
``mask_url_credentials_deep``), the Settings and ``config_read`` masker
(``config_mask.mask_deep``) and every approval card read it.
``tests/test_token_shapes_masked.py`` feeds each shape through every masker
found in the source.

Every pattern is linear on hostile text (``tests/test_regexes_stay_linear.py``):
each starts at a literal or at the start of a run (a lookbehind), so a failed
attempt never rescans a long run from every position inside it.
"""

from __future__ import annotations

import re

__all__ = ["MASK", "TOKEN_SHAPES", "has_token", "mask_tokens"]

#: What a token is replaced with. Four stars: the settings store refuses to
#: write a value holding them (``is_masked_secret_placeholder``), so a form
#: that posts a masked value back never stores the stars.
MASK = "****"

_B64 = r"[A-Za-z0-9_\-]"

#: (name, pattern). A pattern with a group named ``keep``/``keep_bot`` keeps
#: that part (an Authorization scheme's word) and masks the rest.
TOKEN_SHAPES: tuple[tuple[str, str], ...] = (
    # An Authorization header's credential: Bearer/Basic/Token, 16+ with a
    # digit or 24+ ("basic configuration" is prose); Discord's "Bot" carries
    # its dotted token.
    ("authorization", r"(?P<keep>\b(?i:bearer|basic|token)\s++)"
                      r"(?:(?=[A-Za-z0-9._~+/=\-]*+\d)[A-Za-z0-9._~+/=\-]{16,}+|[A-Za-z0-9._~+/=\-]{24,}+)"),
    ("discord_bot", r"(?P<keep_bot>\b(?i:bot)\s++)" + _B64 + r"{20,}+\." + _B64 + r"{4,}+\." + _B64 + r"{20,}+"),
    ("discord_token", r"(?<![A-Za-z0-9_\-])[MNO]" + _B64 + r"{23,27}\." + _B64 + r"{6}\." + _B64 + r"{27,}+"),
    ("openai_stripe", r"\b(?:sk|rk)[\-_]" + _B64 + r"{8,}+"),
    ("slack", r"\bx(?:ox[abeoprs]|app)-[A-Za-z0-9\-]{8,}+"),
    ("github", r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{8,}+"),
    ("gitlab", r"\bglpat-[A-Za-z0-9_\-]{16,}+"),
    ("huggingface", r"\bhf_[A-Za-z0-9]{20,}+"),
    ("google_api_key", r"\bAIza[A-Za-z0-9_\-]{30,}+"),
    ("google_oauth", r"\bya29\.[A-Za-z0-9_\-]{16,}+"),
    ("google_refresh", r"(?<![A-Za-z0-9/])1//0[A-Za-z0-9_\-]{16,}+"),
    ("meta", r"\bEAA[A-Za-z0-9]{24,}+"),
    ("aws_access_key", r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b"),
    ("kazma_api_token", r"\bkazma_[0-9a-f]{32,}+"),
    ("jwt", r"(?<![A-Za-z0-9_\-])eyJ" + _B64 + r"{8,}+\.eyJ" + _B64 + r"{8,}+\." + _B64 + r"*+"),
    ("telegram_bot", r"(?<![0-9])(?:bot)?[0-9]{6,}+:[A-Za-z0-9_\-]{30,}+"),
)

_COMPILED = re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in TOKEN_SHAPES))
_KEEP_GROUPS = ("keep", "keep_bot")


def _replace(match: re.Match[str], mask: str) -> str:
    for group in _KEEP_GROUPS:
        kept = match.group(group)
        if kept is not None:
            return kept + mask
    return mask


def mask_tokens(text: str, mask: str = MASK) -> str:
    """*text* with every token-shaped value replaced by *mask*."""
    if not isinstance(text, str) or not text:
        return text
    return _COMPILED.sub(lambda m: _replace(m, mask), text)


def has_token(text: str) -> bool:
    """True when *text* holds a token-shaped value."""
    return isinstance(text, str) and _COMPILED.search(text) is not None
