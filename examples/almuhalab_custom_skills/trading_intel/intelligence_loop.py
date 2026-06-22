"""Autonomous Intelligence Loop — Continuously generates trading intel.

Runs as an async background loop with configurable interval. Integrates
market data ingestion, trade correlation, and report generation with
cost breaker circuit protection.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class LoopStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"           # Cost breaker tripped
    ERROR = "error"


@dataclass
class LoopMetrics:
    """Metrics from the intelligence loop for observability."""
    cycles_completed: int = 0
    reports_generated: int = 0
    total_correlations: int = 0
    errors: int = 0
    last_cycle_at: str = ""
    last_error: str = ""
    avg_cycle_duration_ms: float = 0.0
    _cycle_durations: list[float] = field(default_factory=list)

    def record_cycle(self, duration_ms: float, reports: int = 0) -> None:
        self.cycles_completed += 1
        self.reports_generated += reports
        self.last_cycle_at = datetime.now(UTC).isoformat()
        self._cycle_durations.append(duration_ms)
        if len(self._cycle_durations) > 100:
            self._cycle_durations = self._cycle_durations[-100:]
        self.avg_cycle_duration_ms = sum(self._cycle_durations) / len(self._cycle_durations)

    def record_error(self, error: str) -> None:
        self.errors += 1
        self.last_error = error


class TradingIntelligenceLoop:
    """Autonomous loop that continuously generates trading intel.

    Flow per cycle:
    1. Check cost breaker — pause if tripped
    2. Fetch market data from all sources
    3. Fetch division trade data (RBAC-gated)
    4. Fetch latest drone inspection insights
    5. Correlate market + division data
    6. Generate reports for each division
    7. Store reports
    8. Notify division admins
    """

    DIVISIONS = ["gas_oil", "tourism", "general_trading"]

    def __init__(
        self,
        market_ingestor: Any,
        correlator: Any,
        report_generator: Any,
        report_store: Any,
        cost_breaker: Any,
        tracer: Any = None,
    ) -> None:
        self.market = market_ingestor
        self.correlator = correlator
        self.generator = report_generator
        self.report_store = report_store
        self.cost_breaker = cost_breaker
        self.tracer = tracer
        self._running = False
        self._status = LoopStatus.STOPPED
        self._task: asyncio.Task[None] | None = None
        self._metrics = LoopMetrics()
        self._on_report_callbacks: list[Callable[..., Coroutine]] = []

    @property
    def status(self) -> LoopStatus:
        return self._status

    @property
    def metrics(self) -> LoopMetrics:
        return self._metrics

    def on_report(self, callback: Callable[..., Coroutine]) -> None:
        """Register callback for when a report is generated."""
        self._on_report_callbacks.append(callback)

    async def start(self, interval_minutes: int = 30) -> None:
        """Start the intelligence loop.

        Args:
            interval_minutes: Minutes between cycles. Default 30.
        """
        if self._running:
            logger.warning("Intelligence loop already running")
            return

        self._running = True
        self._status = LoopStatus.RUNNING
        self._task = asyncio.create_task(self._loop(interval_minutes))
        logger.info("Intelligence loop started (interval=%dm)", interval_minutes)

    async def stop(self) -> None:
        """Stop the intelligence loop gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._status = LoopStatus.STOPPED
        logger.info("Intelligence loop stopped")

    async def _loop(self, interval_minutes: int) -> None:
        """Main loop body."""
        while self._running:
            try:
                # Check cost breaker
                if self.cost_breaker and self.cost_breaker.should_halt():
                    self._status = LoopStatus.PAUSED
                    logger.warning("Cost breaker tripped, pausing intelligence loop")
                    await asyncio.sleep(60)
                    continue

                self._status = LoopStatus.RUNNING
                cycle_start = time.time()

                # Run one cycle
                reports = await self._run_cycle()

                # Record metrics
                duration_ms = (time.time() - cycle_start) * 1000
                self._metrics.record_cycle(duration_ms, reports=len(reports))
                self._metrics.total_correlations += 1

                logger.info(
                    "Intelligence cycle completed: %d reports in %.0fms",
                    len(reports), duration_ms,
                )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                duration_ms = (time.time() - cycle_start) * 1000 if 'cycle_start' in dir() else 0
                self._metrics.record_error(str(exc))
                self._status = LoopStatus.ERROR
                logger.error("Intelligence cycle failed: %s", exc, exc_info=True)
                # Don't crash the loop — wait and retry
                await asyncio.sleep(60)
                continue

            # Wait for next cycle
            await asyncio.sleep(interval_minutes * 60)

    async def _run_cycle(self) -> list[Any]:
        """Execute one cycle of intelligence generation.

        Returns list of generated reports.
        """
        reports = []

        # 1. Fetch market data
        market_data = await self.market.fetch_indices()
        logger.debug(
            "Market data fetched: oil=%s gold=%s errors=%d",
            market_data.oil.price_usd if market_data.oil else "N/A",
            market_data.gold.price_kwd if market_data.gold else "N/A",
            len(market_data.errors),
        )

        # 2. Generate correlation + report for each division
        for division in self.DIVISIONS:
            try:
                # 2a. Correlate
                correlation = await self.correlator.correlate(
                    boursa_index=market_data.boursa_index.value if market_data.boursa_index else None,
                    boursa_change_pct=market_data.boursa_index.change_pct if market_data.boursa_index else None,
                    oil_price=market_data.oil.price_usd if market_data.oil else None,
                    oil_change_pct=market_data.oil.change_pct if market_data.oil else None,
                    gold_price_kwd=market_data.gold.price_kwd if market_data.gold else None,
                    gold_change_pct=market_data.gold.change_pct if market_data.gold else None,
                )

                # 2b. Generate report
                report = await self.generator.generate(
                    correlation=correlation,
                    division=division,
                )

                # 2c. Store report
                if self.report_store:
                    await self.report_store.store(report)

                reports.append(report)

                # 2d. Notify callbacks
                for cb in self._on_report_callbacks:
                    try:
                        await cb(report)
                    except Exception as cb_exc:
                        logger.warning("Report callback failed: %s", cb_exc)

            except Exception as exc:
                logger.error("Failed to generate report for %s: %s", division, exc)
                self._metrics.record_error(f"{division}: {exc}")

        return reports

    async def run_once(self) -> list[Any]:
        """Run a single cycle (useful for testing)."""
        cycle_start = time.time()
        reports = await self._run_cycle()
        duration_ms = (time.time() - cycle_start) * 1000
        self._metrics.record_cycle(duration_ms, reports=len(reports))
        return reports
