"""An update window (resilience plan B2b).

``kazma maintenance begin`` pauses the guard under a maintenance reason, with
an expiry, so planned work restarts and pages nothing; ``end`` resumes, waits
for ``/health/deep`` and pages the owner if Kazma is not healthy within five
minutes. The guard is faked: its commands are recorded.
"""

from __future__ import annotations

from kazma_cli import maintenance


class _Guard:
    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str, timeout: float = 1200):
        self.calls.append(args)
        return self.code, ""


def test_begin_pauses_with_a_reason_and_an_expiry():
    guard = _Guard()
    assert maintenance.begin("topgrade", 1.5, guard=guard) == 0
    (call,) = guard.calls
    assert call[0] == "--pause" and "--when-idle" in call
    assert call[call.index("--reason") + 1] == "maintenance: topgrade"
    assert call[call.index("--ttl") + 1] == "5400"


def test_end_resumes_and_is_quiet_when_healthy():
    guard = _Guard()
    assert maintenance.end(guard=guard, deep_ok=lambda: True, sleep=lambda s: None) == 0
    assert guard.calls == [("--resume",)]


def test_end_pages_when_kazma_stays_unhealthy():
    guard = _Guard()
    assert maintenance.end(guard=guard, deep_ok=lambda: False, sleep=lambda s: None, wait_s=30) == 1
    assert guard.calls[0] == ("--resume",)
    page = guard.calls[-1]
    assert page[:2] == ("--page", "Kazma is not healthy after maintenance")


def test_a_pause_that_failed_opens_no_window():
    assert maintenance.begin("x", 1, guard=_Guard(code=2)) == 1
