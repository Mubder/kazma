"""Page shells, sub-router mounts, and endpoints without a larger group.

Extracted from the former ``kazma_ui/routes_direct.py`` god module
(3,862 lines) — audit O5. Handler bodies are unchanged; only their
module changed. Registration order within this group is preserved.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import Depends, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.responses import JSONResponse as _JSONResponse

from kazma_ui.rate_limit import rate_limit
from kazma_ui.hitl_decision import GateDecisionUnavailable, GateNotYours
from kazma_core.safety.hitl_gates import TransitionConflict
from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json

logger = logging.getLogger(__name__)

__all__ = ["register_misc_routes"]

_resume_inflight: set[str] = set()


def _body_interrupt_id(body: dict[str, Any] | None) -> str:
    """Prefer interrupt_id, then gate_id (dashboard/TUI send both)."""
    if not isinstance(body, dict):
        return ""
    return str(body.get("interrupt_id") or body.get("gate_id") or "").strip()


def _attach_hitl_view(
    payload: dict[str, Any],
    thread_id: str,
    interrupt_id: str = "",
    *,
    tool: str = "",
    state_hint: str = "",
) -> dict[str, Any]:
    """Stamp ``view`` + ``gate_id`` on an approve 200/409 body. Never raises.

    It reads the gate registry (SQLite): the route calls it through
    ``asyncio.to_thread`` (audit 2026-10-26 AUD-111).
    """
    out = dict(payload)
    iid = str(
        interrupt_id
        or out.get("interrupt_id")
        or out.get("gate_id")
        or ""
    ).strip()
    hint = str(state_hint or out.get("hitl_state") or "").strip()
    try:
        from kazma_ui.gate_view import view_for_interrupt

        view = view_for_interrupt(
            thread_id, iid, tool=tool, state_hint=hint
        )
        if view:
            out["view"] = view
            out["gate_id"] = view.get("gate_id") or iid
    except Exception:
        logger.debug("[HITL] approve view stamp skipped", exc_info=True)
    if iid and not out.get("interrupt_id"):
        out["interrupt_id"] = iid
    return out


def _not_yours_response(thread_id: str, code: str, card: dict[str, Any] | None) -> _JSONResponse:
    """The answer to someone who may not decide a card (Spaces S2-05): 403,
    nothing claimed, the card still pending for whoever it asks. Names who
    it waits for when the card says (its ``answered_by_names``)."""
    unavailable = code == "identity_unavailable"
    names = [str(n) for n in ((card or {}).get("answered_by_names") or []) if n]
    body: dict[str, Any] = {
        "ok": False,
        "code": code,
        "reason": code,
        "thread_id": thread_id,
        "hitl_state": "pending",
        "running": False,
        "error": ("Who answers this approval could not be read; it stays pending."
                  if unavailable else "This approval is not yours to answer; it waits for "
                  + (", ".join(names) if names else "the person it asks") + "."),
    }
    if names:
        body["answered_by_names"] = names
    return _JSONResponse(body, status_code=503 if unavailable else 403,
                         headers={"Retry-After": "5"} if unavailable else None)


def _current_tenant() -> str:
    """The space this request acts in (Spaces S1-02): ``""`` -- which no row
    holds -- when none is bound once Spaces is enforced, never the owner's."""
    from kazma_core.spaces.scope import NoSpaceBound, require_space_id

    try:
        return require_space_id()
    except NoSpaceBound:
        return ""


def _sampling_shown_here() -> bool:
    """Whether the MCP servers' sampling requests are shown to this request:
    always while Spaces is not enforced; once it is, where Development is
    (``spaces.tools.host_allowance``). Blocking (the identity store)."""
    from kazma_core.spaces.scope import spaces_enforced

    if not spaces_enforced():
        return True
    from kazma_core.spaces.tools import host_allowance

    return host_allowance().development


def _is_own_bridge_gate(item: dict) -> bool:
    """True for a bus-bridge gate belonging to this request's tenant.

    Every other pending card is admitted by finding the chat session that owns
    its thread. A gate queued by `kazma mcp` has no session -- it was written
    by a different process with no conversation behind it -- so it would be
    filtered out of the dashboard and the operator would never see the card
    the MCP client is blocked on.

    Tenancy is the only claim such a row carries, so it is the only thing
    checked, and it is checked rather than skipped: on a multi-tenant install
    an unmatched row stays invisible.
    """
    if str(item.get("mechanism") or "") != "bus_bridge":
        return False
    from kazma_core.spaces.scope import space_of_record

    return space_of_record(item.get("tenant_id")) == _current_tenant()


async def _request_actor(request: Any) -> str:
    """Who decided, from the SERVER-side session -- never the request body.

    Body text is attacker-chosen and made the gate-registry decision log
    spoofable (audit M-W3). Falls back to a short hash of the presented
    credential so the trail is still attributable when no cookie session
    exists. Shared by the graph path and the bus-bridge branch (AUD-100: the
    bridge recorded ``body["actor"]`` as the human who decided).
    """
    try:
        from kazma_core.security.web_sessions import (
            SESSION_COOKIE as _SC,
            get_session_payload,
        )

        actor_sid = request.cookies.get(_SC) or ""
        payload = await asyncio.to_thread(get_session_payload, actor_sid) or {}
        if not actor_sid:
            return "web:token"
        from kazma_ui.web_actor import actor_for_session

        return actor_for_session(actor_sid, payload, default_role="user")
    except Exception:
        logger.debug("[HITL] approval actor lookup failed", exc_info=True)
        return "web:anon"


