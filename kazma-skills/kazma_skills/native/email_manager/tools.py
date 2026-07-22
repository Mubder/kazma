"""Email manager tools — thin wrappers over backends."""

from __future__ import annotations

import json
import logging
from typing import Any

from kazma_skills.native.email_manager.analyze import analyze_email_text
from kazma_skills.native.email_manager.models import (
    CategorizeRequest,
    ListQuery,
    SendRequest,
)
from kazma_skills.native.email_manager.router import get_backend, mode_banner, resolve_provider

# account= multi-account alias (EMAIL_ACCOUNTS + EMAIL_ACCOUNT_{ALIAS}_*)

logger = logging.getLogger(__name__)


def _parse_addr_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    if not s:
        return []
    return [a.strip() for a in s.split(",") if a.strip()]


async def email_list(
    folder: str = "INBOX",
    query: str = "",
    limit: int = 20,
    offset: int = 0,
    unread_only: bool = False,
    provider: str = "auto",
    account: str = "",
) -> str:
    """List/search emails in a folder (sandbox, Gmail, Microsoft Graph, or IMAP)."""
    try:
        backend = get_backend(provider, account=account or None)
        banner = mode_banner(backend)
        msgs = await backend.list_messages(
            ListQuery(
                folder=folder or "INBOX",
                query=query or "",
                limit=int(limit or 20),
                offset=int(offset or 0),
                unread_only=bool(unread_only),
            )
        )
        if not msgs:
            return f"{banner}\nNo messages found in `{folder}`."
        lines = [
            f"{banner}",
            f"### Emails in `{folder}` ({len(msgs)})",
            "",
            "| id | flags | from | subject | date | labels | snippet |",
            "|----|-------|------|---------|------|--------|---------|",
        ]
        for m in msgs:
            lines.append(m.short_row())
        lines.append("")
        lines.append("Use `email_get(message_id=...)` for full body.")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("email_list failed")
        return f"Error listing email: {exc}"


async def email_get(
    message_id: str,
    include_body: bool = True,
    max_body_chars: int = 32000,
    provider: str = "auto",
    account: str = "",
) -> str:
    """Fetch one email by id."""
    if not message_id or not str(message_id).strip():
        return "Error: message_id is required."
    try:
        backend = get_backend(provider, account=account or None)
        banner = mode_banner(backend)
        msg = await backend.get_message(str(message_id).strip())
        body = msg.body if include_body else ""
        cap = max(500, min(100_000, int(max_body_chars or 32000)))
        truncated = ""
        if len(body) > cap:
            body = body[:cap]
            truncated = f"\n\n[body truncated to {cap} chars]"
        return (
            f"{banner}\n"
            f"**From:** {msg.from_addr}\n"
            f"**To:** {', '.join(msg.to_addrs)}\n"
            f"**Date:** {msg.date}\n"
            f"**Subject:** {msg.subject}\n"
            f"**Labels:** {', '.join(msg.labels) or '—'}\n"
            f"**Unread:** {msg.unread} · **Starred:** {msg.starred}\n"
            f"**Id:** `{msg.id}`\n\n"
            f"{body}{truncated}"
        )
    except KeyError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        logger.exception("email_get failed")
        return f"Error getting email: {exc}"


async def email_send(
    to: str | list[str] = "",
    subject: str = "",
    body: str = "",
    action: str = "send",
    cc: str | list[str] = "",
    message_id: str = "",
    body_format: str = "text",
    provider: str = "auto",
    account: str = "",
) -> str:
    """Send, reply, forward, or save a draft. Requires HITL approval in production."""
    try:
        action_n = (action or "send").lower().strip()
        if action_n not in ("send", "reply", "forward", "draft"):
            return "Error: action must be send|reply|forward|draft"
        to_list = _parse_addr_list(to)
        if action_n in ("send", "forward") and not to_list and action_n != "draft":
            if action_n == "send" and not to_list:
                return "Error: `to` is required for send."
        backend = get_backend(provider, account=account or None)
        banner = mode_banner(backend)
        result = await backend.send(
            SendRequest(
                action=action_n,  # type: ignore[arg-type]
                to=to_list,
                cc=_parse_addr_list(cc),
                subject=subject or "",
                body=body or "",
                body_format=body_format or "text",
                message_id=message_id or "",
            )
        )
        if not result.ok:
            return f"{banner}\nError: {result.detail}"
        kind = "Draft saved" if result.draft else "Sent"
        return (
            f"{banner}\n{kind} successfully.\n"
            f"message_id: `{result.message_id or 'n/a'}`\n"
            f"{result.detail}"
        )
    except Exception as exc:
        logger.exception("email_send failed")
        return f"Error sending email: {exc}"


