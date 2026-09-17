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
}

#: How the summoner allowlist is enforced.
SUMMON_ALLOWLIST = "allowlist"
SUMMON_ANYONE = "anyone"
_SUMMON_POLICIES = (SUMMON_ALLOWLIST, SUMMON_ANYONE)

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

    def can_draft(self) -> bool:
        return self.enabled and self.mode in (MODE_DRAFT, MODE_AUTO) and bool(self.subjects)

    def is_trusted_summoner(self, handle: str) -> bool:
        """On the operator's explicit allowlist. Independent of policy."""
        h = (handle or "").strip().lstrip("@").lower()
        return bool(h) and h in self.summoners

    def is_summoner(self, handle: str) -> bool:
        """May this handle summon a reply at all?

        Under ``allowlist`` (the default) an empty list means nobody, never
        everybody — a config mistake must not open the account to the world.
        Under ``anyone`` the gate is off and every other rail still applies:
        subject match, the three caps, the follower floor, the screen.
        """
        if self.summoner_policy == SUMMON_ANYONE:
            return bool((handle or "").strip())
        return self.is_trusted_summoner(handle)

    def mood_override_allowed(self, handle: str) -> bool:
        """Only a trusted summoner may dial the tone.

        Under ``anyone``, honouring a stranger's 🤬 would let someone pick
        which register the operator answers in — a small but real manipulation
        lever, and one with no upside. Strangers get the subject's declared
        mood; the operator and their named cohosts get the emoji.
        """
        return self.allow_emoji_mood and self.is_trusted_summoner(handle)

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
        if not sid or not view or not match:
            logger.warning(
                "[x-reply] subject %r skipped — id, match and view are all required",
                sid or "<unnamed>",
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
    for subject in subjects:
        for kw in subject.match:
            if not kw:
                continue
            if kw.isascii():
                if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", low):
                    return subject
            elif kw in low:
                return subject
    return None


async def _llm_pick(text: str, subjects: tuple[Subject, ...]) -> Subject | None:
    """Closed-set classification. Returns a declared subject or None.

    Never generative. The model is handed the ids it may choose from and
    ``none``; anything else it returns is discarded, so a hallucinated
    subject cannot become a reply.
    """
    catalogue = "\n".join(f"- {s.id}: {', '.join(s.match[:6])}" for s in subjects)
    prompt = (
        "Classify the post below into exactly ONE of these subject ids, or "
        "'none' if it fits none of them.\n\n"
        f"Subjects:\n{catalogue}\n\n"
        f"Post:\n{text[:1500]}\n\n"
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

    ``None`` is a first-class answer meaning **do not reply**. Callers must
    not fall back to a default subject; that would be the bot inventing a
    view, which is the one thing this module exists to prevent.
    """
    cfg = cfg or get_reply_config()
    if not cfg.subjects or not (text or "").strip():
        return None
    hit = _keyword_hit(text, cfg.subjects)
    if hit is not None:
        logger.info("[x-reply] subject %r matched on keyword", hit.id)
        return hit
    if not allow_llm:
        return None
    # Propagates ClassifierUnavailable — the caller must be able to tell
    # "your keywords did not match" from "the classifier never ran".
    hit = await _llm_pick(text, cfg.subjects)
    if hit is not None:
        logger.info("[x-reply] subject %r matched via classifier", hit.id)
    else:
        logger.info("[x-reply] no declared subject matched — not replying")
    return hit
