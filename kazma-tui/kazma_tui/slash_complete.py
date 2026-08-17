"""Slash-command autocomplete rules for the TUI chat input.

``/session`` is a prefix of ``/sessions``. If Enter always applied the first
match, ``/session 12`` never ran — the palette stole the key and filled
``/sessions``.
"""

from __future__ import annotations

__all__ = [
    "command_token",
    "enter_completes_autocomplete",
    "has_args",
    "slash_matches",
]


def command_token(value: str) -> str:
    """First whitespace-delimited token, lowercased."""
    stripped = (value or "").strip()
    if not stripped:
        return ""
    return stripped.split(None, 1)[0].lower()


def has_args(value: str) -> bool:
    """True when the user has already typed a command plus an argument."""
    return len((value or "").strip().split(None, 1)) > 1


def slash_matches(
    value: str,
    commands: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Prefix-match slash commands. Empty once the user starts typing args."""
    raw = value or ""
    if not raw.startswith("/"):
        return []
    if has_args(raw):
        return []
    token = command_token(raw)
    if not token:
        return []
    return [(cmd, desc) for cmd, desc in commands if cmd.startswith(token)]


def enter_completes_autocomplete(
    value: str,
    matches: list[tuple[str, str]],
) -> bool:
    """Enter fills a suggestion only for an incomplete unique prefix.

    Already-complete commands (``/session``) and commands with args
    (``/session 12``) must submit instead.
    """
    if not matches:
        return False
    if has_args(value):
        return False
    token = (value or "").strip()
    if any(cmd == token for cmd, _desc in matches):
        return False
    return True