async def _decide_bridge_gate(
    thread_id: str, body: dict, approved: bool, actor: str = "web:anon"
):
    """Claim a pending bus-bridge gate for *thread_id*, or return ``None``.

    ``None`` means "not a bridge gate" and the caller carries on down the
    normal graph path untouched. This never guesses: it only acts on a row
    that is pending, has ``mechanism == "bus_bridge"``, and matches both the
    thread and this request's tenant.

    Scope is deliberately ignored. "Approve for the session" and YOLO are
    properties of a chat thread, and the caller here is a separate process
    with no thread and no way to have its later calls re-checked against a
    grant. One decision, one tool call.
    """
    from kazma_core.safety.hitl_gates import (
        GateRow,
        claim_gate_async,
        gate_registry_enabled,
        pending_gates_async,
        settle_gate_async,
    )
    if not gate_registry_enabled():
        return None
    try:
        rows: list[GateRow] = await pending_gates_async()
    except Exception:
        logger.debug("[HITL] bridge gate lookup failed", exc_info=True)
        return None

    from kazma_core.spaces.scope import space_of_record

    tenant = _current_tenant()
    match = next(
        (
            r
            for r in rows
            if str(getattr(r, "mechanism", "")) == "bus_bridge"
            and str(getattr(r, "thread_id", "")) == thread_id
            and space_of_record(getattr(r, "tenant_id", "")) == tenant
        ),
        None,
    )
    if match is None:
        return None
    # Whose card it is decides who answers it (Spaces S2-05): a bridge gate
    # is the machine's work for the person bound when it was queued.
    from kazma_ui.hitl_answerers import refusal_for_card

    refusal, _whose = await asyncio.to_thread(
        refusal_for_card, thread_id, match.gate_id, match.payload(), row=match)
    if refusal:
        return _not_yours_response(thread_id, refusal, match.payload())

    decision = "approve" if approved else "deny"
    # AUD-100: the actor is the caller's server-side session (``actor``,
    # from ``_request_actor``); ``body["actor"]`` was recorded verbatim, so
    # any client could name who approved.
    try:
        await claim_gate_async(match.gate_id, decision, actor)
        await settle_gate_async(match.gate_id, decision)
    except Exception:
        logger.warning("[HITL] bridge gate claim failed: %s", match.gate_id, exc_info=True)
        return _JSONResponse({"error": "Approval no longer pending"}, status_code=409)

    logger.info(
        "[HITL] bus-bridge gate %s %s by %s (tool=%s)",
        match.gate_id, decision, actor, match.tool,
    )
    return _JSONResponse(
        {
            "status": "ok",
            "approved": approved,
            "bridge": True,
            "tool": match.tool,
            "gate_id": match.gate_id,
        }
    )


#: Gate-identity check, shared with the WebSocket approve path.
#:
#: This function used to have its body here. It moved to
#: ``kazma_ui.hitl_gate_bridge`` when the WS ``approve_tool`` handler was
#: found resuming the live interrupt without one — the same hole this route
#: closed, one transport over, because the check lived in the caller instead
#: of in the bridge both callers share. The alias is kept so the (many)
#: existing references in this module keep reading naturally.
from kazma_ui.hitl_gate_bridge import gate_is_pending as _gate_is_pending
from kazma_ui.hitl_gate_bridge import gate_not_pending as _gate_not_pending

#: How long an Approve waits for the segment that showed its gate to finish.
_SEGMENT_TAIL_WAIT_S = 10.0


async def _await_segment_tail(thread_id: str, timeout: float = _SEGMENT_TAIL_WAIT_S) -> bool:
    """Wait for the turn segment that showed this gate to end; True when it has.

    A segment journals its approval card first and then finishes: the turn's
    totals, the reply's save. Until its task ends the thread counts as
    running, and an Approve sent the moment the card appeared was refused as
    "no longer pending" over a gate that WAS pending: the button did nothing
    and the human had to click again (the lifecycle job caught it on
    2026-10-01, once the route's own checks got faster than that tail).
    Called only after the registry has the answered gate on record as
    pending, so the graph is parked at it and nothing else can be running.
    The resume flag clears in the task's done callback; poll briefly for it.
    """
    from kazma_ui.active_turns import get_active_turn, is_turn_running

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while is_turn_running(thread_id) or thread_id in _resume_inflight:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        task = get_active_turn(thread_id)
        if task is not None and not task.done():
            await asyncio.wait({task}, timeout=remaining)
        else:
            await asyncio.sleep(min(0.05, remaining))
    return True


def _approve_lock_for(thread_id: str) -> asyncio.Lock:
    """The thread's checkpoint-mutation lock (``kazma_ui.thread_locks``).

    It was a lock of the approve route's own, so a restore, a fork or a
    gateway command on the same thread did not wait for it (Time Travel
    audit TT-04). One lock per thread now, shared by every rewriter.
    """
    from kazma_ui.thread_locks import thread_mutation

    return thread_mutation(thread_id)


def _live_hitl_interrupt(snapshot: Any) -> tuple[str, dict[str, Any] | None, int]:
    """``(LangGraph interrupt id, payload, interrupts)`` of the approval the
    checkpoint is paused at; ``("", None, n)`` when it is paused at none."""
    found_id, found = "", None
    count = 0
    for task in getattr(snapshot, "tasks", None) or ():
        for intr in getattr(task, "interrupts", None) or ():
            count += 1
            value = getattr(intr, "value", None)
            if isinstance(value, (list, tuple)) and value:
                value = value[0]
            if found is None and isinstance(value, dict) and value.get("type") == "hitl_approval":
                found_id, found = str(getattr(intr, "id", "") or ""), value
    return found_id, found, count


