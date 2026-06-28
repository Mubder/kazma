"""Tests for TradeDataCorrelator."""

from __future__ import annotations

import pytest

from almuhalab_custom_skills.trading_intel.correlator import (
    CorrelatedFactor,
    CorrelationDirection,
    CorrelationResult,
    Division,
    DivisionTradeData,
    DroneInsight,
    ImpactSeverity,
    TradeDataCorrelator,
)


@pytest.fixture
def correlator():
    return TradeDataCorrelator()


@pytest.fixture
def gas_oil_data():
    return DivisionTradeData(
        division=Division.GAS_OIL,
        period="2026-Q2",
        contract_volume=1_500_000,
        contract_change_pct=5.2,
        inventory_level=0.6,
        supplier_lead_days=12,
        revenue_ytd=4_200_000,
        expense_ytd=2_800_000,
    )


@pytest.fixture
def tourism_data():
    return DivisionTradeData(
        division=Division.TOURISM,
        period="2026-Q2",
        contract_volume=800_000,
        inventory_level=0.3,
        supplier_lead_days=7,
    )


@pytest.fixture
def general_data():
    return DivisionTradeData(
        division=Division.GENERAL_TRADING,
        period="2026-Q2",
        contract_volume=2_000_000,
        inventory_level=0.8,
        supplier_lead_days=18,
    )


class TestBasicCorrelation:
    """Test basic correlation without data."""

    @pytest.mark.asyncio
    async def test_empty_correlation(self, correlator):
        result = await correlator.correlate()
        assert isinstance(result, CorrelationResult)
        assert result.division == Division.GENERAL_TRADING
        assert result.overall_impact == ImpactSeverity.NEUTRAL
        assert result.overall_direction == CorrelationDirection.NEUTRAL

    @pytest.mark.asyncio
    async def test_neutral_market(self, correlator):
        result = await correlator.correlate(
            boursa_index=7500,
            boursa_change_pct=0.1,
            oil_price=72.0,
            oil_change_pct=0.2,
            gold_price_kwd=88.0,
            gold_change_pct=0.1,
        )
        assert result.boursa_index == 7500
        assert result.oil_price == 72.0
        assert result.gold_price == 88.0


class TestGasOilCorrelation:
    """Test Gas & Oil division correlations."""

    @pytest.mark.asyncio
    async def test_high_oil_positive(self, correlator, gas_oil_data):
        result = await correlator.correlate(
            oil_price=90.0,
            oil_change_pct=1.0,
            division_data=gas_oil_data,
        )
        assert result.division == Division.GAS_OIL
        assert len(result.factors) > 0
        # High oil should be positive for gas_oil
        pos_factors = [f for f in result.factors if f.direction == CorrelationDirection.POSITIVE]
        assert len(pos_factors) > 0

    @pytest.mark.asyncio
    async def test_low_oil_negative(self, correlator, gas_oil_data):
        result = await correlator.correlate(
            oil_price=50.0,
            oil_change_pct=-2.0,
            division_data=gas_oil_data,
        )
        neg_factors = [f for f in result.factors if f.direction == CorrelationDirection.NEGATIVE]
        assert len(neg_factors) > 0

    @pytest.mark.asyncio
    async def test_oil_volatility(self, correlator, gas_oil_data):
        result = await correlator.correlate(
            oil_price=72.0,
            oil_change_pct=5.0,  # High volatility
            division_data=gas_oil_data,
        )
        vol_factors = [f for f in result.factors if f.factor_name == "oil_volatility"]
        assert len(vol_factors) == 1
        assert vol_factors[0].severity == ImpactSeverity.HIGH

    @pytest.mark.asyncio
    async def test_drone_critical_findings(self, correlator, gas_oil_data):
        insights = [
            DroneInsight(
                inspection_id="insp-001",
                drone_id="drone-001",
                timestamp="2026-06-20T12:00:00Z",
                findings_count=5,
                critical_findings=3,
                asset_condition="critical",
                supply_chain_risk="high",
            ),
        ]
        result = await correlator.correlate(
            oil_price=72.0,
            division_data=gas_oil_data,
            drone_insights=insights,
        )
        critical = [f for f in result.factors if f.factor_name == "drone_critical_findings"]
        assert len(critical) == 1
        assert critical[0].severity in (ImpactSeverity.CRITICAL, ImpactSeverity.HIGH)

    @pytest.mark.asyncio
    async def test_drone_asset_degradation(self, correlator, gas_oil_data):
        insights = [
            DroneInsight(
                inspection_id="insp-002",
                drone_id="drone-002",
                timestamp="2026-06-20T12:00:00Z",
                findings_count=2,
                critical_findings=0,
                asset_condition="degraded",
                supply_chain_risk="medium",
            ),
        ]
        result = await correlator.correlate(
            oil_price=72.0,
            division_data=gas_oil_data,
            drone_insights=insights,
        )
        degraded = [f for f in result.factors if f.factor_name == "asset_degradation"]
        assert len(degraded) == 1

    @pytest.mark.asyncio
    async def test_overall_impact_critical_when_drone_critical(self, correlator, gas_oil_data):
        insights = [
            DroneInsight(
                inspection_id="insp-003",
                drone_id="drone-003",
                timestamp="2026-06-20T12:00:00Z",
                findings_count=10,
                critical_findings=5,
                asset_condition="critical",
                supply_chain_risk="high",
            ),
        ]
        result = await correlator.correlate(
            oil_price=72.0,
            division_data=gas_oil_data,
            drone_insights=insights,
        )
        assert result.overall_impact == ImpactSeverity.CRITICAL


