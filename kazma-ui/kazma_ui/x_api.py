"""X (Twitter) official API connector — Settings status / save / test.

Secrets never leave the server in API responses. Mutating POSTs require
the same Origin + X-Requested-With CSRF pair as the email API.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from kazma_core.errors import validation_error
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/x", tags=["x"])
protected_router = APIRouter(prefix="/api/x", tags=["x"])


class XCredentialsBody(BaseModel):
    api_key: str = Field(default="")
    api_key_secret: str = Field(default="")
    access_token: str = Field(default="")
    access_token_secret: str = Field(default="")
    handle: str = Field(default="")
    enabled: bool = Field(default=True)
    max_posts_per_day: int | None = Field(default=None)
    max_posts_per_month: int | None = Field(default=None)


class XPreviewBody(BaseModel):
    text: str = Field(default="")
    reply_to_id: str = Field(default="")


class XPostBody(BaseModel):
    text: str = Field(..., min_length=1)
    reply_to_id: str = Field(default="")
    proposal_id: str = Field(default="")
    idempotency_key: str = Field(default="", max_length=200)


class XDeleteBody(BaseModel):
    tweet_id: str = Field(..., min_length=1)


class XDraftDiscardBody(BaseModel):
    #: A draft's item id (``prop_x:3``) or a set id; restore brings back
    #: drafts that were dismissed, never touching posted/scheduled ones.
    id: str = Field(..., min_length=1, max_length=200)
    restore: bool = Field(default=False)


class _XGenerateBody(BaseModel):
    brief: str = Field(..., min_length=1, max_length=4000)
    count: int = Field(default=1, ge=1, le=3)
    subject_id: str = Field(default="", max_length=80)


class _XQueueActionBody(BaseModel):
    expected_version: int = Field(..., ge=1, strict=True)
    when: str = Field(default="", max_length=100)


class _XComposerBody(BaseModel):
    expected_revision: int = Field(..., ge=0, strict=True)
    text: str = Field(default="", max_length=32000)
    reply_to_id: str = Field(default="", max_length=200)
    when: str = Field(default="", max_length=100)
    proposal_id: str = Field(default="", max_length=200)
    draft_text: str = Field(default="", max_length=32000)


def _is_production() -> bool:
    return (os.environ.get("KAZMA_PRODUCTION") or "").strip().lower() in (
        "1", "true", "on", "yes",
    )


def _safe_error(exc: Exception, status: int = 500) -> JSONResponse:
    logger.exception("[x_api] %s", exc)
    return JSONResponse(
        {
            "ok": False,
            "error": "internal_error",
            "detail": "" if _is_production() else str(exc)[:300],
        },
        status_code=status,
    )


def _public_audit_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Drop raw JSON bodies — the UI shows ``text`` / ``error_detail``.

    The store keeps ``request_body`` / ``response_body`` for forensics;
    shipping them to ``/scheduled`` is what made a click dump JSON.
    """
    out = dict(row)
    out.pop("request_body", None)
    out.pop("response_body", None)
    return out


async def _verify_same_origin(request: Request) -> None:
    xrw = request.headers.get("x-requested-with", "").lower()
    if xrw != "xmlhttprequest":
        raise HTTPException(status_code=403, detail="missing custom request header")
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if origin:
        own_host = request.headers.get("host") or ""
        try:
            from urllib.parse import urlparse

            origin_host = urlparse(origin).netloc
        except Exception:
            origin_host = ""
        if own_host and origin_host and origin_host != own_host:
            raise HTTPException(status_code=403, detail="cross-origin request denied")


def _tenant_id() -> str:
    from kazma_core.x_api.ownership import x_tenant_id

    return x_tenant_id()


def _bind_proposal(text: str, proposal_id: str) -> tuple[str, str]:
    """If *proposal_id* is set, the stored draft wins. Raises ValueError."""
    ref = (proposal_id or "").strip()
    body = (text or "").strip()
    if not ref:
        return body, ""
    from kazma_core.agent.artifacts import get_artifact_store

    stored = get_artifact_store().stored_text_for(ref, tenant_id=_tenant_id())
    if not stored:
        raise ValueError("proposal_id did not resolve to a single saved draft")
    return stored, ref


def _mark_proposal_posted(ref: str, *, via: str, used_ref: str = "") -> None:
    """Mark the ONE draft *ref* names as used (never its whole set)."""
    if not ref:
        return
    try:
        from kazma_core.agent.artifacts import get_artifact_store

        get_artifact_store().proposal_posted(
            ref, tenant_id=_tenant_id(), via=via, used_ref=used_ref
        )
    except Exception:
        logger.debug("[x_api] proposal_posted failed for %s", ref, exc_info=True)


