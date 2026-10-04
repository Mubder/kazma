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

import asyncio
import logging
import time
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kazma_core.x_api.ownership import x_config_key, x_tenant_id
from pydantic import BaseModel, ConfigDict, Field, StrictBool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/x/reply", tags=["x"])
protected_router = APIRouter(prefix="/api/x/reply", tags=["x"])

_CATEGORY = "connectors"


class _QualificationBody(BaseModel):
    report: dict[str, Any]


class SubjectBody(BaseModel):
    # `register` shadows a BaseModel attribute, so the field is named
    # `register_hint` and aliased back. The wire key stays `register` —
    # it matches the ConfigStore key and the docs, and renaming it to
    # dodge a Pydantic warning would be the tail wagging the dog.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(default="")
    match: list[str] = Field(default_factory=list)
    view: str = Field(default="")
    mood: str = Field(default="dry")
    register_hint: str = Field(default="", alias="register")
    hard_lines: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    side: str = Field(default="")
    schema_version: int = 1
    revision: int = 1
    target: str = ""
    aliases: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    scope: str = ""
    exceptions: list[str] = Field(default_factory=list)
    allowed_moods: list[str] = Field(default_factory=list)
    allow_draft: StrictBool = True
    allow_auto: StrictBool = False
    evidence_policy: str = "required"
    evidence_max_age_days: int = Field(default=30, ge=1, le=3650, strict=True)
    required_checks: list[str] = Field(default_factory=lambda: ["context", "target", "stance", "evidence", "safety"])
    counterexamples: list[str] = Field(default_factory=list)
    owner: str = ""
    change_reason: str = ""


class _XAISelectionBody(BaseModel):
    """An exact X provider/model pair; credentials stay in the registry."""

    model_config = ConfigDict(extra="forbid")
    selection: str = Field(default="global", max_length=20)
    provider: str = Field(default="", max_length=200)
    model: str = Field(default="", max_length=300)
    local_only: StrictBool = False
    roles: dict[str, dict[str, str]] = Field(default_factory=dict)


class ReplyConfigBody(BaseModel):
    expected_revision: int | None = Field(default=None, ge=0, strict=True)
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
    unmatched: str = Field(default="skip")
    use_knowledge: bool = Field(default=False)
    knowledge_library: str = Field(default="")
    open_thread_marker: str = Field(default="")
    close_thread_marker: str = Field(default="")
    subjects: list[SubjectBody] = Field(default_factory=list)
    ai: _XAISelectionBody | None = Field(default=None)


class SummonIdBody(BaseModel):
    summon_id: str = Field(default="")
    approval_token: str = Field(default="", max_length=64)


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
    ai: _XAISelectionBody | None = Field(default=None)


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


def _actor(request: Request) -> str:
    from kazma_ui.auth import get_request_principal

    principal = get_request_principal(request) or {}
    return str(principal.get("user_id") or principal.get("username") or "local-operator")


def _validate_subjects(subjects: list[SubjectBody]) -> list[str]:
    """Use the same versioned validator as runtime loading."""
    from kazma_core.x_api.subject_policy import normalize_cards

    return list(normalize_cards([subject.model_dump(by_alias=True) for subject in subjects])[1])


def _payload() -> dict[str, Any]:
    """Current config plus the context the panel needs to explain itself."""
    from kazma_core.config_store import get_config_store
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.model_selection import X_MODEL_ROLES, model_options, validate_selection
    from kazma_core.x_api.qualification import qualification_hold
    from kazma_core.x_api.stance import MOOD_EMOJI, MOODS, get_reply_config

    with tenant_scope(x_tenant_id()):
        settings_revision = get_config_store().get(x_config_key("connectors.x.reply.settings_revision"), 0)
        cfg = get_reply_config()
        xcfg = get_x_config()
        ai = validate_selection(get_config_store().get(x_config_key("connectors.x.ai")))
        ai_options = model_options()

    return {
        "ok": True,
        "settings_revision": settings_revision,
        "enabled": cfg.enabled,
        "mode": cfg.mode,
        "ai": ai,
        "ai_options": ai_options,
        "ai_roles": list(X_MODEL_ROLES),
        "auto_qualification_hold": qualification_hold(cfg),
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
        "unmatched": cfg.unmatched,
        "use_knowledge": cfg.use_knowledge,
        "knowledge_library": cfg.knowledge_library,
        "open_thread_marker": cfg.open_thread_marker,
        "close_thread_marker": cfg.close_thread_marker,
        "subjects": [asdict(subject) for subject in cfg.subjects],
        "moods": sorted(MOODS),
        # The panel shows the emoji legend rather than making the
        # operator guess which ones are wired.
        "mood_emoji": {e: m for e, m in MOOD_EMOJI.items()},
        # The panel shows these rather than letting the operator discover them
        # by watching nothing happen.
        "connector_ready": xcfg.can_post(),
        "handle": xcfg.handle,
        "can_draft": cfg.can_draft(),
        # Is the poller actually running right now? can_draft() says the SAVED
        # config would allow it; this says whether the loop exists, which is a
        # different question after a config change without a restart.
        "poller_running": _poller_running(),
        "live_reason": _live_reason(cfg, xcfg),
    }


