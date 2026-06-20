"""Tests for MarketDataIngestor."""

from __future__ import annotations

import asyncio
import os
import time

import pytest


from almuhalab_custom_skills.trading_intel.market_data import (
    MarketDataError,
    MarketDataIngestor,
    MarketIndices,
    MarketSource,
    OilPrice,
    GoldPrice,
    MarketIndex,
)


class TestMarketIndexModel:
    """Test MarketIndex data model."""

    def test_boursa_index_property(self):
        idx = MarketIndices(indices=[
            MarketIndex(source="boursa_kuwait", name="BKM", value=7500, change_pct=1.0),
        ])
        assert idx.boursa_index is not None
        assert idx.boursa_index.value == 7500

    def test_boursa_index_missing(self):
        idx = MarketIndices(indices=[])
        assert idx.boursa_index is None

    def test_brent_price_property(self):
        oil = OilPrice(price_usd=80.0, change_pct=-0.5)
        idx = MarketIndices(oil=oil)
        assert idx.brent_price == 80.0

    def test_brent_price_missing(self):
        idx = MarketIndices(oil=None)
        assert idx.brent_price is None

    def test_empty_indices(self):
        idx = MarketIndices()
        assert idx.indices == []
        assert idx.oil is None
        assert idx.gold is None
        assert idx.errors == []


class TestMarketDataIngestor:
    """Test MarketDataIngestor."""

    def test_init_default(self):
        ingestor = MarketDataIngestor()
        assert ingestor.update_interval_seconds == 300
        assert ingestor.max_retries == 3
        assert ingestor.stub_mode is False

    def test_init_stub_mode(self):
        ingestor = MarketDataIngestor(stub_mode=True)
        assert ingestor.stub_mode is True

    def test_init_custom_params(self):
        ingestor = MarketDataIngestor(
            update_interval_seconds=60,
            max_retries=5,
            retry_base_delay=0.5,
        )
        assert ingestor.update_interval_seconds == 60
        assert ingestor.max_retries == 5
        assert ingestor.retry_base_delay == 0.5

    def test_cache_freshness(self):
        ingestor = MarketDataIngestor(update_interval_seconds=10)
        assert ingestor._is_cache_valid("nonexistent") is False
        ingestor._set_cache("test_key", "test_value")
        assert ingestor._is_cache_valid("test_key") is True
        assert ingestor._get_cache("test_key") == "test_value"

    def test_cache_expiry(self):
        ingestor = MarketDataIngestor(update_interval_seconds=0)
        ingestor._set_cache("test_key", "test_value")
        # With 0-second interval, cache should be invalid immediately
        # (unless checked in the same sub-second window)
        import time
        time.sleep(0.01)
        assert ingestor._is_cache_valid("test_key") is False
        assert ingestor._get_cache("test_key") is None


class TestStubMode:
    """Test stub mode returns valid data."""

    @pytest.mark.asyncio
    async def test_fetch_indices_stub(self):
        ingestor = MarketDataIngestor(stub_mode=True)
        indices = await ingestor.fetch_indices()
        assert isinstance(indices, MarketIndices)
        assert len(indices.indices) == 1
        assert indices.oil is not None
        assert indices.gold is not None
        assert indices.oil.price_usd > 0
        assert indices.gold.price_kwd > 0
        assert indices.boursa_index is not None
        assert indices.boursa_index.value > 0

    @pytest.mark.asyncio
    async def test_fetch_oil_price_stub(self):
        ingestor = MarketDataIngestor(stub_mode=True)
        oil = await ingestor.fetch_oil_price()
        assert isinstance(oil, OilPrice)
        assert oil.price_usd > 0

    @pytest.mark.asyncio
    async def test_fetch_gold_price_stub(self):
        ingestor = MarketDataIngestor(stub_mode=True)
        gold = await ingestor.fetch_gold_price()
        assert isinstance(gold, GoldPrice)
        assert gold.price_kwd > 0
        assert gold.price_usd > 0

    @pytest.mark.asyncio
    async def test_cache_returns_fresh(self):
        ingestor = MarketDataIngestor(stub_mode=True, update_interval_seconds=300)
        oil1 = await ingestor.fetch_oil_price()
        oil2 = await ingestor.fetch_oil_price()
        # Second call should hit cache (same object)
        assert oil1 is oil2


class TestCacheStatus:
    """Test cache status reporting."""

    @pytest.mark.asyncio
    async def test_cache_status_empty(self):
        ingestor = MarketDataIngestor(stub_mode=True)
        status = await ingestor.get_cache_status()
        assert "indices" in status
        assert "oil" in status
        assert "gold" in status

    @pytest.mark.asyncio
    async def test_cache_status_after_fetch(self):
        ingestor = MarketDataIngestor(stub_mode=True)
        await ingestor.fetch_oil_price()
        status = await ingestor.get_cache_status()
        assert status["oil"]["cached"] is True
        assert status["oil"]["fresh"] is True
        assert status["oil"]["age_seconds"] is not None


class TestMarketSourceEnum:
    """Test MarketSource enum values."""

    def test_sources_exist(self):
        assert MarketSource.BOURSA_KUWAIT.value == "boursa_kuwait"
        assert MarketSource.OIL_PRICES.value == "oil_prices"
        assert MarketSource.GOLD_PRICES.value == "gold_prices"
        assert MarketSource.DUBAI_FINANCE.value == "dubai_finance"