def _placeholder(value: str) -> bool:
    from kazma_core.config_store import is_masked_secret_placeholder

    return is_masked_secret_placeholder(value) or not (value or "").strip()


def _status_payload() -> dict[str, Any]:
    import time

    from kazma_core.x_api.ai_budget import budget_status
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.ledger import get_ledger
    from kazma_core.x_api.publication_store import get_publication_store

    cfg = get_x_config()
    ledger = get_ledger()
    day = ledger.count_since(time.time() - 86400)
    month = ledger.count_since(time.time() - 30 * 86400)
    publication = get_publication_store().summary(tenant_id=_tenant_id(), account_id=cfg.account_id)
    day = max(day, publication["posts_today"])
    month = max(month, publication["posts_30d"])
    return {
        "ok": True,
        "configured": cfg.credentials.complete(),
        "enabled": cfg.enabled,
        "kill_switch": cfg.kill_switch,
        "handle": cfg.handle,
        "can_post": cfg.can_post(),
        "restore_paused": cfg.restore_paused,
        "verified_account_id": cfg.account_id,
        "publication": publication,
        "ai_budget": budget_status(),
        "verified_username": "",
        "caps": {
            "max_posts_per_day": cfg.max_posts_per_day,
            "posts_today": day,
            "max_posts_per_month": cfg.max_posts_per_month,
            "posts_30d": month,
            "max_chars": cfg.max_chars,
            "max_mentions": cfg.max_mentions,
        },
        "always_hitl": True,
        "keys_set": {
            "api_key": bool(cfg.credentials.api_key),
            "api_key_secret": bool(cfg.credentials.api_key_secret),
            "access_token": bool(cfg.credentials.access_token),
            "access_token_secret": bool(cfg.credentials.access_token_secret),
        },
    }


@router.get("/status")
def x_status() -> JSONResponse:
    try:
        return JSONResponse(_status_payload())
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/resume", dependencies=[Depends(_verify_same_origin)])
def x_resume() -> JSONResponse:
    from kazma_core.x_api.restore import resume_verified_account

    try:
        resume_verified_account()
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    return JSONResponse(_status_payload())


def _composer_actor(request: Request) -> str:
    from kazma_ui.auth import get_request_principal

    principal = get_request_principal(request) or {}
    return str(principal.get("user_id") or principal.get("username") or "local-operator")


@router.get("/composer")
def x_composer_get(request: Request) -> JSONResponse:
    from kazma_core.x_api.composer import load

    return JSONResponse({"ok": True, **load(actor=_composer_actor(request))})


@protected_router.put("/composer", dependencies=[Depends(_verify_same_origin)])
def x_composer_save(request: Request, body: _XComposerBody) -> JSONResponse:
    from kazma_core.x_api.composer import save
    from kazma_core.x_api.publication_store import PublicationConflictError

    try:
        content = body.model_dump(exclude={"expected_revision"})
        result = save(actor=_composer_actor(request), expected_revision=body.expected_revision, content=content)
    except PublicationConflictError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    return JSONResponse({"ok": True, **result})


@router.get("/operations")
def x_operations(limit: int = 50, state: str = "", query: str = "", cursor: str = "") -> JSONResponse:
    from kazma_core.x_api.publication_service import operation_inventory

    try:
        return JSONResponse({"ok": True, **operation_inventory(limit=limit, state=state, query=query, cursor=cursor)})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/operations/{operation_id}")
def x_operation(operation_id: str) -> JSONResponse:
    from kazma_core.x_api.publication_store import get_publication_store

    store = get_publication_store()
    row = store.get(operation_id, tenant_id=_tenant_id())
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown publication operation")
    keys = ("id", "account_id", "state", "version", "text", "reply_to_id", "kind", "origin", "due_at", "tweet_id", "outcome", "reason", "attempts")
    return JSONResponse({"ok": True, "operation": {key: row[key] for key in keys},
                         "events": store.events(operation_id, tenant_id=_tenant_id())})


@router.post("/preview")
async def x_preview(body: XPreviewBody) -> JSONResponse:
    """Dry-run ToU policy for the composer. No network, no ledger write."""
    try:
        from kazma_core.x_api.config import get_x_config
        from kazma_core.x_api.policy import evaluate_post

        cfg = await asyncio.to_thread(get_x_config)
        text = body.text or ""
        decision = await asyncio.to_thread(evaluate_post, text, cfg=cfg, reply_to_id=body.reply_to_id or "")
        from kazma_core.x_api.text_length import validate_text

        length = await asyncio.to_thread(validate_text, text.strip(), maximum=cfg.max_chars)
        return JSONResponse(
            {
                "ok": True,
                "allow": decision.allow,
                "reason": decision.reason,
                "chars": length.weighted,
                "max_chars": cfg.max_chars,
                "mentions": list(decision.mentions),
                "hashtags": list(decision.hashtags),
                "cashtags": list(decision.cashtags),
                "can_post": cfg.can_post(),
                "handle": cfg.handle,
            }
        )
    except Exception as exc:
        return _safe_error(exc)