def _live_reason(cfg: Any, xcfg: Any) -> str:
    """One sentence: is this thing actually going to do anything, and if not, why.

    An operator can have a working dry run and a completely inert feature at
    the same time -- the preview reads the card in the editor, everything else
    reads saved config. That happened on the first live test: subject typed,
    preview drafting happily, `enabled` never saved, and a real mention on X
    went nowhere with nothing to explain it.
    """
    if not xcfg.can_post():
        return "The X connector is not posting-ready — save and test the credentials above."
    if not cfg.enabled:
        return "Auto-reply is OFF. Nothing will happen on X until you enable it and press Save."
    if cfg.mode == "off":
        return "Mode is 'off'. Pick draft or auto, then press Save."
    if not cfg.summoners and cfg.summoner_policy != "anyone":
        return "No trusted handles saved, so nobody can summon it."
    if not _poller_running():
        return (
            f"Saved and live in {cfg.mode} mode, but the mentions poller is not "
            "running — Save again to start it, or restart Kazma. /x roast works meanwhile."
        )
    n = len(cfg.subjects)
    if n == 0:
        return (
            f"Live in {cfg.mode} mode, poller running, voice-only — "
            "no subjects, emoji picks the tone."
        )
    return f"Live in {cfg.mode} mode, poller running, {n} subject(s)."


@router.get("")
def x_reply_status() -> JSONResponse:
    try:
        return JSONResponse(_payload())
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


@router.get("/recent")
def x_reply_recent(limit: int = 20) -> JSONResponse:
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
def x_reply_conversations(limit: int = 30, query: str = "", state: str = "", cursor: str = "") -> JSONResponse:
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

        page = get_reply_store().conversation_page(limit=limit, query=query, state=state, cursor=cursor)
        rows = page.pop("rows")
        out = []
        for r in rows:
            out.append({
                "summon_id": r.summon_id,
                "approval_token": r.approval_token,
                "attempt_no": r.attempt_no,
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
                "can_approve": bool(r.draft_text) and (r.status == "awaiting_approval" or
                                                       (r.status == "failed" and r.publish_outcome in ("rejected", "not_sent"))),
                "can_retry": r.status in ("skipped", "failed", "awaiting_approval", "retry_pending") or
                             (r.status == "drafting" and r.updated_at < time.time() - 600),
                "needs_reconciliation": r.status in ("sending", "outcome_unknown"),
                "subject": r.subject_id,
                "checks": r.decision.get("checks", []),
                "context": r.decision.get("context", {}),
                "models": r.decision.get("models", []),
                "reason": (
                    r.reason
                    or (
                        "Stuck while drafting — the model call never finished. Retry."
                        if r.status == "drafting" else ""
                    )
                ),
                "tweet_id": r.tweet_id,
                "url": (
                    f"https://x.com/i/web/status/{r.tweet_id}"
                    if r.tweet_id else
                    (f"https://x.com/i/web/status/{r.parent_id}" if r.parent_id else "")
                ),
                "at": r.created_at,
            })
        return JSONResponse({"ok": True, **page, "rows": out})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


@router.get("/history/{summon_id}")
def x_reply_history(summon_id: str) -> JSONResponse:
    try:
        from kazma_core.x_api.reply_store import ReplyRecord, get_reply_store

        history = get_reply_store().history(summon_id, limit=200)
        return JSONResponse({"ok": True, "rows": [{**entry, "actor": entry["record"].get("decision_actor", ""),
                                                   "record": ReplyRecord(entry["record"]).to_dict()} for entry in history]})
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


