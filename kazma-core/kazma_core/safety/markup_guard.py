"""Markup and Placeholder Protection Guard for Kazma Localization.

Ensures that code blocks, template placeholders, HTML tags, and Markdown formatting
are stashed before translation/l10n routines and cleanly restored afterward,
preventing content corruption.
"""

from __future__ import annotations

import re

__all__ = ["protect_markup_tokens", "restore_markup_tokens"]

# Preserved patterns: placeholders {foo}, HTML tags <...>, code blocks `...`, markdown bold/links
PLACEHOLDER_RE = re.compile(
    r"(\{[a-zA-Z0-9_]+\}|<[^>]+>|`[^`]+`|\*\*.+?\*\*|\[.+?\]\(.+?\))"
)


def protect_markup_tokens(text: str) -> tuple[str, list[str]]:
    """Stash placeholders, markdown code, and HTML tags before translation/formatting.

    Returns:
        Tuple of (protected_text, tokens_list)
    """
    if not text:
        return "", []

    tokens: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        tokens.append(m.group(0))
        return f"__KAZMA_TOKEN_{len(tokens)-1}__"

    protected_text = PLACEHOLDER_RE.sub(_stash, text)
    return protected_text, tokens


def restore_markup_tokens(protected_text: str, tokens: list[str]) -> str:
    """Restore stashed placeholders and markup tokens after translation/formatting."""
    if not protected_text:
        return ""

    text = protected_text
    for idx, token in enumerate(tokens):
        text = text.replace(f"__KAZMA_TOKEN_{idx}__", token)
    return text
