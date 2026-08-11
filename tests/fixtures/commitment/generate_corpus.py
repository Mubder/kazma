"""Generate the Phase 0 relative-time corpus (plan §R2.3).

Outputs ``relative_time_corpus.jsonl`` next to this file. The expected
labels are defined SEMANTICALLY here (event_date − lead, request_at + lead),
NOT by running the resolver — so the corpus is honest ground truth and the
resolver (safety.commitment.relative_time) is the thing G2 measures.

Anchors (fixed so fire_at is deterministic):
  request_at            = 2026-08-11T10:00:00Z
  copilot_next_reset    = 2026-09-01   (21d away → outside 7d relevance window)
  grok_next_reset       = 2026-08-15   ( 4d away → inside window)
  subscription_ends     = 2026-08-13   ( 2d away → inside window)

Distribution target: ~50/50 EN/AR (≥40% AR), categories per §R2.3. Split:
golden (held-out CI, zero-false-allow gate) / train / hostile (mixed-script,
Arabic-Indic digits, noise).

Run:  python tests/fixtures/commitment/generate_corpus.py
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).with_name("relative_time_corpus.jsonl")

REQUEST_AT = datetime(2026, 8, 11, 10, 0, 0, tzinfo=timezone.utc)

# predicate → (object date string, EN aliases, AR aliases)
EVENTS = {
    "copilot_next_reset": (
        "2026-09-01",
        ["the copilot reset", "the copilot monthly reset", "copilot's next reset",
         "the github copilot reset"],
        ["إعادة تعيين copilot", "إعادة تعيين copilot الشهرية"],
    ),
    "grok_next_reset": (
        "2026-08-15",
        ["the grok reset", "the grok weekly reset", "the supergrok reset"],
        ["إعادة تعيين grok", "إعادة تعيين grok الاسبوعية"],
    ),
    "subscription_ends": (
        "2026-08-13",
        ["the subscription ends", "the subscription reset", "the sub ends"],
        ["انتهاء الاشتراك", "تجديد الاشتراك"],
    ),
}

# ── Arabic number grammar ──────────────────────────────────────────────────
_AR_UNIT = {
    "day":    {1: "يوم",     2: "يومين",   "pl": "أيام"},
    "hour":   {1: "ساعة",    2: "ساعتين",  "pl": "ساعات"},
    "week":   {1: "أسبوع",   2: "أسبوعين", "pl": "أسابيع"},
    "minute": {1: "دقيقة",   2: "دقيقتين", "pl": "دقائق"},
}
_AR_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def ar_unit_phrase(n: int, unit: str, *, indic: bool = False) -> str:
    tbl = _AR_UNIT[unit]
    if n == 1:
        return tbl[1]
    if n == 2:
        return tbl[2]                      # dual, no digit
    d = str(n).translate(_AR_INDIC) if indic else str(n)
    return f"{d} {tbl['pl']}"


def en_unit(n: int, unit: str) -> str:
    plural = {"day": "days", "hour": "hours", "week": "weeks",
              "minute": "minutes"}[unit]
    return "1 " + unit if n == 1 else f"{n} {plural}"


def lead_td(n: int, unit: str) -> timedelta:
    secs = {"day": 86400, "hour": 3600, "week": 604800, "minute": 60}[unit]
    return timedelta(seconds=n * secs)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def belief(p: str) -> dict:
    return {"predicate": p, "object": EVENTS[p][0]}


def event_date(p: str) -> datetime:
    return datetime.fromisoformat(EVENTS[p][0]).replace(tzinfo=timezone.utc)


def alias(pred: str, lang: str, idx: int) -> str:
    en, ar = EVENTS[pred][1], EVENTS[pred][2]
    return (ar if lang == "ar" else en)[idx % len(ar if lang == "ar" else en)]


_counter = [0]


def cid(lang: str, cat: str) -> str:
    _counter[0] += 1
    return f"rt_{lang}_{cat}_{_counter[0]:04d}"


def case(text, lang, script, category, split, memory, expected) -> dict:
    return {"id": cid(lang, category[:6]), "text": text, "lang": lang,
            "script": script, "request_at": iso(REQUEST_AT), "memory": memory,
            "category": category, "split": split, "expected": expected}


NS = [1, 2, 3, 4, 5, 7]
UNITS = ["day", "hour", "week", "minute"]
GOLDEN_EVERY = 5   # ~20% golden


def _split(i: int) -> str:
    return "golden" if i % GOLDEN_EVERY == 0 else "train"


# ── builders (EN/AR symmetric) ─────────────────────────────────────────────

def build_before_event() -> list[dict]:
    """N units BEFORE a referenced memory event → allow, anchored to event."""
    out = []
    i = 0
    for pred in EVENTS:
        ev = event_date(pred)
        for n in NS:
            for unit in UNITS:
                fire = ev - lead_td(n, unit)
                for vi in (0, 1):  # alias rotation
                    i += 1
                    sp = _split(i)
                    a_en = alias(pred, "en", vi)
                    a_ar = alias(pred, "ar", vi)
                    en_templates = [
                        "remind me {u} before {a}",
                        "remind me before {a} in {u}",
                        "ping me {u} before {a}",
                    ]
                    ar_templates = [
                        "ذكرني قبل {u} من {a}",
                        "ذكرني قبل {a} بـ{u}",
                    ]
                    out.append(case(
                        en_templates[vi % len(en_templates)].format(
                            u=en_unit(n, unit), a=a_en),
                        "en", "latin", "relative_before_event", sp, [belief(pred)],
                        {"decision": "allow", "anchor": pred,
                         "fire_at_iso": iso(fire), "overwrite_belief": False}))
                    out.append(case(
                        ar_templates[vi % len(ar_templates)].format(
                            u=ar_unit_phrase(n, unit), a=a_ar),
                        "ar", "arabic", "relative_before_event", sp, [belief(pred)],
                        {"decision": "allow", "anchor": pred,
                         "fire_at_iso": iso(fire), "overwrite_belief": False}))
    return out


def build_from_now_clean() -> list[dict]:
    """Bare relative-from-now, only the FAR copilot event (21d) in memory → allow."""
    out = []
    i = 0
    for n in NS:
        for unit in UNITS:
            fire = REQUEST_AT + lead_td(n, unit)
            for indic in (False, True):
                i += 1
                sp = _split(i)
                out.append(case(
                    f"remind me in {en_unit(n, unit)}", "en", "latin",
                    "relative_from_now", sp, [belief("copilot_next_reset")],
                    {"decision": "allow", "anchor": "request_at",
                     "fire_at_iso": iso(fire), "overwrite_belief": False}))
                out.append(case(
                    f"ذكرني بعد {ar_unit_phrase(n, unit, indic=indic)}", "ar",
                    "arabic", "relative_from_now", sp,
                    [belief("copilot_next_reset")],
                    {"decision": "allow", "anchor": "request_at",
                     "fire_at_iso": iso(fire), "overwrite_belief": False}))
    return out


def build_absolute_date() -> list[dict]:
    """Absolute calendar date, no event cue → allow, anchor=absolute.

    Memory may hold copilot but the user said a literal date with no 'before',
    so no overwrite / no conflict. fire_at = that date.
    """
    out = []
    i = 0
    # NB: text is date-only ("on 2026-09-10"), so expected fire_at must be
    # midnight — a time component in the date with no time in the text is a
    # labeling bug (resolver would correctly parse midnight, label would say
    # 14:30 → false failure). All absolute cases here are date-only.
    dates = [datetime(2026, 8, 30, tzinfo=timezone.utc),
             datetime(2026, 8, 20, tzinfo=timezone.utc),
             datetime(2026, 9, 5, tzinfo=timezone.utc),
             datetime(2026, 8, 25, tzinfo=timezone.utc),
             datetime(2026, 9, 10, tzinfo=timezone.utc),
             datetime(2026, 8, 28, tzinfo=timezone.utc),
             datetime(2026, 9, 1, tzinfo=timezone.utc),
             datetime(2026, 8, 18, tzinfo=timezone.utc)]
    for d in dates:
        i += 1
        sp = _split(i)
        out.append(case(
            f"remind me on {d.date().isoformat()}", "en", "latin",
            "absolute_date", sp, [belief("copilot_next_reset")],
            {"decision": "allow", "anchor": "absolute",
             "fire_at_iso": iso(d), "overwrite_belief": False}))
        out.append(case(
            f"ذكرني في {d.date().isoformat()}", "ar", "arabic",
            "absolute_date", sp, [belief("copilot_next_reset")],
            {"decision": "allow", "anchor": "absolute",
             "fire_at_iso": iso(d), "overwrite_belief": False}))
    return out


def build_ambiguous_nearby() -> list[dict]:
    """Bare from-now but memory has a NEARBY event (grok 4d / sub 2d) → clarify."""
    out = []
    i = 0
    for pred in ("grok_next_reset", "subscription_ends"):
        for n in (1, 2, 3, 4):
            for unit in ("day", "hour"):
                i += 1
                sp = _split(i)
                out.append(case(
                    f"remind me in {en_unit(n, unit)}", "en", "latin",
                    "ambiguous_nearby_event", sp, [belief(pred)],
                    {"decision": "clarify", "anchor": "request_at",
                     "fire_at_iso": None, "overwrite_belief": False}))
                out.append(case(
                    f"ذكرني بعد {ar_unit_phrase(n, unit)}", "ar", "arabic",
                    "ambiguous_nearby_event", sp, [belief(pred)],
                    {"decision": "clarify", "anchor": "request_at",
                     "fire_at_iso": None, "overwrite_belief": False}))
    return out


def build_ambiguous_event_mentioned() -> list[dict]:
    """Event mentioned + relative time, NO before/after cue → clarify."""
    out = []
    i = 0
    for pred in EVENTS:
        for n in (2, 3, 4, 5):
            i += 1
            sp = _split(i)
            a_en = alias(pred, "en", 0)
            a_ar = alias(pred, "ar", 0)
            out.append(case(
                f"remind me about {a_en} in {en_unit(n, 'day')}", "en", "latin",
                "ambiguous_event_mentioned", sp, [belief(pred)],
                {"decision": "clarify", "anchor": pred, "fire_at_iso": None,
                 "overwrite_belief": False}))
            out.append(case(
                f"ذكرني عن {a_ar} بعد {ar_unit_phrase(n, 'day')}", "ar", "arabic",
                "ambiguous_event_mentioned", sp, [belief(pred)],
                {"decision": "clarify", "anchor": pred, "fire_at_iso": None,
                 "overwrite_belief": False}))
    return out


def build_no_time() -> list[dict]:
    """No time expression → clarify (missing slot)."""
    en = ["remind me about the meeting", "remind me to call mom",
          "set a reminder for the dentist", "don't let me forget the standup",
          "remind me about the flight", "remind me to pay the bill",
          "remind me about my doctor appointment", "remind me to submit the report"]
    ar = ["ذكرني بالاجتماع", "ذكرني بمكالمة أمي", "ضع تذكير لزيارة الطبيب",
          "ذكرني بالرحلة", "لا تنسني الموعد", "ذكرني بدفع الفاتورة",
          "ذكرني بموعد الطبيب", "ذكرني بتسليم التقرير"]
    out = []
    i = 0
    for t in en:
        i += 1
        out.append(case(t, "en", "latin", "no_time_expression", "train", [],
                        {"decision": "clarify", "anchor": "none",
                         "fire_at_iso": None, "overwrite_belief": False}))
    for t in ar:
        i += 1
        out.append(case(t, "ar", "arabic", "no_time_expression", "train", [],
                        {"decision": "clarify", "anchor": "none",
                         "fire_at_iso": None, "overwrite_belief": False}))
    return out


def build_multi_goal() -> list[dict]:
    """Remind (gated) + independent read in one turn. The remind half is what
    we label; the read half is noise the resolver must ignore."""
    out = []
    ev = event_date("copilot_next_reset")
    i = 0
    for n in (2, 3, 4, 5):
        for pred in ("copilot_next_reset", "grok_next_reset"):
            i += 1
            sp = _split(i)
            ev = event_date(pred)
            fire = ev - lead_td(n, "day")
            out.append(case(
                f"remind me {en_unit(n, 'day')} before {alias(pred, 'en', 0)} and read my notes",
                "en", "latin", "multi_goal_remind", sp, [belief(pred)],
                {"decision": "allow", "anchor": pred,
                 "fire_at_iso": iso(fire), "overwrite_belief": False}))
            out.append(case(
                f"ذكرني قبل {ar_unit_phrase(n, 'day')} من {alias(pred, 'ar', 0)} واقرأ ملاحظاتي",
                "ar", "arabic", "multi_goal_remind", sp, [belief(pred)],
                {"decision": "allow", "anchor": pred,
                 "fire_at_iso": iso(fire), "overwrite_belief": False}))
    return out


def build_hostile() -> list[dict]:
    """Adversarial / edge cases (hostile split). Ground truth stays semantic."""
    ev = event_date("copilot_next_reset")
    out = []
    out.append(case(
        "ذكرني قبل ٢ يوم ... قبل إعادة تعيين copilot", "ar", "mixed",
        "relative_before_event", "hostile", [belief("copilot_next_reset")],
        {"decision": "allow", "anchor": "copilot_next_reset",
         "fire_at_iso": iso(ev - timedelta(days=2)), "overwrite_belief": False}))
    out.append(case(
        "hey so um remind me 2 days before the copilot reset please thanks",
        "en", "latin", "relative_before_event", "hostile",
        [belief("copilot_next_reset")],
        {"decision": "allow", "anchor": "copilot_next_reset",
         "fire_at_iso": iso(ev - timedelta(days=2)), "overwrite_belief": False}))
    out.append(case(
        "remind me on 2026-08-30", "en", "latin", "absolute_date", "hostile",
        [belief("copilot_next_reset")],
        {"decision": "allow", "anchor": "absolute",
         "fire_at_iso": iso(datetime(2026, 8, 30, tzinfo=timezone.utc)),
         "overwrite_belief": False}))
    out.append(case(
        "ذكرني قبل ٣ أيام من إعادة تعيين grok", "ar", "arabic",
        "relative_before_event", "hostile", [belief("grok_next_reset")],
        {"decision": "allow", "anchor": "grok_next_reset",
         "fire_at_iso": iso(event_date("grok_next_reset") - timedelta(days=3)),
         "overwrite_belief": False}))
    out.append(case(
        "remind me 1 hour before the subscription ends", "en", "latin",
        "relative_before_event", "hostile", [belief("subscription_ends")],
        {"decision": "allow", "anchor": "subscription_ends",
         "fire_at_iso": iso(event_date("subscription_ends") - timedelta(hours=1)),
         "overwrite_belief": False}))
    # word-number ("a week" = 7d) before copilot
    out.append(case(
        "remind me a week before the copilot reset", "en", "latin",
        "relative_before_event", "hostile", [belief("copilot_next_reset")],
        {"decision": "allow", "anchor": "copilot_next_reset",
         "fire_at_iso": iso(ev - timedelta(days=7)), "overwrite_belief": False}))
    # code-switching: AR relative phrase + EN event cue
    out.append(case(
        "remind me بعد يومين before the copilot reset", "ar", "mixed",
        "relative_before_event", "hostile", [belief("copilot_next_reset")],
        {"decision": "allow", "anchor": "copilot_next_reset",
         "fire_at_iso": iso(ev - timedelta(days=2)), "overwrite_belief": False}))
    # Eastern-Arabic (Persian) digits ۳ before grok
    out.append(case(
        "ذكرني قبل ۳ أيام من إعادة تعيين grok", "ar", "arabic",
        "relative_before_event", "hostile", [belief("grok_next_reset")],
        {"decision": "allow", "anchor": "grok_next_reset",
         "fire_at_iso": iso(event_date("grok_next_reset") - timedelta(days=3)),
         "overwrite_belief": False}))
    # minute precision before subscription
    out.append(case(
        "remind me 30 minutes before the subscription ends", "en", "latin",
        "relative_before_event", "hostile", [belief("subscription_ends")],
        {"decision": "allow", "anchor": "subscription_ends",
         "fire_at_iso": iso(event_date("subscription_ends") - timedelta(minutes=30)),
         "overwrite_belief": False}))
    # bare from-now with nearby grok in memory → must clarify (not silently fire)
    out.append(case(
        "remind me in 2 days", "en", "latin", "ambiguous_nearby_event",
        "hostile", [belief("grok_next_reset")],
        {"decision": "clarify", "anchor": "request_at", "fire_at_iso": None,
         "overwrite_belief": False}))
    # long noisy preamble before a clean before-event
    out.append(case(
        "okay so like real quick — remind me 2 days before the copilot reset",
        "en", "latin", "relative_before_event", "hostile",
        [belief("copilot_next_reset")],
        {"decision": "allow", "anchor": "copilot_next_reset",
         "fire_at_iso": iso(ev - timedelta(days=2)), "overwrite_belief": False}))
    # AR dual with redundant leading digit (ungrammatical stress; dual = 2 days)
    out.append(case(
        "ذكرني قبل ٢ يومين من إعادة تعيين copilot", "ar", "arabic",
        "relative_before_event", "hostile", [belief("copilot_next_reset")],
        {"decision": "allow", "anchor": "copilot_next_reset",
         "fire_at_iso": iso(ev - timedelta(days=2)), "overwrite_belief": False}))
    return out


def main() -> None:
    random.seed(42)
    cases: list[dict] = []
    for fn in (build_before_event, build_from_now_clean, build_absolute_date,
               build_ambiguous_nearby, build_ambiguous_event_mentioned,
               build_no_time, build_multi_goal, build_hostile):
        cases.extend(fn())
    random.shuffle(cases)
    with OUT.open("w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    by_lang, by_cat, by_split = {}, {}, {}
    ar = 0
    for c in cases:
        by_lang[c["lang"]] = by_lang.get(c["lang"], 0) + 1
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
        by_split[c["split"]] = by_split.get(c["split"], 0) + 1
        if c["lang"] == "ar":
            ar += 1
    print(f"wrote {len(cases)} cases → {OUT}")
    print(f"AR ratio: {ar}/{len(cases)} = {ar/len(cases)*100:.1f}% (floor 40%)")
    print(f"by lang:   {by_lang}")
    print(f"by split:  {by_split}")
    print(f"by category: {by_cat}")


if __name__ == "__main__":
    main()
