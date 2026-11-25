"""A model's thinking text is never part of its answer, for any provider.

Most providers send a thinking model's reasoning in a field of its own, which
Kazma never reads into the answer. Some send it inside the content: a
``<think>`` block before the answer (vLLM, llama.cpp, Ollama with no reasoning
parser), parts of their own in a list (Mistral), or -- Qwen, once in 36
streamed replies in the real-use runs of 2026-11-22 -- a stray ``</think>``
with the answer on both sides of it, which a person read as it came.

:func:`split_reasoning` takes a whole text, :class:`ThinkFilter` the same text
as it streams; both follow one rule:

* a ``<think>`` (or ``<thinking>``) block at the START of the answer is
  reasoning; one never closed is all reasoning;
* a lone ``</think>`` ends reasoning that had no opening tag -- when it stands
  alone on its line, or the same text is on both sides of it. What comes after
  it is the answer (what came before, when nothing does);
* anything else is the answer, tags and all: a reply that explains the tag, or
  shows it in code, is not cut.

No I/O. Every client's text goes through here
(``tests/test_reasoning_is_never_the_answer.py``).
"""

from __future__ import annotations

from typing import Any

__all__ = ["REASONING_PARTS", "ThinkFilter", "split_reasoning", "text_of_parts"]

_OPEN = ("<think>", "<thinking>")
_CLOSE = ("</think>", "</thinking>")

#: The part types of a content list that are reasoning, not answer.
REASONING_PARTS = frozenset({"thinking", "reasoning", "redacted_thinking", "reasoning_content"})

#: How much of the two sides of a stray tag must agree to be one text twice.
_SAME = 20


def text_of_parts(content: Any) -> str:
    """The answer text of a message's or delta's ``content``: a string, or a
    list of parts without its reasoning parts."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out: list[str] = []
    for part in content:
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, dict) and str(part.get("type") or "").lower() not in REASONING_PARTS:
            out.append(str(part.get("text") or part.get("content") or ""))
    return "".join(out)


def _starts_with(text: str, tags: tuple[str, ...]) -> str:
    """The tag *text* starts with, or ""."""
    head = text[:12].lower()
    return next((t for t in tags if head.startswith(t)), "")


def _find(text: str, tags: tuple[str, ...], start: int = 0) -> tuple[int, str]:
    """The first of *tags* in *text* from *start*: ``(index, tag)``, or ``(-1, "")``."""
    low = text.lower()
    best, found = -1, ""
    for tag in tags:
        at = low.find(tag, start)
        if at != -1 and (best == -1 or at < best):
            best, found = at, tag
    return best, found


def _in_code(before: str) -> bool:
    """True when the text after *before* is inside a code fence or a code span."""
    if before.count("```") % 2:
        return True
    line = before.rsplit("\n", 1)[-1]
    return line.count("`") % 2 == 1


def _same_text(a: str, b: str) -> bool:
    a, b = a.strip(), b.strip()
    if a == b:
        return bool(a)
    n = min(len(a), len(b), 200)
    return n >= _SAME and a[:n] == b[:n]


def _stray_close(text: str) -> tuple[int, str]:
    """The first lone closing tag that ends reasoning, or ``(-1, "")``."""
    at, tag = _find(text, _CLOSE)
    while at != -1:
        before, after = text[:at], text[at + len(tag):]
        if not _in_code(before):
            alone = (not before.strip() or before.rstrip(" \t").endswith("\n")) and (
                not after.strip() or after.lstrip(" \t\r").startswith("\n"))
            if alone or _same_text(before, after):
                return at, tag
        at, tag = _find(text, _CLOSE, at + len(tag))
    return -1, ""


def split_reasoning(text: str) -> tuple[str, str]:
    """``(answer, reasoning)`` of a model's whole *text*. Text with neither
    tag is returned as it is, the same object."""
    if not isinstance(text, str) or "think" not in text.lower():
        return text, ""
    reasoning: list[str] = []
    rest = text
    while True:
        opened = rest.lstrip()
        tag = _starts_with(opened, _OPEN)
        if not tag:
            break
        at, close = _find(opened, _CLOSE, len(tag))
        if at == -1:
            return "", "\n".join([*reasoning, opened[len(tag):].strip()]).strip()
        reasoning.append(opened[len(tag):at].strip())
        rest = opened[at + len(close):].lstrip()
    at, close = _stray_close(rest)
    while at != -1:
        before, after = rest[:at], rest[at + len(close):]
        if not after.strip():
            rest = before.rstrip()  # nothing after it: what came before is the answer
            break
        reasoning.append(before.strip())
        rest = after.lstrip()
        at, close = _stray_close(rest)
    return rest, "\n".join(r for r in reasoning if r)


class ThinkFilter:
    """:func:`split_reasoning` for text that streams: :meth:`feed` answers what
    may be shown now, :meth:`finish` what was still held. The whole answer is
    the final response's (``turn_complete`` replaces what streamed)."""

    def __init__(self) -> None:
        self._held = ""
        self._state = "start"  # start | think | answer | doubt
        self._shown: list[str] = []

    def feed(self, piece: str) -> str:
        """The part of *piece* (and of what was held) that may be shown."""
        self._held += piece
        out = ""
        while True:
            shown, again = self._step()
            out += shown
            if not again:
                break
        if out:
            self._shown.append(out)
        return out

    def _step(self) -> tuple[str, bool]:
        if self._state == "start":
            text = self._held.lstrip()
            tag = _starts_with(text, _OPEN)
            if tag:
                self._held, self._state = text[len(tag):], "think"
                return "", True
            if not text or any(t.startswith(text.lower()) for t in _OPEN):
                return "", False  # it may still become a tag
            self._state = "answer"
            return "", True
        if self._state == "think":
            at, close = _find(self._held, _CLOSE)
            if at == -1:
                # The reasoning itself is not kept, only what may be the
                # start of its closing tag.
                self._held = self._held[-len(_CLOSE[1]):]
                return "", False
            self._held, self._state = self._held[at + len(close):].lstrip(), "start"
            return "", True
        if self._state == "answer":
            at, _close = _find(self._held, _CLOSE)
            while at != -1 and _in_code("".join(self._shown) + self._held[:at]):
                at, _close = _find(self._held, _CLOSE, at + 1)
            if at != -1:
                # A stray closing tag: what comes next decides what it was.
                shown, self._held, self._state = self._held[:at], self._held[at:], "doubt"
                return shown, False
            keep = _tail_of_a_tag(self._held)
            shown = self._held[: len(self._held) - keep]
            self._held = self._held[len(self._held) - keep:]
            return shown, False
        return "", False  # doubt: held to the end

    def finish(self) -> str:
        """What was held back and is answer after all."""
        held, self._held = self._held, ""
        if self._state in ("think", "start"):
            return held if self._state == "start" and held.strip() else ""
        before = "".join(self._shown)
        if self._state == "doubt":
            answer, reasoning = split_reasoning(before + held)
            if reasoning or answer != before + held:
                return ""  # the final response says what the answer is
        self._shown.append(held)
        return held


def _tail_of_a_tag(text: str) -> int:
    """How many characters at the end of *text* may be the start of a closing tag."""
    at = text.rfind("<", max(0, len(text) - len(_CLOSE[1])))
    if at == -1:
        return 0
    tail = text[at:].lower()
    return len(tail) if any(t.startswith(tail) for t in _CLOSE) else 0
