"""Microsoft Graph mail backend (Outlook / M365)."""

from __future__ import annotations

import logging
import os
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

GRAPH = "https://graph.microsoft.com/v1.0"


# Well-known folder display names → Graph well-known names
_WELL_KNOWN_FOLDERS = {
    "inbox": "inbox",
    "sent": "sentitems",
    "sent items": "sentitems",
    "sentitems": "sentitems",
    "drafts": "drafts",
    "trash": "deleteditems",
    "deleted": "deleteditems",
    "deleteditems": "deleteditems",
    "junk": "junkemail",
    "spam": "junkemail",
    "archive": "archive",
}


class MicrosoftGraphBackend:
    name = "microsoft_graph"

    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str = "",
        client_id: str = "",
        client_secret: str = "",
        tenant_id: str = "common",
        account_alias: str = "",
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id or "common"
        self.account_alias = account_alias or ""
        if account_alias:
            self.name = f"microsoft_graph:{account_alias}"

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
        url = path if path.startswith("http") else f"{GRAPH}{path}"
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.request(
                method,
                url,
                headers=self._headers(),
                json=json_body,
                params=params,
            )
            if r.status_code == 401 and self.refresh_token and self.client_id:
                await self._refresh()
                r = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_body,
                    params=params,
                )
            if r.status_code >= 400:
                raise RuntimeError(f"Graph {method} {path} → {r.status_code}: {r.text[:300]}")
            if r.status_code == 204 or not r.content:
                return {}
            return r.json()

    async def _refresh(self) -> None:
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "scope": "https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send offline_access",
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(token_url, data=data)
            if r.status_code >= 400:
                raise RuntimeError(f"Token refresh failed: {r.status_code} {r.text[:200]}")
            payload = r.json()
            self.access_token = payload.get("access_token") or self.access_token
            if payload.get("refresh_token"):
                self.refresh_token = payload["refresh_token"]
            self._persist_tokens()

    def _persist_tokens(self) -> None:
        """Write refreshed tokens to env + vault (and per-account keys if aliased)."""
        import os

        try:
            from kazma_skills.native.email_manager.credentials import vault_store

            if self.access_token and self.access_token != "pending_refresh":
                os.environ["EMAIL_MS_ACCESS_TOKEN"] = self.access_token
                vault_store("email.microsoft.access_token", self.access_token)
                if self.account_alias:
                    vault_store(
                        f"email.account.{self.account_alias}.access_token",
                        self.access_token,
                    )
            if self.refresh_token:
                os.environ["EMAIL_MS_REFRESH_TOKEN"] = self.refresh_token
                vault_store("email.microsoft.refresh_token", self.refresh_token)
                if self.account_alias:
                    vault_store(
                        f"email.account.{self.account_alias}.refresh_token",
                        self.refresh_token,
                    )
        except Exception as exc:
            logger.debug("[graph] token persist skipped: %s", exc)

    def _folder_path(self, folder: str) -> str:
        key = (folder or "INBOX").strip().lower()
        known = _WELL_KNOWN_FOLDERS.get(key)
        if known:
            return f"/me/mailFolders/{known}/messages"
        if key in ("inbox", "mail", ""):
            return "/me/messages"
        # Custom folder name — Graph expects folder id; try displayName filter via well-known path
        return f"/me/mailFolders/{quote(folder, safe='')}/messages"

    def _map_message(self, item: dict[str, Any], folder: str = "INBOX") -> EmailMessage:
        from_obj = (item.get("from") or {}).get("emailAddress") or {}
        to_list = [
            (r.get("emailAddress") or {}).get("address", "")
            for r in (item.get("toRecipients") or [])
        ]
        cc_list = [
            (r.get("emailAddress") or {}).get("address", "")
            for r in (item.get("ccRecipients") or [])
        ]
        body_obj = item.get("body") or {}
        body = body_obj.get("content") or item.get("bodyPreview") or ""
        return EmailMessage(
            id=str(item.get("id") or ""),
            subject=str(item.get("subject") or ""),
            from_addr=str(from_obj.get("address") or from_obj.get("name") or ""),
            to_addrs=[a for a in to_list if a],
            cc_addrs=[a for a in cc_list if a],
            date=str(item.get("receivedDateTime") or item.get("sentDateTime") or ""),
            body=body,
            snippet=str(item.get("bodyPreview") or body[:120]).replace("\n", " "),
            unread=not bool(item.get("isRead", True)),
            starred=bool((item.get("flag") or {}).get("flagStatus") == "flagged"),
            labels=[folder],
            folder=folder,
            thread_id=str(item.get("conversationId") or ""),
            provider="microsoft_graph",
        )

    async def list_messages(self, query: ListQuery) -> list[EmailMessage]:
        limit = max(1, min(50, int(query.limit or 20)))
        offset = max(0, int(query.offset or 0))
        params: dict[str, Any] = {
            "$top": limit,
            "$skip": offset,
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,isRead,flag,conversationId,categories",
        }
        folder = (query.folder or "INBOX").strip()
        path = self._folder_path(folder)
        if query.unread_only:
            params["$filter"] = "isRead eq false"
        if query.query:
            q = query.query.replace("'", "''")
            filt = params.get("$filter", "")
            search_f = f"contains(subject,'{q}')"
            params["$filter"] = f"{filt} and {search_f}" if filt else search_f
        data = await self._request("GET", path, params=params)
        items = data.get("value") or []
        out = []
        for it in items:
            m = self._map_message(it, folder=folder)
            cats = it.get("categories") or []
            if cats:
                m.labels = list(dict.fromkeys([*m.labels, *[str(c) for c in cats]]))
            out.append(m)
        return out

    async def get_message(self, message_id: str) -> EmailMessage:
        data = await self._request(
            "GET",
            f"/me/messages/{quote(message_id, safe='')}",
            params={
                "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,bodyPreview,isRead,flag,conversationId",
            },
        )
        return self._map_message(data)

    async def send(self, req: SendRequest) -> SendResult:
        to_list = req.to if isinstance(req.to, list) else [str(req.to)]
        to_recipients = [{"emailAddress": {"address": a}} for a in to_list]
        cc_recipients = [
            {"emailAddress": {"address": a}}
            for a in (req.cc if isinstance(req.cc, list) else ([req.cc] if req.cc else []))
        ]
        content_type = "HTML" if req.body_format == "html" else "Text"
        subject = req.subject
        body = req.body

        if req.action == "reply" and req.message_id:
            await self._request(
                "POST",
                f"/me/messages/{quote(req.message_id, safe='')}/reply",
                json_body={"comment": body},
            )
            return SendResult(ok=True, message_id=req.message_id, detail="Reply sent via Graph")

        if req.action == "forward" and req.message_id:
            await self._request(
                "POST",
                f"/me/messages/{quote(req.message_id, safe='')}/forward",
                json_body={
                    "comment": body,
                    "toRecipients": to_recipients,
                },
            )
            return SendResult(ok=True, message_id=req.message_id, detail="Forwarded via Graph")

        message = {
            "subject": subject,
            "body": {"contentType": content_type, "content": body},
            "toRecipients": to_recipients,
            "ccRecipients": cc_recipients,
        }

        if req.action == "draft":
            created = await self._request("POST", "/me/messages", json_body=message)
            return SendResult(
                ok=True,
                message_id=str(created.get("id") or ""),
                detail="Draft created in Outlook",
                draft=True,
            )

        await self._request(
            "POST",
            "/me/sendMail",
            json_body={"message": message, "saveToSentItems": True},
        )
        return SendResult(ok=True, detail=f"Sent via Graph to {', '.join(to_list)}")

    async def delete(self, message_id: str, permanent: bool = False) -> None:
        if permanent:
            await self._request("DELETE", f"/me/messages/{quote(message_id, safe='')}")
        else:
            # Move to deleted items
            await self._request(
                "POST",
                f"/me/messages/{quote(message_id, safe='')}/move",
                json_body={"destinationId": "deleteditems"},
            )

    async def categorize(self, req: CategorizeRequest) -> None:
        patch: dict[str, Any] = {}
        if req.mark_read is True:
            patch["isRead"] = True
        elif req.mark_read is False:
            patch["isRead"] = False
        if req.star is True:
            patch["flag"] = {"flagStatus": "flagged"}
        elif req.star is False:
            patch["flag"] = {"flagStatus": "notFlagged"}
        if req.add_labels:
            patch["categories"] = list(req.add_labels)
        if patch:
            await self._request(
                "PATCH",
                f"/me/messages/{quote(req.message_id, safe='')}",
                json_body=patch,
            )
        if req.move_to_folder:
            await self._request(
                "POST",
                f"/me/messages/{quote(req.message_id, safe='')}/move",
                json_body={"destinationId": req.move_to_folder},
            )


def graph_token_from_env() -> str:
    return (
        (os.environ.get("EMAIL_MS_ACCESS_TOKEN") or "").strip()
        or (os.environ.get("MICROSOFT_GRAPH_TOKEN") or "").strip()
    )
