"""X (Twitter) official API connector — Settings status / save / test.

Secrets never leave the server in API responses. Mutating POSTs require
the same Origin + X-Requested-With CSRF pair as the email API.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from kazma_core.errors import described_error, validation_error
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


class _XThreadBody(BaseModel):
    segments: list[str] = Field(..., min_length=2, max_length=25)
    intent_key: str = Field(..., min_length=1, max_length=200)


class _XThreadActionBody(BaseModel):
    expected_revision: int = Field(..., ge=1, strict=True)
    approval_token: str = Field(default="", max_length=64)


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
    thread_segments: list[Annotated[str, Field(max_length=32000)]] = Field(default_factory=lambda: ["", ""], min_length=2, max_length=25)


def _safe_error(exc: Exception, status: int = 500) -> JSONResponse:
    """An unexpected failure: a stable code and a reference, the exception
    logged under it (``kazma_core.errors.safe_error``). ``detail`` echoed the
    exception's own text whenever ``KAZMA_PRODUCTION`` was unset."""
    info = described_error("Something went wrong", exc)
    return JSONResponse(
        {
            "ok": False,
            "error": "internal_error",
            "detail": str(info),
            "code": info.code,
            "ref": info.ref,
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


# The one same-origin rule (kazma_ui.same_origin, Scheduled audit SCH-S12):
# this copy compared the Origin with the Host header only.
from kazma_ui.same_origin import verify_same_origin as _verify_same_origin  # noqa: E402


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
        return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=409)
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
        return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=409)
    return JSONResponse({"ok": True, **result})


@router.get("/operations")
def x_operations(limit: int = 50, state: str = "", query: str = "", cursor: str = "") -> JSONResponse:
    from kazma_core.x_api.publication_service import operation_inventory

    try:
        return JSONResponse({"ok": True, **operation_inventory(limit=limit, state=state, query=query, cursor=cursor)})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=400)


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


class _XCheckBody(BaseModel):
    expected_version: int = Field(..., ge=1, strict=True)


@protected_router.post("/operations/{operation_id}/check", dependencies=[Depends(_verify_same_origin)])
async def x_operation_check(operation_id: str, body: _XCheckBody) -> JSONResponse:
    """Ask X what became of an operation whose outcome is unknown, and settle it.
    An unexpected error is the app's catch-all's (logged, a safe 500)."""
    from kazma_core.x_api.publication_service import check_on_x

    result = await check_on_x(operation_id, expected_version=body.expected_version)
    status = 200 if result.get("ok") else {"not_found": 404, "conflict": 409, "not_unknown": 409,
                                           "x_error": 502}.get(str(result.get("code")), 422)
    return JSONResponse(result, status_code=status)


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
        return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=400)
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
        return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=400)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/generate", dependencies=[Depends(_verify_same_origin)])
async def x_generate(body: _XGenerateBody) -> JSONResponse:
    """Save manual alternatives using the X model; never send to X."""
    from kazma_core.agent.artifacts import get_artifact_store
    from kazma_core.x_api.post_drafting import draft_posts
    from kazma_core.x_api.references import ReferenceUnreadable
    from kazma_core.x_api.reply import DraftFailed

    try:
        tenant = await asyncio.to_thread(_tenant_id)  # a settings read (audit 2026-10-26 AUD-111)
        result = await draft_posts(body.brief, count=body.count, subject_id=body.subject_id)
        store = await asyncio.to_thread(get_artifact_store)
        reviews = [{**review, "models": list(result.models)} for review in result.reviews]
        saved = await asyncio.to_thread(store.save_proposal, tenant, "", "x_posts", list(result.drafts), reviews=reviews)
        return JSONResponse({"ok": True, "proposal": saved, "models": list(result.models),
                             "review_required": True, "subject_id": result.subject_id, "usage": result.usage,
                             "references": list(result.references)})
    except ReferenceUnreadable as exc:
        # Names the reference file the owner set in Settings -> X, which
        # validation_error would show as <redacted>.
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except (DraftFailed, ValueError) as exc:
        return JSONResponse({"ok": False, "error": validation_error(exc, max_chars=600)}, status_code=400)
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
        return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=400)
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
                {"ok": False, "posted": False, "error": validation_error(exc)},
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

        # Each key is deleted: the store refuses an empty write over a stored
        # secret, so "" left all four keys in place behind "disconnected"
        # (found 2026-11-17).
        cs.batch_set([(x_config_key("connectors.x.enabled"), False, "connectors")])
        for key in CREDENTIAL_KEYS:
            cs.delete(x_config_key(key))
        payload = _status_payload()
        payload["disconnected"] = True
        return JSONResponse(payload)
    except Exception as exc:
        return _safe_error(exc)


# ── The whole X API: the app's Bearer token and Sign in with X ─────────────
# x_api.operations names, for each of X's API operations, which of the three
# authentications it takes; these routes keep the two besides the OAuth 1.0a
# keys above (x_api.auth_tokens) and run the OAuth 2.0 sign-in.

#: A Bearer token or an OAuth 2.0 client id/secret as X issues them: URL-safe
#: characters only, never a space or a control character.
_X_TOKEN_TEXT = re.compile(r"[A-Za-z0-9%._~+/=:-]{8,2048}")


