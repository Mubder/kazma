"""FanOutBusAdapter — multi-platform swarm bus."""

from __future__ import annotations

import asyncio

import pytest

from kazma_core.swarm.bus import (
    ApprovalRequest,
    BusAdapter,
    BusMessage,
    FanOutBusAdapter,
    SwarmReport,
)


class _RecordingAdapter(BusAdapter):
    def __init__(self, name: str, *, approve: bool = False, delay: float = 0.0) -> None:
        self.name = name
        self.approve = approve
        self.delay = delay
        self.sends: list[str] = []
        self.reports: list[str] = []
        self.approvals = 0

    async def send(self, message: BusMessage) -> None:
        self.sends.append(message.content)

    async def send_report(self, report: SwarmReport) -> None:
        self.reports.append(report.status)

    async def request_approval(self, approval: ApprovalRequest, timeout: float = 60.0) -> bool:
        self.approvals += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.approve


@pytest.mark.asyncio
async def test_fanout_send_reaches_all() -> None:
    a = _RecordingAdapter("a")
    b = _RecordingAdapter("b")
    fan = FanOutBusAdapter([a, b])
    await fan.send(BusMessage(worker_name="w", worker_role="r", content="hi"))
    assert a.sends == ["hi"]
    assert b.sends == ["hi"]


@pytest.mark.asyncio
async def test_fanout_approval_any_yes() -> None:
    deny = _RecordingAdapter("deny", approve=False, delay=0.05)
    allow = _RecordingAdapter("allow", approve=True, delay=0.01)
    fan = FanOutBusAdapter([deny, allow])
    ok = await fan.request_approval(
        ApprovalRequest(worker_name="w", task_description="t", proposed_output="x")
    )
    assert ok is True
    assert allow.approvals == 1


@pytest.mark.asyncio
async def test_fanout_approval_all_no() -> None:
    a = _RecordingAdapter("a", approve=False)
    b = _RecordingAdapter("b", approve=False)
    fan = FanOutBusAdapter([a, b])
    ok = await fan.request_approval(
        ApprovalRequest(worker_name="w", task_description="t", proposed_output="x")
    )
    assert ok is False
