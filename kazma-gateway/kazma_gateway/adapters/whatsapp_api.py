"""Meta's WhatsApp Cloud API, as Kazma uses it (2026-11-14).

One client per adapter: send text, buttons and files, download what a person
sent, mark a message read, and ask about the business phone number (the
Test). Every call goes to ``graph.facebook.com`` with the access token as a
Bearer header -- never in a URL -- through one ``httpx`` client that follows
no redirect (a redirect would carry the token to another host).

A send is tried again only after Meta's "slow down" (HTTP 429). A timeout
or a dropped connection is not tried again: the message may have arrived,
and the chat outbox (``kazma_gateway.chat_outbox``) decides what a failed
part means from ``send_report``. A read (the phone number, a media address)
is tried again after a 5xx too.

Meta's error codes that tell the owner what to do are worded by
:func:`explain`; the rest are shown as Meta wrote them, without the token.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from kazma_core.http_tls import shared_ssl_context

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_API_VERSION", "GRAPH", "WhatsAppClient", "WhatsAppError", "explain"]

GRAPH = "https://graph.facebook.com"
#: The Graph API version Kazma calls (a setting: connectors.whatsapp.api_version).
DEFAULT_API_VERSION = "v23.0"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_RETRIES = 3
#: The most a download may be (WhatsApp documents go to 100 MB; Kazma reads less).
MAX_DOWNLOAD_BYTES = 19 * 1024 * 1024

#: What some of Meta's error codes mean for the owner.
_EXPLAINED: dict[int, str] = {
    190: "the access token is not valid any more (a temporary token lasts 24 hours): make a new one, "
         "or a permanent one from a system user in Meta Business settings",
    100: "Meta refused a parameter: check the Phone number ID",
    131030: "this number is not in the test number's list of recipients: add it in the Meta app "
            "under WhatsApp > API Setup",
    131047: "more than 24 hours passed since this person's last message, so WhatsApp allows only an "
            "approved message template now; the next message they send reopens the window",
    131026: "WhatsApp could not deliver to this number (it may not use WhatsApp)",
    131056: "too many messages to this person in a short time; WhatsApp asks to slow down",
    130429: "the business number's message rate limit was reached",
    368: "Meta temporarily blocked this account for policy reasons",
    80007: "the WhatsApp Business Account's rate limit was reached",
}


@dataclass
class WhatsAppError(Exception):
    """A call Meta refused or did not answer."""

    status: int
    code: int | None
    message: str

    def __str__(self) -> str:
        return explain(self.code, self.message, self.status)


def explain(code: int | None, message: str = "", status: int = 0) -> str:
    """The owner's words for a Meta error."""
    if code in _EXPLAINED:
        return f"{_EXPLAINED[code]} (Meta error {code})"
    said = message.strip() or "no answer"
    return f"Meta answered HTTP {status}: {said}" + (f" (error {code})" if code else "")


def _error_of(resp: httpx.Response) -> WhatsAppError:
    code: int | None = None
    message = ""
    try:
        body = resp.json()
        err = body.get("error") or {}
        code = int(err.get("code")) if err.get("code") is not None else None
        message = str(err.get("error_user_msg") or err.get("message") or "")
    except (ValueError, TypeError, AttributeError):
        message = resp.text[:200]
    return WhatsAppError(resp.status_code, code, message[:300])


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    try:
        return min(30.0, max(0.5, float(resp.headers.get("Retry-After") or 0)))
    except ValueError:
        return min(30.0, 1.0 * (2 ** attempt))


