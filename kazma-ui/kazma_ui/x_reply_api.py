"""X auto-reply settings — read, save, and dry-run the operator's subjects.

Sits beside ``x_api.py``, which owns the connector itself (credentials, post,
delete, audit). This owns ``connectors.x.reply.*``: the modes, the caps, the
allowlist, and the declared subjects Kazma is allowed to have a view on.

Why a dedicated router rather than the generic ``PUT /api/settings``: the
subjects list is the only config in the product where a *malformed* entry is
silently harmless — ``stance._parse_subjects`` skips anything missing ``id``,
``match`` or ``view`` so one bad subject cannot disable the rest. That is the
right runtime behaviour and the wrong authoring experience: an operator who
fat-fingers a key gets a subject that simply never fires, with nothing to tell
them why. So saving validates and reports per-subject errors instead.

The dry-run endpoint is the point of the panel. Tuning a ``view`` is guesswork
until you can see what it produces, and the alternative — burning a real
summon per iteration — is slow, rate-capped, and irreversible.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/x/reply", tags=["x"])
protected_router = APIRouter(prefix="/api/x/reply", tags=["x"])

_CATEGORY = "connectors"


class SubjectBody(BaseModel):
    # `register` shadows a BaseModel attribute, so the field is named
    # `register_hint` and aliased back. The wire key stays `register` —
    # it matches the ConfigStore key and the docs, and renaming it to
    # dodge a Pydantic warning would be the tail wagging the dog.
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default="")
    match: list[str] = Field(default_factory=list)
    view: str = Field(default="")
    mood: str = Field(default="dry")
    register_hint: str = Field(default="", alias="register")
    hard_lines: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class ReplyConfigBody(BaseModel):
    enabled: bool = Field(default=False)
    mode: str = Field(default="off")
    summoners: list[str] = Field(default_factory=list)
    trigger: str = Field(default="")
    max_replies_per_day: int = Field(default=5)
    max_replies_per_target_per_day: int = Field(default=1)
    cooldown_per_thread_s: int = Field(default=3600)
    min_target_followers: int = Field(default=500)
    poll_interval_s: int = Field(default=600)
    summoner_policy: str = Field(default="allowlist")
    allow_emoji_mood: bool = Field(default=True)
    stance_check: bool = Field(default=True)
    subjects: list[SubjectBody] = Field(default_factory=list)


class PreviewBody(BaseModel):
    # The subject as the editor holds it right now, unsaved edits and all.
    # Without this the dry run could only test stored config, so tuning a
    # view meant saving half-finished subjects to live config to see what
    # they produce.
    subject: SubjectBody | None = Field(default=None)
    parent_text: str = Field(default="")
    parent_handle: str = Field(default="")
    subject_id: str = Field(default="")
    mood: str = Field(default="")


async def _csrf(request: Request) -> None:
    """Same Origin + X-Requested-With pair the X credential routes use."""
    from kazma_ui.x_api import _verify_same_origin as _shared

    await _shared(request)


def _safe_error(exc: Exception) -> JSONResponse:
    logger.exception("[x_reply_api] %s", exc)
    return JSONResponse(
        {"ok": False, "error": "Request failed. Details are in the server log."},
        status_code=500,
    )


def _validate_subjects(subjects: list[SubjectBody]) -> list[str]:
    """Per-subject problems, in operator words. Empty list means clean."""
    from kazma_core.x_api.stance import MOODS

    problems: list[str] = []
    seen: set[str] = set()
    for i, s in enumerate(subjects, start=1):
        label = s.id.strip() or f"#{i}"
        if not s.id.strip():
            problems.append(f"Subject {label}: needs an id (short, lowercase).")
        elif s.id.strip() in seen:
            problems.append(f"Subject {label}: duplicate id.")
        else:
            seen.add(s.id.strip())
        if not [m for m in s.match if m.strip()]:
            problems.append(
                f"Subject {label}: needs at least one keyword, or it can never match."
            )
        if not s.view.strip():
            problems.append(
                f"Subject {label}: needs a view. Without one there is nothing "
                "for Kazma to argue, and it would be inventing a position."
            )
        if s.mood.strip().lower() not in MOODS:
            problems.append(
                f"Subject {label}: unknown mood {s.mood!r} "
                f"(pick one of {', '.join(sorted(MOODS))})."
            )
    return problems


def _payload() -> dict[str, Any]:
    """Current config plus the context the panel needs to explain itself."""
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.stance import MOOD_EMOJI, MOODS, get_reply_config

    with tenant_scope("default"):
        cfg = get_reply_config()
        xcfg = get_x_config()

    return {
        "ok": True,
        "enabled": cfg.enabled,
        "mode": cfg.mode,
        "summoners": list(cfg.summoners),
        "trigger": cfg.trigger,
        "max_replies_per_day": cfg.max_replies_per_day,
        "max_replies_per_target_per_day": cfg.max_replies_per_target_per_day,
        "cooldown_per_thread_s": cfg.cooldown_per_thread_s,
        "min_target_followers": cfg.min_target_followers,
        "poll_interval_s": cfg.poll_interval_s,
        "summoner_policy": cfg.summoner_policy,
        "allow_emoji_mood": cfg.allow_emoji_mood,
        "stance_check": cfg.stance_check,
        "subjects": [
            {
                "id": s.id,
                "match": list(s.match),
                "view": s.view,
                "mood": s.mood,
                "register": s.register,
                "hard_lines": list(s.hard_lines),
                "examples": list(s.examples),
            }
            for s in cfg.subjects
        ],
        "moods": sorted(MOODS),
        # The panel shows the emoji legend rather than making the
        # operator guess which ones are wired.
        "mood_emoji": {e: m for e, m in MOOD_EMOJI.items()},
        # The panel shows these rather than letting the operator discover them
        # by watching nothing happen.
        "connector_ready": xcfg.can_post(),
        "handle": xcfg.handle,
        "can_draft": cfg.can_draft(),
    }


@router.get("")
async def x_reply_status() -> JSONResponse:
    try:
        return JSONResponse(_payload())
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


@router.get("/recent")
async def x_reply_recent(limit: int = 20) -> JSONResponse:
    """Recent summons — including the skipped ones, which are the useful half.

    "Why didn't it reply?" is the question this panel exists to answer, and a
    list that showed only successes would answer everything except that.
    """
    try:
        from kazma_core.x_api.reply_store import get_reply_store

        rows = get_reply_store().recent(limit=max(1, min(100, int(limit))))
        return JSONResponse({"ok": True, "rows": [r.to_dict() for r in rows]})
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


@router.get("/conversations")
async def x_reply_conversations(limit: int = 30) -> JSONResponse:
    """Whole exchanges, newest first — who summoned, what was said, what Kazma said.

    Distinct from ``/recent``, which is a state list for the settings panel.
    This is the reading view: three turns per row, so "why did it say that?"
    is answerable without opening X and reconstructing the thread by hand.

    Skipped and failed summons are included on purpose. "It did not reply"
    is the most common question, and an exchange showing the incoming post
    with `no declared subject matched` against it is the answer.
    """
    try:
        from kazma_core.x_api.reply_store import get_reply_store

        rows = get_reply_store().recent(limit=max(1, min(100, int(limit))))
        out = []
        for r in rows:
            out.append({
                "summon_id": r.summon_id,
                "parent_id": r.parent_id,
                # Who wrote the post being replied to.
                "target": r.target_handle,
                "said": r.parent_text,
                # Who called Kazma in, and how.
                "summoner": r.summoner,
                "summon": r.summon_text,
                # What Kazma said, or why it did not.
                "reply": r.draft_text,
                "status": r.status,
                "subject": r.subject_id,
                "reason": r.reason,
                "tweet_id": r.tweet_id,
                "url": (
                    f"https://x.com/i/web/status/{r.tweet_id}"
                    if r.tweet_id else
                    (f"https://x.com/i/web/status/{r.parent_id}" if r.parent_id else "")
                ),
                "at": r.created_at,
            })
        return JSONResponse({"ok": True, "rows": out})
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


@protected_router.put("", dependencies=[Depends(_csrf)])
async def x_reply_save(body: ReplyConfigBody) -> JSONResponse:
    try:
        from kazma_core.config_store import get_config_store
        from kazma_core.x_api.stance import MODE_AUTO, MODE_DRAFT, MODE_OFF

        problems = _validate_subjects(body.subjects)
        if problems:
            return JSONResponse(
                {"ok": False, "error": "Fix these first.", "problems": problems},
                status_code=400,
            )

        mode = (body.mode or MODE_OFF).strip().lower()
        if mode not in (MODE_OFF, MODE_DRAFT, MODE_AUTO):
            mode = MODE_OFF
        if body.enabled and mode == MODE_OFF:
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        "Auto-reply is enabled but the mode is 'off'. Pick "
                        "'draft' (Kazma asks before posting) or 'auto'."
                    ),
                },
                status_code=400,
            )
        from kazma_core.x_api.stance import SUMMON_ALLOWLIST, SUMMON_ANYONE

        policy = (body.summoner_policy or SUMMON_ALLOWLIST).strip().lower()
        if policy not in (SUMMON_ALLOWLIST, SUMMON_ANYONE):
            policy = SUMMON_ALLOWLIST

        if body.enabled and policy == SUMMON_ALLOWLIST and not body.summoners:
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        "No summoners listed. An empty allowlist means nobody "
                        "can summon Kazma, so nothing would ever fire. Add "
                        "your own handle, or switch to 'anyone'."
                    ),
                },
                status_code=400,
            )

        # `anyone` + `auto` is the one combination where a stranger's tweet
        # causes an unattended post under the operator's name. It is their
        # account and their call, so this warns rather than refuses — but it
        # says so out loud rather than letting it be discovered in the
        # replies. The other rails still hold: subject match, three caps, the
        # follower floor and the content screen.
        warnings: list[str] = []
        if body.enabled and mode == MODE_AUTO and not body.stance_check:
            warnings.append(
                "Stance check is off and replies post unattended. Nothing "
                "verifies a draft argues your view before it publishes — a "
                "model that drifts will post the other side under your name."
            )
        if body.enabled and policy == SUMMON_ANYONE and mode == MODE_AUTO:
            warnings.append(
                "Anyone can summon and replies post unattended. A stranger's "
                "mention will publish under your name without you reading it. "
                "Consider 'draft' until you have watched it for a while."
            )
        if body.enabled and policy == SUMMON_ANYONE and not body.summoners:
            warnings.append(
                "With no handles listed, nobody is a trusted summoner, so the "
                "emoji tone control is inactive — every reply uses the "
                "subject's own mood."
            )

        subjects = [
            {
                "id": s.id.strip(),
                "match": [m.strip() for m in s.match if m.strip()],
                "view": s.view.strip(),
                "mood": s.mood.strip().lower() or "dry",
                "register": s.register_hint.strip(),
                "hard_lines": [h.strip() for h in s.hard_lines if h.strip()],
                "examples": [e.strip() for e in s.examples if e.strip()],
            }
            for s in body.subjects
        ]

        items: list[tuple[str, Any, str]] = [
            ("connectors.x.reply.enabled", bool(body.enabled), _CATEGORY),
            ("connectors.x.reply.mode", mode, _CATEGORY),
            (
                "connectors.x.reply.summoners",
                [h.strip().lstrip("@").lower() for h in body.summoners if h.strip()],
                _CATEGORY,
            ),
            ("connectors.x.reply.trigger", body.trigger.strip(), _CATEGORY),
            (
                "connectors.x.reply.max_replies_per_day",
                max(0, min(50, int(body.max_replies_per_day))),
                _CATEGORY,
            ),
            (
                "connectors.x.reply.max_replies_per_target_per_day",
                max(0, min(10, int(body.max_replies_per_target_per_day))),
                _CATEGORY,
            ),
            (
                "connectors.x.reply.cooldown_per_thread_s",
                max(0, min(86400, int(body.cooldown_per_thread_s))),
                _CATEGORY,
            ),
            (
                "connectors.x.reply.min_target_followers",
                max(0, min(1_000_000, int(body.min_target_followers))),
                _CATEGORY,
            ),
            (
                "connectors.x.reply.poll_interval_s",
                max(60, min(3600, int(body.poll_interval_s))),
                _CATEGORY,
            ),
            ("connectors.x.reply.summoner_policy", policy, _CATEGORY),
            (
                "connectors.x.reply.allow_emoji_mood",
                bool(body.allow_emoji_mood),
                _CATEGORY,
            ),
            (
                "connectors.x.reply.stance_check",
                bool(body.stance_check),
                _CATEGORY,
            ),
            ("connectors.x.reply.subjects", subjects, _CATEGORY),
        ]
        get_config_store().batch_set(items)

        payload = _payload()
        payload["saved"] = True
        payload["warnings"] = warnings
        # The poller reads config live, but it is only STARTED at boot when
        # can_draft() was true. Turning auto-reply on in a running server
        # therefore needs a restart -- say so rather than letting the operator
        # wonder why nothing polls.
        payload["restart_required_for_poller"] = bool(
            payload.get("can_draft") and not _poller_running()
        )
        return JSONResponse(payload)
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


def _poller_running() -> bool:
    try:
        from kazma_core.x_api.mentions_fire import get_mentions_task

        task = get_mentions_task()
        return task is not None and not task.done()
    except Exception:  # noqa: BLE001
        return False


@protected_router.post("/preview", dependencies=[Depends(_csrf)])
async def x_reply_preview(body: PreviewBody) -> JSONResponse:
    """Draft against pasted text. Publishes nothing, records nothing."""
    try:
        text = (body.parent_text or "").strip()
        if not text:
            return JSONResponse(
                {"ok": False, "error": "Paste the post you want a reply to."},
                status_code=400,
            )
        from kazma_core.tenant_context import tenant_scope
        from kazma_core.x_api.reply import preview_reply

        # Settings requests carry a tenant, but provider keys are tenant-scoped
        # vault rows and this is the one endpoint that spends a model call.
        with tenant_scope("default"):
            override = None
            if body.subject is not None and body.subject.view.strip():
                from kazma_core.x_api.stance import Subject

                override = Subject(
                    id=body.subject.id.strip() or "(unsaved)",
                    match=tuple(
                        m.strip().lower() for m in body.subject.match if m.strip()
                    ),
                    view=body.subject.view.strip(),
                    mood=body.subject.mood.strip().lower() or "dry",
                    register=body.subject.register_hint.strip(),
                    hard_lines=tuple(
                        h.strip() for h in body.subject.hard_lines if h.strip()
                    ),
                    examples=tuple(
                        e.strip() for e in body.subject.examples if e.strip()
                    ),
                )
            result = await preview_reply(
                parent_text=text,
                parent_handle=(body.parent_handle or "").strip().lstrip("@"),
                subject_id=(body.subject_id or "").strip(),
                mood=(body.mood or "").strip().lower(),
                subject_override=override,
            )
        payload = result.to_dict()
        payload["ok"] = result.ok
        return JSONResponse(payload)
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)
