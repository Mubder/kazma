"""Kazma's Markdown answer, as WhatsApp shows text.

WhatsApp has its own marks: ``*bold*``, ``_italic_``, ``~strikethrough~``,
```` ```monospace``` ```` and `` `inline code` ``. A Markdown ``**bold**``
shows its asterisks doubled there and a ``# heading`` its hash, so the answer
is converted: bold and headings to ``*...*``, Markdown italics to ``_..._``,
``~~x~~`` to ``~x~``, links to "text (address)". Code, fenced or inline, is
left exactly as written. Tables were already turned into lines by the gateway
(``chat_tables``).

A text message holds at most 4,096 characters: :func:`split_for_whatsapp`
cuts a long answer at paragraph, then line, boundaries, and never inside a
fenced code block when a block fits in one message.
"""

from __future__ import annotations

import re

__all__ = ["MAX_TEXT", "md_to_whatsapp", "split_for_whatsapp"]

#: WhatsApp's limit for a text message's body.
MAX_TEXT = 4096

_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`[^`\n]+`")
_HOLD = "\x00{}\x00"


def _hold(text: str, pattern: re.Pattern[str], held: list[str]) -> str:
    def keep(m: re.Match[str]) -> str:
        held.append(m.group(0))
        return _HOLD.format(len(held) - 1)

    return pattern.sub(keep, text)


def md_to_whatsapp(text: str) -> str:
    """*text* (Markdown) in WhatsApp's marks; code kept as written."""
    if not text:
        return ""
    held: list[str] = []
    out = _hold(text, _FENCE, held)
    out = _hold(out, _INLINE, held)
    # Images and links: the words, then the address.
    out = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", lambda m: f"{m.group(1) or 'image'} ({m.group(2)})", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1 (\2)", out)
    # Headings: the line in bold.
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", lambda m: "\x01" + m.group(1).strip() + "\x01", out)
    # Bold (** or __) first, held as \x01 so the italic pass leaves it.
    out = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", lambda m: "\x01" + m.group(1) + "\x01", out)
    out = re.sub(r"__(?=\S)(.+?)(?<=\S)__", lambda m: "\x01" + m.group(1) + "\x01", out)
    # A Markdown list item written "* item" keeps its bullet as "- ".
    out = re.sub(r"(?m)^(\s*)\*\s+", r"\1- ", out)
    # Markdown italics: *x* -> _x_ (a single asterisk pair inside a line).
    out = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"_\1_", out)
    out = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"~\1~", out)
    out = out.replace("\x01", "*")
    for index, original in enumerate(held):
        out = out.replace(_HOLD.format(index), original)
    return out


def _pieces(text: str) -> list[str]:
    """Paragraphs, keeping a fenced code block whole."""
    parts: list[str] = []
    cursor = 0
    for m in _FENCE.finditer(text):
        before = text[cursor:m.start()]
        parts.extend(p for p in re.split(r"\n{2,}", before) if p.strip())
        parts.append(m.group(0))
        cursor = m.end()
    parts.extend(p for p in re.split(r"\n{2,}", text[cursor:]) if p.strip())
    return parts


def _hard_split(piece: str, limit: int) -> list[str]:
    """A piece longer than *limit*: by lines, then by characters."""
    out: list[str] = []
    current = ""
    for line in piece.split("\n"):
        while len(line) > limit:
            if current:
                out.append(current)
                current = ""
            out.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            out.append(current)
            current = line
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def split_for_whatsapp(text: str, limit: int = MAX_TEXT) -> list[str]:
    """*text* in messages of at most *limit* characters each."""
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    messages: list[str] = []
    current = ""
    for piece in _pieces(text):
        for part in ([piece] if len(piece) <= limit else _hard_split(piece, limit)):
            candidate = f"{current}\n\n{part}" if current else part
            if len(candidate) > limit:
                messages.append(current)
                current = part
            else:
                current = candidate
    if current:
        messages.append(current)
    return [m for m in messages if m.strip()]