@protected_router.put("", dependencies=[Depends(_csrf)])
async def x_reply_save(body: ReplyConfigBody) -> JSONResponse:
    try:
        from kazma_core.config_store import ConfigRevisionConflict, get_config_store
        from kazma_core.x_api.stance import MODE_AUTO, MODE_DRAFT, MODE_OFF

        revision_key = x_config_key("connectors.x.reply.settings_revision")
        revision = await asyncio.to_thread(get_config_store().get, revision_key, 0)
        if body.expected_revision is not None and body.expected_revision != revision:
            return JSONResponse({"ok": False, "error": "X settings changed. Reload and review before saving."}, status_code=409)

        problems = _validate_subjects(body.subjects)
        ai = None
        if body.ai is not None:
            from kazma_core.x_api.model_selection import (
                XModelUnavailableError,
                validate_provider,
                validate_selection,
            )

            try:
                ai = validate_selection(body.ai.model_dump(exclude_unset=True))
                await asyncio.to_thread(validate_provider, ai)
            except XModelUnavailableError as exc:
                problems.append(str(exc))
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
                "Stance check is off. Auto mode will hold drafts for approval; "
                "mandatory verification and release qualification cannot be disabled."
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

        from kazma_core.x_api.subject_policy import normalize_cards, revise_cards

        subjects, _ = normalize_cards([subject.model_dump(by_alias=True) for subject in body.subjects])
        previous = await asyncio.to_thread(get_config_store().get, x_config_key("connectors.x.reply.subjects"), [])
        subjects = revise_cards(previous, subjects)


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
            (
                "connectors.x.reply.unmatched",
                "voice" if (body.unmatched or "").strip().lower() == "voice" else "skip",
                _CATEGORY,
            ),
            (
                "connectors.x.reply.use_knowledge",
                bool(body.use_knowledge),
                _CATEGORY,
            ),
            (
                "connectors.x.reply.knowledge_library",
                (body.knowledge_library or "").strip(),
                _CATEGORY,
            ),
            (
                "connectors.x.reply.open_thread_marker",
                (body.open_thread_marker or "").strip(),
                _CATEGORY,
            ),
            (
                "connectors.x.reply.close_thread_marker",
                (body.close_thread_marker or "").strip(),
                _CATEGORY,
            ),
            ("connectors.x.reply.subjects", subjects, _CATEGORY),
        ]
        if ai is not None:
            items.append(("connectors.x.ai", ai, _CATEGORY))
        items.append(("connectors.x.reply.settings_revision", revision + 1, _CATEGORY))
        try:
            await asyncio.to_thread(get_config_store().batch_set,
                                   [(x_config_key(key), value, category) for key, value, category in items],
                                   expected=(revision_key, revision))
        except ConfigRevisionConflict as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)

        # Start/stop the poller now so Save is the action, not a restart.
        try:
            from kazma_core.x_api.mentions_fire import ensure_mentions_loop

            await ensure_mentions_loop()
        except Exception:
            logger.exception("[x_reply_api] ensure_mentions_loop failed")

        payload = await asyncio.to_thread(_payload)
        payload["saved"] = True
        payload["warnings"] = warnings
        payload["restart_required_for_poller"] = bool(
            payload.get("can_draft") and not payload.get("poller_running")
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


def _summon_payload(result: Any) -> dict[str, Any]:
    payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    payload["ok"] = bool(getattr(result, "ok", payload.get("ok")))
    return payload


@protected_router.post("/poll", dependencies=[Depends(_csrf)])
async def x_reply_poll() -> JSONResponse:
    """Fetch mentions from X now. Conversations Refresh is this, not a DB reread.

    Ignores the since_id cursor: a deleted mention as cursor made X report
    zero results forever, which is why a new @KazmaAI mention did not appear
    after hard-refresh.
    """
    from kazma_core.x_api.client import XApiError

    try:
        from kazma_core.tenant_context import tenant_scope
        from kazma_core.x_api.mentions_fire import poll_once
        from kazma_core.x_api.stance import get_reply_config

        with tenant_scope(x_tenant_id()):
            cfg = await asyncio.to_thread(get_reply_config)
            if not cfg.can_draft():
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "Auto-reply is not live — enable it in Settings → X "
                            "and Save first."
                        ),
                    },
                    status_code=400,
                )
            rows = await poll_once(cfg=cfg, ignore_cursor=True)
        n = len(rows)
        acted = sum(1 for r in rows if r.get("action") not in ("skipped", None))
        if n == 0:
            msg = (
                "No mentions in X's latest window. If you just posted, wait a "
                "few seconds and poll again."
            )
        else:
            msg = f"Polled {n} mention(s): {acted} new, {n - acted} already handled or skipped."
        return JSONResponse({"ok": True, "rows": rows, "count": n, "message": msg})
    except XApiError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


