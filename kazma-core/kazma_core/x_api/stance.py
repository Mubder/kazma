"""Operator-declared subjects and the views Kazma argues from.

The whole point of this module is that **Kazma never invents an opinion**.
The operator writes a `view` per subject; a reply is only ever drafted when
the post being replied to matches one of those subjects. No match means no
reply — see :func:`classify`, which returns ``None`` rather than guessing.

That is the difference between a bot with the operator's voice and a bot
with *a* voice. A model asked to "be funny about whatever this is" will
eventually be funny about something the operator would never have touched,
under the operator's handle, with no way to point at the line it crossed.

Config lives in ConfigStore under ``connectors.x.reply.*`` so it is editable
from Settings -> X and live-reloaded on every use (no restart to fix a view
that is landing badly).

Classification is deliberately two-stage:

1. **Keywords** (:attr:`Subject.match`) — deterministic, free, auditable.
   A hit here never calls a model, which matters because the poller runs on
   a timer and an LLM call per mention would be the whole budget.
2. **LLM fallback** — only when no keyword hits, and it picks from the
   *declared subject ids or nothing*. It is never generative: the closed set
   is passed in the prompt and anything outside it is discarded. A classifier
   allowed to mint a subject would reintroduce exactly the freelancing this
   module exists to prevent.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "Subject",
    "ReplyConfig",
    "get_reply_config",
    "classify",
    "MODE_OFF",
    "MODE_DRAFT",
    "MODE_AUTO",
    "MOODS",
    "MOOD_EMOJI",
    "SUMMON_ALLOWLIST",
    "SUMMON_ANYONE",
    "mood_from_text",
    "ClassifierUnavailable",
    "no_match_detail",
    "implicit_voice_subject",
    "implicit_summon_subject",
    "side_from_summon",
    "is_opinion_ask",
    "marker_in_text",
    "VOICE_SUBJECT_ID",
    "SUMMON_SUBJECT_ID",
]


class ClassifierUnavailable(Exception):
    """The LLM classifier could not run — which is NOT "no match".

    Live, 2026-09-17: the poller found two real summons, the keyword pass
    missed, the LLM fallback raised (the profile's key was unusable), the
    exception was swallowed, and the log said "no declared subject matched
    — not replying". The operator was told their subject did not match a
    post the classifier never actually looked at.
    """

MODE_OFF = "off"
MODE_DRAFT = "draft"
MODE_AUTO = "auto"
_MODES = (MODE_OFF, MODE_DRAFT, MODE_AUTO)

#: Tone presets. These only steer wording — they never widen *what* may be
#: said, which is fixed by the subject's ``view`` and ``hard_lines``.
MOODS: dict[str, str] = {
    "roast": "Mocking and sharp. Punch at the argument, not the person's identity.",
    "angry": "Blunt and indignant. Short sentences. No slurs, no threats.",
    "dry": "Deadpan understatement. Let the fact do the work.",
    "deadpan": "Deadpan understatement. Let the fact do the work.",
    "supportive": "Agreeing and additive. Add one thing the post missed.",
}

#: Emoji in the summon that dial the TONE. "what do you think Kazma? 😂" and
#: the same sentence with 🤬 should not produce the same reply — the emoji is
#: the whole point of how people actually summon a bot, and throwing it away
#: meant every reply came out in whatever mood the subject was saved with.
#:
#: This changes tone ONLY. It cannot reach the subject's ``view`` or its
#: ``hard_lines``, which is what keeps it safe to honour text written by
#: someone else: the worst a hostile summoner can do is pick which of the
#: operator's own registers their reply arrives in.
MOOD_EMOJI: dict[str, str] = {
    "😂": "roast", "🤣": "roast", "😹": "roast", "💀": "roast", "🔥": "roast",
    "🤬": "angry", "😡": "angry", "😠": "angry", "👿": "angry",
    "🙄": "dry", "😐": "dry", "😑": "dry", "🫠": "dry", "🤨": "dry",
    "❤️": "supportive", "👏": "supportive", "💯": "supportive",
    "🙏": "supportive", "👍": "supportive",
    "👎": "angry",
}

#: How the summoner allowlist is enforced.
SUMMON_ALLOWLIST = "allowlist"
SUMMON_ANYONE = "anyone"
_SUMMON_POLICIES = (SUMMON_ALLOWLIST, SUMMON_ANYONE)

#: What to do when no declared subject matches.
#: ``skip`` (default) — stay silent. ``voice`` — reply with no position,
#: emoji picking the tone. A ``*`` catch-all still answers everything.
UNMATCHED_SKIP = "skip"
UNMATCHED_VOICE = "voice"
_UNMATCHED = (UNMATCHED_SKIP, UNMATCHED_VOICE)

#: Fixed polarity. Emoji is tone only; this is which side the reply takes.
SIDE_AGAINST = "against"
SIDE_SUPPORT = "support"
_SIDES = (SIDE_AGAINST, SIDE_SUPPORT)

#: Applied to EVERY subject on top of whatever the operator wrote. These are
#: the lines that get an account suspended rather than merely disliked, and
#: an operator editing a `view` at speed should not have to remember them.
UNIVERSAL_HARD_LINES: tuple[str, ...] = (
    "never attack a person for their ethnicity, nationality, religion, "
    "gender, sexuality, disability or any other protected characteristic",
    "no slurs",
    "no threats, no wishes of harm, no calls to action against anyone",
    "no claims about a private individual's personal life",
    "criticise governments, institutions, arguments and public conduct — "
    "never a people",
)

#: Implicit subject when the operator declared none (or none matched).
#: The view is a VOICE, not a position — emoji picks the tone, the post
#: picks the topic. Stance-check is skipped because there is nothing to
#: contradict.
VOICE_SUBJECT_ID = "voice"
#: Unmatched summon: the mention itself chose against/support for THIS post.
SUMMON_SUBJECT_ID = "post"
_SUMMON_VIEW = (
    "the claim, product, person, company, or institution this post is about "
    "— as named in the post. Do not pick a different target, and do not "
    "drag in a Settings subject that is not in this post."
)
_AGAINST_WORDS = (
    "against", "roast", "slam", "criticize", "criticise", "attack",
)
_AGAINST_AR = ("ضد", "هاجم", "اهجم", "انقد", "انتقد")
_SUPPORT_WORDS = ("support", "defend")
_SUPPORT_AR = ("دافع", "أيد", "ايد", "معاه")
_VOICE_VIEW = (
    "You have no declared political position on this post. React to what "
    "it actually says, in the requested tone. Be specific to THIS post. "
    "Do not invent a crusade, a cause, or a view the operator did not "
    "write. Short, human, no hashtags, no preamble."
)


def implicit_voice_subject(*, mood: str = "dry") -> Subject:
    """Catch-all used when no subject is declared, or none matched.

    Still a Subject so every downstream rail (hard lines, screen, 280 cap)
    keeps working. ``match=('*',)`` makes ``is_catch_all()`` true, which
    is what skips the stance check — a voice has no position to drift from.
    """
    m = (mood or "dry").strip().lower()
    if m not in MOODS:
        m = "dry"
    return Subject(id=VOICE_SUBJECT_ID, match=("*",), view=_VOICE_VIEW, mood=m)


def implicit_summon_subject(*, side: str, mood: str = "dry") -> Subject:
    """No Settings card matched; the summoner set the side in the mention.

    Not a catch-all: stance check still runs. ``id`` is ``post`` so the
    drafter aims at whatever this tweet is about, not at a Settings card.
    """
    s = (side or "").strip().lower()
    if s not in _SIDES:
        s = SIDE_AGAINST
    m = (mood or "dry").strip().lower()
    if m not in MOODS:
        m = "dry"
    return Subject(
        id=SUMMON_SUBJECT_ID, match=(), view=_SUMMON_VIEW, mood=m, side=s,
    )


def side_from_summon(text: str) -> str:
    """Against/support from the mention: magic words first, then emoji.

    Declared subjects still win in :func:`classify`. This is only the
    unmatched path — roast 😂 on an xAI post means criticise xAI, not
    Iran. Supportive ❤️ means defend whatever the post is about.

    Roast/angry/dry/👎 → against. Heart/clap/100/👍 → support.
    Words: against/roast/slam/ضد/هاجم vs support/defend/دافع/معاه.
    """
    body = str(text or "")
    low = body.lower()
    for w in _SUPPORT_WORDS:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low):
            return SIDE_SUPPORT
    for w in _SUPPORT_AR:
        if w in body:
            return SIDE_SUPPORT
    for w in _AGAINST_WORDS:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low):
            return SIDE_AGAINST
    for w in _AGAINST_AR:
        if w in body:
            return SIDE_AGAINST
    mood = mood_from_text(body)
    if mood == "supportive":
        return SIDE_SUPPORT
    if mood in ("roast", "angry", "dry", "deadpan"):
        return SIDE_AGAINST
    return ""


_OPINION_EN = (
    "thoughts", "wdyt", "opinion",
)
_OPINION_AR = (
    "شرايك", "شرايكم", "شرايچ", "رأيك", "رايك", "رايكم", "رأيكم",
    "شنو رايك", "وش رايك", "كيف", "شلون", "شنو", "ليش", "هل",
)
_QUESTION_EN = re.compile(
    r"(?<!\w)(how|what|why|when|where|should|could|would)\b",
    re.IGNORECASE,
)


def is_opinion_ask(text: str) -> bool:
    """True when the mention is asking for a take, not voting a side.

    Live 2026-09-19: ``How er can make use of this into Kazma framework?``
    on an NVIDIA Dynamo post skipped because there was no subject keyword
    and no 😂/against word. A question mark, شرايك, or how/what/why means
    react to THIS post — do not stay silent and do not pick a country card.
    """
    body = str(text or "")
    if not body.strip():
        return False
    has_up = "👍" in body
    has_down = "👎" in body
    if has_up and has_down:
        return True
    if "❤️" in body and "😂" in body:
        return True
    if "?" in body or "؟" in body:
        return True
    low = body.lower()
    if "what do you think" in low or "what do u think" in low:
        return True
    if "make use" in low or "into kazma" in low or "in kazma" in low:
        return True
    if _QUESTION_EN.search(low):
        return True
    for w in _OPINION_EN:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low):
            return True
    for w in _OPINION_AR:
        if w in body:
            return True
    return False


def marker_in_text(text: str, marker: str) -> bool:
    """True if *marker* appears in *text* as its own token.

    Hashtags (``#Open``) match case-insensitively and do **not** match
    ``#OpenAI``. Bare words use word boundaries. Emoji / punctuation
    markers stay a literal substring.
    """
    tok = (marker or "").strip()
    body = str(text or "")
    if not tok or not body:
        return False
    if tok.startswith("#"):
        core = re.escape(tok.lstrip("#"))
        return bool(re.search(rf"(?i)(?<![#\w])#{core}(?!\w)", body))
    if tok.isascii() and tok.replace("-", "").replace("_", "").isalnum():
        return bool(re.search(rf"(?i)(?<!\w){re.escape(tok)}(?!\w)", body))
    return tok in body


@dataclass(frozen=True)
class Subject:
    """One operator-declared topic and the position to argue from."""

    id: str
    match: tuple[str, ...]
    view: str
    mood: str = "dry"
    register: str = ""
    hard_lines: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    #: ``against`` | ``support`` | ``""``. When set, the reply ALWAYS takes
    #: that side on this subject. Emoji only changes tone (roast/angry/dry),
    #: never the side. Empty = legacy free-text ``view`` only.
    side: str = ""

    def is_sided(self) -> bool:
        return self.side in _SIDES

    def is_catch_all(self) -> bool:
        """``*`` as a keyword means "any post".

        The rule this product is built on is that Kazma never invents a
        position — not that it must stay silent on subjects the operator has
        not enumerated. Those are different things, and conflating them meant
        an operator who wanted it to answer everything had no way to say so.

        A catch-all is still a DECLARED subject: it has a view the operator
        wrote, its own hard lines, and it passes the same screen and stance
        check. What it drops is the requirement to predict the topic in
        advance. Specific subjects are always tried first, so adding one does
        not blunt the others.
        """
        return "*" in self.match

    def matches(self, text: str) -> bool:
        """True if this card is allowed for *text*.

        Voice/summon/catch-all always match. A named Settings card matches
        only when one of its keywords is actually in the post — so a
        classifier cannot pin an AI tweet on a country card.
        """
        if self.id in (VOICE_SUBJECT_ID, SUMMON_SUBJECT_ID) or self.is_catch_all():
            return True
        return _keyword_hit(text, (self,)) is not None

    def mood_hint(self) -> str:
        return MOODS.get(self.mood.strip().lower(), MOODS["dry"])

    def all_hard_lines(self) -> tuple[str, ...]:
        return tuple(self.hard_lines) + UNIVERSAL_HARD_LINES


@dataclass(frozen=True)
class ReplyConfig:
    """Live snapshot of ``connectors.x.reply.*``."""

    enabled: bool
    mode: str
    summoners: tuple[str, ...]
    trigger: str
    max_replies_per_day: int
    max_replies_per_target_per_day: int
    cooldown_per_thread_s: int
    min_target_followers: int
    poll_interval_s: int
    subjects: tuple[Subject, ...] = field(default=())
    summoner_policy: str = SUMMON_ALLOWLIST
    allow_emoji_mood: bool = True
    stance_check: bool = True
    unmatched: str = UNMATCHED_SKIP
    classify_llm: bool = False
    use_knowledge: bool = False
    knowledge_library: str = ""
    #: If this token appears in the parent post (or the mention), strangers
    #: may summon on that thread. Empty = never: only trusted handles.
    open_thread_marker: str = ""
    close_thread_marker: str = ""

    def can_draft(self) -> bool:
        # Subjects are optional. Zero subjects = voice-only: reply to
        # whatever is summoned, emoji picks the tone. The original
        # "no subject means no reply" rule fought the actual UX.
        return self.enabled and self.mode in (MODE_DRAFT, MODE_AUTO)

    def is_trusted_summoner(self, handle: str) -> bool:
        """On the operator's explicit allowlist. Independent of policy."""
        h = (handle or "").strip().lstrip("@").lower()
        return bool(h) and h in self.summoners

    def is_summoner(
        self,
        handle: str,
        *,
        parent_text: str = "",
        summon_text: str = "",
        conversation_closed: bool = False,
    ) -> bool:
        """May this handle summon a reply at all?

        Under ``allowlist`` (the default) an empty list means nobody, never
        everybody — a config mistake must not open the account to the world.
        Under ``anyone`` the gate is off and every other rail still applies.

        ``open_thread_marker`` is the per-post exception: if that token is
        in the parent tweet (the one you already summoned on) or in this
        mention, a stranger may join. ``close_thread_marker`` or a stored
        closed conversation wins — strangers stop, you can still talk.
        """
        if self.is_trusted_summoner(handle):
            return True
        if conversation_closed:
            return False
        if self.marker_in(self.close_thread_marker, parent_text, summon_text):
            return False
        if self.summoner_policy == SUMMON_ANYONE:
            return bool((handle or "").strip())
        return self.thread_is_open(parent_text, summon_text)

    def thread_is_open(self, *texts: str) -> bool:
        if self.marker_in(self.close_thread_marker, *texts):
            return False
        return self.marker_in(self.open_thread_marker, *texts)

    def marker_in(self, marker: str, *texts: str) -> bool:
        tok = (marker or "").strip()
        if not tok:
            return False
        return any(marker_in_text(t, tok) for t in texts)

    def mood_override_allowed(self, handle: str) -> bool:
        """The summon emoji is the tone dial.

        Anyone who is allowed to summon (see :meth:`is_summoner`) may set
        it. Restricting this to the allowlist made the feature's actual UX
        — mention + emoji — silently no-op for everyone when policy is
        ``anyone``, which is the opposite of "the emoji decides".
        """
        if not self.allow_emoji_mood:
            return False
        return bool((handle or "").strip())

    def subject_by_id(self, sid: str) -> Subject | None:
        for s in self.subjects:
            if s.id == sid:
                return s
        return None


def _cs_get(key: str, default: Any = None) -> Any:
    try:
        from kazma_core.config_store import get_config_store

        val = get_config_store().get(key)
        return default if val is None else val
    except Exception:
        logger.debug("[x-reply] ConfigStore read failed for %s", key, exc_info=True)
        return default


def _as_bool(val: Any, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return default


def _as_int(val: Any, default: int, *, lo: int, hi: int) -> int:
    try:
        n = int(val)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _as_tuple(val: Any) -> tuple[str, ...]:
    if isinstance(val, str):
        parts = [p.strip() for p in val.split(",")]
    elif isinstance(val, (list, tuple)):
        parts = [str(p).strip() for p in val]
    else:
        return ()
    return tuple(p for p in parts if p)


def _parse_subjects(raw: Any) -> tuple[Subject, ...]:
    """Build subjects from the stored list. A malformed entry is skipped loudly.

    Skipping rather than failing the whole config is deliberate: one bad
    subject should not silently disable every other one, and a subject that
    fails to parse simply never matches, which is the safe direction.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            logger.warning("[x-reply] subjects is a string but not JSON — ignoring")
            return ()
    if not isinstance(raw, list):
        return ()

    out: list[Subject] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        view = str(item.get("view") or "").strip()
        match = _as_tuple(item.get("match"))
        side = str(item.get("side") or "").strip().lower()
        if side not in _SIDES:
            side = ""
        catch_all = "*" in match
        if not sid or not match:
            logger.warning(
                "[x-reply] subject %r skipped — id and match are required",
                sid or "<unnamed>",
            )
            continue
        if not catch_all and not side and not view:
            logger.warning(
                "[x-reply] subject %r skipped — set against/support, or a view",
                sid,
            )
            continue
        out.append(
            Subject(
                id=sid,
                match=tuple(m.lower() for m in match),
                view=view,
                mood=str(item.get("mood") or "dry").strip().lower(),
                register=str(item.get("register") or "").strip(),
                hard_lines=_as_tuple(item.get("hard_lines")),
                examples=_as_tuple(item.get("examples")),
                side=side,
            )
        )
    return tuple(out)


def _killed() -> bool:
    """Env kill switches win over any stored config.

    ``KAZMA_X_POST=0`` already stops every write in ``x_api.policy``; it is
    repeated here so the poller does not spend read quota drafting replies
    that could never post. ``KAZMA_X_REPLY=0`` is the narrower switch —
    scheduled posts keep working, auto-reply stops.
    """
    import os

    for name in ("KAZMA_X_REPLY", "KAZMA_X_POST"):
        if (os.environ.get(name) or "").strip().lower() in ("0", "false", "no", "off"):
            return True
    return False


def get_reply_config() -> ReplyConfig:
    """Live-read the auto-reply config. Never raises."""
    mode = str(_cs_get("connectors.x.reply.mode", MODE_OFF) or MODE_OFF).strip().lower()
    if mode not in _MODES:
        mode = MODE_OFF
    killed = _killed()
    if killed:
        mode = MODE_OFF
    return ReplyConfig(
        enabled=(not killed) and _as_bool(_cs_get("connectors.x.reply.enabled"), False),
        mode=mode,
        # Stored without '@', compared lowercased.
        summoners=tuple(
            h.lstrip("@").lower() for h in _as_tuple(_cs_get("connectors.x.reply.summoners"))
        ),
        trigger=str(_cs_get("connectors.x.reply.trigger", "") or "").strip().lower(),
        max_replies_per_day=_as_int(
            _cs_get("connectors.x.reply.max_replies_per_day"), 5, lo=0, hi=50
        ),
        max_replies_per_target_per_day=_as_int(
            _cs_get("connectors.x.reply.max_replies_per_target_per_day"), 1, lo=0, hi=10
        ),
        cooldown_per_thread_s=_as_int(
            _cs_get("connectors.x.reply.cooldown_per_thread_s"), 3600, lo=0, hi=86400
        ),
        min_target_followers=_as_int(
            _cs_get("connectors.x.reply.min_target_followers"), 500, lo=0, hi=1_000_000
        ),
        poll_interval_s=_as_int(
            _cs_get("connectors.x.reply.poll_interval_s"), 600, lo=60, hi=3600
        ),
        subjects=_parse_subjects(_cs_get("connectors.x.reply.subjects", [])),
        summoner_policy=(
            str(_cs_get("connectors.x.reply.summoner_policy", SUMMON_ALLOWLIST)
                or SUMMON_ALLOWLIST).strip().lower()
            if str(_cs_get("connectors.x.reply.summoner_policy", SUMMON_ALLOWLIST)
                   or "").strip().lower() in _SUMMON_POLICIES
            else SUMMON_ALLOWLIST
        ),
        allow_emoji_mood=_as_bool(
            _cs_get("connectors.x.reply.allow_emoji_mood"), True
        ),
        stance_check=_as_bool(
            _cs_get("connectors.x.reply.stance_check"), True
        ),
        unmatched=(
            str(_cs_get("connectors.x.reply.unmatched", UNMATCHED_SKIP) or UNMATCHED_SKIP)
            .strip().lower()
            if str(_cs_get("connectors.x.reply.unmatched", UNMATCHED_SKIP) or "")
            .strip().lower() in _UNMATCHED
            else UNMATCHED_SKIP
        ),
        classify_llm=_as_bool(
            _cs_get("connectors.x.reply.classify_llm"), False
        ),
        use_knowledge=_as_bool(
            _cs_get("connectors.x.reply.use_knowledge"), False
        ),
        knowledge_library=str(
            _cs_get("connectors.x.reply.knowledge_library", "") or ""
        ).strip(),
        open_thread_marker=str(
            _cs_get("connectors.x.reply.open_thread_marker", "") or ""
        ).strip(),
        close_thread_marker=str(
            _cs_get("connectors.x.reply.close_thread_marker", "") or ""
        ).strip(),
    )


def mood_from_text(text: str) -> str:
    """Mood named by the FIRST recognised emoji in *text*, or ``""``.

    First rather than last: "😂 but seriously 🤬" reads as a joke with an
    aside, and the opening emoji is the one the summoner led with. Unknown
    emoji are ignored rather than defaulting, so an unrelated 🎉 leaves the
    subject's own mood alone instead of silently reclassifying the reply.
    """
    body = str(text or "")
    best: tuple[int, str] = (len(body) + 1, "")
    for emoji, mood in MOOD_EMOJI.items():
        idx = body.find(emoji)
        if idx != -1 and idx < best[0]:
            best = (idx, mood)
    return best[1]


# ── Classification ────────────────────────────────────────────────────────

def _keyword_hit(text: str, subjects: tuple[Subject, ...]) -> Subject | None:
    """First subject whose keyword appears as a WHOLE word in *text*.

    Whole-word, not substring: a substring match on "var" also fires on
    "variable", "variance" and "Varsity" — a keyword short enough to be useful
    is short enough to be a fragment of something unrelated.
    Arabic and other non-Latin keywords have no ASCII word boundary, so they
    fall back to a plain containment check.
    """
    low = (text or "").lower()
    # Specific subjects first; a catch-all is the floor, never the ceiling.
    specific = [s for s in subjects if not s.is_catch_all()]
    for subject in specific:
        for kw in subject.match:
            if not kw:
                continue
            # 1–2 Arabic letters match almost every sentence (في، من، أو).
            if not kw.isascii() and len(kw) < 3:
                continue
            if kw.isascii():
                if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", low):
                    return subject
            elif kw in low:
                return subject
    for subject in subjects:
        if subject.is_catch_all():
            return subject
    return None


async def _llm_pick(text: str, subjects: tuple[Subject, ...]) -> Subject | None:
    """Closed-set classification. Returns a declared subject or None.

    Never generative. The model is handed the ids it may choose from and
    ``none``; anything else it returns is discarded, so a hallucinated
    subject cannot become a reply.
    """
    specifics = [s for s in subjects if not s.is_catch_all()]
    if not specifics:
        return None
    catalogue = "\n".join(
        f"- {s.id}: side={s.side or 'view'}; keywords={', '.join(s.match[:6])}; "
        f"view={s.view[:180]}"
        for s in specifics
    )
    try:
        from kazma_core.safety.prompt_fence import format_untrusted_block

        fenced = format_untrusted_block(text[:1500], source="x_post")
    except Exception:
        fenced = text[:1500]
    prompt = (
        "Classify the post below into exactly ONE of these subject ids, or "
        "'none' if it is not clearly ABOUT any of them.\n"
        "If the post does not name that topic, answer none. Do not pick a "
        "subject just because it is on the list or looks like the operator's "
        "country. When unsure, none.\n\n"
        f"Subjects:\n{catalogue}\n\n"
        f"Post:\n{fenced}\n\n"
        "Answer with the id alone. No explanation."
    )
    try:
        from kazma_core.model_registry import get_model_registry
        from kazma_core.tenant_context import get_current_tenant_id, tenant_scope

        # Provider API keys are tenant-scoped vault rows; a context-less
        # caller resolves none of them and the registry silently substitutes
        # another vendor (audit 2026-09-17, and measured live: 0/4).
        def _client():
            return get_model_registry().get_client()

        if get_current_tenant_id():
            provider = _client()
        else:
            with tenant_scope("default"):
                provider = _client()
        if provider is None:
            return None
        resp = await provider.chat(
            [{"role": "user", "content": prompt}],
            # A reasoning model emits reasoning before content; 16 tokens
            # is a ceiling it never gets past, and the classifier then
            # returns empty and silently means 'no subject matched'.
            max_tokens=600,
            temperature=0.0,
        )
        answer = str(getattr(resp, "content", "") or "").strip().lower()
    except Exception as exc:
        # NOT a no-match. Swallowing this told the operator their subject did
        # not fit a post the classifier never read.
        logger.warning("[x-reply] subject classifier could not run: %s", exc)
        raise ClassifierUnavailable(str(exc)[:200]) from exc

    answer = re.sub(r"[^a-z0-9_\-]", "", answer.split()[0] if answer.split() else "")
    for subject in subjects:
        if subject.id.lower() == answer:
            return subject
    return None


async def classify(
    text: str,
    cfg: ReplyConfig | None = None,
    *,
    allow_llm: bool = True,
) -> Subject | None:
    """Match *text* to a declared subject, or return ``None``.

    Empty text returns ``None``. No subjects at all still returns the
    implicit voice catch-all (emoji-only mode). When specific subjects
    exist and none match: ``unmatched=skip`` (default) returns ``None``
    so the summon stays silent; ``unmatched=voice`` returns the voice
    catch-all. A ``*`` subject is a keyword hit and never reaches this.
    """
    cfg = cfg or get_reply_config()
    if not (text or "").strip():
        return None
    if not cfg.subjects:
        logger.info("[x-reply] no subjects declared — voice-only reply")
        return implicit_voice_subject()
    hit = _keyword_hit(text, cfg.subjects)
    if hit is not None:
        logger.info("[x-reply] subject %r matched on keyword", hit.id)
        return hit
    if allow_llm and not any(sub.is_catch_all() for sub in cfg.subjects):
        try:
            hit = await _llm_pick(text, cfg.subjects)
        except ClassifierUnavailable as exc:
            if cfg.unmatched == UNMATCHED_VOICE:
                logger.warning(
                    "[x-reply] classifier could not run (%s) — voice-only", exc,
                )
                return implicit_voice_subject()
            raise
        if hit is not None:
            logger.info("[x-reply] subject %r matched via classifier", hit.id)
            return hit
    if cfg.unmatched == UNMATCHED_VOICE:
        logger.info("[x-reply] no declared subject matched — voice-only reply")
        return implicit_voice_subject()
    logger.info("[x-reply] no declared subject matched — not replying")
    return None


def no_match_detail(text: str, subjects: tuple[Subject, ...]) -> str:
    """Name the keywords that were checked and found absent.

    "no declared subject matched" is true and unactionable — it does not say
    what was looked for, so an operator cannot tell a narrow keyword list from
    a broken classifier from a subject they forgot to save. The answer is
    already in hand at the moment of the miss; it was simply not written down.
    """
    if not subjects:
        return (
            "no subjects are declared — replies still go out in voice-only "
            "mode; emoji picks the tone. Add a subject to argue a view, or "
            "* to write the voice yourself"
        )
    parts = []
    for s in subjects[:4]:
        kws = ", ".join(s.match[:8])
        parts.append(f"{s.id} [{kws}]")
    more = "" if len(subjects) <= 4 else f" (+{len(subjects) - 4} more)"
    tip = " — add * as a keyword to answer every post"
    head = " ".join((text or "").split())[:90]
    return (
        f"checked {'; '.join(parts)}{more} — none present in: “{head}…”{tip}"
    )
