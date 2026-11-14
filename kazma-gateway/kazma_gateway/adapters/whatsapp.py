"""WhatsApp, through Meta's WhatsApp Cloud API (2026-11-14).

Meta delivers every message to Kazma's webhook (``/api/webhooks/whatsapp``):
there is no polling. The webhook answers at once and the adapter reads each
person's messages in order behind it (``_chains``): a voice note being
transcribed never makes Meta wait, and so never makes it resend.

* **Who may write in** is the one sender rule (``sender_rule``): Allowed
  numbers (``connectors.whatsapp.allowed_users``, phone numbers, any spelling)
  or, empty, the owner's own number Kazma knows (``KAZMA_GATEWAY_ADMINS``
  ``whatsapp:<number>``). A stranger is told once a day that the number is
  private, as on the other chat apps.
* **Every call is Meta's** or it is refused: ``whatsapp_parse.signature_ok``
  checks ``X-Hub-Signature-256`` under the app secret before anything is read.
  With no app secret saved, the webhook answers nothing.
* **What is read**: text, photos, documents (downloaded through the Graph
  API, capped like Telegram's), voice notes (transcribed by the shared voice
  path) and Kazma's own buttons. Anything else is recorded, not answered.
* **Approvals** come as reply buttons (WhatsApp allows three on a message:
  Approve, Deny, Approve for task), or a list for more choices. A press goes
  through the same callback scheme as every chat app (``platform_callbacks``)
  and the same admin gate.
* **Every message's fate** is in the receive record (``receive_log``), which
  the connector Test reads.

Meta allows a free-form message only within 24 hours of the person's last
one; outside it a send fails with Meta's error 131047, said in the log and
by the chat outbox.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from kazma_gateway.adapters.whatsapp_api import DEFAULT_API_VERSION, WhatsAppClient, WhatsAppError
from kazma_gateway.adapters.whatsapp_format import md_to_whatsapp, split_for_whatsapp
from kazma_gateway.adapters.whatsapp_parse import (
    WaInbound,
    count_statuses,
    inbound_messages,
    signature_ok,
    wa_digits,
)
from kazma_gateway.adapters.whatsapp_receive import WHATSAPP_REASONS
from kazma_gateway.gateway import Attachment, BaseAdapter, IncomingMessage, OutboundMessage
from kazma_gateway.receive_log import ReceiveLog
from kazma_gateway.sender_rule import SenderGate

logger = logging.getLogger(__name__)

__all__ = ["BUTTONS_KEY", "WEBHOOK_PATH", "WhatsAppAdapter", "whatsapp_webhook_router"]

#: Where the webhook is mounted (``kazma_ui.app``); Meta is given this address.
WEBHOOK_PATH = "/api/webhooks/whatsapp"
#: The context_metadata key carrying reply buttons (``adapters.markup``).
BUTTONS_KEY = "whatsapp_buttons"
#: The largest webhook body read (Meta's payloads are a few KB).
MAX_WEBHOOK_BYTES = 1024 * 1024
#: How long a message id is remembered against Meta's resends.
_SEEN_S = 24 * 3600.0
_SEEN_MAX = 5000
#: What a call to Meta can fail with: Meta's refusal, the network, a body
#: that is not what Meta sends.
_META_FAILURES = (WhatsAppError, httpx.HTTPError, OSError, ValueError)
#: How often a connected adapter asks Meta about the number (its health).
_HEALTH_EVERY_S = 600.0
_BACKOFF_S = (30.0, 60.0, 120.0, 300.0, 900.0)


class WhatsAppAdapter(SenderGate, BaseAdapter):
    """One WhatsApp business phone number."""

    name = "whatsapp"

    #: Drops that point at a setting to fix (a WARNING, throttled).
    _DROP_WARNINGS = frozenset({"no_allowlist", "queue_full", "media_failed", "not_this_number"})

    def __init__(
        self,
        token: str,
        phone_number_id: str,
        *,
        app_secret: str = "",
        verify_token: str = "",
        api_version: str = DEFAULT_API_VERSION,
        allowed_users: list[str] | None = None,
        allow_all: bool = False,
        client: WhatsAppClient | None = None,
    ) -> None:
        super().__init__()
        self._phone_number_id = str(phone_number_id or "").strip()
        self._client = client or WhatsAppClient(token, self._phone_number_id, api_version=api_version)
        self._app_secret = app_secret or ""
        self._verify_token = verify_token or ""
        self._allowed_users = {wa_digits(u) for u in (allowed_users or []) if wa_digits(u)}
        self._allow_all = allow_all
        self._queue: asyncio.Queue[IncomingMessage] | None = None
        self._receive = ReceiveLog(WHATSAPP_REASONS)
        self._seen: dict[str, float] = {}
        self._chains: dict[str, asyncio.Task[None]] = {}
        self._bg: set[asyncio.Task[Any]] = set()

    # ── settings and diagnostics ──────────────────────────────────────

    def set_allowed_users(self, numbers: list[str] | set[str]) -> None:
        """Allowed numbers, in any spelling (``+965 9999 1234``): kept as WhatsApp ids."""
        self._allowed_users = {wa_digits(n) for n in numbers if wa_digits(n)}

    @property
    def webhook_ready(self) -> bool:
        """Both secrets Meta's webhook needs are saved."""
        return bool(self._app_secret and self._verify_token)

    def diagnostics(self) -> dict[str, Any]:
        """The live receive record for the connector Test (plain data)."""
        return {
            **self._receive.snapshot(),
            **self.admission(),
            "phone_number_id": self._phone_number_id,
            "webhook_ready": self.webhook_ready,
        }

    # ── lifecycle ─────────────────────────────────────────────────────

    async def start(self, queue: asyncio.Queue[IncomingMessage], shutdown_event: asyncio.Event) -> None:
        self._queue = queue
        await super().start(queue, shutdown_event)

    async def listen(self, queue: asyncio.Queue[IncomingMessage], shutdown_event: asyncio.Event) -> None:
        """No polling: Meta calls the webhook. This loop asks Meta about the
        number now and then, so the connection's state says whether the token
        and number still work."""
        failures = 0
        first = True
        try:
            while not shutdown_event.is_set():
                try:
                    info = await self._client.phone_number()
                    self._receive.extra.update({
                        "display_phone_number": info.get("display_phone_number"),
                        "verified_name": info.get("verified_name"),
                        "quality_rating": info.get("quality_rating"),
                    })
                    if not self._receive.connected:
                        self._receive.connected_now(new_session=first)
                        logger.info(
                            "[whatsapp] Connected: %s (%s)",
                            info.get("display_phone_number") or self._phone_number_id,
                            info.get("verified_name") or "no verified name",
                        )
                    else:
                        self._receive.alive()
                    first = False
                    failures = 0
                    wait = _HEALTH_EVERY_S
                except _META_FAILURES as exc:
                    what = str(exc) if isinstance(exc, WhatsAppError) else f"{type(exc).__name__}: {exc}"
                    if self._receive.connected or failures == 0:
                        logger.warning("[whatsapp] Meta did not accept the number or token: %s", what)
                    self._receive.disconnected(what)
                    wait = _BACKOFF_S[min(failures, len(_BACKOFF_S) - 1)]
                    failures += 1
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=wait)
                except TimeoutError:
                    pass
        finally:
            from kazma_core.bounded_cancel import cancel_and_wait

            for task in list(self._chains.values()) + list(self._bg):
                await cancel_and_wait(task, what="whatsapp message")
            await self._client.aclose()

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    # ── the webhook ───────────────────────────────────────────────────

    def verify_subscription(self, mode: str, token: str, challenge: str) -> str | None:
        """Meta's verification call: the challenge back when *token* is ours."""
        if mode != "subscribe" or not self._verify_token or not token:
            return None
        if not hmac.compare_digest(token, self._verify_token):
            return None
        self._receive.event("webhook_verified")
        logger.info("[whatsapp] Meta verified the webhook")
        return challenge

    def accept(self, payload: dict[str, Any]) -> int:
        """Take a signed webhook payload; returns how many messages it held.
        Each is read in its sender's order, after this returns."""
        statuses = count_statuses(payload)
        for _ in range(statuses):
            self._receive.event("status")
        messages = inbound_messages(payload)
        for message in messages:
            self._receive.event(message.kind)
            self._chain(message)
        return len(messages)

    def _chain(self, message: WaInbound) -> None:
        """Read *message* after its sender's previous one (order kept)."""
        previous = self._chains.get(message.from_id)

        async def run() -> None:
            if previous is not None and not previous.done():
                # Wait for the previous message's end, never its outcome
                # (asyncio.wait does not raise it).
                await asyncio.wait({previous})
            try:
                await self._process(message)
            except Exception:
                logger.exception("[whatsapp] Reading a message failed")
                self._note_drop("processing_failed", message)

        task = asyncio.get_running_loop().create_task(run())
        self._chains[message.from_id] = task

        def done(t: asyncio.Task[None], key: str = message.from_id) -> None:
            if self._chains.get(key) is t:
                del self._chains[key]

        task.add_done_callback(done)

    async def settled(self) -> None:
        """Wait until every message taken so far has been read (and its read
        receipt sent): the webhook answers before they are."""
        while True:
            pending = [t for t in (*self._chains.values(), *self._bg) if not t.done()]
            if not pending:
                return
            await asyncio.wait(pending)

    def _first_time(self, message_id: str) -> bool:
        now = time.monotonic()
        if len(self._seen) > _SEEN_MAX:
            for key in [k for k, at in self._seen.items() if now - at > _SEEN_S][: len(self._seen) // 2 or 1]:
                del self._seen[key]
        if message_id in self._seen:
            return False
        self._seen[message_id] = now
        return True

    # ── reading one message ───────────────────────────────────────────

    def _note_drop(self, reason: str, message: WaInbound) -> None:
        record = self._receive.drop(
            reason, message_id=message.message_id, author_id=message.from_id,
            channel_id=message.from_id, where="direct message",
        )
        self._receive.log_drop(logger, "whatsapp", record, warn=self._DROP_WARNINGS)

    def _refuse(self, reason: str, message: WaInbound) -> None:
        self._note_drop(reason, message)
        self.tell_refused_sender(
            reason,
            user_id=message.from_id,
            direct=True,
            target_id=f"whatsapp:{message.from_id}",
            context_metadata={"chat_id": message.from_id},
            user_text=message.text,
        )

    async def _process(self, m: WaInbound) -> None:
        if not self._first_time(m.message_id):
            # Counted, but not under its id: the first copy's fate stands.
            self._receive.message(None, person=False)
            record = self._receive.drop("duplicate", author_id=m.from_id, channel_id=m.from_id,
                                        where="direct message", person=False)
            self._receive.log_drop(logger, "whatsapp", record, warn=self._DROP_WARNINGS)
            return
        # Every message a person sent is counted, whatever becomes of it.
        self._receive.message(m.message_id, author_id=m.from_id, where="direct message")
        if self._phone_number_id and m.phone_number_id and m.phone_number_id != self._phone_number_id:
            self._note_drop("not_this_number", m)
            return
        refusal = self.sender_refusal(m.from_id)
        if refusal:
            self._refuse(refusal, m)
            return
        self._spawn(self._mark_read(m.message_id))
        if m.button_id:
            await self._button(m)
            return
        if not m.readable or m.kind in ("interactive", "button"):
            self._note_drop("unsupported_type", m)
            return
        attachments: list[Attachment] = []
        if m.media_id:
            try:
                data, mime = await self._client.download_media(m.media_id)
            except _META_FAILURES as exc:
                logger.warning("[whatsapp] A %s could not be downloaded: %s", m.kind, exc)
                self._note_drop("media_failed", m)
                await self._say(m.from_id, m.text,
                                "I could not download that file from WhatsApp. Please send it again.",
                                "تعذّر عليّ تنزيل ذلك الملف من واتساب. أرسله مرة أخرى من فضلك.")
                return
            kind = {"image": "image", "audio": "audio"}.get(m.kind, "file")
            attachments.append(Attachment(
                kind=kind, mime=mime or m.mime, filename=m.filename or _default_name(m.kind, mime or m.mime),
                data=data, meta={"whatsapp_media_id": m.media_id},
            ))
        msg = IncomingMessage(
            platform="whatsapp",
            sender_id=f"whatsapp:{m.from_id}",
            text=m.text,
            attachments=attachments,
            context_metadata={
                "chat_id": m.from_id,
                "user_id": m.from_id,
                "username": m.name or m.from_id,
                "message_id": m.message_id,
                "chat_type": "private",
                "receive_key": m.message_id,
            },
        )
        if m.kind == "audio":
            # The shared speech-to-text path (as Discord and Slack): with voice
            # off or a failed transcription the note is handed on as a file,
            # never dropped.
            from kazma_gateway.adapters.voice_helpers import transcribe_inbound_message

            msg = await transcribe_inbound_message(msg)
        self._enqueue(msg, m)

    def _enqueue(self, msg: IncomingMessage, m: WaInbound) -> None:
        if self._queue is None:
            self._note_drop("processing_failed", m)
            return
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            self._note_drop("queue_full", m)
            return
        self._receive.note_passed_on(m.message_id)
        logger.info("[whatsapp] Enqueued from %s: %.80s", m.name or m.from_id, msg.text)

    async def _button(self, m: WaInbound) -> None:
        """A press on one of Kazma's buttons: the callback scheme every chat app shares."""
        from kazma_gateway.adapters.callback_store import decode_callback_data
        from kazma_gateway.adapters.platform_callbacks import parse_callback_data

        data = decode_callback_data(m.button_id)
        action = parse_callback_data(data)
        actor = f"whatsapp:{m.from_id}"
        if action.kind in ("hitl", "swarm", "run"):
            from kazma_gateway.allowlists import is_gateway_admin

            if not await asyncio.to_thread(is_gateway_admin, actor, "whatsapp"):
                logger.warning("[whatsapp] Non-admin %s tried to answer a %s approval", m.from_id, action.kind)
                await self._say(m.from_id, "", "Not authorized: answering an approval needs the owner.",
                                "غير مصرّح: الرد على طلب الموافقة للمالك فقط.")
                return
        if action.kind == "swarm":
            from kazma_core.swarm.shared_approvals import decide_from_button

            task_id = await asyncio.to_thread(
                decide_from_button, decode_callback_data(action.swarm_data or data), actor=actor, platform="whatsapp",
            )
            if task_id is not None:
                logger.info("[whatsapp] Swarm approval answered: %s by %s", task_id, actor)
            return
        if not action.text:
            logger.debug("[whatsapp] A button Kazma does not know was pressed")
            return
        self._enqueue(IncomingMessage(
            platform="whatsapp",
            sender_id=actor,
            text=action.text,
            context_metadata={
                "chat_id": m.from_id, "user_id": m.from_id, "username": m.name or m.from_id,
                "message_id": m.message_id, "chat_type": "private", "receive_key": m.message_id,
            },
        ), m)

    async def _mark_read(self, message_id: str) -> None:
        try:
            await self._client.mark_read(message_id)
        except _META_FAILURES as exc:  # a read receipt is a courtesy
            logger.debug("[whatsapp] Could not mark a message read: %s", exc)

    async def _say(self, to: str, said: str, english: str, arabic: str) -> None:
        """A short note to *to*, in the language they wrote in."""
        from kazma_core.language_lock import in_their_language

        try:
            await self._client.send_text(to, in_their_language(said, english, arabic))
        except _META_FAILURES as exc:  # the note is best effort; the drop is recorded
            logger.warning("[whatsapp] Could not send a note to %s: %s", to, exc)

    # ── sending ───────────────────────────────────────────────────────

    async def send(self, outbound: OutboundMessage) -> bool:
        """Text (in WhatsApp's marks, split at 4,096 characters), then files,
        then any buttons. Each part is reported to the chat outbox
        (``send_report``); the first that fails ends the send."""
        from kazma_gateway.send_report import part_delivered, part_failed

        ctx = outbound.context_metadata or {}
        to = wa_digits(outbound.target_id.split(":", 1)[1] if ":" in outbound.target_id else ctx.get("chat_id"))
        if not to:
            logger.warning("[whatsapp] A reply with no number to send to: %s", outbound.target_id)
            return False
        try:
            for part in split_for_whatsapp(md_to_whatsapp(outbound.text or "")):
                await self._client.send_text(to, part)
                part_delivered()
            for attachment in outbound.attachments:
                await self._send_attachment(to, attachment)
                part_delivered()
            buttons = ctx.get(BUTTONS_KEY)
            if isinstance(buttons, dict) and buttons.get("buttons"):
                await self._send_buttons(to, buttons)
                part_delivered()
        except _META_FAILURES as exc:  # reported and said; the outbox decides
            part_failed(exc)
            logger.warning("[whatsapp] Sending to %s failed: %s", to, exc)
            return False
        return True

    async def _send_attachment(self, to: str, attachment: Attachment) -> None:
        if attachment.data is None:
            raise WhatsAppError(0, None, "an attachment with no bytes cannot be sent")
        mime = attachment.mime or "application/octet-stream"
        kind = "image" if mime.startswith("image/") and attachment.kind == "image" else (
            "audio" if mime.startswith("audio/") else "document")
        media_id = await self._client.upload_media(attachment.data, mime, attachment.filename or "file")
        await self._client.send_media(to, kind, media_id, filename=attachment.filename or "")

    async def _send_buttons(self, to: str, spec: dict[str, Any]) -> None:
        prompt = str(spec.get("prompt") or "Choose:")
        buttons = [(str(b[0]), str(b[1])) for b in spec.get("buttons") or [] if len(b) == 2]
        if len(buttons) <= 3:
            await self._client.send_buttons(to, prompt, buttons)
        else:
            await self._client.send_list(to, prompt, str(spec.get("list_button") or "Choose"), buttons)

    # ── buttons Kazma sends ───────────────────────────────────────────

    @staticmethod
    def build_approval_keyboard(request_id: str, **labels: Any) -> dict[str, Any]:
        """Approve / Deny (/ Approve for task) for one approval card.

        A public action names itself on the approving button and has no task
        approval (``task=False``), as on every chat app (2026-10-16)."""
        from kazma_gateway.adapters.callback_store import encode_callback_data

        approve = str(labels.get("approve_label") or "Approve")[:20]
        buttons = [
            (encode_callback_data(f"hitl:approve:{request_id}", max_bytes=256), approve),
            (encode_callback_data(f"hitl:deny:{request_id}", max_bytes=256), "Deny"),
        ]
        if labels.get("task", True):
            buttons.append((encode_callback_data(f"hitl:approve_task:{request_id}", max_bytes=256), "Approve for task"))
        return {"prompt": "Approve or deny the request above?", "buttons": buttons}

    @staticmethod
    def build_semantic_markup(request_id: str, question: str, options: list[dict[str, Any]]) -> dict[str, Any]:
        """One button per option of a clarify/confirm question (a list past three)."""
        from kazma_gateway.adapters.callback_store import encode_callback_data

        buttons = [
            (encode_callback_data(f"hitl:opt:{o.get('id', '')}:{request_id}", max_bytes=256),
             str(o.get("label") or o.get("id") or "")[:24])
            for o in (options or []) if o.get("id")
        ]
        if not buttons:
            return WhatsAppAdapter.build_approval_keyboard(request_id)
        return {"prompt": (question or "Choose an option")[:1024], "buttons": buttons, "list_button": "Options"}


def _default_name(kind: str, mime: str) -> str:
    ext = (mime.split("/", 1)[1].split(";", 1)[0] if "/" in mime else "bin") or "bin"
    return {"image": f"photo.{ext}", "audio": f"voice.{'ogg' if 'ogg' in ext else ext}"}.get(kind, f"document.{ext}")


# ── the webhook route ─────────────────────────────────────────────────────

_WARN_EVERY_S = 600.0
_warned: dict[str, tuple[float | None, int]] = {}


def _warn_refused(reason: str, message: str) -> None:
    """One WARNING per reason per ten minutes; the rest counted at DEBUG."""
    now = time.monotonic()
    last, held = _warned.get(reason, (None, 0))
    if last is not None and now - last < _WARN_EVERY_S:
        _warned[reason] = (last, held + 1)
        logger.debug("[whatsapp-webhook] refused again (%s)", reason)
        return
    _warned[reason] = (now, 0)
    logger.warning("%s%s", message, f" ({held} more refused since the last warning)" if held else "")


async def _bounded_body(request: Request, limit: int = MAX_WEBHOOK_BYTES) -> bytes | None:
    """The request's body, or None past *limit* (declared or streamed)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        return None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def whatsapp_webhook_router(resolve: Callable[[], WhatsAppAdapter | None]) -> Any:
    """Meta's webhook: GET verifies the subscription, POST delivers messages.

    *resolve* answers which adapter takes the call NOW (a saved setting
    replaces it at run time; none: 503). Mounted at :data:`WEBHOOK_PATH`.
    """
    router = APIRouter(tags=["whatsapp-webhook"])

    @router.get("")
    def verify(request: Request) -> Any:
        adapter = resolve()
        if adapter is None:
            return JSONResponse({"error": "WhatsApp is not connected"}, status_code=503)
        params = request.query_params
        challenge = adapter.verify_subscription(
            params.get("hub.mode", ""), params.get("hub.verify_token", ""), params.get("hub.challenge", ""),
        )
        if challenge is None:
            _warn_refused("bad_verify_token", "[whatsapp-webhook] A verification call had the wrong verify token")
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        return PlainTextResponse(challenge)

    @router.post("")
    async def receive(request: Request) -> JSONResponse:
        adapter = resolve()
        if adapter is None:
            return JSONResponse({"error": "WhatsApp is not connected"}, status_code=503)
        if not adapter._app_secret:
            _warn_refused(
                "no_secret",
                "[whatsapp-webhook] No app secret saved: every call is refused. Save the Meta app's "
                "App secret in Settings > Platform Connectors > WhatsApp.",
            )
            return JSONResponse({"error": "App secret not configured"}, status_code=503)
        body = await _bounded_body(request)
        if body is None:
            return JSONResponse({"error": "Too large"}, status_code=413)
        if not signature_ok(adapter._app_secret, body, request.headers.get("X-Hub-Signature-256")):
            _warn_refused("bad_signature", "[whatsapp-webhook] A call without Meta's valid signature was refused")
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        import json

        try:
            payload = json.loads(body)
        except ValueError:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "Invalid payload"}, status_code=400)
        if adapter._queue is None:
            return JSONResponse({"error": "Gateway not ready"}, status_code=503)
        # Meta needs a 2xx at once or it resends; each message's fate is in
        # the receive record and the log.
        count = adapter.accept(payload)
        return JSONResponse({"status": "accepted", "messages": count})

    @router.get("/health")
    def health() -> dict[str, Any]:
        from kazma_core.diagnostic_scope import read_only_diagnostic

        with read_only_diagnostic("whatsapp webhook /health"):
            adapter = resolve()
            return {
                "status": "ok" if adapter is not None else "not_connected",
                "webhook_ready": bool(adapter is not None and adapter.webhook_ready),
            }

    return router
