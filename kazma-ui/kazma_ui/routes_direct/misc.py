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


def register_misc_routes(self: Any) -> None:
    """Register the misc routes onto ``self.app``."""
    @self.app.delete("/api/mcp/servers/{server_name}")
    async def _delete_mcp_server(server_name: str):
        try:
            self.agent.remove_mcp_server(server_name)
            return {"status": "ok", "message": f"Server '{server_name}' deleted"}
        except Exception as exc:
            return {"status": "error", "message": safe_error(exc)}
    @self.app.get("/api/telemetry/typing")
    async def _typing_signal():
        return {"status": "processing", "timestamp": __import__("time").time()}
    @self.app.post("/api/telemetry/typing/stream_start")
    async def _stream_start(req: dict):
        worker_name = req.get("worker_name", "unknown")
        task_id = req.get("task_id", "")
        logger.info("[Stream] Typing started — worker=%s task=%s", worker_name, task_id)
        return {"status": "stream_started", "worker_name": worker_name, "task_id": task_id}
    import kazma_core.time_travel as _tt_mod
    @self.app.get("/api/session/history")
    async def _session_history(thread_id: str = "", limit: int = 20):
        store = _tt_mod.SnapshotStore()
        if thread_id:
            records = store.list_for_thread(thread_id)[:limit]
        else:
            records = []
        return {"sessions": [r.to_dict() for r in records]}
    @self.app.post("/api/session/replay")
    async def _session_replay(req: dict):
        thread_id = req.get("thread_id", "")
        iteration = req.get("iteration", 0)
        if not thread_id:
            from fastapi import HTTPException as _httpx

            raise _httpx(status_code=400, detail="thread_id required")
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
    @self.app.get("/chat", response_class=HTMLResponse)
    async def chat_redirect() -> RedirectResponse:
        return RedirectResponse("/", status_code=307)
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
    @self.app.get("/health")
    async def health_check() -> dict[str, Any]:
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
                            "status": "expired",
                            "thread_id": thread_id,
                            "content": "",
                            "error": "No pending approval for this thread (already resumed or expired).",
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

            # Apply scope grants *before* resume so subsequent danger tools in
            # later supervisor rounds skip interrupt entirely.
            actor = f"web:{(body.get('session_id') or '')[:12] or 'anon'}"
            grant_info: dict[str, Any] | None = None
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
                    # Client may also pass explicit tool name
                    explicit = body.get("tool") or body.get("grant_tool")
                    if explicit:
                        tools_to_grant.append(str(explicit))
                    tools_to_grant = list(dict.fromkeys(tools_to_grant))  # dedupe
                    grant_info = {"tools": []}
                    for tname in tools_to_grant:
                        st = grant_tool(thread_id, tname, actor=actor)
                        grant_info["tools"].append(st)
                except Exception:
                    logger.exception("[HITL] failed to apply tool grant")

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

            from collections.abc import AsyncGenerator

            from fastapi.responses import StreamingResponse
            from kazma_core.safety.hitl import (
                reset_current_thread_id,
                set_current_thread_id,
            )

            # ── Durable identity for the resumed reply ─────────────────
            # This endpoint used to stream the post-approval answer to the
            # browser and persist NOTHING: it passed no session_id, and the
            # Command path inside the streamer takes ainvoke (no pump, so no
            # done-callback). Two finished answers were lost that way on
            # 2026-08-28 (1,402 and 1,781 chars) — present on screen, absent
            # after refresh.
            #
            # Resolving the OPEN turn (rather than minting a new one) keeps
            # the pre-approval narration and the final answer in one bubble,
            # which is what the user asked one question to get.
            from kazma_ui.reply_sink import resolve_reply_turn
            from kazma_ui.session_manager import get_session_manager as _gsm_resume
            from kazma_ui.sse_chat import _sse_frame, _stream_langgraph_events

            from kazma_ui.turn_runtime import ensure_session_for_thread

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

            async def _approval_stream_generator() -> AsyncGenerator[str, None]:
                status_msg = (
                    "Executing approved tool..."
                    if approved
                    else "Continuing after denial..."
                )
                yield _sse_frame("status", {"content": status_msg})

                # Update checkpoint metadata with HITL resolution state
                # This ensures the thread won't show up in pending approvals after this
                _hitl_state = "approved" if approved else "denied"
                _resolution_time = datetime.now(UTC).isoformat()
                # Postgres jsonb_set needs JSON text ('"approved"'), not bare approved.
                import json as _json

                _hitl_state_json = _json.dumps(_hitl_state)
                _resolution_json = _json.dumps(_resolution_time)

                try:
                    cp = _resolve_hitl_checkpointer()
                    if cp is not None:
                        conn = getattr(cp, "conn", None)
                        if conn is not None:
                            try:
                                if hasattr(conn, "execute"):
                                    # SQLite: plain strings become JSON strings
                                    await conn.execute(
                                        "UPDATE checkpoints SET metadata = json_set(metadata, '$.hitl_state', ?) WHERE thread_id = ?",
                                        (_hitl_state, thread_id),
                                    )
                                    await conn.execute(
                                        "UPDATE checkpoints SET metadata = json_set(metadata, '$.hitl_resolved_at', ?) WHERE thread_id = ?",
                                        (_resolution_time, thread_id),
                                    )
                                    await conn.commit()
                                elif hasattr(conn, "connection"):
                                    # Postgres: jsonb_set requires a JSON document
                                    async with conn.connection() as pg_conn:
                                        async with pg_conn.cursor() as cur:
                                            await cur.execute(
                                                "UPDATE checkpoints SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{hitl_state}', %s::jsonb) WHERE thread_id = %s",
                                                (_hitl_state_json, thread_id),
                                            )
                                            await cur.execute(
                                                "UPDATE checkpoints SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{hitl_resolved_at}', %s::jsonb) WHERE thread_id = %s",
                                                (_resolution_json, thread_id),
                                            )
                                            await pg_conn.commit()
                            except Exception as e:
                                logger.warning(
                                    "[HITL] Failed to update checkpoint metadata for thread=%s: %s",
                                    thread_id,
                                    e,
                                )
                except Exception as e:
                    logger.debug("[HITL] Could not update checkpoint metadata: %s", e)

                _tid_token = set_current_thread_id(thread_id)
                try:
                    async for frame in _stream_langgraph_events(
                        graph_ref,
                        resume_cmd,
                        config=config,
                        thread_id=thread_id,
                        session_id=_resume_session_id,
                        reply_turn_id=_resume_turn,
                    ):
                        yield frame
                finally:
                    reset_current_thread_id(_tid_token)

            return StreamingResponse(
                _approval_stream_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        except Exception:
            logger.exception("[HITL] Failed to resume graph for thread=%s", thread_id)
            return _JSONResponse({"error": "Internal error"}, status_code=500)
    @self.app.get("/api/pending-approvals")
    async def list_pending_approvals() -> _JSONResponse:
        from kazma_ui.hitl_approval import _get_pending_approvals
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
    async def clear_pending_approvals_route() -> _JSONResponse:
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