async def email_delete(
    message_id: str,
    permanent: bool = False,
    provider: str = "auto",
    account: str = "",
) -> str:
    """Move email to trash or permanently delete. Requires HITL approval."""
    if not message_id or not str(message_id).strip():
        return "Error: message_id is required."
    try:
        backend = get_backend(provider, account=account or None)
        banner = mode_banner(backend)
        await backend.delete(str(message_id).strip(), permanent=bool(permanent))
        action = "Permanently deleted" if permanent else "Moved to Trash"
        return f"{banner}\n{action}: `{message_id}`"
    except KeyError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        logger.exception("email_delete failed")
        return f"Error deleting email: {exc}"


async def email_categorize(
    message_id: str,
    mark_read: bool | None = None,
    star: bool | None = None,
    add_labels: str | list[str] | None = None,
    remove_labels: str | list[str] | None = None,
    move_to_folder: str = "",
    provider: str = "auto",
    account: str = "",
) -> str:
    """Mark read/unread, star, labels/folders. Requires HITL approval."""
    if not message_id or not str(message_id).strip():
        return "Error: message_id is required."
    try:
        backend = get_backend(provider, account=account or None)
        banner = mode_banner(backend)
        await backend.categorize(
            CategorizeRequest(
                message_id=str(message_id).strip(),
                mark_read=mark_read,
                star=star,
                add_labels=_parse_addr_list(add_labels) if add_labels else [],
                remove_labels=_parse_addr_list(remove_labels) if remove_labels else [],
                move_to_folder=move_to_folder or "",
            )
        )
        return f"{banner}\nUpdated categories for `{message_id}`."
    except KeyError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        logger.exception("email_categorize failed")
        return f"Error categorizing email: {exc}"


async def email_analyze(
    message_id: str = "",
    raw_text: str = "",
    focus: str = "full",
    provider: str = "auto",
    max_body_chars: int = 32000,
    account: str = "",
) -> str:
    """Summarize, extract actions, sentiment, and phishing risk for an email."""
    try:
        subject = ""
        from_addr = ""
        body = raw_text or ""
        mode = resolve_provider(provider, account=account or None)
        if message_id and str(message_id).strip():
            backend = get_backend(provider, account=account or None)
            mode = getattr(backend, "name", mode)
            msg = await backend.get_message(str(message_id).strip())
            subject = msg.subject
            from_addr = msg.from_addr
            body = msg.body
        if not body and not subject:
            return "Error: provide message_id or raw_text."
        data = await analyze_email_text(
            subject=subject,
            from_addr=from_addr,
            body=body,
            focus=focus or "full",
            max_body_chars=int(max_body_chars or 32000),
        )
        data["mode"] = mode
        banner = f"[{mode} mode]"
        sec = data.get("security") or {}
        actions = data.get("action_items") or []
        act_lines = "\n".join(
            f"- {a.get('text', a) if isinstance(a, dict) else a}"
            + (f" (deadline: {a.get('deadline')})" if isinstance(a, dict) and a.get("deadline") else "")
            for a in actions
        ) or "- (none)"
        signals = sec.get("phishing_signals") or []
        return (
            f"{banner}\n"
            f"## Email analysis\n\n"
            f"**Summary:** {data.get('summary', '')}\n\n"
            f"**Sentiment:** {data.get('sentiment', 'neutral')}\n\n"
            f"**Security risk:** {sec.get('risk_level', 'low')}\n"
            f"**Phishing signals:** {', '.join(signals) if signals else 'none'}\n"
            f"**Notes:** {sec.get('notes', '')}\n\n"
            f"**Action items:**\n{act_lines}\n\n"
            f"```json\n{json.dumps(data, indent=2, ensure_ascii=False)[:4000]}\n```"
        )
    except KeyError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        logger.exception("email_analyze failed")
        return f"Error analyzing email: {exc}"