class WhatsAppClient:
    """Calls for one business phone number."""

    def __init__(
        self,
        token: str,
        phone_number_id: str,
        *,
        api_version: str = DEFAULT_API_VERSION,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self.phone_number_id = str(phone_number_id or "").strip()
        self._version = (api_version or DEFAULT_API_VERSION).strip()
        self._http = http

    # ── plumbing ──────────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=False, verify=shared_ssl_context(),
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _url(self, path: str) -> str:
        return f"{GRAPH}/{self._version}/{path.lstrip('/')}"

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _request(self, method: str, path: str, *, read: bool, **kwargs: Any) -> dict[str, Any]:
        """One call; 429 (and a 5xx on a read) tried again with a backoff."""
        for attempt in range(_RETRIES):
            resp = await self._client().request(method, self._url(path), headers=self._auth(), **kwargs)
            if resp.status_code < 400:
                try:
                    data = resp.json()
                except ValueError:
                    data = {}
                return data if isinstance(data, dict) else {}
            retry = resp.status_code == 429 or (read and resp.status_code >= 500)
            if retry and attempt + 1 < _RETRIES:
                await asyncio.sleep(_retry_after(resp, attempt))
                continue
            raise _error_of(resp)
        raise WhatsAppError(0, None, "no answer")  # pragma: no cover - the loop returns or raises

    async def _send(self, payload: dict[str, Any]) -> str:
        body = {"messaging_product": "whatsapp", "recipient_type": "individual", **payload}
        data = await self._request("POST", f"{self.phone_number_id}/messages", read=False, json=body)
        messages = data.get("messages") or [{}]
        return str((messages[0] or {}).get("id") or "")

    # ── reading ───────────────────────────────────────────────────────

    async def phone_number(self) -> dict[str, Any]:
        """The business phone number as Meta knows it (display number, verified name, quality)."""
        return await self._request(
            "GET", self.phone_number_id, read=True,
            params={"fields": "display_phone_number,verified_name,quality_rating,code_verification_status"},
        )

    async def download_media(self, media_id: str, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> tuple[bytes, str]:
        """A file a person sent: its bytes and type. Refused past *max_bytes*."""
        info = await self._request("GET", media_id, read=True)
        url = str(info.get("url") or "")
        if not url.startswith("https://"):
            raise WhatsAppError(0, None, "Meta gave no download address for this file")
        size = int(info.get("file_size") or 0)
        if size and size > max_bytes:
            raise WhatsAppError(0, None, f"the file is {size} bytes, more than Kazma reads ({max_bytes})")
        async with self._client().stream("GET", url, headers=self._auth()) as resp:
            if resp.status_code >= 400:
                raise WhatsAppError(resp.status_code, None, "the file could not be downloaded")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise WhatsAppError(0, None, f"the file is more than Kazma reads ({max_bytes} bytes)")
                chunks.append(chunk)
        return b"".join(chunks), str(info.get("mime_type") or "")

    # ── sending ───────────────────────────────────────────────────────

    async def send_text(self, to: str, body: str) -> str:
        """A text message; links never get a preview (a preview fetches the page)."""
        return await self._send({"to": to, "type": "text", "text": {"body": body, "preview_url": False}})

    async def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> str:
        """Up to three reply buttons under *body* (1,024 characters at most)."""
        return await self._send({
            "to": to, "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body[:1024]},
                "action": {"buttons": [
                    {"type": "reply", "reply": {"id": bid[:256], "title": title[:20]}} for bid, title in buttons[:3]
                ]},
            },
        })

    async def send_list(self, to: str, body: str, button: str, rows: list[tuple[str, str]]) -> str:
        """A list of up to ten choices, opened by one button."""
        return await self._send({
            "to": to, "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body[:1024]},
                "action": {"button": button[:20], "sections": [{
                    "title": "Choose",
                    "rows": [{"id": rid[:200], "title": title[:24]} for rid, title in rows[:10]],
                }]},
            },
        })

    async def upload_media(self, data: bytes, mime: str, filename: str) -> str:
        """A file uploaded for sending; its media id."""
        result = await self._request(
            "POST", f"{self.phone_number_id}/media", read=False,
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename or "file", data, mime)},
        )
        media_id = str(result.get("id") or "")
        if not media_id:
            raise WhatsAppError(0, None, "Meta took the file but gave no media id")
        return media_id

    async def send_media(self, to: str, kind: str, media_id: str, *, caption: str = "", filename: str = "") -> str:
        """An uploaded image, document or audio file."""
        media: dict[str, Any] = {"id": media_id}
        if caption and kind in ("image", "document"):
            media["caption"] = caption[:1024]
        if filename and kind == "document":
            media["filename"] = filename[:240]
        return await self._send({"to": to, "type": kind, kind: media})

    async def mark_read(self, message_id: str) -> None:
        """Two blue ticks on the person's message: Kazma has it."""
        await self._request(
            "POST", f"{self.phone_number_id}/messages", read=False,
            json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id},
        )
