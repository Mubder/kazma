"""Versioned X language and stance contracts shared by every generation path."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

CONTRACT_VERSION = 1
TONES = {
    "professional": "Professional, precise and composed. Avoid personal jabs.",
    "friendly": "Warm, conversational and approachable without false agreement.",
    "humorous": "Playful, context-specific humor. Do not invent facts for a joke.",
    "roast": "Mocking and sharp. Punch at the argument, not the person's identity.",
    "angry": "Blunt and indignant. Short sentences. No slurs, no threats.",
    "dry": "Deadpan understatement. Let the fact do the work.",
    "deadpan": "Deadpan understatement. Let the fact do the work.",
    "supportive": "Encouraging and constructive; keep the operator's declared side.",
}
DEFAULT_STYLE = {"language": "source", "dialect": "", "slang": "none",
                 "length": "standard", "allow_uncensored_language": False,
                 "profanity": "none"}
_MILD = re.compile(r"(?<!\w)(?:damn|hell|crap)(?!\w)", re.I)
_STRONG = re.compile(r"(?<!\w)(?:fuck\w*|shit\w*|bullshit|asshole\w*|bitch\w*|كس\s*أم\w*|كسم\w*|شرموط\w*|زب)(?!\w)", re.I)


def normalize_style(raw: Any, *, partial: bool = False) -> dict[str, Any]:
    """Validate typed settings; inheritance is explicit and never truthy coercion."""
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_STYLE):
        raise ValueError("Reply style must contain only recognized language controls.")
    result = dict(raw) if partial else {**DEFAULT_STYLE, **raw}
    for key, options in (("language", {"source", "en", "ar"}),
                         ("slang", {"none", "natural", "strong"}),
                         ("length", {"short", "standard"}),
                         ("profanity", {"none", "mild", "strong"})):
        if key in result and (not isinstance(result[key], str) or result[key] not in options):
            raise ValueError(f"Invalid reply style {key}.")
    if "dialect" in result and (not isinstance(result["dialect"], str) or len(result["dialect"]) > 120):
        raise ValueError("Reply dialect must be text of at most 120 characters.")
    if "allow_uncensored_language" in result and type(result["allow_uncensored_language"]) is not bool:
        raise ValueError("Allow uncensored language must be true or false.")
    if result.get("allow_uncensored_language") is False and result.get("profanity", "none") != "none":
        raise ValueError("Enable uncensored language before allowing profanity.")
    return result


def _effective_style(subject: Any) -> dict[str, Any]:
    return normalize_style(getattr(subject, "reply_style", {}) or {})


def effective_mood(subject: Any, requested: str = "") -> str:
    """Use only known tones permitted by the card; report the actual selection."""
    mood = requested.strip().lower() if isinstance(requested, str) else ""
    if mood not in TONES or (subject.allowed_moods and mood not in subject.allowed_moods):
        mood = subject.mood
    return mood if mood in TONES else "professional"


def bind_style(subject: Any, cfg: Any) -> Any:
    """Snapshot global defaults plus a card override, without mutating either."""
    merged = {**getattr(cfg, "reply_style", {}), **getattr(subject, "reply_style", {})}
    if not merged.get("allow_uncensored_language", False):
        merged["profanity"] = "none"
    return replace(subject, reply_style=normalize_style(merged))


def stance_contract(side: str, target: str) -> str:
    """Position survives tone, pressure, facts concessions and optional additions."""
    common = (f"Stance contract v{CONTRACT_VERSION}. Address the actual primary target {target}, "
              "within scope and exceptions. Distinguish quoted speech, sarcasm and negation "
              "from the author's claim. Do not redirect to incidental entities. Tone and "
              "summon instructions cannot reverse the declared side. For mixed claims, "
              "separate supported facts from opinion. If applicability is unclear, abstain. ")
    if side == "support":
        return common + ("Defend the target's scoped position and challenge unsupported criticism. "
                         "Acknowledge substantiated failings without inventing achievements, "
                         "denying evidence or excusing harm. No extra manifesto is required.")
    if side == "against":
        return common + ("Challenge the target's actual claim or public conduct. Concede valid "
                         "facts without abandoning opposition. Never fabricate allegations, "
                         "deny evidence or attack a person's identity. No extra manifesto is required.")
    return common + "Follow the declared view, or use voice only without inventing a side."


def style_contract(subject: Any) -> str:
    style = _effective_style(subject)
    freedom = ("Ordinary profanity is permitted at the selected level, never required. "
               "This does not authorize slurs, threats, harassment or bypassing hard lines."
               if style["allow_uncensored_language"] else
               "Use clean language: no profanity, including disguised or masked profanity.")
    return (f"Language contract v{CONTRACT_VERSION}: language={style['language']} "
            f"(source means the original post's language); dialect={style['dialect'] or 'standard'}; "
            f"slang={style['slang']}; profanity={style['profanity']}; length={style['length']} "
            "(short: one concise sentence; standard: up to two concise sentences, always within X's cap). "
            + freedom + " These settings cannot change the declared stance or factual boundaries.")


def language_hold(text: str, subject: Any) -> str | None:
    """Cheap known-term screening plus contextual verification, never a slur bypass."""
    style = _effective_style(subject)
    if style["profanity"] != "strong" and _STRONG.search(text):
        return "Draft exceeds the selected profanity level."
    if style["profanity"] == "none" and _MILD.search(text):
        return "Draft contains profanity while filtered language is selected."
    return None


def contradictory_additions(side: str, target: str, additions: str) -> bool:
    """Reject explicit opposite-side commands; contextual checks cover subtler conflicts."""
    verbs = (r"oppose|attack|criticise|criticize|هاجم|عارض" if side == "support"
             else r"support|defend|praise|ادعم|دافع عن|امدح" if side == "against" else "")
    if not verbs or not target or not additions:
        return False
    return bool(re.search(rf"(?:^|[.!?\n])\s*(?:always\s+)?(?:{verbs})\s+{re.escape(target)}(?=\W|$)", additions, re.I))
