"""Gmail / generic IMAP+SMTP backend."""

from __future__ import annotations

import email
import email.utils
import imaplib
import logging
import smtplib
import ssl
import uuid
from email.message import EmailMessage as StdEmailMessage
from typing import Any

from kazma_skills.native.email_manager.models import (
    CategorizeRequest,
    EmailMessage,
    ListQuery,
    SendRequest,
    SendResult,
)

logger = logging.getLogger(__name__)


class ImapSmtpBackend:
    """IMAP list/get/delete/flags + SMTP send.

    Used for Gmail (app password) and generic IMAP/SMTP hosts.
    """

    def __init__(
        self,
        *,
        name: str,
        address: str,
        password: str,
        imap_host: str,
        imap_port: int = 993,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_starttls: bool = True,
    ) -> None:
        self.name = name
        self.address = address
        self.password = password
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.smtp_host = smtp_host or imap_host.replace("imap", "smtp")
        self.smtp_port = smtp_port
        self.smtp_starttls = smtp_starttls

    def _imap(self) -> imaplib.IMAP4_SSL:
        M = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        M.login(self.address, self.password)
        return M

    def _parse_msg(self, raw: bytes, uid: str, folder: str) -> EmailMessage:
        msg = email.message_from_bytes(raw)
        subject = str(msg.get("Subject", ""))
        from_addr = str(msg.get("From", ""))
        to_raw = str(msg.get("To", ""))
        cc_raw = str(msg.get("Cc", ""))
        date = str(msg.get("Date", ""))
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    try:
                        body = part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", errors="replace"
                        )
                    except Exception:
                        body = str(part.get_payload())
                    break
            if not body:
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        try:
                            body = part.get_payload(decode=True).decode(
                                part.get_content_charset() or "utf-8", errors="replace"
                            )
                        except Exception:
                            pass
                        break
        else:
            try:
                payload = msg.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
                else:
                    body = str(msg.get_payload())
            except Exception:
                body = str(msg.get_payload())
        to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
        cc_addrs = [a.strip() for a in cc_raw.split(",") if a.strip()]
        return EmailMessage(
            id=uid,
            subject=subject,
            from_addr=from_addr,
            to_addrs=to_addrs,
            cc_addrs=cc_addrs,
            date=date,
            body=body,
            snippet=(body or "")[:120].replace("\n", " "),
            unread=True,
            starred=False,
            labels=[folder],
            folder=folder,
            provider=self.name,
        )

    async def list_messages(self, query: ListQuery) -> list[EmailMessage]:
        folder = query.folder or "INBOX"
        if folder.lower() == "inbox":
            folder = "INBOX"
        limit = max(1, min(50, int(query.limit or 20)))
        offset = max(0, int(query.offset or 0))
        M = self._imap()
        try:
            typ, _ = M.select(folder, readonly=True)
            if typ != "OK":
                raise RuntimeError(f"Cannot select folder {folder}")
            criteria = "UNSEEN" if query.unread_only else "ALL"
            if query.query:
                # Simple subject search
                q = query.query.replace('"', "")
                criteria = f'(OR SUBJECT "{q}" FROM "{q}")'
            typ, data = M.uid("search", None, criteria)
            if typ != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()
            # newest last in IMAP often — reverse
            uids = list(reversed(uids))
            page = uids[offset : offset + limit]
            out: list[EmailMessage] = []
            for uid in page:
                typ, msg_data = M.uid("fetch", uid, "(RFC822.HEADER)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                m = self._parse_msg(bytes(raw), uid.decode() if isinstance(uid, bytes) else str(uid), folder)
                # header-only body empty — snippet from subject
                if not m.snippet:
                    m.snippet = m.subject[:80]
                m.unread = query.unread_only or True
                out.append(m)
            return out
        finally:
            try:
                M.logout()
            except Exception:
                pass

    async def get_message(self, message_id: str) -> EmailMessage:
        M = self._imap()
        try:
            for folder in ("INBOX", "[Gmail]/All Mail", "All Mail", "Sent"):
                try:
                    typ, _ = M.select(folder, readonly=True)
                    if typ != "OK":
                        continue
                    typ, msg_data = M.uid("fetch", message_id.encode() if isinstance(message_id, str) else message_id, "(RFC822)")
                    if typ == "OK" and msg_data and msg_data[0]:
                        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                        if isinstance(raw, (bytes, bytearray)):
                            return self._parse_msg(bytes(raw), message_id, folder)
                except Exception:
                    continue
            raise KeyError(f"Message not found: {message_id}")
        finally:
            try:
                M.logout()
            except Exception:
                pass

    async def send(self, req: SendRequest) -> SendResult:
        to_list = req.to if isinstance(req.to, list) else [str(req.to)]
        msg = StdEmailMessage()
        msg["From"] = self.address
        msg["To"] = ", ".join(to_list)
        if req.cc:
            msg["Cc"] = ", ".join(req.cc if isinstance(req.cc, list) else [req.cc])
        subject = req.subject
        body = req.body
        if req.action == "reply" and req.message_id:
            try:
                orig = await self.get_message(req.message_id)
                if not subject.lower().startswith("re:"):
                    subject = f"Re: {orig.subject}"
                msg["In-Reply-To"] = req.message_id
            except Exception:
                pass
        if req.action == "forward" and req.message_id:
            try:
                orig = await self.get_message(req.message_id)
                if not subject.lower().startswith("fwd:"):
                    subject = f"Fwd: {orig.subject}"
                body = f"{req.body}\n\n---------- Forwarded message ----------\n{orig.body}"
            except Exception:
                pass
        msg["Subject"] = subject
        msg["Message-ID"] = f"<{uuid.uuid4().hex}@{self.address.split('@')[-1]}>"
        if req.body_format == "html":
            msg.set_content(body, subtype="html")
        else:
            msg.set_content(body)

        if req.action == "draft":
            # Best-effort APPEND to Drafts
            M = self._imap()
            try:
                for draft_folder in ("[Gmail]/Drafts", "Drafts", "INBOX"):
                    try:
                        M.append(
                            draft_folder,
                            "\\Draft",
                            None,
                            msg.as_bytes(),
                        )
                        return SendResult(
                            ok=True,
                            message_id=msg["Message-ID"] or "",
                            detail=f"Draft appended to {draft_folder}",
                            draft=True,
                        )
                    except Exception:
                        continue
                return SendResult(ok=False, detail="Could not save draft folder")
            finally:
                try:
                    M.logout()
                except Exception:
                    pass

        context = ssl.create_default_context()
        if self.smtp_starttls:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as s:
                s.ehlo()
                s.starttls(context=context)
                s.login(self.address, self.password)
                s.send_message(msg)
        else:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context, timeout=30) as s:
                s.login(self.address, self.password)
                s.send_message(msg)
        return SendResult(
            ok=True,
            message_id=str(msg["Message-ID"] or ""),
            detail=f"Sent via SMTP to {', '.join(to_list)}",
            draft=False,
        )

    async def delete(self, message_id: str, permanent: bool = False) -> None:
        M = self._imap()
        try:
            M.select("INBOX")
            if permanent:
                M.uid("store", message_id, "+FLAGS", "(\\Deleted)")
                M.expunge()
            else:
                # Gmail: move to trash via label if possible
                try:
                    M.uid("store", message_id, "+X-GM-LABELS", "(\\Trash)")
                    M.uid("store", message_id, "+FLAGS", "(\\Deleted)")
                except Exception:
                    M.uid("store", message_id, "+FLAGS", "(\\Deleted)")
                    M.expunge()
        finally:
            try:
                M.logout()
            except Exception:
                pass

    async def categorize(self, req: CategorizeRequest) -> None:
        M = self._imap()
        try:
            M.select("INBOX")
            mid = req.message_id
            if req.mark_read is True:
                M.uid("store", mid, "+FLAGS", "(\\Seen)")
            elif req.mark_read is False:
                M.uid("store", mid, "-FLAGS", "(\\Seen)")
            if req.star is True:
                M.uid("store", mid, "+FLAGS", "(\\Flagged)")
            elif req.star is False:
                M.uid("store", mid, "-FLAGS", "(\\Flagged)")
            # Gmail labels best-effort
            for lab in req.add_labels or []:
                try:
                    M.uid("store", mid, "+X-GM-LABELS", f"({lab})")
                except Exception:
                    logger.debug("label add failed: %s", lab)
            for lab in req.remove_labels or []:
                try:
                    M.uid("store", mid, "-X-GM-LABELS", f"({lab})")
                except Exception:
                    pass
        finally:
            try:
                M.logout()
            except Exception:
                pass
