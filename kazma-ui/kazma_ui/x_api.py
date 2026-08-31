"""X (Twitter) official API connector — Settings status / save / test.

Secrets never leave the server in API responses. Mutating POSTs require
the same Origin + X-Requested-With CSRF pair as the email API.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from kazma_core.errors import validation_error

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


def _placeholder(value: str) -> bool:
    from kazma_core.config_store import is_masked_secret_placeholder

    return is_masked_secret_placeholder(value) or not (value or "").strip()


def _status_payload() -> dict[str, Any]:
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.ledger import get_ledger
    import time

    cfg = get_x_config()
    ledger = get_ledger()
    day = ledger.count_since(time.time() - 86400)
    month = ledger.count_since(time.time() - 30 * 86400)
    return {
        "ok": True,
        "configured": cfg.credentials.complete(),
        "enabled": cfg.enabled,
        "kill_switch": cfg.kill_switch,
        "handle": cfg.handle,
        "can_post": cfg.can_post(),
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
async def x_status() -> JSONResponse:
    try:
        return JSONResponse(_status_payload())
    except Exception as exc:
        return _safe_error(exc)


@router.post("/preview")
async def x_preview(body: XPreviewBody) -> JSONResponse:
    """Dry-run ToU policy for the composer. No network, no ledger write."""
    try:
        from kazma_core.x_api.config import get_x_config
        from kazma_core.x_api.policy import evaluate_post

        cfg = get_x_config()
        text = body.text or ""
        decision = evaluate_post(text, cfg=cfg, reply_to_id=body.reply_to_id or "")
        return JSONResponse(
            {
                "ok": True,
                "allow": decision.allow,
                "reason": decision.reason,
                "chars": len(text.strip()),
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


@router.get("/drafts")
async def x_drafts(limit: int = 50) -> JSONResponse:
    """Flattened save_proposal items for the X Studio inbox."""
    try:
        from kazma_core.agent.artifacts import get_artifact_store

        tenant_id = "default"
        try:
            from kazma_core.tenant_isolation import require_tenant_id

            tenant_id = require_tenant_id() or "default"
        except Exception:
            pass
        items = get_artifact_store().list_proposals(
            tenant_id=tenant_id, limit=max(1, min(int(limit or 50), 200))
        )
        return JSONResponse({"ok": True, "count": len(items), "drafts": items})
    except Exception as exc:
        return _safe_error(exc)


@router.get("/audit")
async def x_audit(limit: int = 50, action: str | None = None) -> JSONResponse:
    """Recent X-integration audit entries (append-only x_audit.db).

    Every API call — post/reply/delete/verify, success, HTTP error, and
    network failure alike — with its full request/response content and a
    local timestamp. Newest first.
    """
    try:
        from kazma_core.x_api.audit import query_x_audit

        bounded = max(1, min(int(limit or 50), 500))
        entries = query_x_audit(limit=bounded, action=(action or None))
        return JSONResponse({"ok": True, "count": len(entries), "entries": entries})
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/post", dependencies=[Depends(_verify_same_origin)])
async def x_post_now(body: XPostBody) -> JSONResponse:
    """Immediate post from X Studio. Operator click is the approval."""
    try:
        from kazma_core.x_api.booking import publish_x_post

        ok, payload = await publish_x_post(
            text=body.text, reply_to_id=body.reply_to_id or ""
        )
        payload["ok"] = ok
        status = 200 if ok else 400
        return JSONResponse(payload, status_code=status)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/credentials", dependencies=[Depends(_verify_same_origin)])
async def x_save_credentials(body: XCredentialsBody) -> JSONResponse:
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
            cs.batch_set(items)
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

        cfg = get_x_config()
        if not cfg.credentials.complete():
            return JSONResponse(
                {"ok": False, "error": "incomplete_credentials", "detail": "Save all four OAuth 1.0a keys first."},
                status_code=400,
            )
        me = await XClient(cfg.credentials).verify_credentials()
        username = str(me.get("username") or "")
        payload = _status_payload()
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
async def x_disconnect() -> JSONResponse:
    try:
        from kazma_core.config_store import get_config_store
        from kazma_core.x_api.config import CREDENTIAL_KEYS

        cs = get_config_store()
        for key in CREDENTIAL_KEYS:
            cs.delete(key)
        cs.set("connectors.x.enabled", False, category="connectors")
        payload = _status_payload()
        payload["disconnected"] = True
        return JSONResponse(payload)
    except Exception as exc:
        return _safe_error(exc)
