"""Market Data Ingestion Engine for ALMuhalab Trading Intelligence.

Fetches real-time market indices, oil prices, and gold prices from
multiple regional sources. Includes caching, retry with exponential
backoff, and graceful degradation when APIs are unavailable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────────────

class MarketSource(str, Enum):
    BOURSA_KUWAIT = "boursa_kuwait"
    DUBAI_FINANCE = "dubai_finance"
    OIL_PRICES = "oil_prices"
    GOLD_PRICES = "gold_prices"


@dataclass
class MarketIndex:
    """A single market index value."""
    source: str
    name: str
    value: float
    change_pct: float
    currency: str = "KWD"
    timestamp: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OilPrice:
    """Brent crude oil price data."""
    price_usd: float
    change_pct: float
    timestamp: str = ""
    source: str = "oilprice.com"


@dataclass
class GoldPrice:
    """Gold price in KWD per troy ounce."""
    price_kwd: float
    price_usd: float
    change_pct: float
    timestamp: str = ""
    source: str = "goldapi.io"


@dataclass
class MarketIndices:
    """Aggregated market indices from all sources."""
    indices: List[MarketIndex] = field(default_factory=list)
    oil: Optional[OilPrice] = None
    gold: Optional[GoldPrice] = None
    fetched_at: str = ""
    errors: List[str] = field(default_factory=list)

    @property
    def boursa_index(self) -> Optional[MarketIndex]:
        """Get Boursa Kuwait main index."""
        for idx in self.indices:
            if idx.source == MarketSource.BOURSA_KUWAIT:
                return idx
        return None

    @property
    def brent_price(self) -> Optional[float]:
        """Get Brent crude price in USD."""
        return self.oil.price_usd if self.oil else None


# ── Market Data Ingestor ───────────────────────────────────────────────

class MarketDataError(Exception):
    """Raised when market data ingestion fails critically."""
    pass


class MarketDataIngestor:
    """Real-time market data ingestion for trading intelligence.

    Fetches data from Boursa Kuwait, Dubai Finance, oil price APIs,
    and gold price APIs. Includes a time-based cache to avoid hitting
    rate limits.

    Retry policy: exponential backoff up to 3 retries per source.
    """

    MARKET_SOURCES: Dict[str, str] = {
        "boursa_kuwait": "https://api.boursakuwait.com.kw/v1/indices",
        "dubai_finance": "https://api.difc.ae/v1/market",
        "oil_prices": "https://api.oilprice.com/v1/brent",
        "gold_prices": "https://api.goldapi.io/v1/XAU/KWD",
    }

    # Kuwait-specific defaults for stub/test mode
    STUB_INDEX = MarketIndex(
        source="boursa_kuwait",
        name="Boursa Kuwait Main Index",
        value=7450.32,
        change_pct=0.85,
        currency="KWD",
    )

    STUB_OIL = OilPrice(price_usd=78.45, change_pct=-1.2)
    STUB_GOLD = GoldPrice(price_kwd=92.50, price_usd=300.25, change_pct=0.3)

    def __init__(
        self,
        update_interval_seconds: int = 300,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        stub_mode: bool = False,
    ) -> None:
        self.update_interval_seconds = update_interval_seconds
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.stub_mode = stub_mode or os.getenv("KAZMA_MARKET_STUB", "0") == "1"
        self._cache: Dict[str, Any] = {}
        self._cache_timestamps: Dict[str, float] = {}
        self._last_fetch: Optional[float] = None

    def _is_cache_valid(self, key: str) -> bool:
        """Check if cached data is still fresh."""
        if key not in self._cache_timestamps:
            return False
        age = time.time() - self._cache_timestamps[key]
        return age < self.update_interval_seconds

    def _set_cache(self, key: str, value: Any) -> None:
        """Store value in cache with timestamp."""
        self._cache[key] = value
        self._cache_timestamps[key] = time.time()

    def _get_cache(self, key: str) -> Optional[Any]:
        """Retrieve value from cache if valid."""
        if self._is_cache_valid(key):
            return self._cache[key]
        return None

    async def _fetch_with_retry(
        self, url: str, source_name: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch URL with exponential backoff retry."""
        import aiohttp

        for attempt in range(self.max_retries):
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        logger.warning(
                            "Market data fetch failed for %s: HTTP %d (attempt %d/%d)",
                            source_name, resp.status, attempt + 1, self.max_retries,
                        )
            except Exception as exc:
                logger.warning(
                    "Market data fetch error for %s: %s (attempt %d/%d)",
                    source_name, exc, attempt + 1, self.max_retries,
                )

            if attempt < self.max_retries - 1:
                delay = self.retry_base_delay * (2 ** attempt)
                await asyncio.sleep(delay)

        logger.error("All %d retries exhausted for %s", self.max_retries, source_name)
        return None

    async def fetch_indices(self) -> MarketIndices:
        """Fetch current market indices from all sources.

        Returns MarketIndices with whatever data was successfully retrieved.
        Errors are captured in the errors list rather than raising.
        """
        now = datetime.now(timezone.utc).isoformat()
        indices = MarketIndices(fetched_at=now)

        if self.stub_mode:
            indices.indices = [self.STUB_INDEX]
            indices.oil = self.STUB_OIL
            indices.gold = self.STUB_GOLD
            return indices

        # Fetch in parallel
        tasks = {
            "boursa_kuwait": self._fetch_with_retry(
                self.MARKET_SOURCES["boursa_kuwait"], "boursa_kuwait"
            ),
            "dubai_finance": self._fetch_with_retry(
                self.MARKET_SOURCES["dubai_finance"], "dubai_finance"
            ),
            "oil_prices": self._fetch_with_retry(
                self.MARKET_SOURCES["oil_prices"], "oil_prices"
            ),
            "gold_prices": self._fetch_with_retry(
                self.MARKET_SOURCES["gold_prices"], "gold_prices"
            ),
        }

        results = await asyncio.gather(
            *tasks.values(), return_exceptions=True
        )
        source_keys = list(tasks.keys())

        for key, result in zip(source_keys, results):
            if isinstance(result, Exception):
                indices.errors.append(f"{key}: {result}")
                continue
            if result is None:
                indices.errors.append(f"{key}: all retries exhausted")
                continue

            # Parse based on source
            try:
                if key == "boursa_kuwait":
                    indices.indices.append(MarketIndex(
                        source=key,
                        name=result.get("name", "Boursa Kuwait"),
                        value=float(result.get("value", 0)),
                        change_pct=float(result.get("change_pct", 0)),
                        currency="KWD",
                        timestamp=result.get("timestamp", now),
                        raw=result,
                    ))
                elif key == "dubai_finance":
                    indices.indices.append(MarketIndex(
                        source=key,
                        name=result.get("name", "DIFC"),
                        value=float(result.get("value", 0)),
                        change_pct=float(result.get("change_pct", 0)),
                        currency="AED",
                        timestamp=result.get("timestamp", now),
                        raw=result,
                    ))
                elif key == "oil_prices":
                    indices.oil = OilPrice(
                        price_usd=float(result.get("price_usd", 0)),
                        change_pct=float(result.get("change_pct", 0)),
                        timestamp=result.get("timestamp", now),
                    )
                elif key == "gold_prices":
                    indices.gold = GoldPrice(
                        price_kwd=float(result.get("price_kwd", 0)),
                        price_usd=float(result.get("price_usd", 0)),
                        change_pct=float(result.get("change_pct", 0)),
                        timestamp=result.get("timestamp", now),
                    )
            except (KeyError, ValueError, TypeError) as exc:
                indices.errors.append(f"{key}: parse error: {exc}")

        self._set_cache("indices", indices)
        self._last_fetch = time.time()
        return indices

    async def fetch_oil_price(self) -> OilPrice:
        """Fetch current Brent crude price."""
        cached = self._get_cache("oil")
        if cached:
            return cached

        if self.stub_mode:
            self._set_cache("oil", self.STUB_OIL)
            return self.STUB_OIL

        data = await self._fetch_with_retry(
            self.MARKET_SOURCES["oil_prices"], "oil_prices"
        )
        if data is None:
            raise MarketDataError("Failed to fetch oil price after retries")

        price = OilPrice(
            price_usd=float(data.get("price_usd", 0)),
            change_pct=float(data.get("change_pct", 0)),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )
        self._set_cache("oil", price)
        return price

    async def fetch_gold_price(self) -> GoldPrice:
        """Fetch current gold price in KWD."""
        cached = self._get_cache("gold")
        if cached:
            return cached

        if self.stub_mode:
            self._set_cache("gold", self.STUB_GOLD)
            return self.STUB_GOLD

        data = await self._fetch_with_retry(
            self.MARKET_SOURCES["gold_prices"], "gold_prices"
        )
        if data is None:
            raise MarketDataError("Failed to fetch gold price after retries")

        price = GoldPrice(
            price_kwd=float(data.get("price_kwd", 0)),
            price_usd=float(data.get("price_usd", 0)),
            change_pct=float(data.get("change_pct", 0)),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )
        self._set_cache("gold", price)
        return price

    async def get_cache_status(self) -> Dict[str, Any]:
        """Return cache freshness info for observability."""
        status = {}
        for key in ["indices", "oil", "gold"]:
            if key in self._cache_timestamps:
                age = time.time() - self._cache_timestamps[key]
                status[key] = {
                    "cached": True,
                    "age_seconds": round(age, 1),
                    "fresh": age < self.update_interval_seconds,
                }
            else:
                status[key] = {"cached": False, "age_seconds": None, "fresh": False}
        return status
