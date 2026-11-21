"""Whether a finished turn has anything to teach (the memory framework audit's
F13, decision 12): a lesson is learned on a signal, never on every turn.

Until 2026-11 every finished turn memory kept sent its question and answer to
the Meta-Refiner -- one more model call per turn -- and the Soul kept the
newest twelve lessons, so one real correction was pushed out by twelve routine
turns. Now a turn teaches only when it carries one of three signals, read from
the turn itself with no model call:

* **correction** -- the user's message says Kazma's previous answer was wrong
  or not what they asked ("No, I meant the other file", "that's not what I
  asked for", "لا، قصدي الملف الثاني", "هذا غلط"). There must be a previous
  answer to correct.
* **recovered failure** -- a tool failed in this turn and a later tool call
  of the same turn succeeded: Kazma found the way around, which is worth
  keeping.
* **feedback** -- the user says how Kazma should work from now on, or judges
  how it answered ("from now on keep it short", "too long", "exactly what I
  wanted", "المرة الجاية لا تكرر", "اختصر ردودك").

Only the user's own words count (``memory.own_words``: quoted, pasted and
attached text is not theirs), quoted spans are left out, and the two
languages are read the same way: the Arabic phrases are folded as the text is
(``documents.arabic.fold_for_search``: letter forms and marks). Greetings and
thanks alone are no signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["LessonSignal", "read_signal"]

#: The kinds of signal, in the order they are looked for.
SIGNAL_KINDS = ("correction", "recovered_failure", "feedback")
#: What the refiner is shown of the evidence, at most.
_EVIDENCE_CHARS = 300


@dataclass(frozen=True, slots=True)
class LessonSignal:
    """What a turn has to teach from: its kind and the words or tool result
    that carry it (bounded; the refiner shows it inside a data fence)."""

    kind: str
    evidence: str


def _fold(text: str) -> str:
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(text or "").lower()


def _phrases(*items: str) -> str:
    """An alternation of *items*, each folded as the text is and escaped,
    longest first; a space in a phrase matches any run of spaces."""
    folded = sorted({_fold(i).strip() for i in items if i.strip()}, key=len, reverse=True)
    return "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in folded)


# ── Corrections ──────────────────────────────────────────────────────────

#: "no"/"لا" opening a sentence, followed by what the user meant instead.
_NO_EN = re.compile(r"^(?:no|nope|nah)\b[\s,.!:;-]{1,4}(?:\w+\W+){2}", re.IGNORECASE)
_NO_AR = re.compile(
    r"^لا(?:\s*[،,.!:؛]\s*(?:\w+\W+){1}|\s+(?:" + _phrases(
        "قصدي", "اقصد", "أقصد", "مو", "مب", "مش", "ابي", "أبي", "ابغى", "أبغى", "اريد", "أريد",
        "هذا", "هذي", "الصح", "الصحيح", "غلط", "خطأ",
    ) + r")\b)"
)
_CORRECTION = re.compile(
    r"(?:"
    # English
    r"\b(?:that'?s|that\s+is|this\s+is|it'?s|it\s+is|you'?re|you\s+are)\s+(?:wrong|incorrect|mistaken)\b"
    r"|\b(?:that'?s|that\s+is|this\s+is|it'?s|it\s+is)\s+not\s+(?:right|correct|what\s+i\s+(?:asked|meant|wanted|said|need(?:ed)?))\b"
    r"|\bnot\s+what\s+i\s+(?:asked|meant|wanted|said|need(?:ed)?)\b"
    r"|\byou\s+(?:misunderstood|misread|got\s+it\s+wrong|got\s+that\s+wrong|ignored\s+what\s+i)\b"
    r"|^i\s+(?:said|meant|asked\s+for|told\s+you)\b"
    r"|\b(?:still|again)\s+(?:wrong|broken|not\s+working|incorrect)\b"
    r"|\b(?:that|it)\s+(?:didn'?t|did\s+not|doesn'?t|does\s+not)\s+work\b"
    # Arabic (folded)
    r"|^(?:(?:" + _phrases("هذا", "هذي", "كلامك", "جوابك", "الجواب", "ردك", "الرد", "إجابتك") + r")\s+)?(?:"
    + _phrases("غلط", "خطأ", "مو صح", "مب صح", "مش صح", "غير صحيح", "ليس صحيحا", "ليس صحيح") + r")\b"
    r"|\b(?:" + _phrases(
        "غلطت", "انت غلطان", "أنت غلطان", "فهمتني غلط", "ما فهمتني", "ما فهمت قصدي", "قصدي",
        "كنت أقصد", "كنت اقصد", "ما زبط", "ما ضبط", "ما اشتغل", "لسا غلط", "لسه غلط",
        "بعده غلط", "ما زال خطأ", "ما زال غلط",
    ) + r")\b"
    r"|\b(?:مو|مب|مش|ليس)\s+(?:هذا|هذي|هذه)\s+(?:اللي|الذي|ما)\s+(?:" + _phrases(
        "طلبت", "طلبته", "ابي", "أبي", "ابيه", "أبيه", "ابغى", "أبغى", "اريد", "أريد", "قصدت", "اقصد", "أقصد",
    ) + r")\b"
    r")",
    re.IGNORECASE,
)

# ── Feedback ─────────────────────────────────────────────────────────────

_FEEDBACK = re.compile(
    r"(?:"
    # English: from now on
    r"\b(?:from\s+now\s+on|going\s+forward|in\s+(?:the\s+)?future|next\s+time|from\s+here\s+on)\b"
    r"|(?:^|[.!?]\s+)(?:please\s+)?(?:always|never)\s+\w+"
    r"|\b(?:you\s+should(?:n'?t|\s+not)?|please\s+don'?t|don'?t\s+ever)\b"
    r"|\bstop\s+(?:doing|using|adding|writing|giving|asking|saying|putting|including|making)\b"
    r"|\bdon'?t\s+\w+(?:\s+\w+){0,6}\s+(?:again|anymore|any\s+more)\b"
    # English: how it answered
    r"|\btoo\s+(?:long|short|verbose|wordy|formal|casual|technical|detailed|vague|brief)\b"
    r"|\bexactly\s+what\s+i\s+(?:wanted|needed|asked\s+for)\b"
    r"|\b(?:that|this|it)\s+(?:was|is)\s+(?:perfect|great|exactly\s+right|just\s+right)\b"
    r"|\b(?:keep|stick\s+to)\s+(?:it|this|that)\s+(?:short|brief|format|style|way)\b"
    r"|\bi\s+(?:prefer|like|want)\s+(?:it\s+)?when\s+you\b"
    r"|\b(?:keep|make)\s+(?:your\s+)?(?:answers|replies|responses)\b"
    # Arabic (folded)
    r"|\b(?:" + _phrases(
        "من الحين", "من الآن", "من الان", "من هني ورايح", "من هنا ورايح", "من هنا وطالع",
        "المرة الجاية", "المرة القادمة", "المرة الجاية لا", "في المستقبل", "بعد كذا", "بعد جذي",
        "لا تعيد", "لا تكرر", "لا تعيدها", "لا تكررها", "طويل وايد", "طويلة وايد", "طويل جدا",
        "طويلة جدا", "قصير وايد", "قصير جدا", "اختصر", "اختصري", "بالضبط اللي أبي", "بالضبط اللي ابي",
        "بالضبط اللي طلبته", "بالضبط ما أريد", "بالضبط ما اريد", "هذا اللي أبيه", "هذا اللي ابيه",
        "ردودك", "أجوبتك", "اجوبتك", "إجاباتك", "اجاباتك",
    ) + r")\b"
    r"|(?:^|[.!؟?،]\s*)(?:" + _phrases("دايما", "دائما", "دايم", "أبدا", "ابدا", "لا تسوي", "لا تستخدم",
                                         "لا تكتب", "لا تحط", "لا تضيف", "لا ترسل") + r")\b"
    r")",
    re.IGNORECASE,
)

#: A polite request is how feedback is often asked ("Can you keep it short
#: from now on?"); ``own_words`` leaves English questions out, so these are
#: read from the message as well.
_REQUEST_EN = re.compile(r"(?:^|(?<=[.!?]\s))(?:can|could|would|will)\s+you\b[^?]{0,300}\?", re.IGNORECASE)
_QUOTED = re.compile(r"\"[^\"\n]{0,600}\"|“[^”\n]{0,600}”|«[^»\n]{0,600}»")


def _own(text: str) -> str:
    from kazma_core.memory.own_words import own_words

    return _QUOTED.sub(" ", own_words(text or "")).strip()


def _small_talk(text: str) -> bool:
    from kazma_core.memory.episode_text import is_small_talk_words

    return is_small_talk_words(text)


def _evidence(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= _EVIDENCE_CHARS else text[: _EVIDENCE_CHARS - 1].rstrip() + "…"


def _message(m: Any) -> dict[str, Any]:
    if isinstance(m, dict):
        return m
    role = getattr(m, "type", "") or getattr(m, "role", "")
    role = {"human": "user", "ai": "assistant"}.get(role, role)
    return {"role": role, "content": getattr(m, "content", ""),
            "tool_call_id": getattr(m, "tool_call_id", None), "tool_calls": getattr(m, "tool_calls", None)}


def _text(content: Any) -> str:
    from kazma_core.memory.consolidator import message_text

    return message_text(content) or ""


def _correction(question: str, previous_answer: str) -> LessonSignal | None:
    if not previous_answer.strip():
        return None  # nothing to correct
    own = _own(question)
    if not own or _small_talk(own):
        return None
    folded = _fold(own)
    if _NO_EN.match(folded) or _NO_AR.match(folded) or _CORRECTION.search(folded):
        return LessonSignal("correction", _evidence(own))
    return None


def _feedback(question: str) -> LessonSignal | None:
    own = _own(question)
    asked = " ".join(m.group(0) for m in _REQUEST_EN.finditer(_QUOTED.sub(" ", question or "")))
    said = " ".join(p for p in (own, asked) if p)
    if not said or _small_talk(said):
        return None
    if _FEEDBACK.search(_fold(said)):
        return LessonSignal("feedback", _evidence(said))
    return None


def _recovered_failure(turn: list[dict[str, Any]], tool_results: dict[str, Any]) -> LessonSignal | None:
    """A tool that failed in the running turn, then a later call that
    worked."""
    from kazma_core.agent.tool_loop_breaker import ToolOutcome, classify_tool_result

    names: dict[str, str] = {}
    for m in turn:
        for call in m.get("tool_calls") or []:
            cid = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            if not name and isinstance(call, dict):
                name = (call.get("function") or {}).get("name")
            if cid:
                names[str(cid)] = str(name or "")
    failed: tuple[str, str] | None = None
    for m in turn:
        if m.get("role") != "tool":
            continue
        cid = str(m.get("tool_call_id") or "")
        result = dict(tool_results.get(cid) or {}) if isinstance(tool_results, dict) else {}
        result.setdefault("content", _text(m.get("content")))
        result.setdefault("name", names.get(cid, ""))
        outcome = classify_tool_result(result)
        if outcome in (ToolOutcome.HARD, ToolOutcome.TRANSIENT, ToolOutcome.REFUSED, ToolOutcome.POLICY):
            failed = (str(result.get("name") or names.get(cid) or "a tool"), str(result.get("content") or ""))
        elif outcome == ToolOutcome.OK and failed is not None:
            worked = str(result.get("name") or names.get(cid) or "a tool")
            first = (failed[1].strip().splitlines() or [""])[0]
            return LessonSignal(
                "recovered_failure",
                _evidence(f"{failed[0]} failed ({first}); then {worked} worked"),
            )
    return None


def read_signal(messages: list[Any], *, tool_results: dict[str, Any] | None = None) -> LessonSignal | None:
    """What the turn *messages* end on has to teach from, or None.

    *messages* is the graph's terminal state (the whole chat, this turn last);
    *tool_results* its ``tool_results`` (by tool call id). No model call.
    """
    msgs = [_message(m) for m in messages or []]
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    if last_user < 0:
        return None
    question = _text(msgs[last_user].get("content"))
    previous = next(
        (_text(m.get("content")) for m in reversed(msgs[:last_user])
         if m.get("role") == "assistant" and _text(m.get("content")).strip()),
        "",
    )
    return (
        _correction(question, previous)
        or _recovered_failure(msgs[last_user + 1:], tool_results or {})
        or _feedback(question)
    )
