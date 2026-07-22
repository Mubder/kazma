"""Gmail REST API backend (OAuth access token)."""

from __future__ import annotations

import base64
import logging
from email.message import EmailMessage as StdEmailMessage
from typing import Any
from urllib.parse import quote

import httpx

from kazma_skills.native.email_manager.models import (
    CategorizeRequest,
    EmailMessage,
    ListQuery,
    SendRequest,
    SendResult,
)

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


class GmailApiBackend:
    """Full mailbox ops via Gmail API — preferred for Workspace OAuth."""

    name = "gmail_oauth"

    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str = "",
        client_id: str = "",
        client_secret: str = "",
        email_address: str = "",
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.email_address = email_address

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = path if path.startswith("http") else f"{GMAIL_API}{path}"
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.request(
                method, url, headers=self._headers(), json=json_body, params=params
            )
            if r.status_code == 401 and self.refresh_token:
                await self._refresh()
                r = await client.request(
                    method, url, headers=self._headers(), json=json_body, params=params
                )
            if r.status_code >= 400:
                raise RuntimeError(f"Gmail API {method} {path} → {r.status_code}: {r.text[:300]}")
            if r.status_code == 204 or not r.content:
                return {}
            return r.json()

    async def _refresh(self) -> None:
        from kazma_skills.native.email_manager.oauth_gmail import refresh_gmail_access_token

        access, refresh = await refresh_gmail_access_token(
            self.refresh_token,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
        self.access_token = access
        self.refresh_token = refresh

    def _map(self, meta: dict[str, Any], body: str = "") -> EmailMessage:
        headers = {h["name"].lower(): h["value"] for h in (meta.get("payload") or {}).get("headers") or []}
        label_ids = meta.get("labelIds") or []
        return EmailMessage(
            id=str(meta.get("id") or ""),
            subject=headers.get("subject", meta.get("snippet", "")[:80]),
            from_addr=headers.get("from", ""),
            to_addrs=[headers.get("to", "")] if headers.get("to") else [],
            cc_addrs=[headers.get("cc", "")] if headers.get("cc") else [],
            date=headers.get("date", ""),
            body=body or meta.get("snippet") or "",
            snippet=(meta.get("snippet") or "")[:120],
            unread="UNREAD" in label_ids,
            starred="STARRED" in label_ids,
            labels=list(label_ids),
            folder="INBOX" if "INBOX" in label_ids else (label_ids[0] if label_ids else "INBOX"),
            thread_id=str(meta.get("threadId") or ""),
            provider="gmail_oauth",
        )

    def _extract_body(self, payload: dict[str, Any]) -> str:
        if not payload:
            return ""
        body = payload.get("body") or {}
        data = body.get("data")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception:
                pass
        for part in payload.get("parts") or []:
            mime = (part.get("mimeType") or "").lower()
            if mime == "text/plain":
                d = (part.get("body") or {}).get("data")
                if d:
                    try:
                        return base64.urlsafe_b64decode(d + "==").decode("utf-8", errors="replace")
                    except Exception:
                        pass
        for part in payload.get("parts") or []:
            mime = (part.get("mimeType") or "").lower()
            if mime == "text/html":
                d = (part.get("body") or {}).get("data")
                if d:
                    try:
                        return base64.urlsafe_b64decode(d + "==").decode("utf-8", errors="replace")
                    except Exception:
                        pass
            nested = self._extract_body(part)
            if nested:
                return nested
        return ""

    async def list_messages(self, query: ListQuery) -> list[EmailMessage]:
        limit = max(1, min(50, int(query.limit or 20)))
        q_parts = []
        folder = (query.folder or "INBOX").strip()
        if folder.upper() == "INBOX" or folder.lower() == "inbox":
            q_parts.append("in:inbox")
        elif folder.lower() in ("sent", "[gmail]/sent mail"):
            q_parts.append("in:sent")
        elif folder.lower() in ("trash",):
            q_parts.append("in:trash")
        elif folder.lower() in ("drafts",):
            q_parts.append("in:drafts")
        elif folder.lower() in ("spam",):
            q_parts.append("in:spam")
        if query.unread_only:
            q_parts.append("is:unread")
        if query.query:
            q_parts.append(query.query.strip())
        params: dict[str, Any] = {"maxResults": limit}
        if q_parts:
            params["q"] = " ".join(q_parts)
        # offset via pageToken not implemented simply — skip for v1
        data = await self._request("GET", "/users/me/messages", params=params)
        ids = [m["id"] for m in (data.get("messages") or []) if m.get("id")]
        # apply offset client-side on first page only
        offset = max(0, int(query.offset or 0))
        ids = ids[offset : offset + limit]
        out: list[EmailMessage] = []
        for mid in ids:
            meta = await self._request(
                "GET",
                f"/users/me/messages/{quote(mid, safe='')}",
                params={"format": "metadata", "metadataHeaders": "From,To,Subject,Date"},
            )
            out.append(self._map(meta))
        return out

    async def get_message(self, message_id: str) -> EmailMessage:
        meta = await self._request(
            "GET",
            f"/users/me/messages/{quote(message_id, safe='')}",
            params={"format": "full"},
        )
        body = self._extract_body(meta.get("payload") or {})
        return self._map(meta, body=body)

    async def send(self, req: SendRequest) -> SendResult:
        to_list = req.to if isinstance(req.to, list) else [str(req.to)]
        subject = req.subject
        body = req.body
        msg = StdEmailMessage()
        msg["To"] = ", ".join(to_list)
        if req.cc:
            msg["Cc"] = ", ".join(req.cc if isinstance(req.cc, list) else [req.cc])
        if self.email_address:
            msg["From"] = self.email_address
        if req.action == "reply" and req.message_id:
            try:
                orig = await self.get_message(req.message_id)
                if not subject.lower().startswith("re:"):
                    subject = f"Re: {orig.subject}"
            except Exception:
                pass
        if req.action == "forward" and req.message_id:
            try:
                orig = await self.get_message(req.message_id)
                if not subject.lower().startswith("fwd:"):
                    subject = f"Fwd: {orig.subject}"
                body = f"{req.body}\n\n---------- Forwarded ----------\n{orig.body}"
            except Exception:
                pass
        msg["Subject"] = subject
        if req.body_format == "html":
            msg.set_content(body, subtype="html")
        else:
            msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
        if req.action == "draft":
            created = await self._request(
                "POST", "/users/me/drafts", json_body={"message": {"raw": raw}}
            )
            mid = ((created.get("message") or {}).get("id")) or created.get("id") or ""
            return SendResult(ok=True, message_id=str(mid), detail="Draft saved (Gmail API)", draft=True)
        sent = await self._request("POST", "/users/me/messages/send", json_body={"raw": raw})
        return SendResult(
            ok=True,
            message_id=str(sent.get("id") or ""),
            detail=f"Sent via Gmail API to {', '.join(to_list)}",
        )

    async def delete(self, message_id: str, permanent: bool = False) -> None:
        if permanent:
            await self._request("DELETE", f"/users/me/messages/{quote(message_id, safe='')}")
        else:
            await self._request("POST", f"/users/me/messages/{quote(message_id, safe='')}/trash")

    async def categorize(self, req: CategorizeRequest) -> None:
        add: list[str] = []
        remove: list[str] = []
        if req.mark_read is True:
            remove.append("UNREAD")
        elif req.mark_read is False:
            add.append("UNREAD")
        if req.star is True:
            add.append("STARRED")
        elif req.star is False:
            remove.append("STARRED")
        for lab in req.add_labels or []:
            add.append(lab)
        for lab in req.remove_labels or []:
            remove.append(lab)
        body: dict[str, Any] = {}
        if add:
            body["addLabelIds"] = add
        if remove:
            body["removeLabelIds"] = remove
        if body:
            await self._request(
                "POST",
                f"/users/me/messages/{quote(req.message_id, safe='')}/modify",
                json_body=body,
            )
        # move_to_folder as label name best-effort
        if req.move_to_folder:
            await self._request(
                "POST",
                f"/users/me/messages/{quote(req.message_id, safe='')}/modify",
                json_body={"addLabelIds": [req.move_to_folder]},
            )
