"""Email models shared by all backends."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ProviderName = Literal["auto", "sandbox", "gmail", "microsoft", "imap"]
SendAction = Literal["send", "reply", "forward", "draft"]


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
        flag = "●" if self.unread else "○"
        star = "★" if self.starred else "☆"
        snip = (self.snippet or self.body or "")[:80].replace("\n", " ")
        labs = ",".join(self.labels) if self.labels else "—"
        return (
            f"| `{self.id}` | {flag}{star} | {self.from_addr[:40]} | "
            f"{self.subject[:50]} | {self.date[:25]} | {labs} | {snip} |"
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
