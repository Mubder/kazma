"""Candidate relative-time resolver + remind conflict detection (Phase 0, §R2.4).

This is the heuristic implementation G1 (latency) and G2 (accuracy) measure.
It is deliberately heuristic — no extra LLM call (plan §R2.7). The two things
it must get right, because they are the CoPilot incident class:

  1. **Anchor resolution.** A relative phrase ("in 2 days") must bind to a
     memory event ("before the CoPilot reset") when one is referenced, NOT
     invent a new absolute event from ``request_at``. The incident happened
     because the agent treated "in 2 days" as the event date (now + 2d) and
     then overwrote the real ``copilot_next_reset = 2026-09-01``.

  2. **Ambiguity surfacing.** A bare relative phrase with a *relevant* event
     in memory but no explicit "before <event>" cue is ambiguous → clarify,
     never silently pick one interpretation.

Design notes
------------
- Language: EN + AR (Arabic-Indic + Eastern Arabic digits normalized).
  Arabic is a first-class corpus language (plan §R2.3), not an afterthought.
- Belief objects are free-text dates ("2026-09-01", "August 10, 02:48") —
  parsed leniently via ``dateutil`` when available, else ISO/fallbacks.
- The resolver NEVER overwrites a belief. A resolution implying a contradicting
  event date returns a conflict/clarify, not a silent supersede.
- This is a candidate: G2 measures its false-allow / false-clarify. If it
  cannot reach zero false-allow on held-out goldens at an acceptable clarify
  rate, plan §R2.2 triggers structured tool args before Phase 2.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "TimeExpression",
    "RemindResolution",
    "parse_time_expressions",
    "resolve_remind",
    "detect_conflicts",
    "normalize_digits",
    "parse_belief_date",
]


# ──────────────────────────────────────────────────────────────────────────
# Digit + language normalization
# ──────────────────────────────────────────────────────────────────────────

_AR_INDIC = "٠١٢٣٤٥٦٧٨٩"      # Arabic-Indic
_AR_EASTERN = "۰۱۲۳۴۵۶۷۸۹"    # Eastern Arabic (Persian/Urdu)
_DIGIT_MAP = str.maketrans(_AR_INDIC + _AR_EASTERN, "0123456789" * 2)


def normalize_digits(text: str) -> str:
    """Map Arabic-Indic / Eastern Arabic digits to ASCII 0-9."""
    return (text or "").translate(_DIGIT_MAP)


# ──────────────────────────────────────────────────────────────────────────
# Unit maps (EN + AR) → seconds
# ──────────────────────────────────────────────────────────────────────────

_UNIT_SECONDS_EN = {
    "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
    "week": 604800, "weeks": 604800,
    "month": 2592000, "months": 2592000,  # 30d approx
}

# Arabic unit forms → (per_unit_seconds, implied_count_or_None).
#   implied_count: 1 (singular يوم), 2 (dual يومين), None (plural أيام —
#   needs a digit). _parse_ar resolves the count from the word form when no
#   digit is present, and binds direction to the nearest preceding قبل/بعد so
#   both "قبل يومين من X" (adjacent) and "قبل X بـيومين" (بـ prefix) resolve.
_AR_FORM_TO_SECS: dict[str, tuple[int, int | None]] = {
    # dual (implied count 2)
    "يومين": (86400, 2), "ساعتين": (3600, 2), "أسبوعين": (604800, 2),
    "اسبوعين": (604800, 2), "دقيقتين": (60, 2),
    # singular (implied count 1)
    "يوم": (86400, 1), "ساعة": (3600, 1), "أسبوع": (604800, 1),
    "اسبوع": (604800, 1), "دقيقة": (60, 1), "شهر": (2592000, 1),
    # plural (count comes from a digit)
    "ايام": (86400, None), "أيام": (86400, None), "ساعات": (3600, None),
    "أسابيع": (604800, None), "اسابيع": (604800, None),
    "دقائق": (60, None), "أشهر": (2592000, None), "اشهر": (2592000, None),
}


def _lead_seconds(count: int, unit: str) -> int | None:
    """Resolve (count, unit) → seconds, checking EN then AR tables."""
    u = unit.strip().lower()
    if u in _UNIT_SECONDS_EN:
        return count * _UNIT_SECONDS_EN[u]
    if unit in _UNIT_SECONDS_AR:  # AR keys are case-sensitive
        return count * _UNIT_SECONDS_AR[unit]
    return None


# ──────────────────────────────────────────────────────────────────────────
# Regexes — relative time phrases (compiled once)
# ──────────────────────────────────────────────────────────────────────────

# "in 2 days", "in 30 minutes", "in 3 weeks"
_RE_IN_N_EN = re.compile(
    r"\bin\s+(\d+)\s+(min(?:ute)?s?|hrs?|hours?|days?|weeks?|months?)\b",
    re.IGNORECASE,
)
# "2 days before", "30 min before", "3 hours after"  (direction separate)
_RE_N_UNITS_EN = re.compile(
    r"(\d+)\s+(min(?:ute)?s?|hrs?|hours?|days?|weeks?|months?)",
    re.IGNORECASE,
)
# bare calendar relatives
_RE_CALENDAR_EN = re.compile(
    r"\b(tomorrow|next\s+week|next\s+month|in\s+a\s+week|in\s+a\s+day)\b",
    re.IGNORECASE,
)

# Capture an optional digit + an Arabic unit form anywhere in text. Direction
# is bound separately (nearest قبل/بعد) so both adjacent and بـ-prefix
# structures resolve. Longer (dual) forms are listed before singular so the
# regex prefers them. The lookbehind/lookahead on Arabic letters (incl.
# tatweel + diacritics) prevent a unit matching INSIDE a longer word — e.g.
# "اسبوع" must not match inside the adjective "الاسبوعية", nor "شهر" inside
# "الشهرية" (those are event-alias words, not time units).
# Real Arabic letters only (excludes tatweel U+0640 + diacritics). Used for
# word-boundary guards so a unit matches after the بـ prefix's tatweel but NOT
# inside a larger word like "الاسبوعية" / "الشهرية".
_AR_LETTER_CLASS = r"[\u0621-\u063a\u0641-\u064a]"
_RE_AR_UNIT = re.compile(
    r"(?P<num>\d+|[٠-٩]+)?\s*"
    r"(?<![\u0621-\u063a\u0641-\u064a])(?P<unit>يومين|ساعتين|أسبوعين|اسبوعين|دقيقتين|"
    r"يوم|ايام|أيام|ساعة|ساعات|أسبوع|اسبوع|أسابيع|اسابيع|دقيقة|دقائق|شهر|أشهر|اشهر)"
    r"(?![\u0621-\u063a\u0641-\u064a])"
)
_RE_AR_DIR = re.compile(r"(بعد|قبل)")
# Direction cue (before/after the event) — English
_RE_BEFORE_EVENT_EN = re.compile(
    r"\b(before|prior\s+to|ahead\s+of)\b", re.IGNORECASE
)
# "before the ... reset/ends"
_RE_BEFORE_THE_EN = re.compile(
    r"(before|prior\s+to|ahead\s+of)\s+(?:the\s+)?(.+?)(?:reset|ends?|renews?)\b",
    re.IGNORECASE,
)
# Arabic "قبل ... من" (before ... of)
_RE_AR_BEFORE = re.compile(r"قبل")


# ──────────────────────────────────────────────────────────────────────────
# Event aliases — map belief predicates to text a user might write
# ──────────────────────────────────────────────────────────────────────────

# Canonical alias table. Extend as new functional predicates ship. Aliases are
# matched case-insensitively as substrings (word-boundary preferred).
_EVENT_ALIASES_EN: dict[str, tuple[str, ...]] = {
    "copilot_next_reset": (
        "copilot reset", "copilot monthly reset", "copilot next reset",
        "github copilot reset", "copilot renewal", "copilot quota",
    ),
    "grok_next_reset": (
        "grok reset", "grok weekly reset", "grok next reset",
        "supergrok reset", "supergrok renewal",
    ),
    "zcode_next_reset": ("zcode reset", "zcode weekly reset", "zcode next reset"),
    "claude_next_reset": ("claude reset", "claude next reset", "anthropic reset"),
    "cursor_next_reset": ("cursor reset", "cursor next reset"),
    "openai_next_reset": ("openai reset", "openai next reset"),
    "chatgpt_next_reset": ("chatgpt reset", "chatgpt next reset"),
    "subscription_ends": (
        "subscription ends", "subscription end", "sub ends", "sub end",
        "subscription reset", "subscription resets",
        "subscription renews", "subscription renewal",
    ),
}

_EVENT_ALIASES_AR: dict[str, tuple[str, ...]] = {
    "copilot_next_reset": (
        "إعادة تعيين copilot", "تجديد copilot", "copilot الشهرية",
        "اعادة copilot",
    ),
    "grok_next_reset": ("إعادة تعيين grok", "تجديد grok", "grok الاسبوعية"),
    "subscription_ends": (
        "انتهاء الاشتراك", "تجديد الاشتراك", "انتهاء اشتراك",
    ),
}


def event_aliases(predicate: str, lang: str = "en") -> list[str]:
    """Return text aliases for a belief predicate (derived + canonical)."""
    p = (predicate or "").strip().lower()
    out: list[str] = []
    if lang == "ar":
        out.extend(_EVENT_ALIASES_AR.get(p, ()))
    else:
        out.extend(_EVENT_ALIASES_EN.get(p, ()))
        # Derive aliases from the predicate name: "grok_next_reset" → "grok reset"
        base = p
        for suf in ("_next_reset", "_weekly_reset", "_quota_reset", "_reset_at"):
            if base.endswith(suf):
                base = base[: -len(suf)]
                break
        if base and base != p:
            out.append(f"{base} reset")
            out.append(f"{base} renewal")
    # de-dup, preserve order
    seen: set[str] = set()
    return [a for a in out if not (a in seen or seen.add(a))]


# ──────────────────────────────────────────────────────────────────────────
# Date parsing for belief objects
# ──────────────────────────────────────────────────────────────────────────

def parse_belief_date(obj: str, *, default_year: int | None = None) -> datetime | None:
    """Leniently parse a belief object into a UTC datetime.

    Belief objects are free text: "2026-09-01", "August 10, 2026 02:48",
    "Sep 1". Tries ISO, then dateutil, then a few common formats. Returns
    None if unparseable (the caller treats an unparseable event date as
    "event referenced but date unknown" → clarify, never invent).
    """
    raw = normalize_digits((obj or "").strip())
    if not raw:
        return None
    # Strip trailing timezone-ish noise / local qualifiers the model appends.
    raw_clean = re.sub(r"\s+(local|local time|utc|gmt)$", "", raw, flags=re.I)

    # 1. ISO 8601 (with or without time/tz)
    iso_candidates = [raw_clean]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_clean):
        iso_candidates.append(raw_clean + "T00:00:00")
    for cand in iso_candidates:
        try:
            dt = datetime.fromisoformat(cand.replace("Z", "+00:00"))
            return _to_utc(dt)
        except ValueError:
            pass

    # 2. dateutil (best effort — optional dep)
    try:
        from dateutil import parser as _du_parser  # type: ignore

        dt = _du_parser.parse(raw_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # 3. Common explicit formats
    for fmt in ("%B %d, %Y %H:%M", "%B %d, %Y", "%b %d, %Y", "%B %d %Y",
                "%d %B %Y", "%d %b %Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(raw_clean, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ──────────────────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class TimeExpression:
    """One parsed time phrase."""
    phrase: str
    kind: str            # relative_from_now | relative_before_event | absolute | calendar
    lead: timedelta | None = None
    absolute: datetime | None = None
    direction: str = "after"   # before | after (for event-anchored)
    event_predicate: str | None = None


@dataclass
class RemindResolution:
    """Full resolution of a remind intent."""
    fire_at: datetime | None = None
    decision: str = "clarify"        # allow | clarify | deny
    reason: str = ""
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    anchor: str = "none"             # request_at | <belief_id/predicate> | none
    overwrite_belief: bool = False   # always False from this resolver
    time_expressions: list[TimeExpression] = field(default_factory=list)
    matched_events: list[str] = field(default_factory=list)
    # When decision == "clarify" due to a nearby-event ambiguity, the from-now
    # candidate is retained so the autonomous mode can allow-with-best-guess
    # instead of forcing a question (plan §9 Phase 6).
    candidate_fire_at: datetime | None = None
    # Phase 3: the concrete fire_at options for the clarify card. "memory_anchor"
    # = event_at − lead; "from_now" = request_at + lead. The card offers both.
    option_fire_ats: dict[str, datetime] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────
# Phrase parsing
# ──────────────────────────────────────────────────────────────────────────

# Absolute calendar date (ISO or "on YYYY-MM-DD"). Anchors to itself, not to
# request_at or an event — no overwrite risk.
_RE_ABSOLUTE_DATE = re.compile(
    r"\b(?P<date>\d{4}-\d{2}-\d{2})(?:[ T](?P<time>\d{2}:\d{2}(?::\d{2})?))?"
)


def _parse_ar(norm: str) -> list[TimeExpression]:
    """Parse Arabic time phrases (singular / dual / plural + بـ prefix).

    Direction is bound to the nearest preceding بعد/قبل so both structures
    resolve correctly:
      • "قبل يومين من إعادة …"   (adjacent)
      • "قبل إعادة … بـيومين"    (بـ prefix; direction word is separated
                                  from the unit by the event phrase)
    A bare unit with no preceding direction word defaults to "after" (from-now).
    """
    out: list[TimeExpression] = []
    dir_matches = list(_RE_AR_DIR.finditer(norm))
    for um in _RE_AR_UNIT.finditer(norm):
        form = um.group("unit")
        info = _AR_FORM_TO_SECS.get(form)
        if info is None:
            continue
        per_unit, default_count = info
        num = um.group("num")
        count = int(num) if num else default_count
        if count is None:
            continue  # plural with no digit → ambiguous count, skip
        nearest = None
        for dm in dir_matches:
            if dm.start() <= um.start():
                nearest = dm.group(1)
            else:
                break
        direction = "before" if nearest == "قبل" else "after"
        out.append(TimeExpression(
            phrase=um.group(0),
            kind="relative_before_event" if direction == "before" else "relative_from_now",
            lead=timedelta(seconds=count * per_unit), direction=direction,
        ))
    return out


def parse_time_expressions(
    text: str, *, request_at: datetime | None = None,
) -> list[TimeExpression]:
    """Extract all time expressions from *text*. Pure parse — no anchoring."""
    norm = normalize_digits(text or "")
    exprs: list[TimeExpression] = []
    now = request_at or datetime.now(timezone.utc)

    # --- Arabic (singular/dual/plural + بـ prefix) ---
    exprs.extend(_parse_ar(norm))

    # --- English: "in N units" (from-now default; before/after refined later) ---
    for m in _RE_IN_N_EN.finditer(norm):
        count = int(m.group(1))
        secs = _lead_seconds(count, m.group(2))
        if secs is not None:
            exprs.append(TimeExpression(
                phrase=m.group(0), kind="relative_from_now",
                lead=timedelta(seconds=secs), direction="after",
            ))
    # --- English: bare "N units" (e.g. "2 days before") — capture count+unit ---
    for m in _RE_N_UNITS_EN.finditer(norm):
        if m.group(0) in {e.phrase for e in exprs}:
            continue
        count = int(m.group(1))
        secs = _lead_seconds(count, m.group(2))
        if secs is not None:
            # direction determined by a nearby before/after cue in resolve_remind
            exprs.append(TimeExpression(
                phrase=m.group(0), kind="relative_from_now",
                lead=timedelta(seconds=secs), direction="after",
            ))

    # --- bare calendar relatives (tomorrow, next week) ---
    for m in _RE_CALENDAR_EN.finditer(norm):
        word = m.group(1).lower()
        if "tomorrow" in word or "a day" in word:
            lead = timedelta(days=1)
        elif "week" in word:
            lead = timedelta(weeks=1)
        else:
            lead = timedelta(days=30)
        exprs.append(TimeExpression(
            phrase=m.group(0), kind="calendar",
            lead=lead, direction="after",
        ))

    # --- absolute calendar dates ("on 2026-08-30", "في 2026-08-30") ---
    for m in _RE_ABSOLUTE_DATE.finditer(norm):
        try:
            d = datetime.fromisoformat(m.group("date"))
            t = m.group("time")
            if t:
                hh, mm, *_ = (int(x) for x in t.split(":"))
                d = d.replace(hour=hh, minute=mm)
            exprs.append(TimeExpression(
                phrase=m.group(0), kind="absolute",
                absolute=d.replace(tzinfo=timezone.utc), direction="after",
            ))
        except ValueError:
            continue

    return exprs


# ──────────────────────────────────────────────────────────────────────────
# Conflict detection
# ──────────────────────────────────────────────────────────────────────────

def detect_conflicts(
    proposed_fire_at: datetime | None,
    memory_beliefs: list[dict[str, Any]],
    *,
    matched_event_predicate: str | None = None,
) -> list[dict[str, Any]]:
    """Return conflicts between a proposed fire_at and known beliefs.

    A conflict arises when the resolution would *imply* an event date that
    contradicts a stored functional belief — i.e. the user/model is trying to
    anchor to an event whose stored date differs from what the math implies.
    This resolver flags it; it never silently overwrites (plan §3.6).
    """
    conflicts: list[dict[str, Any]] = []
    if proposed_fire_at is None or not memory_beliefs:
        return conflicts
    # (Future: cross-check fire_at against existing cron jobs for double-booking.
    # Out of scope for the candidate — the corpus measures date-conflict class.)
    return conflicts


# ──────────────────────────────────────────────────────────────────────────
# Event matching
# ──────────────────────────────────────────────────────────────────────────

def _match_events(
    text: str, memory_beliefs: list[dict[str, Any]],
) -> list[tuple[str, datetime | None, str]]:
    """Find memory beliefs referenced in *text*.

    Returns [(predicate, event_at_or_None, alias_matched), ...].
    """
    norm = normalize_digits(text or "").lower()
    hits: list[tuple[str, datetime | None, str]] = []
    seen_pred: set[str] = set()
    for b in memory_beliefs:
        pred = str(b.get("predicate") or "").strip().lower()
        if not pred or pred in seen_pred:
            continue
        for lang in ("en", "ar"):
            for alias in event_aliases(pred, lang=lang):
                if alias and alias.lower() in norm:
                    obj = str(b.get("object") or "")
                    event_at = parse_belief_date(obj)
                    hits.append((pred, event_at, alias))
                    seen_pred.add(pred)
                    break
            if pred in seen_pred:
                break
    return hits


def _has_before_cue(text: str) -> bool:
    norm = normalize_digits(text or "")
    if _RE_BEFORE_EVENT_EN.search(norm):
        return True
    if _RE_AR_BEFORE.search(norm):
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────
# Public: full remind resolution
# ──────────────────────────────────────────────────────────────────────────

# Window within which an unmentioned memory event is "plausibly relevant" to a
# from-now relative phrase, triggering a clarify. Tunable; measured by G2.
_RELEVANCE_WINDOW = timedelta(days=7)


def resolve_remind(
    text: str,
    *,
    request_at: datetime,
    memory_beliefs: list[dict[str, Any]] | None = None,
    lang_hint: str = "auto",
    relevance_window: timedelta | None = None,
) -> RemindResolution:
    """Resolve a remind intent to a fire_at or a clarify/deny.

    Decision logic (plan §3.7, §3.4):

    - **before <event>** + memory has event date → anchor to event,
      fire_at = event_at − lead. **allow** (no overwrite).
    - **relative from now**, no event mention, no relevant nearby event →
      fire_at = request_at + lead. **allow**.
    - **relative from now**, no event mention, BUT a memory event falls within
      the relevance window of the from-now fire → **ambiguous → clarify**
      ("did you mean before <event>?"). This is the CoPilot guard.
    - **event mentioned + relative time but no before/after cue** → ambiguous
      (relation unclear) → **clarify**.
    - **event mentioned but date unparseable** → **clarify** (never invent).
    - **no time expression at all** → **clarify** (missing slot).
    """
    memory_beliefs = memory_beliefs or []
    res = RemindResolution(anchor="none", overwrite_belief=False)
    res.time_expressions = parse_time_expressions(text, request_at=request_at)
    res.matched_events = [p for p, _, _ in _match_events(text, memory_beliefs)]

    matched = _match_events(text, memory_beliefs)
    before_cue = _has_before_cue(text)
    exprs = res.time_expressions

    if not exprs:
        res.decision = "clarify"
        res.reason = "no time expression found — ask when to fire"
        return res

    # Single expression path (multi-goal is out of scope for the candidate;
    # resolve_remind handles one fire_at. Multi-act ordering is plan §3.10.)
    expr = exprs[0]

    # Case 1: explicit "before <event>" + memory event date known
    if before_cue and matched:
        pred, event_at, _alias = matched[0]
        if event_at is None:
            res.decision = "clarify"
            res.reason = f"event {pred!r} referenced but date unparseable — ask"
            res.anchor = pred
            return res
        if expr.lead is None:
            # "before the reset" with no lead → default 1 day before? clarify.
            res.decision = "clarify"
            res.reason = "before <event> but no lead given — ask how far before"
            res.anchor = pred
            return res
        # direction before → event_at - lead
        fire_at = event_at - expr.lead
        res.fire_at = fire_at
        res.anchor = pred
        res.decision = "allow"
        res.reason = f"anchored to memory {pred}={event_at.date()}; fire = event - {expr.lead}"
        res.conflicts = detect_conflicts(fire_at, memory_beliefs, matched_event_predicate=pred)
        return res

    # Case 2: event mentioned + relative time but no before/after cue → ambiguous
    if matched and not before_cue:
        pred, _ev_at, _alias = matched[0]
        res.decision = "clarify"
        res.reason = (
            f"event {pred!r} mentioned with a relative time but no "
            "'before/after' cue — ask whether fire is before/after the event"
        )
        res.anchor = pred
        # Phase 3: concrete fire_at options for the clarify card.
        if _ev_at is not None and expr.lead is not None:
            res.option_fire_ats = {
                "memory_anchor": _ev_at - expr.lead,
                "from_now": request_at + expr.lead,
            }
        return res

    # Case 3: relative from now, no event mentioned
    if not matched:
        # from-now fire candidate
        if expr.kind in ("relative_from_now", "calendar") and expr.lead is not None:
            fire_at = request_at + expr.lead
            # check for a *relevant* unmentioned nearby event → ambiguous
            window = relevance_window if relevance_window is not None else _RELEVANCE_WINDOW
            nearby = _nearby_unmentioned_event(fire_at, memory_beliefs, window)
            if nearby:
                pred, event_at = nearby
                res.decision = "clarify"
                res.reason = (
                    f"relative time with no anchor, but memory event {pred!r} "
                    f"({event_at.date()}) is within the relevance window — ask "
                    f"whether you mean before {pred!r} or from now"
                )
                res.anchor = "request_at"
                res.fire_at = None
                res.candidate_fire_at = fire_at  # retained for autonomous mode
                # Phase 3: concrete fire_at options for the clarify card.
                res.option_fire_ats = {
                    "memory_anchor": event_at - expr.lead,
                    "from_now": fire_at,
                }
                return res
            res.fire_at = fire_at
            res.anchor = "request_at"
            res.decision = "allow"
            res.reason = f"anchored to request_at; fire = request + {expr.lead}"
            res.conflicts = detect_conflicts(fire_at, memory_beliefs)
            return res
        if expr.kind == "absolute" and expr.absolute is not None:
            res.fire_at = expr.absolute
            res.anchor = "absolute"
            res.decision = "allow"
            res.reason = "absolute date"
            return res
        # expr present but unusable
        res.decision = "clarify"
        res.reason = "time expression present but not resolvable — ask"
        return res

    # Fallback (shouldn't reach)
    res.decision = "clarify"
    res.reason = "unable to resolve — ask"
    return res


def _nearby_unmentioned_event(
    fire_at: datetime, memory_beliefs: list[dict[str, Any]], window: timedelta,
) -> tuple[str, datetime] | None:
    """Return a memory event whose date is within ±window of *fire_at*."""
    for b in memory_beliefs:
        pred = str(b.get("predicate") or "").strip().lower()
        event_at = parse_belief_date(str(b.get("object") or ""))
        if event_at is None:
            continue
        if abs((event_at - fire_at).total_seconds()) <= window.total_seconds():
            return pred, event_at
    return None
