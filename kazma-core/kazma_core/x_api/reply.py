"""Draft (and optionally publish) a reply to a post Kazma was summoned under.

**Parameter-driven on purpose.** :func:`handle_summon` takes the parent
post's *text* rather than fetching it. Two triggers feed it — the operator
pasting a link into chat, and the mentions poller — and neither the drafting
logic nor the rails below should care which. It also means the feature keeps
working if the X tier is ever downgraded and reads disappear: it degrades to
paste-driven instead of dying.

**Not a parallel mouth.** This calls an LLM outside the supervisor graph, in
the same way ``swarm.aggregator`` and ``swarm.patterns`` do. The distinction
that matters (and that got ``router.py``'s pipelines deleted in the
2026-09-17 audit) is that those were answering *users* outside the graph.
This produces a candidate that a human approves before anything leaves the
machine — and in ``auto`` mode, one that has passed a declared-subject match,
an allowlist, three caps and a content screen. The output is never returned
to a user as an assistant turn.

**Order of operations is the safety property.** Claim, then check rails, then
draft, then screen, then publish. Drafting costs a model call, so rails come
first; screening happens after drafting because the thing being screened is
what the model wrote, not what we asked for.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from kazma_core.x_api.stance import (
    MODE_AUTO,
    MODE_DRAFT,
    MOODS,
    ReplyConfig,
    Subject,
    classify,
    get_reply_config,
    mood_from_text,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SummonResult",
    "approve_summon",
    "handle_summon",
    "preview_reply",
    "parse_tweet_url",
    "draft_reply",
    "screen_draft",
]

#: x.com/<handle>/status/<id>, twitter.com, /i/web/status/<id>, with or
#: without query junk. The id is all we need to reply; the handle, when the
#: URL carries one, is the target for per-account caps.
_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:twitter|x)\.com/"
    r"(?:(?P<handle>[A-Za-z0-9_]{1,15})/status|i/web/status)/(?P<id>\d{5,25})",
    re.IGNORECASE,
)

#: Screened out of any draft regardless of subject. Deliberately crude and
#: deliberately not the only line of defence — the model is also told these
#: in the prompt. A regex cannot understand a slur it has not seen; the HITL
#: card in ``draft`` mode is what catches the rest.
_BANNED_PATTERNS = (
    r"\bkill\s+(?:them|him|her|all)\b",
    r"\b(?:should|must)\s+(?:die|be\s+killed|be\s+shot)\b",
    r"\bgas\s+the\b",
    r"\bdeserve\s+to\s+die\b",
    r"\bwipe\s+(?:them|him|her)\s+out\b",
)
_BANNED_RE = re.compile("|".join(_BANNED_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class SummonResult:
    """What happened, in a shape both the gateway and the poller can render."""

    ok: bool
    action: str  # "posted" | "awaiting_approval" | "skipped" | "failed"
    reason: str = ""
    draft: str = ""
    subject_id: str = ""
    tweet_id: str = ""
    url: str = ""
    parent_id: str = ""
    #: The idempotency key, and what ``approve_summon`` takes. Carried on the
    #: result because the approval prompt has to quote it back to the operator
    #: — quoting parent_id there would look right and never resolve.
    summon_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "reason": self.reason,
            "draft": self.draft,
            "subject": self.subject_id,
            "tweet_id": self.tweet_id,
            "url": self.url,
            "parent_id": self.parent_id,
            "summon_id": self.summon_id,
        }


def parse_tweet_url(raw: str) -> tuple[str, str]:
    """Return ``(tweet_id, handle)`` from a URL or a bare id. ``("","")`` if none."""
    text = (raw or "").strip()
    m = _URL_RE.search(text)
    if m:
        return m.group("id"), (m.group("handle") or "").lower()
    if re.fullmatch(r"\d{5,25}", text):
        return text, ""
    return "", ""


# ── Rails ─────────────────────────────────────────────────────────────────

async def _rail_error(
    cfg: ReplyConfig,
    *,
    parent_id: str,
    target_handle: str,
    target_followers: int | None,
) -> str | None:
    """Return a refusal reason, or None when every cap passes.

    These sit ON TOP of ``x_api.policy``, which already caps total posts and
    blocks duplicates. What policy cannot see: that four of today's posts
    were replies at the same account, or that this thread was answered nine
    minutes ago.
    """
    from kazma_core.x_api.reply_store import get_reply_store

    store = get_reply_store()
    now = time.time()

    # SQLite off the loop. These are tiny indexed lookups, but the loop
    # they would pin is the one serving every SSE and WebSocket stream,
    # and the static gate cannot see a blocking call behind a method
    # (audit 2026-09-17 F-4 class -- scheduled_fire.py:91 still does).
    if cfg.max_replies_per_day and (
        await asyncio.to_thread(store.posted_since, now - 86400)
    ) >= cfg.max_replies_per_day:
        return f"daily auto-reply cap reached ({cfg.max_replies_per_day})"

    if target_handle and cfg.max_replies_per_target_per_day:
        n = await asyncio.to_thread(
            store.posted_to_target_since, target_handle, now - 86400
        )
        if n >= cfg.max_replies_per_target_per_day:
            return (
                f"already replied to @{target_handle} {n}x today "
                f"(cap {cfg.max_replies_per_target_per_day}). Repeatedly answering "
                "one account is what gets reported as targeted harassment."
            )

    if parent_id and cfg.cooldown_per_thread_s:
        last = await asyncio.to_thread(store.last_reply_in_thread, parent_id)
        if last and (now - last) < cfg.cooldown_per_thread_s:
            wait = int(cfg.cooldown_per_thread_s - (now - last))
            return f"thread cooldown — {wait}s left"

    # A roast aimed at a large account is banter; the same text aimed at
    # someone with forty followers is pointing a bot at a stranger. Only
    # enforced when the caller actually knows the count (the poller does,
    # the paste path usually does not).
    if (
        target_followers is not None
        and cfg.min_target_followers
        and target_followers < cfg.min_target_followers
    ):
        return (
            f"@{target_handle} has {target_followers} followers, below the "
            f"{cfg.min_target_followers} floor for unsolicited replies"
        )
    return None


def screen_draft(text: str, subject: Subject) -> str | None:
    """Return a refusal reason if the DRAFT itself crosses a line.

    Runs after generation because what is being checked is what the model
    wrote. A model in "roast" mode drifts, and the drift is always toward
    the thing the subject's hard_lines forbade.
    """
    body = (text or "").strip()
    if not body:
        return "model returned an empty draft"
    hit = _BANNED_RE.search(body)
    if hit:
        return f"draft contains a banned construction ({hit.group(0)!r})"
    if len(body) > 280:
        return f"draft is {len(body)} chars; X caps replies at 280"
    return None


# ── Drafting ──────────────────────────────────────────────────────────────

def _build_prompt(
    subject: Subject,
    parent_text: str,
    parent_handle: str,
    mood: str = "",
) -> list[dict[str, str]]:
    lines = [
        "You write a single reply to a post on X, as the operator of this "
        "account. You are not a neutral assistant here — you argue the "
        "operator's declared position, in their voice.",
        "",
        # Tone can be dialled by the summon emoji; the view and the hard
        # lines below cannot, which is what makes that safe to honour.
        f"TONE: {MOODS.get((mood or subject.mood).strip().lower(), subject.mood_hint())}",
    ]
    if subject.register:
        lines.append(f"REGISTER: {subject.register}")
    lines += [
        "",
        "THE OPERATOR'S POSITION (this is the ONLY view you may argue):",
        subject.view.strip(),
        "",
        "HARD LINES — breaking any of these is worse than being unfunny:",
    ]
    lines += [f"- {rule}" for rule in subject.all_hard_lines()]
    if subject.examples:
        lines += ["", "Replies the operator has written before (match this voice):"]
        lines += [f"- {ex}" for ex in subject.examples[:3]]
    lines += [
        "",
        "RULES:",
        "- 280 characters maximum. Shorter lands harder.",
        "- One reply. No thread, no numbering, no hashtags.",
        "- Do not @-mention anyone; the reply already threads to the post.",
        "- No preamble, no quotes around it, no explanation. Output the reply only.",
    ]
    system = "\n".join(lines)

    who = f"@{parent_handle}" if parent_handle else "someone"
    user = f"The post by {who} you are replying to:\n\n{parent_text[:1500]}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def draft_reply(
    *,
    subject: Subject,
    parent_text: str,
    parent_handle: str = "",
    mood: str = "",
) -> str:
    """Generate one candidate reply. Returns "" on any failure."""
    from kazma_core.model_registry import get_model_registry
    from kazma_core.tenant_context import get_current_tenant_id, tenant_scope

    def _client():
        return get_model_registry().get_client()

    try:
        # Tenant bind before touching the registry: provider keys are
        # tenant-scoped vault rows and a context-less read resolves none of
        # them, after which the registry substitutes a different vendor
        # (audit 2026-09-17; measured live at 0/4).
        if get_current_tenant_id():
            provider = _client()
        else:
            with tenant_scope("default"):
                provider = _client()
        if provider is None:
            logger.warning("[x-reply] no LLM provider available for drafting")
            return ""
        resp = await provider.chat(
            _build_prompt(subject, parent_text, parent_handle, mood),
            max_tokens=200,
            temperature=0.9,
        )
        text = str(getattr(resp, "content", "") or "").strip()
    except Exception:
        logger.exception("[x-reply] drafting failed")
        return ""

    # Models like to wrap a one-liner in quotes or prefix it with "Reply:".
    text = re.sub(r"^\s*(?:reply|response)\s*:\s*", "", text, flags=re.IGNORECASE)
    if len(text) >= 2 and text[0] in "\"'“" and text[-1] in "\"'”":
        text = text[1:-1].strip()
    return text.strip()


# ── Orchestration ─────────────────────────────────────────────────────────

async def handle_summon(
    *,
    summon_id: str,
    parent_id: str,
    parent_text: str,
    parent_handle: str = "",
    summoner: str = "",
    target_followers: int | None = None,
    cfg: ReplyConfig | None = None,
    force_mode: str = "",
    summon_text: str = "",
) -> SummonResult:
    """Claim, gate, draft, screen, and publish-or-hold one summon.

    Args:
        summon_id: Stable id for this summon — the mention's tweet id, or
            ``manual:<parent_id>`` for the paste path. The idempotency key.
        parent_id: The tweet being replied to.
        parent_text: Its text. The caller supplies this; see module docstring.
        parent_handle: Author of the parent, for per-target caps.
        summoner: Who asked. Must be on the allowlist unless *force_mode*.
        target_followers: Parent author's follower count when known.
        force_mode: Override the configured mode. The ``/x`` command passes
            ``draft`` so an operator typing the command by hand is always the
            approval, whatever the poller is configured to do.
    """
    cfg = cfg or get_reply_config()
    mode = (force_mode or cfg.mode).strip().lower()

    if not cfg.enabled or mode not in (MODE_DRAFT, MODE_AUTO):
        return SummonResult(False, "skipped", reason="auto-reply is off",
                            parent_id=parent_id, summon_id=summon_id)
    if not cfg.subjects:
        return SummonResult(
            False, "skipped",
            reason="no subjects declared — nothing to argue from",
            parent_id=parent_id, summon_id=summon_id,
        )
    # An empty allowlist means nobody. `force_mode` does not bypass this:
    # the operator issuing /x is themselves checked, which is what makes the
    # allowlist meaningful rather than decorative.
    if summoner and not cfg.is_summoner(summoner):
        return SummonResult(
            False, "skipped",
            reason=f"@{summoner.lstrip('@')} is not in connectors.x.reply.summoners",
            parent_id=parent_id, summon_id=summon_id,
        )

    from kazma_core.x_api.reply_store import get_reply_store

    store = get_reply_store()
    claimed = await asyncio.to_thread(
        lambda: store.claim(
            summon_id=summon_id,
            parent_id=parent_id,
            target_handle=parent_handle,
            summoner=summoner,
        )
    )
    if not claimed:
        return SummonResult(
            False, "skipped", reason="already handled",
            parent_id=parent_id, summon_id=summon_id,
        )

    rail = await _rail_error(
        cfg,
        parent_id=parent_id,
        target_handle=parent_handle,
        target_followers=target_followers,
    )
    if rail:
        await asyncio.to_thread(store.mark_skipped, summon_id, rail)
        return SummonResult(False, "skipped", reason=rail,
                            parent_id=parent_id, summon_id=summon_id)

    subject = await classify(parent_text, cfg)
    if subject is None:
        reason = (
            "no declared subject matched this post — Kazma does not have a "
            "view on it, so it said nothing"
        )
        await asyncio.to_thread(store.mark_skipped, summon_id, reason)
        return SummonResult(False, "skipped", reason=reason,
                            parent_id=parent_id, summon_id=summon_id)

    # "what do you think Kazma? 😂" and the same line with 🤬 must not
    # produce the same reply. Only a TRUSTED summoner can dial it -- see
    # ReplyConfig.mood_override_allowed.
    mood = ""
    if summon_text and cfg.mood_override_allowed(summoner):
        mood = mood_from_text(summon_text)
        if mood:
            logger.info("[x-reply] emoji set mood=%s for %s", mood, summon_id)
    draft = await draft_reply(
        subject=subject, parent_text=parent_text,
        parent_handle=parent_handle, mood=mood,
    )
    screen = screen_draft(draft, subject)
    if screen:
        await asyncio.to_thread(store.mark_failed, summon_id, screen)
        logger.warning("[x-reply] draft rejected by screen: %s", screen)
        return SummonResult(
            False, "failed", reason=screen, draft=draft,
            subject_id=subject.id, parent_id=parent_id, summon_id=summon_id,
        )

    if mode == MODE_DRAFT:
        await asyncio.to_thread(
            lambda: store.mark_awaiting(
                summon_id, draft=draft, subject_id=subject.id
            )
        )
        return SummonResult(
            True, "awaiting_approval", draft=draft,
            subject_id=subject.id, parent_id=parent_id, summon_id=summon_id,
            reason="approve to post",
        )

    # auto: publish_x_post re-runs evaluate_post (length, mentions, dedupe,
    # daily/monthly caps) and records the ledger row on success.
    from kazma_core.x_api.booking import publish_x_post

    ok, payload = await publish_x_post(text=draft, reply_to_id=parent_id)
    if not ok:
        err = str(payload.get("error") or "publish failed")
        await asyncio.to_thread(store.mark_failed, summon_id, err)
        return SummonResult(
            False, "failed", reason=err, draft=draft,
            subject_id=subject.id, parent_id=parent_id, summon_id=summon_id,
        )
    tweet_id = str(payload.get("tweet_id") or "")
    await asyncio.to_thread(
        lambda: store.mark_posted(
            summon_id, tweet_id=tweet_id, draft=draft, subject_id=subject.id
        )
    )
    return SummonResult(
        True, "posted", draft=draft, subject_id=subject.id,
        tweet_id=tweet_id, url=str(payload.get("url") or ""), parent_id=parent_id,
        summon_id=summon_id,
    )


async def preview_reply(
    *,
    parent_text: str,
    parent_handle: str = "",
    cfg: ReplyConfig | None = None,
    subject_id: str = "",
    mood: str = "",
) -> SummonResult:
    """Draft against *parent_text* without claiming, storing, or publishing.

    The tuning loop. Editing a ``view`` is guesswork until you can see what it
    produces, and burning a real summon per iteration is both slow and
    irreversible — the caps exist precisely to stop you doing it twenty times.

    Deliberately skips the summoner allowlist (the caller is an authenticated
    operator in Settings, not a stranger on X) and every rate cap (nothing is
    published). It does NOT skip subject matching or the content screen: those
    are the things being tuned, so a preview that bypassed them would be
    testing something other than what ships.

    *subject_id* forces a subject, so you can check how one reads against a
    post its keywords would not have matched.
    """
    cfg = cfg or get_reply_config()
    if not cfg.subjects:
        return SummonResult(
            False, "skipped",
            reason="no subjects declared — nothing to argue from",
        )

    subject: Subject | None
    if subject_id:
        subject = cfg.subject_by_id(subject_id)
        if subject is None:
            return SummonResult(
                False, "skipped", reason=f"no subject with id {subject_id!r}"
            )
    else:
        subject = await classify(parent_text, cfg)
        if subject is None:
            return SummonResult(
                False, "skipped",
                reason=(
                    "no declared subject matched this post — live, Kazma would "
                    "say nothing"
                ),
            )

    # *mood* is passed straight in here (the panel has a picker), rather than
    # read off a summon — a preview has no summoner to trust.
    draft = await draft_reply(
        subject=subject, parent_text=parent_text,
        parent_handle=parent_handle, mood=mood,
    )
    screen = screen_draft(draft, subject)
    if screen:
        return SummonResult(
            False, "failed", reason=screen, draft=draft, subject_id=subject.id
        )
    return SummonResult(
        True, "preview", draft=draft, subject_id=subject.id,
        reason="preview only — nothing was posted or recorded",
    )


async def approve_summon(summon_id: str) -> SummonResult:
    """Publish a draft that was held for approval.

    The stored draft is what posts — not anything the caller passes in.
    Approval resolves an id, not a memory: the same rule
    ``_resolve_proposal_backed_post`` enforces for ``x_post``.
    """
    from kazma_core.x_api.booking import publish_x_post
    from kazma_core.x_api.reply_store import STATUS_AWAITING, get_reply_store

    store = get_reply_store()
    rec = await asyncio.to_thread(store.get, summon_id)
    if rec is None:
        return SummonResult(False, "failed", reason="unknown summon id",
                            summon_id=summon_id)
    if rec.status != STATUS_AWAITING:
        return SummonResult(
            False, "skipped",
            reason=f"nothing to approve (status={rec.status})",
            parent_id=rec.parent_id, summon_id=summon_id,
        )

    ok, payload = await publish_x_post(text=rec.draft_text, reply_to_id=rec.parent_id)
    if not ok:
        err = str(payload.get("error") or "publish failed")
        await asyncio.to_thread(store.mark_failed, summon_id, err)
        return SummonResult(False, "failed", reason=err, draft=rec.draft_text,
                            subject_id=rec.subject_id, parent_id=rec.parent_id,
                            summon_id=summon_id)
    tweet_id = str(payload.get("tweet_id") or "")
    await asyncio.to_thread(store.mark_posted, summon_id, tweet_id=tweet_id)
    return SummonResult(
        True, "posted", draft=rec.draft_text, subject_id=rec.subject_id,
        tweet_id=tweet_id, url=str(payload.get("url") or ""), parent_id=rec.parent_id,
        summon_id=summon_id,
    )
