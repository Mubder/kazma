"""Per-acquisition navigation scope; never shared between crawl jobs."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass


class AcquisitionScopeError(ValueError):
    pass


@dataclass
class AcquisitionScope:
    check: Callable[[str], bool]
    violation: str = ""

    def allows(self, url: str) -> bool:
        try:
            allowed = bool(self.check(url))
        except Exception:
            if not self.violation:
                self.violation = "Crawl scope validator failed; acquisition was refused."
            return False
        if not allowed:
            if not self.violation:
                self.violation = f"Redirect or navigation outside crawl scope: {url}"
        return allowed

    def require(self, url: str) -> None:
        if not self.allows(url):
            raise AcquisitionScopeError(self.violation)


active_scope: ContextVar[AcquisitionScope | None] = ContextVar("acquisition_scope", default=None)


def require_scope(url: str) -> None:
    scope = active_scope.get()
    if scope is not None:
        scope.require(url)
