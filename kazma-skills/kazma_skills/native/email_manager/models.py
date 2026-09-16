"""Email models shared by all backends."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ProviderName = Literal["auto", "sandbox", "gmail", "microsoft", "imap"]
SendAction = Literal["send", "reply", "forward", "draft"]


def _cell(value: str | None, limit: int) -> str:
    """Flatten sender-controlled text into a single safe markdown table cell.

    Collapses every newline/carriage-return/tab and escapes the pipe, so no
    field can end a row, start a new one, or add a column.
    """
    text = (value or "")[:limit]
    for ch in ("\r", "\n", "\t"):
        text = text.replace(ch, " ")
    return text.replace("|", "\\|").strip()


@dataclass
class EmailMessage:
    id: str
    subject: str
    from_addr: str
    to_addrs: list[str] = field(default_factory=list)
    cc_addrs: list[str] = field(default_factory=list)
    date: str = ""
    body: str = ""
    snippet: str = ""
    unread: bool = False
    starred: bool = False
    labels: list[str] = field(default_factory=list)
    folder: str = "INBOX"
    thread_id: str = ""
    provider: str = "sandbox"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def short_row(self) -> str:
        """One markdown table row. Every field here is sender-controlled.

        `snip` used to be the only field with its newlines stripped, so a
        newline in a *subject* (or a display name in `from_addr`) broke out
        of the row and forged free-form lines in the tool result — the model
        read them as the tool speaking. Cells are now uniformly flattened
        and `|` is escaped so a crafted header cannot restructure the table
        (audit 2026-09-16 F-2). The whole block is fenced by `email_list`;
        this keeps the fence's contents from being reshaped.
        """
        flag = "●" if self.unread else "○"
        star = "★" if self.starred else "☆"
        snip = _cell(self.snippet or self.body, 80)
        labs = _cell(",".join(self.labels), 40) if self.labels else "—"
        return (
            f"| `{_cell(self.id, 60)}` | {flag}{star} | {_cell(self.from_addr, 40)} | "
            f"{_cell(self.subject, 50)} | {_cell(self.date, 25)} | {labs} | {snip} |"
        )


@dataclass
class ListQuery:
    folder: str = "INBOX"
    query: str = ""
    limit: int = 20
    offset: int = 0
    unread_only: bool = False


@dataclass
class SendRequest:
    action: SendAction = "send"
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    body_format: str = "text"
    message_id: str = ""  # for reply/forward
    client_request_id: str = ""


@dataclass
class SendResult:
    ok: bool
    message_id: str = ""
    detail: str = ""
    draft: bool = False


@dataclass
class CategorizeRequest:
    message_id: str
    mark_read: bool | None = None
    star: bool | None = None
    add_labels: list[str] = field(default_factory=list)
    remove_labels: list[str] = field(default_factory=list)
    move_to_folder: str = ""
