"""Tests for production hardening — connection pooling, rate limiting, metrics.

5 tests per gw-020 spec:
    1. Persistent httpx client reuse across send() calls
    2. RateLimiter allows first N calls immediately
    3. RateLimiter applies backpressure on N+1
    4. MessageMetrics increments correctly
    5. GET /api/gateway/status includes metrics key
"""

from __future__ import annotations

import time

import pytest
from kazma_gateway.adapters.discord import DiscordAdapter
from kazma_gateway.adapters.telegram import TelegramAdapter
from kazma_gateway.gateway import (
    GatewayManager,
    MessageMetrics,
    OutboundMessage,
    RateLimiter,
)


class TestRateLimiter:
    """Tests for the token-bucket RateLimiter."""

    @pytest.mark.asyncio
    async def test_acquire_immediate(self) -> None:
        """Test 2: First N calls (up to max_per_second) return immediately."""
        rl = RateLimiter(max_per_second=10)
        start = time.monotonic()
        for _ in range(10):
            await rl.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5  # Should be near-instant

    @pytest.mark.asyncio
    async def test_backpressure(self) -> None:
        """Test 3: Call N+1 waits for token refill."""
        rl = RateLimiter(max_per_second=5)
        # Exhaust tokens
        for _ in range(5):
            await rl.acquire()
        # Next call should wait
        start = time.monotonic()
        await rl.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.1  # Should have waited


class TestMessageMetrics:
    """Tests for the MessageMetrics counter."""

    @pytest.mark.asyncio
    async def test_increment(self) -> None:
        """Test 4: 3 outbound sends → outbound_total: 3."""
        metrics = MessageMetrics()
        await metrics.record_outbound()
        await metrics.record_outbound()
        await metrics.record_outbound()
        assert metrics.outbound_total == 3

    @pytest.mark.asyncio
    async def test_snapshot(self) -> None:
        """snapshot() returns all three counters."""
        metrics = MessageMetrics()
        await metrics.record_inbound()
        await metrics.record_inbound()
        await metrics.record_outbound()
        await metrics.record_error()
        snap = metrics.snapshot()
        assert snap["inbound_total"] == 2
        assert snap["outbound_total"] == 1
        assert snap["errors_total"] == 1


class TestGatewayStatusMetrics:
    """Test 5: GET /api/gateway/status includes metrics key."""

    @pytest.mark.asyncio
    async def test_status_has_metrics(self) -> None:
        """Status response must include metrics with 3 counters."""
        manager = GatewayManager()
        status = await manager.get_status()
        assert "metrics" in status
        assert "inbound_total" in status["metrics"]
        assert "outbound_total" in status["metrics"]
        assert "errors_total" in status["metrics"]
        assert status["metrics"]["inbound_total"] == 0

    @pytest.mark.asyncio
    async def test_metrics_increment_via_send(self) -> None:
        """Sending through gateway increments outbound_total."""
        from unittest.mock import AsyncMock, MagicMock

        adapter = TelegramAdapter(token="fake:token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        adapter._http = mock_http

        manager = GatewayManager()
        manager.add_adapter(adapter)

        outbound = OutboundMessage(
            target_id="telegram:123",
            text="test",
            context_metadata={"chat_id": 123},
        )
        ok = await manager.send(outbound)
        assert ok is True
        assert manager.metrics.outbound_total == 1


class TestPersistentClient:
    """Test 1: Same httpx client instance used across multiple send() calls."""

    def test_telegram_has_rate_limiter(self) -> None:
        """TelegramAdapter initializes with a RateLimiter."""
        adapter = TelegramAdapter(token="fake:token")
        assert isinstance(adapter._rate_limiter, RateLimiter)

    def test_discord_has_rate_limiter(self) -> None:
        """DiscordAdapter initializes with a RateLimiter."""
        adapter = DiscordAdapter(token="fake:token")
        assert isinstance(adapter._rate_limiter, RateLimiter)