class TestTourismCorrelation:
    """Test Tourism division correlations."""

    @pytest.mark.asyncio
    async def test_boursa_rise_positive(self, correlator, tourism_data):
        result = await correlator.correlate(
            boursa_index=7600,
            boursa_change_pct=2.0,
            division_data=tourism_data,
        )
        pos = [f for f in result.factors if f.direction == CorrelationDirection.POSITIVE]
        assert len(pos) > 0

    @pytest.mark.asyncio
    async def test_boursa_decline_negative(self, correlator, tourism_data):
        result = await correlator.correlate(
            boursa_index=7200,
            boursa_change_pct=-2.0,
            division_data=tourism_data,
        )
        neg = [f for f in result.factors if f.direction == CorrelationDirection.NEGATIVE]
        assert len(neg) > 0

    @pytest.mark.asyncio
    async def test_gold_luxury_indicator(self, correlator, tourism_data):
        result = await correlator.correlate(
            gold_price_kwd=100.0,
            division_data=tourism_data,
        )
        luxury = [f for f in result.factors if f.factor_name == "luxury_tourism_indicator"]
        assert len(luxury) == 1
        assert luxury[0].direction == CorrelationDirection.POSITIVE


class TestGeneralTradingCorrelation:
    """Test General Trading division correlations."""

    @pytest.mark.asyncio
    async def test_boursa_broad_market(self, correlator, general_data):
        result = await correlator.correlate(
            boursa_index=7500,
            boursa_change_pct=2.5,
            division_data=general_data,
        )
        broad = [f for f in result.factors if f.factor_name == "boursa_broad_market"]
        assert len(broad) == 1

    @pytest.mark.asyncio
    async def test_inventory_price_mismatch(self, correlator, general_data):
        result = await correlator.correlate(
            oil_price=50.0,  # Low oil
            division_data=general_data,  # inventory_level=0.8 (high)
        )
        mismatch = [f for f in result.factors if f.factor_name == "inventory_price_mismatch"]
        assert len(mismatch) == 1
        assert mismatch[0].direction == CorrelationDirection.NEGATIVE

    @pytest.mark.asyncio
    async def test_supply_chain_volatility(self, correlator, general_data):
        result = await correlator.correlate(
            gold_price_kwd=90.0,
            gold_change_pct=4.0,  # High volatility
            division_data=general_data,  # supplier_lead_days=18 (>14)
        )
        supply = [f for f in result.factors if f.factor_name == "supply_chain_volatility"]
        assert len(supply) == 1


class TestOverallComputation:
    """Test overall impact/direction computation."""

    @pytest.mark.asyncio
    async def test_majority_negative_direction(self, correlator, gas_oil_data):
        result = await correlator.correlate(
            oil_price=50.0,   # Low — negative
            oil_change_pct=5.0,  # Volatile — negative
            division_data=gas_oil_data,
        )
        # Both factors are negative
        assert result.overall_direction == CorrelationDirection.NEGATIVE

    @pytest.mark.asyncio
    async def test_critical_severity_propagates(self, correlator, gas_oil_data):
        insights = [
            DroneInsight(
                inspection_id="insp-crit",
                drone_id="drone-x",
                timestamp="2026-06-20T12:00:00Z",
                findings_count=10,
                critical_findings=5,
                asset_condition="critical",
                supply_chain_risk="high",
            ),
        ]
        result = await correlator.correlate(
            oil_price=72.0,
            division_data=gas_oil_data,
            drone_insights=insights,
        )
        assert result.overall_impact == ImpactSeverity.CRITICAL

    @pytest.mark.asyncio
    async def test_summary_populated(self, correlator, gas_oil_data):
        result = await correlator.correlate(
            oil_price=90.0,
            oil_change_pct=1.0,
            division_data=gas_oil_data,
        )
        assert result.summary_en != ""
        assert result.summary_ar != ""
        assert "gas_oil" in result.summary_en.lower() or "gas" in result.summary_en.lower()


class TestCorrelatedFactorModel:
    """Test CorrelatedFactor data model."""

    def test_factor_attributes(self):
        f = CorrelatedFactor(
            factor_name="test",
            market_value=100.0,
            division_value=200.0,
            direction=CorrelationDirection.POSITIVE,
            severity=ImpactSeverity.MEDIUM,
            description="test desc",
            description_ar="test desc ar",
            confidence=0.8,
        )
        assert f.factor_name == "test"
        assert f.confidence == 0.8
