"""Calendar backend protocol."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CalendarBackend(Protocol):
    """Structural interface for calendar backends."""

    name: str

    async def list_events(
        self, time_min: str, time_max: str, max_results: int = 25
    ) -> list[dict[str, Any]]:
        ...

    async def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        location: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        ...

    async def update_event(
        self, event_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        ...

    async def delete_event(self, event_id: str) -> bool:
        ...

    async def find_free_slots(
        self, date: str, duration_minutes: int = 30
    ) -> list[dict[str, str]]:
        ...