def _apply_scope_grants(
    thread_id: str, approved: bool, scope: str, actor: str,
    pending_tools: list[Any], pending_tool_name: str, requested_tool: str,
    person: str | None = None,
) -> str:
    """Apply longer-lived grants only after a durable claim; run off the loop.

    The card's own calls then run under the grant just made: they were
    asked, and are not "ran without asking" (``safety.unasked``). Once Spaces
    is enforced the grant is *person*'s -- the card's asker, whose cards it
    skips, whoever answered (S2-06) -- and nobody else's in the chat.
    """
    from kazma_core.safety.unasked import card_answered

    if approved and scope == "yolo":
        try:
            from kazma_core.safety.yolo import try_enable_yolo

            result = try_enable_yolo(thread_id, actor=actor, person=person)
            if result.get("downgraded"):
                return "once"
        except Exception:
            logger.exception("[HITL] failed to enable YOLO scope")
            return "once"
        card_answered(thread_id, pending_tools, pending_tool_name)
    elif approved and scope == "tool":
        try:
            from kazma_core.safety.hitl_grants import grant_tool, tools_to_grant

            for name in tools_to_grant(pending_tools, pending_tool_name, requested_tool):
                grant_tool(thread_id, name, actor=actor, person=person)
        except Exception:
            logger.exception("[HITL] failed to apply tool grant")
            return "once"
        card_answered(thread_id, pending_tools, pending_tool_name)
    return scope