class _XApiAccessBody(BaseModel):
    bearer_token: str = Field(default="", max_length=2048)
    client_id: str = Field(default="", max_length=256)
    client_secret: str = Field(default="", max_length=512)
    remove_bearer: bool = Field(default=False)
    remove_client: bool = Field(default=False)


class _XSignInBody(BaseModel):
    every_scope: bool = Field(default=False)


def _own_base(request: Request) -> str:
    """The install's own address (KAZMA_PUBLIC_URL, else its host and port),
    never one a request's Host header names (email_api._request_base)."""
    from kazma_ui.email_api import _request_base

    return _request_base(request).rstrip("/")


def _api_access_payload(base: str) -> dict[str, Any]:
    """What Settings -> X shows about the whole X API: which authentications
    are there, who signed in with X, and how many operations Kazma can call
    now. Never a token."""
    import os

    from kazma_core.x_api import auth_tokens
    from kazma_core.x_api import operations as ops
    from kazma_core.x_api.oauth2_sign_in import redirect_uri

    have = auth_tokens.available_auth()
    client_id, client_secret = auth_tokens.oauth2_client()
    account = auth_tokens.oauth2_account()
    every = ops.all_operations()
    callable_now = [o for o in every if o.kind != "refused" and any(have.get(a) for a in o.auth)]
    return {
        "ok": True,
        "spec_version": ops.spec_version(),
        "available_auth": have,
        "bearer_set": bool(auth_tokens.bearer_token()),
        "bearer_from_env": bool(os.environ.get("X_BEARER_TOKEN", "").strip()),
        "client_id": client_id,
        "client_secret_set": bool(client_secret),
        "signed_in": bool(have.get("oauth2")),
        "account": {k: account.get(k) for k in ("username", "name", "id") if account.get(k)},
        "scopes": sorted(str(s) for s in (account.get("scopes") or [])),
        "callback_url": redirect_uri(base),
        "operations": {
            "total": len(every),
            "callable": len(callable_now),
            "refused": sum(1 for o in every if o.kind == "refused"),
        },
    }


@router.get("/api-access")
def x_api_access(request: Request) -> JSONResponse:
    return JSONResponse(_api_access_payload(_own_base(request)))


def _token_text(value: str, what: str) -> str:
    text = value.strip()
    if not _X_TOKEN_TEXT.fullmatch(text):
        raise ValueError(f"That does not look like an X {what}: paste it as X shows it.")
    return text


@protected_router.post("/api-access", dependencies=[Depends(_verify_same_origin)])
def x_api_access_save(request: Request, body: _XApiAccessBody) -> JSONResponse:
    """Keep (or remove) the app's Bearer token and the OAuth 2.0 client; a
    field left empty or masked keeps what is stored. Removing the client also
    signs out of X (its tokens are useless without it)."""
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api import auth_tokens
    from kazma_core.x_api.ownership import x_config_key

    items: list[tuple[str, Any, str]] = []
    # A secret is cleared by deleting it: the store refuses to write one
    # empty (that is what an unreadable vault pointer looks like).
    removed: list[str] = []
    try:
        if body.remove_bearer:
            removed.append(auth_tokens.BEARER_KEY)
        elif not _placeholder(body.bearer_token):
            items.append((auth_tokens.BEARER_KEY, _token_text(body.bearer_token, "Bearer token"), "connectors"))
        if body.remove_client:
            removed += [auth_tokens.CLIENT_ID_KEY, auth_tokens.CLIENT_SECRET_KEY]
        else:
            if not _placeholder(body.client_id):
                items.append((auth_tokens.CLIENT_ID_KEY, _token_text(body.client_id, "client ID"), "connectors"))
            if not _placeholder(body.client_secret):
                items.append((auth_tokens.CLIENT_SECRET_KEY,
                              _token_text(body.client_secret, "client secret"), "connectors"))
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": "invalid_value", "detail": validation_error(exc)},
                            status_code=400)
    store = get_config_store()
    if items:
        store.batch_set([(x_config_key(k), v, c) for k, v, c in items])
    for key in removed:
        store.delete(x_config_key(key))
    if body.remove_client:
        auth_tokens.forget_oauth2()
    payload = _api_access_payload(_own_base(request))
    payload["saved"] = bool(items or removed)
    return JSONResponse(payload)


@protected_router.post("/oauth2/start", dependencies=[Depends(_verify_same_origin)])
async def x_oauth2_start(request: Request, body: _XSignInBody | None = None) -> JSONResponse:
    """Start Sign in with X from Kazma's own page: the answer is X's consent
    page (opened in this tab) and the binding cookie (kazma_ui.oauth_sign_in)."""
    from kazma_core.x_api import oauth2_sign_in

    from kazma_ui.oauth_sign_in import answer_start

    base = _own_base(request)
    every = bool(body.every_scope) if body else False
    return await answer_start(
        request,
        callback_url=oauth2_sign_in.redirect_uri(base),
        start=lambda: oauth2_sign_in.start(base, every_scope=every),
    )


