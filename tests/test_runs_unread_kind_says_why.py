"""A kind of run that cannot be read says why in the log.

``list_runs`` logged ``"[runs] <kind> runs could not be read: %s"`` with the
exception's own text, and a TimeoutError has none: on 2026-11-13 the live log
held three such lines ending in ": " (research sessions, X posts and memory
tasks, read while the computer was busy). The reason it had just worked out
("timed out") now goes in the line, with the deadline; the old line is the
negative control.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from kazma_core.runs import registry


class _Slow:
    async def list_runs(self, now, *, window_s):
        await asyncio.sleep(5)
        return []


class _Broken:
    async def list_runs(self, now, *, window_s):
        raise RuntimeError()


def _kind():
    return next(iter(registry.KINDS))


@pytest.mark.asyncio
async def test_a_timed_out_kind_says_it_timed_out(monkeypatch, caplog):
    kind = _kind()
    monkeypatch.setattr(registry, "ADAPTER_DEADLINE_S", 0.05)
    monkeypatch.setattr(registry, "adapter_for", lambda k: _Slow())
    with caplog.at_level(logging.WARNING, logger="kazma_core.runs.registry"):
        listing = await registry.list_runs(kinds=(kind,), mark_handled=False)
    assert listing.errors == {kind: "timed out"}
    lines = [r.getMessage() for r in caplog.records if r.name == "kazma_core.runs.registry"]
    assert lines == [f"[runs] {kind} runs could not be read: timed out after 0.05 s"]


@pytest.mark.asyncio
async def test_an_error_with_no_text_names_its_type(monkeypatch, caplog):
    kind = _kind()
    monkeypatch.setattr(registry, "adapter_for", lambda k: _Broken())
    with caplog.at_level(logging.WARNING, logger="kazma_core.runs.registry"):
        listing = await registry.list_runs(kinds=(kind,), mark_handled=False)
    assert listing.errors == {kind: "RuntimeError"}
    assert any(r.getMessage().endswith(": RuntimeError") for r in caplog.records)


def test_negative_control_the_old_line_said_nothing():
    assert "[runs] %s runs could not be read: %s" % ("memory_task", TimeoutError()) == (
        "[runs] memory_task runs could not be read: ")