def register_misc_routes(self: Any) -> None:
    """Register the misc routes onto ``self.app``."""
    # /api/telemetry/typing and /api/telemetry/typing/stream_start were
    # removed (audit AUD-015, 2026-09-30): stubs no page or client called --
    # one answered a constant, the other only logged
    # (tests/test_api_route_callers.py).
    # /api/session/history and /api/session/replay were removed (audit
    # 2026-09-22): duplicates of /api/replay/* with no caller, no test, and no
    # ownership check — history returned any thread's full state_json, and
    # replay raised TypeError on every call. Use /api/replay/*.
    @self.app.get("/api/alerts/recent")
    def _get_recent_alerts():
        from fastapi.encoders import jsonable_encoder
        from kazma_core.observability.alerts import AlertDispatcher

        # Serialize defensively: one non-JSON-safe alert field (NaN from a
        # metric, an exotic object) can kill the response mid-flight — the
        # browser sees the truncated keep-alive reply as HTTP 502 and the
        # alerts panel goes blank. A bad row must degrade to a placeholder,
        # not take down the endpoint.
        out: list[Any] = []
        import sqlite3

        from kazma_core.runs.notify import bell_alerts
        from kazma_core.web_acquire.monitor import recent_alerts
        from kazma_ui.i18n import current_language

        try:
            incidents = bell_alerts(current_language())
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            # A broken incident store must not erase the other alerts.
            logger.warning("[alerts] run incidents could not be read: %s", exc)
            incidents = []
        from kazma_core.observability.alerts import FALLBACK_PAGE, alert_link
        from kazma_core.db.pg_helpers import store_errors
        from kazma_core.observability.notification_reads import read_ids

        try:
            read = read_ids()
        except store_errors() as exc:  # a store that cannot answer: everything counts as unread
            logger.warning("[alerts] the read notifications could not be read: %s", type(exc).__name__)
            read = set()
        from kazma_core.runs.spaces import install_notices_here

        # The install's own alerts are the admin's once Spaces is enforced;
        # a run's notices keep to its space (S1-32).
        install = AlertDispatcher.get_recent_alerts() if install_notices_here() else []
        # The cards waiting for this person's answer (Spaces S2-05): theirs
        # only, once Spaces is enforced; none before.
        from kazma_ui.hitl_answerers import bell_items

        try:
            waiting = bell_items(current_language())
        except (OSError, RuntimeError, ValueError) as exc:
            # A card read that fails must not erase the other alerts (an
            # identity store that cannot answer is a RuntimeError).
            logger.warning("[alerts] waiting approvals could not be read: %s", type(exc).__name__)
            waiting = []
        for a in [*install, *recent_alerts(current_language()), *incidents, *waiting]:
            item = a.to_dict() if hasattr(a, "to_dict") else a
            try:
                encoded = jsonable_encoder(item)
            except Exception:
                out.append({"repr": repr(item), "error": "unserializable alert omitted", "link": FALLBACK_PAGE})
                continue
            # The page a click in the bell opens (2026-11-08): its own link,
            # else its subsystem's page, else the Dashboard's open problems.
            if isinstance(encoded, dict):
                encoded["link"] = alert_link(encoded)
                # Opened, or marked with "Mark all as read" (2026-11-08): the
                # bell counts the rest.
                encoded["read"] = str(encoded.get("id") or "") in read
            out.append(encoded)
        return out

    @self.app.post("/api/alerts/read")
    async def _mark_alerts_read(request: Request):
        """Record notifications as read: ``{"ids": [...]}``, at most 200
        (kazma_core.observability.notification_reads). The bell sends it when
        one is opened and for "Mark all as read"."""
        from kazma_core.observability.notification_reads import clean_ids, mark_read

        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()
        try:
            ids = clean_ids((body or {}).get("ids") if isinstance(body, dict) else None)
        except ValueError as exc:
            from kazma_core.errors import validation_error

            return _JSONResponse({"ok": False, "error": validation_error(exc), "code": "invalid_ids"},
                                 status_code=400)
        marked = await asyncio.to_thread(mark_read, ids)
        return {"ok": True, "read": marked}
    @self.app.get("/packages")
    def _packages_redirect() -> RedirectResponse:
        """Legacy /packages page → Settings Packages tab."""
        return RedirectResponse("/settings?tab=packages", status_code=307)
    # ── Auth bootstrap (remote clients — loopback auto-cookie is disabled) ──
    @self.app.get("/login", response_class=HTMLResponse)
    def _login_page(request: Request) -> HTMLResponse:
        """Render the secret login form for non-loopback browsers. Once a
        second person has an account, it says why the owner is asked to sign
        in on this machine too (Spaces S4-02)."""
        from kazma_ui.auth import everyone_signs_in

        return self.templates.TemplateResponse(
            request,
            "login.html",
            {"everyone_signs_in": everyone_signs_in()},
        )
    @self.app.get("/invite", response_class=HTMLResponse)
    def _invite_page(request: Request) -> HTMLResponse:
        """An invitation link's page (Spaces S4-02): a name and a password
        make the invited person's account. The link is ``/invite#<token>``:
        the page reads the token after the '#' -- which a browser never sends,
        so no access log holds it (the review of S4-02, item 6) -- asks
        ``/api/invitations/lookup`` whether it is open, and says so (one
        answer for a link that never was, ran out, was used or revoked)."""
        return self.templates.TemplateResponse(
            request,
            "invite.html",
            {},
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    @self.app.websocket("/ws/dashboard")
    async def ws_dashboard(websocket: WebSocket) -> None:
        from kazma_ui.auth import principal_allows, websocket_principal

        # Accept FIRST, then close 4003 — closing before accept makes the
        # server send an HTTP handshake rejection, so the client saw a 1006
        # close instead of 4003 and the session-expired redirect never fired
        # (the /ws/chat endpoint already does it in this order).
        await websocket.accept()
        # The session lookup is a store read (audit 2026-10-26 AUD-111).
        try:
            principal = await asyncio.to_thread(websocket_principal, websocket)
        except Exception:
            logger.warning("[WS] dashboard authentication failed closed", exc_info=True)
            principal = None
        if principal is None:
            await websocket.close(code=4003, reason="Unauthorized")
            return
        # The feed is every tool call and model call's trace: what its HTTP
        # twin (GET /api/agents/tools) shows, and only to whom it shows it --
        # a viewer was streamed every user's traces (security audit 2026-11-10).
        if not principal_allows(principal, "/api/agents/tools", "GET"):
            # 4403, not 4003: the page sends a 4003 to the sign-in page, and a
            # signed-in viewer would loop there.
            await websocket.close(code=4403, reason="Forbidden for your role")
            return
        from kazma_core.shutdown import is_shutting_down
        from kazma_core.spaces.scope import spaces_enforced
        from kazma_core.tracing import get_trace_store

        # The feed of the space the page names, checked at the handshake
        # like every request (Spaces S1-22): another space's traces are
        # never sent to it.
        shown: str | None = None
        asker: str | None = None
        if spaces_enforced():
            from kazma_ui import space_binding

            try:
                binding = await asyncio.to_thread(space_binding.space_for_socket, principal, websocket)
            except space_binding.SpaceRefused as refused:
                await websocket.close(code=refused.close_code, reason=refused.code)
                return
            shown, asker = binding.space_id, binding.asker
        from kazma_ui.live_connections import socket_watched

        store = get_trace_store()
        store.register_ws(websocket, shown)
        # Closed when its person leaves the space (the S2-12 review, finding 7).
        watched = socket_watched(asker, shown, websocket)
        watched.__enter__()
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
            watched.__exit__(None, None, None)
            store.unregister_ws(websocket)
    @self.app.get("/")
    def root(request: Request) -> RedirectResponse:
        # Hands 0.11: chat is home. Dashboard stays an inspector at /dashboard.
        # Auth middleware still gates HTML pages — do not add "/" to ALWAYS_OPEN_PATHS.
        return RedirectResponse(url="/chat", status_code=303)
    @self.app.get("/workspace", response_class=HTMLResponse)
    def workspace_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "workspace.html",
            {
                "config": self.agent.config,
                "active_page": "workspace",
            },
        )
    @self.app.get("/ide", response_class=HTMLResponse)
    def ide_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "ide.html",
            {
                "config": self.agent.config,
                "active_page": "ide",
            },
        )
    @self.app.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request) -> HTMLResponse:
        """The first-run setup guide (kazma_ui/setup_api.py serves its data)."""
        return self.templates.TemplateResponse(
            request,
            "setup.html",
            {
                "config": self.agent.config,
                "active_page": "setup",
            },
        )

    @self.app.get("/replay", response_class=HTMLResponse)
    def replay_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "replay.html",
            {
                "config": self.agent.config,
                "active_page": "replay",
            },
        )
    @self.app.get("/research", response_class=HTMLResponse)
    def research_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "research.html",
            {
                "config": self.agent.config,
                "active_page": "research",
            },
        )
    @self.app.get("/knowledge", response_class=HTMLResponse)
    def knowledge_page(request: Request) -> HTMLResponse:
        # A single-operator surface (Knowledge KB-S04, decision D1): the page
        # says so in its own language; every /api/kb route refuses.
        from kazma_core.stores.knowledge_lifecycle import kb_refusal

        return self.templates.TemplateResponse(
            request,
            "knowledge_base.html",
            {
                "config": self.agent.config,
                "active_page": "knowledge",
                "kb_single_operator": bool(kb_refusal()),
            },
        )
    @self.app.get("/documents", response_class=HTMLResponse)
    def documents_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "documents.html",
            {
                "config": self.agent.config,
                "active_page": "documents",
            },
        )
    @self.app.get("/x", response_class=HTMLResponse)
    def x_studio_page(request: Request) -> HTMLResponse:
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
        """Rebuild every chat adapter from the saved settings now (Settings'
        Refresh Gateway): the "reconnect everything" button.

        A saved setting reaches the running adapters on its own; this builds
        through the same code as boot and the saved-setting path
        (``kazma_gateway.chat_adapters``), so a platform switched off stays
        off and the access posture and webhook secret are kept.
        """
        if self.gateway is None or self.chat_adapters is None:
            return {"status": "error", "message": "Gateway not initialized"}
        logger.info("[Gateway] Refreshing adapters from the saved settings")
        await self.chat_adapters.apply(force=True)
        logger.info("[Gateway] Adapter refresh complete — %d adapter(s) running", len(self.gateway.adapters))
        return {
            "status": "ok",
            "adapters_count": len(self.gateway.adapters),
            "adapters": [a.name for a in self.gateway.adapters],
        }

    def _is_caller_admin(request: Request) -> bool:
        from kazma_ui.auth import admin_decision

        return admin_decision(request) == "ok"

    @self.app.get("/health")
    async def health_check(request: Request) -> dict[str, Any]:
        from kazma_core.diagnostic_scope import read_only_diagnostic

        def _caller_is_admin() -> bool:
            with read_only_diagnostic("/health"):
                return _is_caller_admin(request)

        # The admin decision reads the session store; the counters below are
        # in memory and stay on the loop (audit 2026-10-26 AUD-111).
        return _health_body(await asyncio.to_thread(_caller_is_admin))

    def _health_body(is_admin: bool) -> dict[str, Any]:
        if not is_admin:
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
        from kazma_ui.auth import aget_kazma_secret, is_authenticated

        _secret = await aget_kazma_secret()
        if _secret and not await asyncio.to_thread(is_authenticated, request, _secret):
            return _JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            body = await read_json(request)
        except InvalidJsonBody:
            return invalid_json_response()

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

        # A bus-bridge gate is decided here and goes no further. There is no
        # graph to resume and no session to own it: the waiting process is
        # `kazma mcp`, polling the registry row for its answer. Handled before
        # the graph lookup so an approval does not need a graph that this
        # request has nothing to do with.
        # Who decides, from the server-side session (AUD-100: the bridge
        # branch recorded the body's ``actor``).
        actor = await _request_actor(request)
        decided = await _decide_bridge_gate(thread_id, body, approved, actor=actor)
        if decided is not None:
            return decided

        graph_ref = _resolve_hitl_graph()
        if graph_ref is None:
            return _JSONResponse({"error": "Graph not available"}, status_code=503)

        # A checkpoint has no authenticated caller identity of its own. Bind
        # every browser approval to a session belonging to this request's tenant
        # before inspecting or resuming the graph state.
        try:
            from kazma_ui.session_manager import get_session_manager

            # A chat-store read (Postgres when the session is not cached),
            # off the loop; an error still reaches the fail-closed except.
            _owner_session = await asyncio.to_thread(
                lambda: get_session_manager().get_by_thread_id(thread_id)
            )
            if _owner_session is None:
                logger.warning(
                    "[HITL] Approval denied for thread not owned by current tenant: %s",
                    thread_id,
                )
                return _JSONResponse(
                    {"error": "Approval request not found"},
                    status_code=404,
                )

            try:
                from kazma_core.mcp.spec_client import pending_sampling_card, resolve_sampling_hitl

                # An MCP server's sampling request is a card like any other
                # (Spaces S2-05): who may answer it is asked first, and the
                # request stays waiting for the person it asks.
                _sampling_card = pending_sampling_card(thread_id)
                if _sampling_card is not None:
                    from kazma_ui.hitl_answerers import refusal_for_card

                    _not_theirs, _ = await asyncio.to_thread(
                        refusal_for_card, thread_id, "", _sampling_card)
                    if _not_theirs:
                        return _not_yours_response(thread_id, _not_theirs, _sampling_card)
                if _sampling_card is not None and resolve_sampling_hitl(thread_id, approved):
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
            pending_tool_name = ""
            pending_tools: list[Any] = []
            try:
                pre = await graph_ref.aget_state(config)
            except Exception:
                logger.warning("[HITL] state of thread=%s unreadable; approval stays paused",
                               thread_id, exc_info=True)
                return _JSONResponse(
                    {"error": "Approval remains paused: the chat's state could not be read.",
                     "code": "state_unavailable"},
                    status_code=503, headers={"Retry-After": "5"},
                )
            # Only a LIVE approval interrupt is resumed. A checkpoint with a
            # next step and no interrupt used to count as paused ("still try
            # resume") with a gate-less security resume: after a restore wrote
            # a mid-turn step, gate A's still-live card ran the restored
            # step's tool call -- one the human had denied (Time Travel audit
            # TT-02).
            _live_iid, _live_payload, _interrupts = _live_hitl_interrupt(pre)
            if _live_payload is None:
                logger.warning(
                    "[HITL] No live approval interrupt for thread=%s (next=%s, interrupts=%d) — refused",
                    thread_id, list(getattr(pre, "next", None) or ()), _interrupts,
                )
                return _JSONResponse(
                    await asyncio.to_thread(_attach_hitl_view,
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
                        thread_id,
                        _body_interrupt_id(body),
                        state_hint="settled",
                    ),
                    status_code=409,
                )
            pending_tool_name = str(_live_payload.get("tool") or "")
            pending_tools = list(_live_payload.get("tools") or [])

            # The card goes to its asker and nobody else answers it (Spaces
            # S2-05, decisions N4 and N21): another member of the same space
            # is refused here, before anything is claimed, and the gate stays
            # pending for the person it asks. Nothing changes on an install
            # that does not enforce Spaces.
            from kazma_ui.hitl_answerers import refusal_for_card

            # Whose card it is comes from the LIVE interrupt alone -- its id
            # and its payload -- never an id the request names: a client could
            # name another person's row.
            _not_yours, _whose = await asyncio.to_thread(
                refusal_for_card, thread_id, _live_iid, _live_payload)
            if _not_yours:
                return _not_yours_response(thread_id, _not_yours, _live_payload)

            # An always-ask tool (X writes) takes a one-time approval only. A
            # "allow for this chat" (or YOLO) answer approved THIS call and
            # stored a grant that can never apply: live 2026-10-16 a click on
            # "Allow tool (session)" published a post the owner read as "it
            # will still ask me before posting". Refused here, before anything
            # is claimed or resumed, so that click does nothing at all; the
            # gate stays pending for Approve once or Deny.
            if approved and scope in ("tool", "yolo"):
                from kazma_core.safety.hitl_grants import one_time_only

                _always = one_time_only(pending_tools, pending_tool_name)
                if _always:
                    logger.warning(
                        "[HITL] %s approval refused: always-ask tool(s) %s take a "
                        "one-time approval only (thread=%s); nothing approved",
                        scope, ",".join(_always), thread_id,
                    )
                    return _JSONResponse(
                        await asyncio.to_thread(_attach_hitl_view,
                            {
                                "ok": False,
                                "status": "refused",
                                "thread_id": thread_id,
                                "error": (
                                    "This tool takes a one-time approval only. "
                                    "Nothing was approved; use Approve once or Deny."
                                ),
                                "reason": "one_time_only",
                                "tools": _always,
                                "running": False,
                                "hitl_state": "pending",
                            },
                            thread_id,
                            _body_interrupt_id(body),
                            state_hint="pending",
                        ),
                        status_code=409,
                    )

            # ``actor`` came from the SERVER-side session above
            # (``_request_actor``), never the client body (audit M-W3).
            # Never trust a client-supplied actor label.
            body.pop("actor", None)

            # Phase 3/§4.3: build the resume Command via the single chokepoint
            # (build_resume_command). Semantic interrupts need {tcid: option_id};
            # security needs {approved: bool}. Routed through one helper so a
            # transport cannot drift again (cf. WS bug, incident 2026-08-12).
            # ── One decision, one gate (plan S7, invariant U11) ────────
            #
            # Everything above establishes that this THREAD is paused.
            # That is not the same question as "is the gate the human
            # answered still open", and the difference is the whole
            # defect: after a resume the graph pauses again, so a retry
            # arriving late finds a pause and decides the NEXT ask.
            #
            # Transport loss is not authorization. A client that never
            # saw its 200 must be told the server's actual view and let
            # to reconcile, which is what plan S7 asks for in those
            # words -- not handed a fresh resume.
            _req_gate = _body_interrupt_id(body)
            _already = await _gate_not_pending(thread_id, _req_gate)
            if _already:
                logger.info(
                    "[HITL] Refusing approve for gate=%s on thread=%s: "
                    "registry state=%s (not pending)",
                    _req_gate, thread_id, _already,
                )
                # Storage vocabulary out, client vocabulary in. "claimed"
                # and "resuming" mean the decision is recorded and the
                # tool is running -- which is "inflight", the state that
                # sends chat.js down its converge-and-reattach arm
                # instead of flashing an error on a row that was approved
                # correctly. Terminal states keep the error arm, because
                # an expired or failed gate really does need a resync.
                _client_state = (
                    "inflight" if _already in ("claimed", "resuming")
                    else "settled" if _already != "foreign"
                    else "foreign"
                )
                return _JSONResponse(
                    await asyncio.to_thread(_attach_hitl_view,
                        {
                            "ok": False,
                            "status": "expired" if _already != "foreign" else "not_found",
                            "thread_id": thread_id,
                            "content": "",
                            "error": (
                                "This request was already decided "
                                f"({_already}); showing the current state."
                                if _already != "foreign"
                                else "This approval does not belong to this thread."
                            ),
                            "reason": "not_pending",
                            "running": False,
                            "hitl_state": _client_state,
                            "registry_state": _already,
                            "interrupt_id": _req_gate,
                        },
                        thread_id,
                        _req_gate,
                        state_hint=_client_state,
                    ),
                    status_code=409,
                )

            # The card the human answered must be the interrupt the graph is
            # paused at (TT-02, §30 B2). A gate the registry knows that is not
            # this interrupt answers 409; an id it has never heard of reaches
            # the live interrupt as before (legacy dashboard/TUI clients).
            from kazma_ui.hitl_gate_bridge import gate_names_interrupt

            if _req_gate and not await gate_names_interrupt(thread_id, _req_gate, _live_iid, _live_payload):
                logger.info("[HITL] gate=%s is not the live interrupt of thread=%s — refused",
                            _req_gate, thread_id)
                return _JSONResponse(
                    await asyncio.to_thread(_attach_hitl_view,
                        {
                            "ok": False,
                            "status": "expired",
                            "thread_id": thread_id,
                            "content": "",
                            "error": "This approval card is not the one the chat is waiting on.",
                            "reason": "not_pending",
                            "running": False,
                            "hitl_state": "settled",
                            "interrupt_id": _req_gate,
                        },
                        thread_id,
                        _req_gate,
                        state_hint="settled",
                    ),
                    status_code=409,
                )

            from kazma_core.safety.commitment.resume import build_resume_command

            _intr_payload = _live_payload
            # Keyed by the live interrupt's id: the resume answers that
            # interrupt and no other (a resume whose interrupt is gone
            # resumes nothing; the graph asks again).
            resume_cmd = build_resume_command(
                _intr_payload,
                approved=approved,
                choices=body.get("choices") if isinstance(body.get("choices"), dict) else None,
                scope=scope,
                reason=body.get("reason", ""),
                approved_ids=body.get("approved_ids") if isinstance(body.get("approved_ids"), list) else None,
                interrupt_id=_live_iid,
            )
            if resume_cmd is None:
                # No gate-less fallback: a resume that names no interrupt is
                # not an answer to the card the human saw (TT-02).
                return _JSONResponse(
                    {"ok": False, "error": "No pending approval for this thread.", "reason": "not_pending"},
                    status_code=409,
                )

            # JSON command, not a second graph SSE. The live tail is the
            # journal attach the chat client already holds (or re-opens).
            from kazma_ui.active_turns import is_turn_running, register_turn
            from kazma_ui.reply_sink import resolve_reply_turn
            from kazma_ui.sse_chat._streaming import (
                _drive_graph_to_journal,
                mark_thread_unpaused,
            )
            from kazma_ui.turn_runtime import ensure_session_for_thread, resolve_session_id

            async with _approve_lock_for(thread_id):
                _busy = is_turn_running(thread_id) or thread_id in _resume_inflight
                if _busy and await _gate_is_pending(thread_id, _req_gate):
                    # The answered gate is on record as pending, so the graph
                    # is parked at it and the running task is the segment
                    # that showed it, finishing up. Wait for it rather than
                    # refuse a click on a card the human is looking at.
                    _busy = not (
                        await _await_segment_tail(thread_id)
                        and await _gate_is_pending(thread_id, _req_gate)
                    )
                if _busy:
                    _claimed_turn = ""
                    try:
                        # WITH the session id. `resolve_reply_turn` guards
                        # its stored-row recovery on `if session_id:`, so
                        # passing "" skipped it and minted a fresh turn id
                        # every time the in-memory one was gone — which is
                        # precisely the case the recovery exists for. The
                        # gate was then claimed under a turn that is not
                        # the reply's, the client saw the identity change,
                        # and every gate decided under the old id was
                        # orphaned on screen (2026-09-20).
                        _claimed_sid = await asyncio.to_thread(ensure_session_for_thread, thread_id)
                    except Exception:
                        _claimed_sid = ""
                    try:
                        _claimed_turn = await asyncio.to_thread(
                            resolve_reply_turn, thread_id, _claimed_sid
                        ) or ""
                    except Exception:
                        _claimed_turn = ""
                    _claimed_iid = _body_interrupt_id(body)
                    try:
                        from kazma_ui.hitl_status import persisted_hitl_for_thread

                        _part = await asyncio.to_thread(persisted_hitl_for_thread, thread_id)
                        if isinstance(_part, dict):
                            _claimed_iid = str(
                                _part.get("interrupt_id")
                                or (_part.get("payload") or {}).get("interrupt_id")
                                or _claimed_iid
                            )
                    except Exception:
                        pass
                    return _JSONResponse(
                        await asyncio.to_thread(_attach_hitl_view,
                            {
                                "ok": False,
                                "error": "This approval is no longer pending.",
                                "reason": "not_pending",
                                "running": True,
                                "turn_id": _claimed_turn,
                                "interrupt_id": _claimed_iid,
                                "hitl_state": "inflight",
                            },
                            thread_id,
                            _claimed_iid,
                            state_hint="inflight",
                        ),
                        status_code=409,
                    )

                # Chat-store reads and a write: off the loop (Postgres when
                # the session is not cached). resolve_session_id logs and
                # answers "" on a failure, as this block did.
                _resume_session_id = await asyncio.to_thread(resolve_session_id, thread_id)
                if not _resume_session_id:
                    _resume_session_id = await asyncio.to_thread(ensure_session_for_thread, thread_id)
                _resume_turn = await asyncio.to_thread(resolve_reply_turn, thread_id, _resume_session_id)
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

                    _part = await asyncio.to_thread(persisted_hitl_for_thread, thread_id)
                    if isinstance(_part, dict):
                        _stored_state = str(_part.get("state") or "")
                        _stored_iid = str(
                            _part.get("interrupt_id")
                            or (_part.get("payload") or {}).get("interrupt_id")
                            or ""
                        )
                except Exception:
                    _stored_iid = ""
                _req_iid = _body_interrupt_id(body)
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
                        await asyncio.to_thread(_attach_hitl_view,
                            {
                                "ok": False,
                                "error": "This approval is no longer pending.",
                                "reason": "not_pending",
                                "running": False,
                                "interrupt_id": _stored_iid,
                                "hitl_state": _stored_state or "settled",
                            },
                            thread_id,
                            _stored_iid,
                            tool=pending_tool_name,
                            state_hint=_stored_state or "settled",
                        ),
                        status_code=409,
                    )
                # Persist the decision and resume intent before projecting it
                # or spawning execution. A registry outage leaves the pause.
                from kazma_ui.hitl_decision import record_gate_decision

                _recorded_iid = await record_gate_decision(
                    thread_id,
                    decision="approved" if approved else "denied",
                    actor=actor,
                    tool=pending_tool_name,
                    payload=_stamp_payload,
                    interrupt_id=_live_iid,
                    session_id=_resume_session_id,
                    turn_id=_resume_turn,
                    require_durable=True,
                )
                scope = await asyncio.to_thread(
                    _apply_scope_grants, thread_id, approved, scope, actor,
                    pending_tools, pending_tool_name, body.get("tool") or body.get("grant_tool") or "",
                    _whose.asker if _whose is not None and _whose.asker else None,
                )
                if isinstance(resume_cmd.resume, dict) and "scope" in resume_cmd.resume:
                    resume_cmd.resume["scope"] = scope
                # Carried out only while its chat is there, in the same step
                # as the drive is registered (chat_runs_end.begin_drive): a
                # chat deleted or archived since took the gate, and its record
                # says the call did not run. Before, the drive started anyway
                # and ran the approved call on a deleted chat (review L1).
                from kazma_ui.chat_runs_end import begin_drive

                if not begin_drive(thread_id, _recorded_iid, _live_iid):
                    logger.warning("[HITL] thread=%s: its chat went away before the %s was carried out; "
                                   "nothing runs", thread_id[:12], "approval" if approved else "denial")
                    from kazma_ui.i18n import current_language
                    from kazma_ui.i18n import t as _t

                    return _JSONResponse(
                        {"ok": False, "reason": "chat_gone", "thread_id": thread_id, "running": False,
                         "interrupt_id": _recorded_iid or _live_iid, "hitl_state": "settled",
                         "error": _t(f"runs.gate_fate.{'not_run' if approved else 'denied'}",
                                     current_language())},
                        status_code=409,
                    )
                _resume_inflight.add(thread_id)
                # The resumed turn is its asker's, whoever answered (S2-05):
                # an approver or the admin pressing for someone never runs
                # their turn as themself -- its tools, limits and records stay
                # the asker's. The task copies the scope bound when it is made.
                from kazma_ui.hitl_answerers import as_the_asker

                with as_the_asker(_whose):
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

                _ok_iid = str(_stamp_payload.get("interrupt_id") or "")
                return _JSONResponse(
                    await asyncio.to_thread(_attach_hitl_view,
                        {
                            "ok": True,
                            "approved": approved,
                            "thread_id": thread_id,
                            "turn_id": _resume_turn,
                            "running": True,
                            "interrupt_id": _ok_iid,
                            "hitl_state": "approved" if approved else "denied",
                        },
                        thread_id,
                        _ok_iid,
                        tool=pending_tool_name,
                        state_hint="approved" if approved else "denied",
                    )
                )
        except GateDecisionUnavailable:
            return _JSONResponse(
                {"error": "Approval remains paused: decision storage unavailable", "code": "gate_decision_unavailable"},
                status_code=503, headers={"Retry-After": "5"},
            )
        except GateNotYours as not_yours:
            return _not_yours_response(thread_id, not_yours.code, None)
        except TransitionConflict as conflict:
            return _JSONResponse(
                await asyncio.to_thread(_attach_hitl_view,
                    {"error": "Approval no longer pending", "reason": "not_pending", "thread_id": thread_id,
                     "hitl_state": "inflight" if conflict.actual in ("claimed", "resuming") else conflict.actual},
                    thread_id, _body_interrupt_id(body),
                ), status_code=409,
            )
        except Exception:
            logger.exception("[HITL] Failed to resume graph for thread=%s", thread_id)
            return _JSONResponse({"error": "Internal error"}, status_code=500)
    @self.app.get("/api/pending-approvals")
    async def list_pending_approvals() -> _JSONResponse:
        graph = _resolve_hitl_graph()
        checkpointer = _resolve_hitl_checkpointer()
        sampling: list[Any] = []
        try:
            from kazma_core.mcp.spec_client import list_sampling_pending

            sampling = list_sampling_pending()
        except Exception:
            sampling = []
        if sampling and not await asyncio.to_thread(_sampling_shown_here):
            # An MCP server's sampling request is the machine's (the admin's
            # MCP servers, Development): once Spaces is enforced it is shown
            # where Development is, never in another space (S1-21).
            sampling = []
        if sampling:
            # The requests are kept process-wide: once Spaces is enforced the
            # list holds only this space's that the caller may answer (S2-05).
            from kazma_ui.hitl_answerers import cards_of_this_space

            sampling = await asyncio.to_thread(cards_of_this_space, sampling)
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
                candidates = list(registry_items)
            else:
                # Kill-switch / registry outage: checkpoint scan is the
                # thin execution fallback (live interrupt ⇒ pending card).
                from kazma_ui.hitl_approval import _get_pending_approvals

                candidates = list(await _get_pending_approvals(graph, checkpointer))
            # One ownership answer for every thread route (thread_ownership):
            # off the event loop, and closed when the session store fails.
            from kazma_ui.thread_ownership import owned_threads_async

            owned = await owned_threads_async(
                str(item.get("thread_id") or "") for item in candidates
            )
            if owned is None:
                return _JSONResponse(
                    {"pending": [], "count": 0, "error": "ownership check failed"},
                    status_code=403,
                )
            owned_set = set(owned)
            pending = [
                item
                for item in candidates
                if str(item.get("thread_id") or "") in owned_set or _is_own_bridge_gate(item)
            ]
            # Each person's own cards (Spaces S2-05): a card of another member
            # of the space is not listed for them to answer.
            from kazma_ui.hitl_answerers import answerable

            pending = await asyncio.to_thread(answerable, pending)
            pending = list(pending) + list(sampling)
            return _JSONResponse({"pending": pending, "count": len(pending)})
        except Exception:
            logger.exception("[HITL] Failed to list pending approvals")
            return _JSONResponse({"pending": [], "count": 0, "error": "Internal error"}, status_code=500)
    # One route clears the paused approvals: the page's POST. A DELETE on
    # /api/pending-approvals did the same through the same handler, called
    # by nothing (removed 2026-10-01).
    @self.app.post("/api/pending-approvals/clear")
    async def clear_pending_approvals_route(request: Request) -> _JSONResponse:
        from kazma_ui.auth import aget_kazma_secret, is_authenticated

        _secret = await aget_kazma_secret()
        if _secret and not await asyncio.to_thread(is_authenticated, request, _secret):
            return _JSONResponse({"error": "Unauthorized"}, status_code=401)
        if not await asyncio.to_thread(_is_caller_admin, request):
            return _JSONResponse({"error": "Admin role required"}, status_code=403)

        from kazma_ui.hitl_approval import _get_pending_approvals
        from kazma_ui.session_manager import get_session_manager

        graph = _resolve_hitl_graph()
        checkpointer = _resolve_hitl_checkpointer()
        if checkpointer is None:
            return _JSONResponse({"error": "Checkpointer not available"}, status_code=503)
        try:
            pending = await _get_pending_approvals(graph, checkpointer)
            # Only this tenant's chats: one chat-store read per paused
            # thread (Postgres when not cached), all of them off the loop.
            owned = await asyncio.to_thread(
                lambda: {
                    str(item["thread_id"])
                    for item in pending
                    if get_session_manager().get_by_thread_id(str(item["thread_id"])) is not None
                }
            )
            cleared = 0
            from kazma_ui.hitl_answerers import clearable_thread

            for item in pending:
                thread_id = str(item["thread_id"])
                if thread_id not in owned:
                    continue
                # Only the chats whose every card is the caller's to answer
                # (Spaces S2-05): clearing denies them.
                if not await asyncio.to_thread(clearable_thread, thread_id, item):
                    continue
                from kazma_ui.chat_runs_end import end_chat_runs
                from kazma_ui.thread_locks import thread_mutation

                # Its turn ends first (chat_runs_end): its gates settled
                # "cleared", its reply turn closed, the run recorded as ended
                # -- it stayed open with nothing running it and was paged.
                await end_chat_runs(thread_id, reason="approvals_cleared", gate_outcome="cleared")
                # The thread's checkpoint-mutation lock (Time Travel TT-04).
                async with thread_mutation(thread_id):
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
    def get_status() -> dict[str, Any]:
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