@router.get("/oauth2/callback")
async def x_oauth2_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """X sends the browser back here: take the attempt, trade the code for
    X's tokens, keep them, read who signed in, and go back to Settings -> X."""
    from urllib.parse import quote

    from kazma_core.security.oauth_attempts import AttemptRefused
    from kazma_core.x_api import auth_tokens, oauth2_sign_in
    from kazma_core.x_api.client import XApiError, XClient
    from kazma_core.x_api.config import get_x_config

    from kazma_ui.oauth_sign_in import forget_binding, take_attempt

    back = _own_base(request) + "/settings?tab=x"
    if error or not code or not state:
        # X's own words for a refusal are a short code (access_denied ...).
        why = re.sub(r"[^a-z_]", "", (error or "missing_code").lower())[:40] or "failed"
        return RedirectResponse(f"{back}&x_oauth=error&msg={quote(why)}", status_code=302)
    try:
        attempt = await take_attempt(request, state, providers=("x",))
    except AttemptRefused as exc:
        logger.warning("[x.oauth2] callback refused: %s", exc.reason)
        return forget_binding(RedirectResponse(f"{back}&x_oauth=error&reason={quote(exc.reason)}",
                                               status_code=302), request, state)
    try:
        answer = await asyncio.to_thread(oauth2_sign_in.exchange, code, state, attempt)
    except oauth2_sign_in.SignInFailed as exc:
        return forget_binding(RedirectResponse(f"{back}&x_oauth=error&msg={quote(exc.code)}", status_code=302),
                              request, state, attempt)
    await asyncio.to_thread(auth_tokens.save_oauth2_tokens, answer, account={})
    username = ""
    try:
        creds = await asyncio.to_thread(lambda: get_x_config().credentials)
        me = await XClient(creds).get_account(auth="oauth2")
        username = str(me.get("username") or "")
        await asyncio.to_thread(auth_tokens.save_oauth2_tokens, answer,
                                account={"username": username, "name": str(me.get("name") or ""),
                                         "id": str(me.get("id") or "")})
    except XApiError as exc:
        # Signed in: only the account's name could not be read now.
        logger.warning("[x.oauth2] signed in; the account could not be read: %s", type(exc).__name__)
    logger.info("[x.oauth2] signed in with X%s", f" as @{username}" if username else "")
    return forget_binding(RedirectResponse(f"{back}&x_oauth=ok&username={quote(username)}", status_code=302),
                          request, state, attempt)


@protected_router.post("/oauth2/sign-out", dependencies=[Depends(_verify_same_origin)])
def x_oauth2_sign_out(request: Request) -> JSONResponse:
    """Forget the Sign in with X tokens and account (the client id stays)."""
    from kazma_core.x_api import auth_tokens

    auth_tokens.forget_oauth2()
    payload = _api_access_payload(_own_base(request))
    payload["signed_out"] = True
    return JSONResponse(payload)


@router.get("/threads")
async def _x_threads(cursor: str = "") -> JSONResponse:
    from kazma_core.x_api.thread_store import thread_page

    try:
        return JSONResponse({"ok": True, **await asyncio.to_thread(thread_page, cursor=cursor)})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=400)


@router.get("/health")
async def _x_health() -> JSONResponse:
    from kazma_core.diagnostic_scope import read_only_diagnostic
    from kazma_core.x_api.health import health_snapshot

    with read_only_diagnostic("/api/x/health"):
        return JSONResponse({"ok": True, **await asyncio.to_thread(health_snapshot)})


@protected_router.post("/threads", dependencies=[Depends(_verify_same_origin)])
async def _x_thread_stage(body: _XThreadBody, request: Request) -> JSONResponse:
    from kazma_core.x_api.threads import prepare_thread

    from kazma_ui.x_reply_api import _actor

    try:
        # The principal is a session-store read (audit 2026-10-26 AUD-111).
        actor = await asyncio.to_thread(_actor, request)
        detail = await asyncio.to_thread(prepare_thread, body.segments, intent_key=body.intent_key, actor=actor)
        return JSONResponse({"ok": True, "thread": detail})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=400)


@protected_router.post("/threads/{ident}/{action}", dependencies=[Depends(_verify_same_origin)])
async def _x_thread_action(ident: str, action: str, body: _XThreadActionBody, request: Request) -> JSONResponse:
    from kazma_core.x_api import thread_store
    from kazma_core.x_api.threads import publish_thread

    from kazma_ui.x_reply_api import _actor

    try:
        # The principal is a session-store read (audit 2026-10-26 AUD-111).
        actor = await asyncio.to_thread(_actor, request)
        if action == "publish":
            detail = await publish_thread(ident, revision=body.expected_revision, token=body.approval_token, actor=actor)
        elif action == "cancel":
            detail = await asyncio.to_thread(thread_store.cancel_thread, ident, revision=body.expected_revision, token=body.approval_token, actor=actor)
        elif action == "review":
            detail = await asyncio.to_thread(thread_store.review_thread, ident, revision=body.expected_revision, actor=actor)
        else:
            raise ValueError("Unsupported thread action.")
        return JSONResponse({"ok": True, "thread": detail})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=409)
