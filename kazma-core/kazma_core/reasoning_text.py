"""A model's thinking text is kept out of its answer when it is certainly
thinking, for any provider.

Most providers send a thinking model's reasoning in a field of its own, which
Kazma never reads into the answer. Some send it inside the content: a
``<think>`` block before the answer (vLLM, llama.cpp, Ollama with no reasoning
parser), parts of their own in a list (Mistral), or -- Qwen, once in 36
streamed replies in the real-use runs of 2026-11-22 -- a stray ``</think>``
with the answer on both sides of it.

Cutting a real answer is worse than showing thinking text (the owner's rule,
2026-11-26), so text is taken out only in these shapes, and is the answer,
tags and all, in every other:

* a ``<think>`` (or ``<thinking>``) block at the START: the opening tag alone
  on its line; or on a line with its first word (no space between) and a
  closing tag that ends its line and has text after it. One whose opening tag
  is alone on its line and that is never closed is all thinking; one on a line
  with text that is never closed is thinking only when the answer was cut off
  by its length limit;
* a ``</think>`` with no opening tag anywhere before it and outside code, when
  the same text is on both sides of it; or it is alone on its line and the line
  before does not introduce it with a colon; or it closes a sentence
  (``Done.</think>``) at the end of its line. What comes after it is the answer
  (what came before, when nothing does).

Code is a fence of backticks or tildes, a code span, or an indented block.

:func:`split_reasoning` takes a whole text, :class:`ThinkFilter` the same text
as it streams. No I/O. Every client's text goes through here
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

#: What ends a sentence, before a closing tag at the end of its line.
_ENDS = ".!?؟…。"


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


def _find(low: str, tags: tuple[str, ...], start: int = 0) -> tuple[int, str]:
    """The first of *tags* in *low* (lower case) from *start*: ``(index, tag)``,
    or ``(-1, "")``."""
    best, found = -1, ""
    for tag in tags:
        at = low.find(tag, start)
        if at != -1 and (best == -1 or at < best):
            best, found = at, tag
    return best, found


def _in_code(before: str) -> bool:
    """True when the text after *before* is inside a code fence, a code span
    or an indented block."""
    fence = ""
    for line in before.split("\n")[:-1]:
        mark = line.lstrip()[:3]
        if mark in ("```", "~~~") and (not fence or mark == fence):
            fence = "" if fence else mark
    if fence:
        return True
    last = before.rsplit("\n", 1)[-1]
    if last.startswith(("    ", "\t")):
        return True
    return last.count("`") % 2 == 1


def _same_text(a: str, b: str) -> bool:
    a, b = a.strip(), b.strip()
    if a == b:
        return bool(a)
    n = min(len(a), len(b), 200)
    return n >= _SAME and a[:n] == b[:n]


def _ends_its_line(after: str) -> bool:
    return not after.strip() or after.lstrip(" \t\r").startswith("\n")


def _ends_reasoning(before: str, after: str) -> bool:
    """Whether a closing tag between *before* and *after* certainly ends
    thinking that had no opening tag."""
    if _in_code(before) or _find(before.lower(), _OPEN)[0] != -1:
        return False
    if _same_text(before, after):
        return True
    if not _ends_its_line(after):
        return False
    line = before.rsplit("\n", 1)[-1]
    if not line.strip():
        return not before.rstrip().endswith(":")  # "The tag is:" introduces it
    return line[-1] in _ENDS


def _stray_close(text: str) -> tuple[int, str]:
    """The first lone closing tag that ends reasoning, or ``(-1, "")``."""
    low = text.lower()
    at, tag = _find(low, _CLOSE)
    while at != -1:
        if _ends_reasoning(text[:at], text[at + len(tag):]):
            return at, tag
        at, tag = _find(low, _CLOSE, at + len(tag))
    return -1, ""


def _leading_block(text: str, cut: bool) -> tuple[int, int, int] | None:
    """The thinking block *text* starts with, as ``(start, end, rest)``, or
    None when it starts with none that is certain. *cut*: the answer ended at
    its length limit."""
    lead = len(text) - len(text.lstrip())
    tag = _starts_with(text[lead:], _OPEN)
    if not tag:
        return None
    start = lead + len(tag)
    after = text[start:]
    alone = _ends_its_line(after)
    at, close = _find(text.lower(), _CLOSE, start)
    if at == -1:
        inline_cut = cut and after[:1].strip() != ""
        return (start, len(text), len(text)) if alone or inline_cut else None
    rest = at + len(close)
    if alone:
        return start, at, rest
    # On a line with text: "<think>Okay ...</think>\n\nThe answer".
    tail = text[rest:]
    glued = after[:1].strip() != ""
    return (start, at, rest) if glued and _ends_its_line(tail) and tail.strip() else None


def split_reasoning(text: str, *, cut: bool = False) -> tuple[str, str]:
    """``(answer, reasoning)`` of a model's whole *text*; *cut*: it ended at
    its length limit. Text with neither tag is returned as it is."""
    if not isinstance(text, str) or "think" not in text.lower():
        return text, ""
    reasoning: list[str] = []
    rest = text
    while (block := _leading_block(rest, cut)) is not None:
        start, end, after = block
        reasoning.append(rest[start:end].strip())
        rest = rest[after:].lstrip()
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
    the final response's (``turn_complete`` replaces what streamed). A model
    whose thinking has no opening tag streams it until its closing tag; what
    was shown so is :attr:`retracted`, so it is not kept as the turn's words."""

    def __init__(self) -> None:
        self._held = ""
        self._state = "start"  # start | think | inline | answer | doubt
        self._shown: list[str] = []
        #: What streamed and was thinking after all; "" when nothing was.
        self.retracted = ""

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
                line = text[len(tag):].lstrip(" \t\r")
                if not line:
                    return "", False  # what follows the tag decides
                self._state = "think" if line.startswith("\n") else "inline"
                if self._state == "think":
                    self._held = text[len(tag):]
                return "", True
            if not text or any(t.startswith(text.lower()) for t in _OPEN):
                return "", False  # it may still become a tag
            self._state = "answer"
            return "", True
        if self._state == "think":
            at, close = _find(self._held.lower(), _CLOSE)
            if at == -1:
                # The reasoning itself is not kept, only what may be the
                # start of its closing tag.
                self._held = self._held[-len(_CLOSE[1]):]
                return "", False
            self._held, self._state = self._held[at + len(close):].lstrip(), "start"
            return "", True
        if self._state == "answer":
            return self._answer()
        return "", False  # inline, doubt: held to the end

    def _answer(self) -> tuple[str, bool]:
        low = self._held.lower()
        at, close = _find(low, _CLOSE)
        while at != -1:
            before = "".join(self._shown) + self._held[:at]
            if not _in_code(before) and _find(before.lower(), _OPEN)[0] == -1:
                # A stray closing tag: what comes next decides what it was.
                shown, self._held, self._state = self._held[:at], self._held[at:], "doubt"
                return shown, False
            at, close = _find(low, _CLOSE, at + len(close))
        keep = _tail_of_a_tag(self._held)
        shown = self._held[: len(self._held) - keep]
        self._held = self._held[len(self._held) - keep:]
        return shown, False

    def finish(self, *, cut: bool = False) -> str:
        """What was held back and is answer after all. *cut*: the answer ended
        at its length limit."""
        held, self._held = self._held, ""
        if self._state == "think":
            return ""
        before = "".join(self._shown)
        if self._state in ("start", "inline", "doubt"):
            answer, reasoning = split_reasoning(before + held, cut=cut)
            if reasoning or answer != before + held:
                # The final response says what the answer is; in doubt, what
                # streamed before the tag was thinking (or the answer twice).
                if self._state != "doubt":
                    self._shown.append(answer)
                    return answer
                if answer.strip() != before.strip() and not _same_text(before, answer):
                    self.retracted = before
                return ""
        self._shown.append(held)
        return held


def _tail_of_a_tag(text: str) -> int:
    """How many characters at the end of *text* may be the start of a closing tag."""
    at = text.rfind("<", max(0, len(text) - len(_CLOSE[1])))
    if at == -1:
        return 0
    tail = text[at:].lower()
    return len(tail) if any(t.startswith(tail) for t in _CLOSE) else 0
