"""Durable IDE request receipts; the original handler retains HITL and validation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

# New POST operations receive receipts by default. Only declared reads bypass
# them, so adding an endpoint cannot silently create another mutation path.
_READ_ONLY_PATHS = frozenset({"/api/ide/diff", "/api/ide/lsp"})


class IdeEffectRoute(APIRoute):
    """A repeated keyed request returns its saved result, never another execution."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            path = request.url.path
            if request.method != "POST" or path in _READ_ONLY_PATHS:
                return await original(request)
            raw = await request.body()
            try:
                payload = await request.json() if raw else {}
            except (ValueError, UnicodeError):
                # Opaque/multipart requests still need a receipt; the original
                # handler keeps responsibility for parsing and validating them.
                payload = {"body_sha256": hashlib.sha256(raw).hexdigest()}
            if path == "/api/ide/git" and isinstance(payload, dict):
                from kazma_core.ide.service import _is_readonly_git

                if _is_readonly_git(str(payload.get("subcommand") or "")):
                    return await original(request)
            key = request.headers.get("Idempotency-Key")
            if key is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
                return JSONResponse({"ok": False, "error": "Invalid Idempotency-Key"}, status_code=400)
            from kazma_ui.auth import get_request_principal

            principal = await asyncio.to_thread(get_request_principal, request)
            # Cached results must never bypass authentication or role checks.
            if key is not None and not principal:
                return JSONResponse({"ok": False, "error": "Authentication required"}, status_code=401)
            if principal and principal.get("role") not in ("operator", "admin"):
                return JSONResponse({"ok": False, "error": "Operator role required"}, status_code=403)
            key = key or uuid.uuid4().hex
            actor = json.dumps({"source": (principal or {}).get("source"),
                                "user": (principal or {}).get("user_id"),
                                "username": (principal or {}).get("username")}, sort_keys=True)
            from kazma_core.agent.effect_journal import EffectUncertain, execute_operation
            from kazma_core.ide.workspace_scope import workspace_path_scope
            from kazma_core.workspace.binding import resolve_active_root

            async def dispatch() -> dict[str, Any]:
                response = await original(request)
                body = json.loads(response.body)
                return {"body": body, "status_code": response.status_code,
                        "effect_uncertain": bool(body.get("effect_uncertain")) if isinstance(body, dict) else False}

            try:
                root = await asyncio.to_thread(resolve_active_root)
                # Capture once: a global workspace switch while awaiting approval
                # cannot redirect this admitted request to a different repository.
                async with workspace_path_scope(root):
                    result = await execute_operation(
                        "ide-http", key, "ide_operation",
                        {"path": path, "query": request.url.query, "payload": payload,
                         "content_type": request.headers.get("content-type")}, dispatch, actor=actor,
                    )
                response = JSONResponse(result["body"], status_code=result["status_code"])
                response.headers["Idempotency-Key"] = key
                return response
            except EffectUncertain as exc:
                return JSONResponse({"ok": False, "error": str(exc), "effect_uncertain": True},
                                    status_code=409, headers={"Idempotency-Key": key})

        return handler
