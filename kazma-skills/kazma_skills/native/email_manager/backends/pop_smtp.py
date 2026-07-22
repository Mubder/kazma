"""POP3 + SMTP backend (Gmail / Microsoft / generic).

POP is inbox-oriented: no real folders, limited flag support. Send uses SMTP.
"""

from __future__ import annotations

import email
import logging
import poplib
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


class PopSmtpBackend:
    """POP3 list/get/delete + SMTP send.

    Message ids are POP message numbers as strings (session-relative; re-list
    after deletes). Prefer IMAP when the provider supports it.
    """

    def __init__(
        self,
        *,
        name: str,
        address: str,
        password: str,
        pop_host: str,
        pop_port: int = 995,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_starttls: bool = True,
    ) -> None:
        self.name = name
        self.address = address
        self.password = password
        self.pop_host = pop_host
        self.pop_port = pop_port
        self.smtp_host = smtp_host or pop_host.replace("pop", "smtp")
        self.smtp_port = smtp_port
        self.smtp_starttls = smtp_starttls

    def _pop(self) -> poplib.POP3_SSL:
        M = poplib.POP3_SSL(self.pop_host, self.pop_port, timeout=45)
        M.user(self.address)
        M.pass_(self.password)
        return M

    def _parse_msg(self, lines: list[bytes], mid: str) -> EmailMessage:
        raw = b"\r\n".join(lines)
        msg = email.message_from_bytes(raw)
        subject = str(msg.get("Subject", ""))
        from_addr = str(msg.get("From", ""))
        to_raw = str(msg.get("To", ""))
        cc_raw = str(msg.get("Cc", ""))
        date = str(msg.get("Date", ""))
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
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
                    body = payload.decode(
                        msg.get_content_charset() or "utf-8", errors="replace"
                    )
                else:
                    body = str(msg.get_payload())
            except Exception:
                body = str(msg.get_payload())
        to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
        cc_addrs = [a.strip() for a in cc_raw.split(",") if a.strip()]
        return EmailMessage(
            id=mid,
            subject=subject,
            from_addr=from_addr,
            to_addrs=to_addrs,
            cc_addrs=cc_addrs,
            date=date,
            body=body,
            snippet=(body or subject or "")[:120].replace("\n", " "),
            unread=True,
            starred=False,
            labels=["INBOX"],
            folder="INBOX",
            provider=self.name,
        )

    async def list_messages(self, query: ListQuery) -> list[EmailMessage]:
        # POP has no folders — always INBOX semantics
        limit = max(1, min(50, int(query.limit or 20)))
        offset = max(0, int(query.offset or 0))
        M = self._pop()
        try:
            _count, _size = M.stat()
            # POP message numbers 1..n (oldest first typically)
            nums = list(range(1, int(_count) + 1))
            nums = list(reversed(nums))  # newest first
            page = nums[offset : offset + limit]
            out: list[EmailMessage] = []
            q = (query.query or "").strip().lower()
            for n in page:
                try:
                    # TOP: headers + few body lines for snippet
                    _resp, lines, _octets = M.top(n, 20)
                    m = self._parse_msg(lines, str(n))
                    if q:
                        hay = f"{m.subject} {m.from_addr} {m.snippet}".lower()
                        if q not in hay:
                            continue
                    out.append(m)
                except Exception as exc:
                    logger.debug("[email.pop] list msg %s: %s", n, exc)
            return out
        finally:
            try:
                M.quit()
            except Exception:
                pass

    async def get_message(self, message_id: str) -> EmailMessage:
        try:
            n = int(str(message_id).strip())
        except ValueError as exc:
            raise KeyError(f"Invalid POP message id: {message_id}") from exc
        M = self._pop()
        try:
            _resp, lines, _octets = M.retr(n)
            return self._parse_msg(lines, str(n))
        except Exception as exc:
            raise KeyError(f"Message not found: {message_id}") from exc
        finally:
            try:
                M.quit()
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
            return SendResult(
                ok=False,
                detail="POP does not support drafts; use IMAP or OAuth for drafts",
                draft=True,
            )

        context = ssl.create_default_context()
        if self.smtp_starttls:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as s:
                s.ehlo()
                s.starttls(context=context)
                s.login(self.address, self.password)
                s.send_message(msg)
        else:
            with smtplib.SMTP_SSL(
                self.smtp_host, self.smtp_port, context=context, timeout=30
            ) as s:
                s.login(self.address, self.password)
                s.send_message(msg)
        return SendResult(
            ok=True,
            message_id=str(msg["Message-ID"] or ""),
            detail=f"Sent via SMTP to {', '.join(to_list)}",
            draft=False,
        )

    async def delete(self, message_id: str, permanent: bool = False) -> None:
        # POP only supports permanent delete (DELE + QUIT)
        try:
            n = int(str(message_id).strip())
        except ValueError as exc:
            raise KeyError(f"Invalid POP message id: {message_id}") from exc
        M = self._pop()
        try:
            M.dele(n)
        finally:
            try:
                M.quit()
            except Exception:
                pass

    async def categorize(self, req: CategorizeRequest) -> None:
        # POP has no flags/labels. No-op with log so tools don't crash.
        logger.info(
            "[email.pop] categorize not supported on POP (message_id=%s)",
            req.message_id,
        )