@router.get("/queue")
def x_queue(limit: int = 50, cursor: str = "", include_finished: bool = False) -> JSONResponse:
    try:
        from kazma_core.x_api.publication_service import queue_inventory

        return JSONResponse({"ok": True, **queue_inventory(limit=limit, cursor=cursor, include_finished=include_finished)})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/queue/{operation_id}/{action}", dependencies=[Depends(_verify_same_origin)])
def x_queue_action(operation_id: str, action: str, body: _XQueueActionBody) -> JSONResponse:
    try:
        from kazma_core.x_api.booking import _parse_when
        from kazma_core.x_api.publication_service import update_schedule

        due_at = _parse_when(body.when) if action == "reschedule" else None
        changed = update_schedule(operation_id, expected_version=body.expected_version, action=action, due_at=due_at)
        return JSONResponse({"ok": changed, "error": "" if changed else "Queue revision changed or the action is no longer allowed. Refresh and review again."},
                            status_code=200 if changed else 409)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/generate", dependencies=[Depends(_verify_same_origin)])
async def x_generate(body: _XGenerateBody) -> JSONResponse:
    """Save manual alternatives using the X model; never send to X."""
    from kazma_core.agent.artifacts import get_artifact_store
    from kazma_core.x_api.post_drafting import draft_posts
    from kazma_core.x_api.reply import DraftFailed

    try:
        tenant = _tenant_id()
        result = await draft_posts(body.brief, count=body.count, subject_id=body.subject_id)
        store = await asyncio.to_thread(get_artifact_store)
        reviews = [{**review, "models": list(result.models)} for review in result.reviews]
        saved = await asyncio.to_thread(store.save_proposal, tenant, "", "x_posts", list(result.drafts), reviews=reviews)
        return JSONResponse({"ok": True, "proposal": saved, "models": list(result.models),
                             "review_required": True, "subject_id": result.subject_id, "usage": result.usage})
    except (DraftFailed, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return _safe_error(exc)


@router.get("/drafts")
def x_drafts(limit: int = 50, dismissed: bool = False, query: str = "", cursor: str = "") -> JSONResponse:
    """Flattened save_proposal items for the X Studio inbox.

    ``dismissed=true`` lists only drafts retired with Dismiss (so they can be
    restored). A plain ``def``: the store is sync SQLite, and FastAPI runs a
    sync handler in its threadpool instead of on the event loop.
    """
    try:
        from kazma_core.agent.artifacts import get_artifact_store

        page = get_artifact_store().proposal_page(tenant_id=_tenant_id(), limit=limit, only_discarded=dismissed, query=query, cursor=cursor)
        return JSONResponse({"ok": True, **page})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/drafts/discard", dependencies=[Depends(_verify_same_origin)])
def x_drafts_discard(body: XDraftDiscardBody) -> JSONResponse:
    """Dismiss an unused draft from the inbox, or restore a dismissed one.

    The same store operation as the agent's ``discard_proposal`` tool: posted
    and scheduled drafts are never touched, and a dismissed draft cannot be
    published until it is restored.
    """
    try:
        from kazma_core.agent.artifacts import get_artifact_store

        report = get_artifact_store().discard_proposal(
            body.id, tenant_id=_tenant_id(), restore=body.restore
        )
        return JSONResponse({"ok": True, **report})
    except Exception as exc:
        return _safe_error(exc)


@router.get("/audit")
def x_audit(limit: int = 50, action: str | None = None) -> JSONResponse:
    """Recent X-integration audit entries (append-only x_audit.db).

    Every API call — post/reply/delete/verify, success, HTTP error, and
    network failure alike — with its full request/response content and a
    local timestamp. Newest first.
    """
    try:
        from kazma_core.x_api.audit import query_x_audit

        bounded = max(1, min(int(limit or 50), 500))
        entries = [
            _public_audit_entry(row)
            for row in query_x_audit(limit=bounded, action=(action or None))
        ]
        return JSONResponse({"ok": True, "count": len(entries), "entries": entries})
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/post", dependencies=[Depends(_verify_same_origin)])
async def x_post_now(body: XPostBody) -> JSONResponse:
    """Immediate post from X Studio. Operator click is the approval."""
    try:
        from kazma_core.x_api.booking import publish_x_post

        try:
            # Saved-drafts SQLite off the event loop (the publish itself is
            # the only awaited network call in this route).
            text, proposal_ref = await asyncio.to_thread(
                _bind_proposal, body.text, body.proposal_id
            )
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "posted": False, "error": str(exc)},
                status_code=400,
            )
        ok, payload = await publish_x_post(
            text=text, reply_to_id=body.reply_to_id or "",
            idempotency_key="proposal:" + proposal_ref if proposal_ref else body.idempotency_key,
            metadata={"proposal_id": proposal_ref},
        )
        payload["ok"] = ok
        if ok:
            await asyncio.to_thread(
                _mark_proposal_posted,
                proposal_ref,
                via="x_studio_post",
                used_ref=str(payload.get("tweet_id") or ""),
            )
            if proposal_ref:
                payload["proposal_id"] = proposal_ref
        status = 200 if ok else 400
        return JSONResponse(payload, status_code=status)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/delete", dependencies=[Depends(_verify_same_origin)])
