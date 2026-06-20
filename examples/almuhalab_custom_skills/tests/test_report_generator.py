"""Tests for TradingIntelReportGenerator."""

from __future__ import annotations

import asyncio
import os

import pytest


from almuhalab_custom_skills.trading_intel.correlator import (
    TradeDataCorrelator,
    CorrelationResult,
    DivisionTradeData,
    CorrelatedFactor,
    Division,
    ImpactSeverity,
    CorrelationDirection,
)
from almuhalab_custom_skills.trading_intel.report_generator import (
    TradingIntelReportGenerator,
    TradingIntelReport,
    ReportSection,
    UrgencyTag,
    RiskItem,
    OpportunityItem,
    ActionItem,
)


@pytest.fixture
def generator():
    return TradingIntelReportGenerator()


@pytest.fixture
def sample_correlation():
    """A realistic correlation result for testing."""
    corr = CorrelationResult(
        division=Division.GAS_OIL,
        period="2026-Q2",
        oil_price=90.0,
        gold_price=88.5,
        boursa_index=7500,
        overall_impact=ImpactSeverity.HIGH,
        overall_direction=CorrelationDirection.NEGATIVE,
        summary_en="Gas & Oil: 2 risks, 1 opportunity",
        summary_ar="الغاز والنفط: 2 مخاطر، فرصة واحدة",
        correlated_at="2026-06-20T12:00:00Z",
    )
    corr.factors = [
        CorrelatedFactor(
            factor_name="brent_vs_contracts",
            market_value=90.0,
            division_value=1_500_000,
            direction=CorrelationDirection.POSITIVE,
            severity=ImpactSeverity.MEDIUM,
            description="Brent at $90 — favorable for contracts",
            description_ar="برنت عند 90$ — م有利 للتعهدات",
            confidence=0.9,
        ),
        CorrelatedFactor(
            factor_name="oil_volatility",
            market_value=5.0,
            division_value=0,
            direction=CorrelationDirection.NEGATIVE,
            severity=ImpactSeverity.HIGH,
            description="Oil price volatility: +5.0%",
            description_ar="تقلبات أسعار النفط: +5.0%",
            confidence=0.8,
        ),
        CorrelatedFactor(
            factor_name="drone_critical_findings",
            market_value=3.0,
            division_value=0,
            direction=CorrelationDirection.NEGATIVE,
            severity=ImpactSeverity.CRITICAL,
            description="3 critical inspection findings",
            description_ar="3 نتائج فحص حرجة",
            confidence=0.95,
        ),
    ]
    return corr


