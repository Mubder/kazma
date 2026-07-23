"""Email backend protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from kazma_skills.native.email_manager.models import (
    CategorizeRequest,
    EmailMessage,
    ListQuery,
    SendRequest,
    SendResult,
)


@runtime_checkable
class EmailBackend(Protocol):
    name: str

    async def list_messages(self, query: ListQuery) -> list[EmailMessage]: ...

    async def get_message(self, message_id: str) -> EmailMessage: ...

    async def send(self, req: SendRequest) -> SendResult: ...

    async def delete(self, message_id: str, permanent: bool = False) -> None: ...

    async def categorize(self, req: CategorizeRequest) -> None: ...
