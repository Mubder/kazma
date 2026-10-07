"""Literal SQL LIKE patterns; callers pair these with ESCAPE '!'."""
from __future__ import annotations


def like_literal(text: str) -> str:
    """Escape wildcards and the escape character, preserving literal text."""
    return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")
