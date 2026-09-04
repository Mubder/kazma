"""Page shells, sub-router mounts, and endpoints without a larger group.

Extracted from the former ``kazma_ui/routes_direct.py`` god module
(3,862 lines) — audit O5. Handler bodies are unchanged; only their
module changed. Registration order within this group is preserved.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.responses import JSONResponse as _JSONResponse
from kazma_core.errors import safe_error

from kazma_ui.rate_limit import rate_limit

logger = logging.getLogger(__name__)

__all__ = ["register_misc_routes"]

_approve_locks: dict[str, asyncio.Lock] = {}
_approve_locks_activity: dict[str, float] = {}
_APPROVE_LOCKS_MAX: int = 512
_resume_inflight: set[str] = set()
_snapshot_store: Any = None


def _get_snapshot_store() -> Any:
    global _snapshot_store
    if _snapshot_store is None:
        import kazma_core.time_travel as _tt_mod

        _snapshot_store = _tt_mod.SnapshotStore()
    return _snapshot_store


def _approve_lock_for(thread_id: str) -> asyncio.Lock:
    import time
    now = time.monotonic()
    lock = _approve_locks.get(thread_id)
    if lock is None:
        if len(_approve_locks) >= _APPROVE_LOCKS_MAX:
            candidates = [
                tid for tid, lk in _approve_locks.items()
                if not lk.locked() and tid != thread_id
            ]
            if candidates:
                victim = min(
                    candidates,
                    key=lambda tid: _approve_locks_activity.get(tid, 0.0),
                )
                _approve_locks.pop(victim, None)
                _approve_locks_activity.pop(victim, None)
        lock = asyncio.Lock()
        _approve_locks[thread_id] = lock
    _approve_locks_activity[thread_id] = now
    return lock


def register_misc_routes(self: Any) -> None:
    """Register the misc routes onto ``self.app``."""
    @self.app.get("/api/telemetry/typing")
    async def _typing_signal():
        return {"status": "processing", "timestamp": __import__("time").time()}
    @self.app.post("/api/telemetry/typing/stream_start")
    async def _stream_start(req: dict):
        worker_name = req.get("worker_name", "unknown")
        task_id = req.get("task_id", "")
        logger.info("[Stream] Typing started — worker=%s task=%s", worker_name, task_id)
        return {"status": "stream_started", "worker_name": worker_name, "task_id": task_id}
    @self.app.get("/api/session/history")
    async def _session_history(thread_id: str = "", limit: int = 20):
        limit = max(1, min(limit, 500))
        if not thread_id:
            return {"sessions": []}
        store = _get_snapshot_store()
        records = await asyncio.to_thread(store.list_for_thread, thread_id)
        return {"sessions": [r.to_dict() for r in records[:limit]]}
    @self.app.post("/api/session/replay")
    async def _session_replay(req: dict):
        thread_id = req.get("thread_id", "")
        iteration = req.get("iteration", 0)
        if not thread_id:
            from fastapi import HTTPException as _httpx

            raise _httpx(status_code=400, detail="thread_id required")
        try:
            iteration = int(iteration)
            if iteration < 0:
                raise ValueError
        except (ValueError, TypeError):
            from fastapi import HTTPException as _httpx

            raise _httpx(status_code=400, detail="iteration must be a non-negative integer")

        import kazma_core.time_travel as _tt_mod
        engine = _tt_mod.ReplayEngine()
        return await engine.replay_from(thread_id, iteration)
    @self.app.get("/api/alerts/recent")
    async def _get_recent_alerts():
        from fastapi.encoders import jsonable_encoder
        from kazma_core.observability.alerts import AlertDispatcher

        # Serialize defensively: one non-JSON-safe alert field (NaN from a
        # metric, an exotic object) can kill the response mid-flight — the
        # browser sees the truncated keep-alive reply as HTTP 502 and the
        # alerts panel goes blank. A bad row must degrade to a placeholder,
        # not take down the endpoint.
        out: list[Any] = []
        for a in AlertDispatcher.get_recent_alerts():
            item = a.to_dict() if hasattr(a, "to_dict") else a
            try:
                out.append(jsonable_encoder(item))
            except Exception:
                out.append({"repr": repr(item), "error": "unserializable alert omitted"})
        return out
    @self.app.get("/packages")
    async def _packages_redirect() -> RedirectResponse:
        """Legacy /packages page → Settings Packages tab."""
        return RedirectResponse("/settings?tab=packages", status_code=307)
    # ── Auth bootstrap (remote clients — loopback auto-cookie is disabled) ──
    @self.app.get("/login", response_class=HTMLResponse)
    async def _login_page(request: Request) -> HTMLResponse:
        """Render the secret login form for non-loopback browsers."""
        return self.templates.TemplateResponse(
            request,
            "login.html",
            {},
        )
    @self.app.websocket("/ws/dashboard")
    async def ws_dashboard(websocket: WebSocket) -> None:
        from kazma_ui.auth import websocket_is_authenticated

        # Accept FIRST, then close 4003 — closing before accept makes the
        # server send an HTTP handshake rejection, so the client saw a 1006
        # close instead of 4003 and the session-expired redirect never fired
        # (the /ws/chat endpoint already does it in this order).
        await websocket.accept()
        if not websocket_is_authenticated(websocket):
            await websocket.close(code=4003, reason="Unauthorized")
            return
        from kazma_core.shutdown import is_shutting_down
        from kazma_core.tracing import get_trace_store

        store = get_trace_store()
        store.register_ws(websocket)
        try:
            import json

            await websocket.send_text(
                json.dumps(
                    {
                        "type": "connected",
                        "message": "Real-time dashboard feed active",
                    }
                )
            )
            while not is_shutting_down():
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                except TimeoutError:
                    continue
                except Exception as _e:
                    logger.debug("[WS] events receive error, closing: %s", _e)
                    break
        except Exception as exc:
            logger.debug("WS events handler stopped: %s", exc)
        finally:
            store.unregister_ws(websocket)
    @self.app.get("/", response_class=HTMLResponse)
    async def root(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "config": self.agent.config,
                "active_page": "dashboard",
                "cost_current": 0.0,
                "cost_max": 0.50,
                "cost_headroom": 0.50,
                "cost_color": "var(--success)",
                "breaker_status": "closed",
                "breaker_color": "var(--success)",
                "silence_info": "",
                "tracing_backend": "console",
                "traces": [],
                "metrics": {},
            },
        )
    @self.app.get("/workspace", response_class=HTMLResponse)
    async def workspace_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "workspace.html",
            {
                "config": self.agent.config,
                "active_page": "workspace",
            },
        )
    @self.app.get("/ide", response_class=HTMLResponse)
    async def ide_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "ide.html",
            {
                "config": self.agent.config,
                "active_page": "ide",
            },
        )
    @self.app.get("/replay", response_class=HTMLResponse)
    async def replay_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "replay.html",
            {
                "config": self.agent.config,
                "active_page": "replay",
            },
        )
    @self.app.get("/research", response_class=HTMLResponse)
    async def research_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "research.html",
            {
                "config": self.agent.config,
                "active_page": "research",
            },
        )
    @self.app.get("/knowledge", response_class=HTMLResponse)
    async def knowledge_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "knowledge_base.html",
            {
                "config": self.agent.config,
                "active_page": "knowledge",
            },
        )
    @self.app.get("/documents", response_class=HTMLResponse)
    async def documents_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "documents.html",
            {
                "config": self.agent.config,
                "active_page": "documents",
            },
        )
    @self.app.get("/x", response_class=HTMLResponse)
    async def x_studio_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "x_studio.html",
            {
                "config": self.agent.config,
                "active_page": "x_studio",
            },
        )
    @self.app.post("/api/gateway/refresh-adapters")
    async def refresh_gateway_adapters() -> dict[str, Any]:
        if self.gateway is None:
            return {"status": "error", "message": "Gateway not initialized"}
        logger.info("[Gateway] Refreshing adapters — stopping old adapters")

        for old_adapter in self.gateway.adapters:
            try:
                await old_adapter.stop()
            except Exception:
                logger.warning("[Gateway] Error stopping adapter %s during refresh", old_adapter.name, exc_info=True)

        self.gateway.adapters.clear()

        telegram_token = (
            self.config_store.get("connectors.telegram.token", "")
            or self.config.raw.get("connectors", {}).get("telegram", {}).get("token", "")
        )
        if not telegram_token:
            telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

        if telegram_token:
            from kazma_gateway.adapters.telegram import TelegramAdapter

            voice_cfg = self.config.raw.get("gateway", {}).get("voice", {})
            tg_adapter = TelegramAdapter(
                token=telegram_token,
                voice_enabled=voice_cfg.get("enabled", False),
                voice_provider=voice_cfg.get("stt_provider", "openai"),
                stt_api_key=None,
                tts_provider=voice_cfg.get("tts_provider", "edgetts"),
                tts_voice=voice_cfg.get("tts_voice", "default"),
                tts_output_format=voice_cfg.get("tts_output_format", "mp3"),
                stt_language=voice_cfg.get("stt_language", "auto"),
            )
            from kazma_gateway.allowlists import apply_adapter_allowlists

            apply_adapter_allowlists(tg_adapter, self.config_store)
            self.gateway.add_adapter(tg_adapter)
            logger.info("[Gateway] Telegram adapter re-registered via refresh")

        discord_token = self.config_store.get("connectors.discord.token", "") or os.environ.get("DISCORD_BOT_TOKEN", "")
        if discord_token:
            from kazma_gateway.adapters.discord import DiscordAdapter
            from kazma_gateway.allowlists import apply_adapter_allowlists

            discord_adapter = DiscordAdapter(token=discord_token)
            apply_adapter_allowlists(discord_adapter, self.config_store)
            self.gateway.add_adapter(discord_adapter)
            logger.info("[Gateway] Discord adapter re-registered via refresh")

        _cs_slack_bot2 = self.config_store.get("connectors.slack.token", "")
        _cs_slack_app2 = self.config_store.get("connectors.slack.app_token", "")
        slack_bot_token = (_cs_slack_bot2 if _cs_slack_bot2.startswith("xoxb-") else "") or os.environ.get("SLACK_BOT_TOKEN", "")
        slack_app_token = (_cs_slack_app2 if _cs_slack_app2.startswith("xapp-") else "") or os.environ.get("SLACK_APP_TOKEN", "")
        if slack_bot_token:
            from kazma_gateway.adapters.slack import SlackAdapter
            from kazma_gateway.allowlists import apply_adapter_allowlists, split_ids

            slack_adapter = SlackAdapter(
                bot_token=slack_bot_token,
                app_token=slack_app_token or None,
                allowed_users=split_ids(
                    self.config_store.get("connectors.slack.allowed_users", "")
                ) or None,
                allowed_teams=split_ids(
                    self.config_store.get("connectors.slack.allowed_teams", "")
                ) or None,
                allowed_channels=split_ids(
                    self.config_store.get("connectors.slack.allowed_channels", "")
                ) or None,
            )
            apply_adapter_allowlists(slack_adapter, self.config_store)
            self.gateway.add_adapter(slack_adapter)
            logger.info("[Gateway] Slack adapter re-registered via refresh")

        for new_adapter in self.gateway.adapters:
            try:
                await new_adapter.start(self.gateway.queue, self.gateway._shutdown)
                logger.info("[Gateway] Adapter %s started via refresh", new_adapter.name)
            except Exception:
                logger.warning("[Gateway] Failed to start adapter %s during refresh", new_adapter.name, exc_info=True)

        logger.info("[Gateway] Adapter refresh complete — %d adapter(s) running", len(self.gateway.adapters))
        return {
            "status": "ok",
            "adapters_count": len(self.gateway.adapters),
            "adapters": [a.name for a in self.gateway.adapters],
        }

    def _is_caller_admin(request: Request) -> bool:
        from kazma_ui.auth import get_kazma_secret, get_request_principal, is_authenticated

        secret = get_kazma_secret()
        if not secret:
            return True
        if not is_authenticated(request, secret):
            return False
        principal = get_request_principal(request) or {}
        if principal.get("source") == "secret":
            return True
        return principal.get("role") == "admin"

    @self.app.get("/health")
    async def health_check(request: Request) -> dict[str, Any]:
        if not _is_caller_admin(request):
            return {"status": "ok"}
        if self.gateway is None:
            return {
                "status": "ok",
                "gateway_started": False,
                "queue_depth": 0,
                "queue_maxsize": 100,
                "adapters_count": 0,
                "adapters_running": 0,
                "adapters": [],
                "init_errors": [
                    {"subsystem": e.get("subsystem", "?")}
                    for e in (self._init_errors or [])
                    if isinstance(e, dict)
                ],
            }
        adapters = [_a for _a in self.gateway.adapters] if hasattr(self.gateway, 'adapters') else []
        queue = getattr(self.gateway, 'queue', None)
        return {
            "status": "ok",
            "gateway_started": getattr(self.gateway, '_started', False),
            "queue_depth": queue.qsize() if queue else 0,
            "queue_maxsize": queue.maxsize if queue and hasattr(queue, 'maxsize') else 100,
            "adapters_count": len(adapters),
            "adapters_running": sum(1 for a in adapters if getattr(a, '_running', False)),
            "adapters": [
                {
                    "name": getattr(a, 'name', '?'),
                    "platform": getattr(a, 'platform', getattr(a, 'name', '?')),
                    "running": getattr(a, '_running', False),
                }
                for a in adapters
            ],
            "init_errors": [
                {"subsystem": e.get("subsystem", "?")}
                for e in (self._init_errors or [])
                if isinstance(e, dict)
            ],
        }
    def _resolve_hitl_graph() -> Any:
        return self._hitl_state.get("graph") or self._graph_holder.get("graph")
    def _resolve_hitl_checkpointer() -> Any:
        return self._hitl_state.get("checkpointer")
    @self.app.post(
        "/api/approve/{thread_id}",
        dependencies=[Depends(rate_limit("approve", 20))],
    )
    async def approve_tool(thread_id: str, request: Request) -> _JSONResponse:
        # Use shared auth: KAZMA_SECRET *or* Account API token.
        from kazma_ui.auth import get_kazma_secret, is_authenticated

        _secret = get_kazma_secret()
        if _secret and not is_authenticated(request, _secret):
            return _JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            logger.debug("[HITL] Malformed or missing JSON body in approval request", exc_info=True)
            return _JSONResponse({"error": "Invalid JSON"}, status_code=400)

        action = body.get("action", "deny")
        approved = action == "approve"
        # scope: once (default) | tool (session grant for this tool) | yolo
        scope = str(body.get("scope") or "once").strip().lower()
        if scope not in ("once", "tool", "yolo", "allow_tool", "session"):
            scope = "once"
        if scope == "allow_tool":
            scope = "tool"
        if scope == "session":
            scope = "yolo"

        graph_ref = _resolve_hitl_graph()
        if graph_ref is None:
            return _JSONResponse({"error": "Graph not available"}, status_code=503)

        # A checkpoint has no authenticated caller identity of its own. Bind
        # every browser approval to a session belonging to this request's tenant
        # before inspecting or resuming the graph state.
        try:
            from kazma_ui.session_manager import get_session_manager

            if get_session_manager().get_by_thread_id(thread_id) is None:
                logger.warning(
                    "[HITL] Approval denied for thread not owned by current tenant: %s",
                    thread_id,
                )
                return _JSONResponse(
                    {"error": "Approval request not found"},
                    status_code=404,
                )

            try:
                from kazma_core.mcp.spec_client import resolve_sampling_hitl

                if resolve_sampling_hitl(thread_id, approved):
                    return _JSONResponse(
                        {
                            "status": "ok",
                            "approved": approved,
                            "sampling": True,
                        }
                    )
            except Exception:
                logger.debug("[HITL] sampling sidecar miss", exc_info=True)

            if self.session_store is not None:
                ctx = None
                try:
                    ctx = await self.session_store.get(thread_id)
                except Exception as _e:
                    logger.debug("[HITL] Failed to fetch session context for ownership check: %s", _e)
                if ctx and isinstance(ctx, dict):
                    owner = (
                        ctx.get("sender_id")
                        or ctx.get("owner")
                        or ctx.get("session_id")
                        or ctx.get("user_id")
                    )
                    # Only enforce when this is clearly a non-web gateway owner
                    # (telegram:/discord:/slack: prefixes or numeric platform ids).
                    owner_s = str(owner or "")
                    is_gateway_owner = bool(
                        owner_s
                        and (
                            owner_s.startswith("telegram:")
                            or owner_s.startswith("discord:")
                            or owner_s.startswith("slack:")
                            or ":" in owner_s
                        )
                    )
                    if is_gateway_owner:
                        # Gateway sessions are not implicitly transferable to a
                        # browser caller. A matching tenant-owned UI session is
                        # required above; platform approval remains on the
                        # platform's native command/callback path.
                        return _JSONResponse(
                            {"error": "Gateway-owned approvals must be completed on their platform"},
                            status_code=403,
                        )
        except Exception as _e:
            # Fail-closed (audit M7): never skip ownership on store errors
            logger.warning("[HITL] Ownership check failed — denying: %s", _e)
            return _JSONResponse(
                {"error": "Ownership check failed — approval denied"},
                status_code=403,
            )

        try:

            # Prefer the live checkpointed graph (same instance as SSE).
            graph_ref = _resolve_hitl_graph() or self._graph_holder.get("graph")
            if graph_ref is None:
                return _JSONResponse({"error": "Graph not available"}, status_code=503)

            config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

            # Verify this thread is actually paused before resume — avoids a
            # silent no-op when the wrong graph/checkpointer is wired.
            # Also snapshot messages + pending tools for scope grants / delta text.
            pre = None
            pre_msg_count = 0
            pending_tool_name = ""
            pending_tools: list[Any] = []
            try:
                pre = await graph_ref.aget_state(config)
                has_interrupt = False
                if pre and getattr(pre, "tasks", None):
                    for task in pre.tasks or []:
                        if getattr(task, "interrupts", None):
                            has_interrupt = True
                            break
                if pre and getattr(pre, "next", None) and not has_interrupt:
                    # Pending next but no interrupt payload — still try resume.
                    has_interrupt = True
                if not has_interrupt and not (pre and getattr(pre, "next", None)):
                    logger.warning(
                        "[HITL] No pending interrupt for thread=%s — approve is a no-op",
                        thread_id,
                    )
                    return _JSONResponse(
                        {
                            "ok": False,
                            "status": "expired",
                            "thread_id": thread_id,
                            "content": "",
                            "error": "No pending approval for this thread (already resumed or expired).",
                            "reason": "not_pending",
                            "running": False,
                            "hitl_state": "settled",
                        },
                        status_code=409,
                    )
                if pre is not None:
                    vals = getattr(pre, "values", None) or {}
                    if isinstance(vals, dict):
                        pre_msgs = vals.get("messages") or []
                        pre_msg_count = len(pre_msgs) if isinstance(pre_msgs, list) else 0
                    for task in getattr(pre, "tasks", None) or []:
                        for intr in getattr(task, "interrupts", None) or []:
                            payload = getattr(intr, "value", None)
                            if isinstance(payload, dict) and payload.get("type") == "hitl_approval":
                                pending_tool_name = str(payload.get("tool") or "")
                                pending_tools = list(payload.get("tools") or [])
                                break
            except Exception:
                logger.debug("[HITL] pre-resume state probe failed", exc_info=True)

            actor = f"web:{(body.get('session_id') or '')[:12] or 'anon'}"
            grant_info: dict[str, Any] | None = None

            # Phase 3/§4.3: build the resume Command via the single chokepoint
            # (build_resume_command). Semantic interrupts need {tcid: option_id};
            # security needs {approved: bool}. Routed through one helper so a
            # transport cannot drift again (cf. WS bug, incident 2026-08-12).
            from kazma_core.safety.commitment.resume import (
                build_resume_command,
                read_pending_interrupt,
            )

            _intr_payload = await read_pending_interrupt(graph_ref, config, snapshot=pre)
            resume_cmd = build_resume_command(
                _intr_payload,
                approved=approved,
                choices=body.get("choices") if isinstance(body.get("choices"), dict) else None,
                scope=scope,
                reason=body.get("reason", ""),
                approved_ids=body.get("approved_ids") if isinstance(body.get("approved_ids"), list) else None,
            )
            if resume_cmd is None:
                # Stale card (race with the earlier 409 guard): fall back to a
                # security resume so the stream still completes deterministically.
                resume_cmd = build_resume_command(
                    {"type": "hitl_approval", "kind": "security"},
                    approved=approved, scope=scope, reason=body.get("reason", ""),
                )

            # JSON command, not a second graph SSE. The live tail is the
            # journal attach the chat client already holds (or re-opens).
            from kazma_ui.active_turns import is_turn_running, register_turn
            from kazma_ui.reply_sink import resolve_reply_turn
            from kazma_ui.session_manager import get_session_manager as _gsm_resume
            from kazma_ui.sse_chat._streaming import (
                _drive_graph_to_journal,
                mark_thread_unpaused,
                stamp_hitl_part_state,
            )
            from kazma_ui.turn_runtime import ensure_session_for_thread

            async with _approve_lock_for(thread_id):
                if is_turn_running(thread_id) or thread_id in _resume_inflight:
                    _claimed_turn = ""
                    try:
                        _claimed_turn = resolve_reply_turn(thread_id, "") or ""
                    except Exception:
                        _claimed_turn = ""
                    _claimed_iid = str(body.get("interrupt_id") or "")
                    try:
                        from kazma_ui.hitl_status import persisted_hitl_for_thread

                        _part = persisted_hitl_for_thread(thread_id)
                        if isinstance(_part, dict):
                            _claimed_iid = str(
                                _part.get("interrupt_id")
                                or (_part.get("payload") or {}).get("interrupt_id")
                                or _claimed_iid
                            )
                    except Exception:
                        pass
                    return _JSONResponse(
                        {
                            "ok": False,
                            "error": "This approval is no longer pending.",
                            "reason": "not_pending",
                            "running": True,
                            "turn_id": _claimed_turn,
                            "interrupt_id": _claimed_iid,
                            "hitl_state": "inflight",
                        },
                        status_code=409,
                    )

                # Grants only after the claim. Extra clicks on a stale card
                # used to write hitl_grant then 409 (cleanup 2026-09-01).
                if approved and scope == "yolo":
                    try:
                        from kazma_core.safety.yolo import try_enable_yolo

                        grant_info = try_enable_yolo(thread_id, actor=actor)
                    except Exception:
                        logger.exception("[HITL] failed to enable YOLO scope")
                        scope = "once"
                elif approved and scope == "tool":
                    try:
                        from kazma_core.safety.hitl_grants import grant_tool

                        tools_to_grant: list[str] = []
                        if pending_tools:
                            for t in pending_tools:
                                if isinstance(t, dict) and t.get("name"):
                                    tools_to_grant.append(str(t["name"]))
                        elif pending_tool_name and " tools" not in pending_tool_name:
                            tools_to_grant.append(pending_tool_name)
                        explicit = body.get("tool") or body.get("grant_tool")
                        if explicit:
                            tools_to_grant.append(str(explicit))
                        tools_to_grant = list(dict.fromkeys(tools_to_grant))
                        grant_info = {"tools": []}
                        for tname in tools_to_grant:
                            st = grant_tool(thread_id, tname, actor=actor)
                            grant_info["tools"].append(st)
                    except Exception:
                        logger.exception("[HITL] failed to apply tool grant")

                _resume_session_id = ""
                try:
                    _owner = _gsm_resume().get_by_thread_id(thread_id)
                    if _owner is not None:
                        _resume_session_id = str(_owner.session_id or "")
                except Exception:
                    logger.debug("[HITL] could not resolve session for resume", exc_info=True)
                if not _resume_session_id:
                    _resume_session_id = ensure_session_for_thread(thread_id)
                _resume_turn = resolve_reply_turn(thread_id, _resume_session_id)
                if not _resume_session_id:
                    logger.warning(
                        "[HITL] Resume mint failed for thread=%s — persist_reply "
                        "will refuse the write",
                        thread_id,
                    )

                _stamp_payload: dict[str, Any] = {}
                if isinstance(_intr_payload, dict):
                    _stamp_payload = dict(_intr_payload)
                _stamp_payload["thread_id"] = thread_id
                if pending_tool_name:
                    _stamp_payload["tool"] = pending_tool_name
                _stored_iid = ""
                _stored_state = ""
                try:
                    from kazma_ui.hitl_status import persisted_hitl_for_thread

                    _part = persisted_hitl_for_thread(thread_id)
                    if isinstance(_part, dict):
                        _stored_state = str(_part.get("state") or "")
                        _stored_iid = str(
                            _part.get("interrupt_id")
                            or (_part.get("payload") or {}).get("interrupt_id")
                            or ""
                        )
                except Exception:
                    _stored_iid = ""
                _req_iid = str(body.get("interrupt_id") or "").strip()
                if _stored_iid:
                    _stamp_payload["interrupt_id"] = _stored_iid
                elif _req_iid:
                    _stamp_payload["interrupt_id"] = _req_iid
                else:
                    try:
                        from kazma_ui.turn_document import assign_interrupt_id

                        assign_interrupt_id(
                            _stamp_payload, thread_id=thread_id
                        )
                    except Exception:
                        logger.debug(
                            "[HITL] interrupt_id stamp skipped", exc_info=True
                        )
                _live_iid = str(_stamp_payload.get("interrupt_id") or "").strip()
                if (
                    _req_iid
                    and _stored_iid
                    and _req_iid != _stored_iid
                    and _stored_state.lower()
                    in ("approved", "denied", "inflight", "settled", "done")
                ):
                    return _JSONResponse(
                        {
                            "ok": False,
                            "error": "This approval is no longer pending.",
                            "reason": "not_pending",
                            "running": False,
                            "interrupt_id": _stored_iid,
                            "hitl_state": _stored_state or "settled",
                        },
                        status_code=409,
                    )
                stamp_hitl_part_state(
                    _resume_session_id,
                    _resume_turn,
                    state="approved" if approved else "denied",
                    thread_id=thread_id,
                    tool=pending_tool_name,
                    payload=_stamp_payload,
                    interrupt_id=str(_stamp_payload.get("interrupt_id") or ""),
                )
                try:
                    from kazma_ui.delivery import get_turn_broker

                    await get_turn_broker().emit(
                        thread_id,
                        {
                            "type": "hitl",
                            "data": {
                                "state": "approved" if approved else "denied",
                                "interrupt_id": str(
                                    _stamp_payload.get("interrupt_id") or ""
                                ),
                                "tool": pending_tool_name,
                                "thread_id": thread_id,
                                "turn_id": _resume_turn,
                            },
                        },
                    )
                except Exception:
                    logger.debug("[HITL] hitl journal frame skipped", exc_info=True)
                # Gate registry: record the decision (CAS) and the resume
                # start. Best-effort — a registry failure never blocks the
                # approve; drift is recorded by the parity counter.
                try:
                    from kazma_ui.hitl_gate_bridge import (
                        gate_claimed as _gate_claimed,
                        gate_resuming as _gate_resuming,
                    )

                    await _gate_claimed(
                        thread_id,
                        _live_iid,
                        "approve" if approved else "deny",
                        actor,
                        tool=pending_tool_name,
                        payload=_stamp_payload,
                    )
                    await _gate_resuming(_live_iid)
                except Exception:
                    logger.debug("[HITL] gate claim skipped", exc_info=True)
                _resume_inflight.add(thread_id)
                _resume_task = asyncio.create_task(
                    _drive_graph_to_journal(
                        graph_ref,
                        resume_cmd,
                        config,
                        thread_id=thread_id,
                        session_id=_resume_session_id,
                        reply_turn_id=_resume_turn,
                    )
                )
                # Register BEFORE unpausing. Unpause-then-register left a
                # window where attach saw not-running and not-paused and
                # closed the live tail (the 70s-tool-after-Approve class).
                register_turn(thread_id, _resume_task)
                mark_thread_unpaused(thread_id)

                def _clear_inflight(t: asyncio.Task, tid: str = thread_id) -> None:
                    _resume_inflight.discard(tid)

                _resume_task.add_done_callback(_clear_inflight)

                return _JSONResponse(
                    {
                        "ok": True,
                        "approved": approved,
                        "thread_id": thread_id,
                        "turn_id": _resume_turn,
                        "running": True,
                        "interrupt_id": str(
                            _stamp_payload.get("interrupt_id") or ""
                        ),
                        "hitl_state": "approved" if approved else "denied",
                    }
                )
        except Exception:
            logger.exception("[HITL] Failed to resume graph for thread=%s", thread_id)
            return _JSONResponse({"error": "Internal error"}, status_code=500)
    @self.app.get("/api/pending-approvals")
    async def list_pending_approvals() -> _JSONResponse:
        from kazma_ui.session_manager import get_session_manager

        graph = _resolve_hitl_graph()
        checkpointer = _resolve_hitl_checkpointer()
        sampling: list[Any] = []
        try:
            from kazma_core.mcp.spec_client import list_sampling_pending

            sampling = list_sampling_pending()
        except Exception:
            sampling = []
        if graph is None or checkpointer is None:
            return _JSONResponse(
                {
                    "pending": sampling,
                    "count": len(sampling),
                    "error": None if sampling else "Graph/checkpointer not yet initialized",
                },
                status_code=200 if sampling else 503,
            )
        try:
            from kazma_ui.hitl_gate_bridge import pending_items_from_registry

            registry_items = await pending_items_from_registry()
            if registry_items is not None:
                pending = [
                    item
                    for item in registry_items
                    if get_session_manager().get_by_thread_id(
                        str(item.get("thread_id") or "")
                    )
                    is not None
                ]
            else:
                # Kill-switch / registry outage: checkpoint scan is the
                # thin execution fallback (live interrupt ⇒ pending card).
                from kazma_ui.hitl_approval import _get_pending_approvals

                pending = [
                    item
                    for item in await _get_pending_approvals(graph, checkpointer)
                    if get_session_manager().get_by_thread_id(str(item["thread_id"]))
                    is not None
                ]
            pending = list(pending) + list(sampling)
            return _JSONResponse({"pending": pending, "count": len(pending)})
        except Exception:
            logger.exception("[HITL] Failed to list pending approvals")
            return _JSONResponse({"pending": [], "count": 0, "error": "Internal error"}, status_code=500)
    @self.app.post("/api/pending-approvals/clear")
    @self.app.delete("/api/pending-approvals")
    async def clear_pending_approvals_route(request: Request) -> _JSONResponse:
        from kazma_ui.auth import get_kazma_secret, is_authenticated

        _secret = get_kazma_secret()
        if _secret and not is_authenticated(request, _secret):
            return _JSONResponse({"error": "Unauthorized"}, status_code=401)
        if not _is_caller_admin(request):
            return _JSONResponse({"error": "Admin role required"}, status_code=403)

        from kazma_ui.hitl_approval import _get_pending_approvals
        from kazma_ui.session_manager import get_session_manager

        graph = _resolve_hitl_graph()
        checkpointer = _resolve_hitl_checkpointer()
        if checkpointer is None:
            return _JSONResponse({"error": "Checkpointer not available"}, status_code=503)
        try:
            pending = await _get_pending_approvals(graph, checkpointer)
            cleared = 0
            for item in pending:
                thread_id = str(item["thread_id"])
                if get_session_manager().get_by_thread_id(thread_id) is None:
                    continue
                if hasattr(checkpointer, "adelete_thread"):
                    await checkpointer.adelete_thread(thread_id)
                elif hasattr(checkpointer, "_saver") and hasattr(
                    checkpointer._saver, "adelete_thread"
                ):
                    await checkpointer._saver.adelete_thread(thread_id)
                else:
                    continue
                cleared += 1
                # Keep the gate registry honest: the pause is gone.
                try:
                    from kazma_core.safety.hitl_gates import (
                        live_gates_async,
                        settle_gate_async,
                    )

                    for row in await live_gates_async(thread_id):
                        await settle_gate_async(row.gate_id, "cleared")
                except Exception:
                    logger.debug("[HITL] gate clear skipped", exc_info=True)
            return _JSONResponse({"status": "ok", "cleared": cleared})
        except Exception:
            logger.exception("[HITL] Failed to clear pending approvals")
            return _JSONResponse({"error": "Internal error"}, status_code=500)
    @self.app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        return {
            "status": "degraded" if self._init_errors else "ok",
            "init_errors": [
                {"subsystem": e.get("subsystem", "?")}
                for e in (self._init_errors or [])
                if isinstance(e, dict)
            ],
        }
    # ── Workspace selection + file-tree scanner ────────────────────────
    try:
        from kazma_gateway.routers.workspace import create_workspace_select_router

        self.app.include_router(create_workspace_select_router())
        logger.info("[routes_direct] Workspace select/tree router mounted at /api/workspace/select, /api/workspace/tree")
    except Exception as _exc:
        logger.warning("[routes_direct] Workspace select/tree router failed to mount: %s", _exc)
    # ── Workspaces Multi-Project Router ────────────────────────────────
    try:
        from kazma_gateway.routers.workspaces import create_workspaces_router

        self.app.include_router(create_workspaces_router())
        logger.info("[routes_direct] Workspaces router mounted at /api/workspaces")
    except Exception as _exc:
        logger.warning("[routes_direct] Workspaces router failed to mount: %s", _exc)
    # ── Live Git status ────────────────────────────────────────────────
    try:
        from kazma_gateway.routers.git import create_git_router

        self.app.include_router(create_git_router())
        logger.info("[routes_direct] Git router mounted at /api/git/status")
    except Exception as _exc:
        logger.warning("[routes_direct] Git router failed to mount: %s", _exc)
    # ── Live GitHub integration ────────────────────────────────────────
    try:
        from kazma_gateway.routers.github import create_github_router

        self.app.include_router(create_github_router())
        logger.info("[routes_direct] GitHub router mounted at /api/github")
    except Exception as _exc:
        logger.warning("[routes_direct] GitHub router failed to mount: %s", _exc)
    # ── Bookmarks CRUD ─────────────────────────────────────────────────
    try:
        from kazma_gateway.routers.bookmarks import create_bookmarks_router

        self.app.include_router(create_bookmarks_router())
        logger.info("[routes_direct] Bookmarks router mounted at /api/bookmarks")
    except Exception as _exc:
        logger.warning("[routes_direct] Bookmarks router failed to mount: %s", _exc)
    # ── Visual Pipeline Sandbox ────────────────────────────────────────
    try:
        from kazma_gateway.routers.pipeline import create_pipeline_router

        self.app.include_router(create_pipeline_router())
        logger.info("[routes_direct] Visual pipeline router mounted at /api/pipelines")
    except Exception as _exc:
        logger.warning("[routes_direct] Visual pipeline router failed to mount: %s", _exc)
    # Commitment Layer — soul-delta confirm queue (Phase 7)
    try:
        from kazma_ui.commitment_api import create_commitment_router

        self.app.include_router(create_commitment_router())
        logger.info("[routes_direct] Commitment soul-confirm router mounted at /api/commitment")
    except Exception as _exc:
        logger.warning("[routes_direct] Commitment router failed to mount: %s", _exc)
    # Chaos Testing UI (extracted to routes_chaos; env-gated)
    try:
        from kazma_ui.routes_chaos import register_chaos_routes

        register_chaos_routes(self.app)
    except Exception as _exc:
        logger.warning("[routes_direct] Chaos testing endpoints failed to mount: %s", _exc)