@protected_router.post("/approve", dependencies=[Depends(_csrf)])
async def x_reply_approve(body: SummonIdBody, request: Request) -> JSONResponse:
    sid = (body.summon_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "summon_id required"}, status_code=400)
    try:
        from kazma_core.tenant_context import tenant_scope
        from kazma_core.x_api.reply import approve_summon

        with tenant_scope(x_tenant_id()):
            result = await approve_summon(sid, approval_token=body.approval_token, actor=await asyncio.to_thread(_actor, request))
        status = 200 if result.ok or result.action == "skipped" else 400
        return JSONResponse(_summon_payload(result), status_code=status)
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


@protected_router.post("/delete", dependencies=[Depends(_csrf)])
async def x_reply_delete(body: SummonIdBody) -> JSONResponse:
    """Delete the posted reply on X (if any) and drop the Conversations row."""
    sid = (body.summon_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "summon_id required"}, status_code=400)
    try:
        from kazma_core.tenant_context import tenant_scope
        from kazma_core.x_api.reply import forget_summon

        with tenant_scope(x_tenant_id()):
            result = await forget_summon(sid, approval_token=body.approval_token)
        status = 200 if result.ok else 400
        return JSONResponse(_summon_payload(result), status_code=status)
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


@protected_router.post("/deny", dependencies=[Depends(_csrf)])
async def x_reply_deny(body: SummonIdBody, request: Request) -> JSONResponse:
    sid = (body.summon_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "summon_id required"}, status_code=400)
    try:
        from kazma_core.tenant_context import tenant_scope
        from kazma_core.x_api.reply import deny_summon

        with tenant_scope(x_tenant_id()):
            result = await deny_summon(sid, approval_token=body.approval_token, actor=await asyncio.to_thread(_actor, request))
        status = 200 if result.ok or result.action == "skipped" else 400
        return JSONResponse(_summon_payload(result), status_code=status)
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


@protected_router.post("/retry", dependencies=[Depends(_csrf)])
async def x_reply_retry(body: SummonIdBody, request: Request) -> JSONResponse:
    """Re-run a skipped/failed/held summon against current config."""
    sid = (body.summon_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "summon_id required"}, status_code=400)
    try:
        from kazma_core.tenant_context import tenant_scope
        from kazma_core.x_api.reply import retry_summon

        with tenant_scope(x_tenant_id()):
            result = await retry_summon(sid, actor=await asyncio.to_thread(_actor, request))
        status = 200 if result.ok or result.action in ("skipped", "awaiting_approval", "posted") else 400
        # failed drafts still 200 — the operator needs the reason, not a 400
        if result.action == "failed":
            status = 200
        return JSONResponse(_summon_payload(result), status_code=status)
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


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
        with tenant_scope(x_tenant_id()):
            override = None
            if body.subject is not None and (
                body.subject.view.strip()
                or (body.subject.side or "").strip().lower() in ("against", "support")
            ):
                from kazma_core.x_api.stance import _parse_subjects

                edited = body.subject.model_dump(by_alias=True)
                edited["id"] = edited["id"].strip() or "unsaved"
                edited["target"] = edited["target"].strip() or edited["id"]
                from kazma_core.x_api.subject_policy import normalize_cards

                _, errors = normalize_cards([edited])
                if errors:
                    return JSONResponse({"ok": False, "error": "; ".join(errors)}, status_code=400)
                parsed = _parse_subjects([edited])
                if parsed:
                    override = parsed[0]
            from kazma_core.x_api.model_selection import x_model_scope

            # Unsaved model selection is tested without activating it.
            async with x_model_scope(body.ai.model_dump(exclude_unset=True) if body.ai else None):
                result = await preview_reply(
                    parent_text=text,
                    parent_handle=(body.parent_handle or "").strip().lstrip("@"),
                    subject_id=(body.subject_id or "").strip(),
                    mood=(body.mood or "").strip().lower(),
                    subject_override=override,
                )
        payload = result.to_dict()
        payload["preview_scope"] = "card_only" if override is not None or body.subject_id else "full_policy"
        payload["publishing_eligible"] = False
        payload["ok"] = result.ok
        return JSONResponse(payload)
    except Exception as exc:  # noqa: BLE001
        return _safe_error(exc)


@router.get("/qualification")
def x_qualification_status() -> JSONResponse:
    from kazma_core.x_api.qualification import qualification_status

    return JSONResponse({"ok": True, **qualification_status()})


@protected_router.put("/qualification", dependencies=[Depends(_csrf)])
def x_qualification_install(body: _QualificationBody) -> JSONResponse:
    from kazma_core.x_api.qualification import install_report

    try:
        metrics = install_report(body.report)
    except (ValueError, TypeError, KeyError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "metrics": metrics})