class TestReportGeneration:
    """Test report generation from correlation results."""

    @pytest.mark.asyncio
    async def test_basic_report(self, generator, sample_correlation):
        report = await generator.generate(sample_correlation, division="gas_oil")
        assert isinstance(report, TradingIntelReport)
        assert report.division == "gas_oil"
        assert report.division_ar == "الغاز والنفط"
        assert report.period == "2026-Q2"
        assert report.report_id.startswith("rpt-")

    @pytest.mark.asyncio
    async def test_market_overview_populated(self, generator, sample_correlation):
        report = await generator.generate(sample_correlation, division="gas_oil")
        assert report.market_overview_en != ""
        assert report.market_overview_ar != ""
        assert report.oil_price == 90.0

    @pytest.mark.asyncio
    async def test_risks_from_negative_factors(self, generator, sample_correlation):
        report = await generator.generate(sample_correlation, division="gas_oil")
        assert len(report.risks) >= 1  # At least the 2 negative factors
        # Oil volatility should be a risk
        risk_titles = [r.title for r in report.risks]
        assert any("volatility" in t.lower() for t in risk_titles)

    @pytest.mark.asyncio
    async def test_opportunities_from_positive_factors(self, generator, sample_correlation):
        report = await generator.generate(sample_correlation, division="gas_oil")
        assert len(report.opportunities) >= 1
        opp_titles = [o.title for o in report.opportunities]
        assert any("contract" in t.lower() or "brent" in t.lower() for t in opp_titles)

    @pytest.mark.asyncio
    async def test_actions_for_critical_risks(self, generator, sample_correlation):
        report = await generator.generate(sample_correlation, division="gas_oil")
        # Should have actions for critical and high risks
        assert len(report.actions) >= 1
        # Critical findings should generate immediate action
        immediate = [a for a in report.actions if a.urgency == UrgencyTag.IMMEDIATE]
        assert len(immediate) >= 1

    @pytest.mark.asyncio
    async def test_summary_populated(self, generator, sample_correlation):
        report = await generator.generate(sample_correlation, division="gas_oil")
        assert report.summary_en != ""
        assert report.summary_ar != ""
        assert "2" in report.summary_en or "risk" in report.summary_en.lower()

    @pytest.mark.asyncio
    async def test_tourism_report(self, generator):
        corr = CorrelationResult(
            division=Division.TOURISM,
            period="2026-Q2",
            boursa_index=7500,
            overall_impact=ImpactSeverity.LOW,
            overall_direction=CorrelationDirection.POSITIVE,
        )
        corr.factors = [
            CorrelatedFactor(
                factor_name="market_confidence_tourism",
                market_value=2.0,
                division_value=0,
                direction=CorrelationDirection.POSITIVE,
                severity=ImpactSeverity.LOW,
                description="Boursa up 2.0% — positive for tourism",
                description_ar="البورصة ارتفعت 2.0% — إيجابي للسياحة",
                confidence=0.7,
            ),
        ]
        report = await generator.generate(corr, division="tourism")
        assert report.division == "tourism"
        assert report.division_ar == "السياحة"

    @pytest.mark.asyncio
    async def test_empty_correlation(self, generator):
        corr = CorrelationResult(
            division=Division.GENERAL_TRADING,
            period="2026-Q2",
        )
        report = await generator.generate(corr, division="general_trading")
        assert report.division == "general_trading"
        assert len(report.risks) == 0
        assert len(report.opportunities) == 0
        assert len(report.actions) == 0


class TestMajlisFormatting:
    """Test Majlis Mode report formatting."""

    @pytest.mark.asyncio
    async def test_majlis_format_output(self, generator, sample_correlation):
        report = await generator.generate(sample_correlation, division="gas_oil")
        majlis_text = await generator.format_for_majlis(report)
        assert isinstance(majlis_text, str)
        assert "تقرير الاستخبارات التجارية" in majlis_text
        assert "الغاز والنفط" in majlis_text
        assert "صورة عامة عن السوق" in majlis_text
        assert "تقييم المخاطر" in majlis_text

    @pytest.mark.asyncio
    async def test_majlis_risk_icons(self, generator, sample_correlation):
        report = await generator.generate(sample_correlation, division="gas_oil")
        majlis_text = await generator.format_for_majlis(report)
        # Critical risks should have red icon
        assert "🔴" in majlis_text or "🟠" in majlis_text

    @pytest.mark.asyncio
    async def test_majlis_action_icons(self, generator, sample_correlation):
        report = await generator.generate(sample_correlation, division="gas_oil")
        majlis_text = await generator.format_for_majlis(report)
        assert "⚡" in majlis_text  # Immediate action icon


class TestReportSections:
    """Test ReportSection enum."""

    def test_all_sections_defined(self):
        sections = [s.value for s in ReportSection]
        assert "market_overview" in sections
        assert "division_impact" in sections
        assert "risk_assessment" in sections
        assert "opportunities" in sections
        assert "recommended_actions" in sections

    def test_generator_has_sections(self, generator):
        assert hasattr(generator, "REPORT_SECTIONS")
        assert len(generator.REPORT_SECTIONS) == 5


class TestUrgencyTags:
    """Test UrgencyTag enum."""

    def test_tags_exist(self):
        assert UrgencyTag.IMMEDIATE.value == "immediate"
        assert UrgencyTag.THIS_WEEK.value == "this_week"
        assert UrgencyTag.THIS_MONTH.value == "this_month"
        assert UrgencyTag.MONITOR.value == "monitor"