async def x_delete_now(body: XDeleteBody) -> JSONResponse:
    """Delete a live tweet from X Studio. Operator click is the approval."""
    try:
        from kazma_core.x_api.booking import delete_x_post

        ok, payload = await delete_x_post(tweet_id=body.tweet_id)
        payload["ok"] = ok
        return JSONResponse(payload, status_code=200 if ok else 400)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/credentials", dependencies=[Depends(_verify_same_origin)])
def x_save_credentials(body: XCredentialsBody) -> JSONResponse:
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        mapping = [
            ("connectors.x.api_key", body.api_key),
            ("connectors.x.api_key_secret", body.api_key_secret),
            ("connectors.x.access_token", body.access_token),
            ("connectors.x.access_token_secret", body.access_token_secret),
        ]
        items: list[tuple[str, Any, str]] = []
        for key, val in mapping:
            if _placeholder(val):
                continue
            items.append((key, val.strip(), "connectors"))
        handle = (body.handle or "").strip()
        if handle:
            if not handle.startswith("@"):
                handle = "@" + handle.lstrip("@")
            items.append(("connectors.x.handle", handle, "connectors"))
        items.append(("connectors.x.enabled", bool(body.enabled), "connectors"))
        if body.max_posts_per_day is not None:
            items.append(
                ("connectors.x.max_posts_per_day", int(body.max_posts_per_day), "connectors")
            )
        if body.max_posts_per_month is not None:
            items.append(
                (
                    "connectors.x.max_posts_per_month",
                    int(body.max_posts_per_month),
                    "connectors",
                )
            )
        if items:
            from kazma_core.x_api.ownership import x_config_key

            cs.batch_set([(x_config_key(key), value, category) for key, value, category in items])
        payload = _status_payload()
        payload["saved"] = True
        return JSONResponse(payload)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/test", dependencies=[Depends(_verify_same_origin)])
async def x_test() -> JSONResponse:
    try:
        from kazma_core.x_api.client import XApiError, XClient
        from kazma_core.x_api.config import get_x_config

        cfg = await asyncio.to_thread(get_x_config)
        if not cfg.credentials.complete():
            return JSONResponse(
                {"ok": False, "error": "incomplete_credentials", "detail": "Save all four OAuth 1.0a keys first."},
                status_code=400,
            )
        me = await XClient(cfg.credentials).verify_credentials()
        from kazma_core.x_api.account_binding import record_account

        await asyncio.to_thread(record_account, cfg.credentials, me)
        username = str(me.get("username") or "")
        payload = await asyncio.to_thread(_status_payload)
        payload["ok"] = True
        payload["verified_username"] = username
        payload["verified"] = True
        return JSONResponse(payload)
    except XApiError as exc:
        return JSONResponse(
            {"ok": False, "error": "x_api", "detail": validation_error(exc)},
            status_code=400,
        )
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/disconnect", dependencies=[Depends(_verify_same_origin)])
def x_disconnect() -> JSONResponse:
    try:
        from kazma_core.config_store import get_config_store
        from kazma_core.x_api.config import CREDENTIAL_KEYS

        cs = get_config_store()
        from kazma_core.x_api.ownership import x_config_key

        cs.batch_set([(x_config_key(key), "", "connectors") for key in CREDENTIAL_KEYS]
                     + [(x_config_key("connectors.x.enabled"), False, "connectors")])
        payload = _status_payload()
        payload["disconnected"] = True
        return JSONResponse(payload)
    except Exception as exc:
        return _safe_error(exc)
