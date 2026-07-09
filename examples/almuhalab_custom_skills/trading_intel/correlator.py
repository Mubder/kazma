"""Trade Data Correlator — Correlates market data with ALMuhalab division trade data.

Combines real-time market indices with division-specific operational data
and drone inspection insights to produce correlation results that drive
trading intelligence reports.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────────────

class Division(str, Enum):
    GAS_OIL = "gas_oil"
    TOURISM = "tourism"
    GENERAL_TRADING = "general_trading"


class ImpactSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NEUTRAL = "neutral"


class CorrelationDirection(str, Enum):
    POSITIVE = "positive"    # Market move helps division
    NEGATIVE = "negative"    # Market move hurts division
    NEUTRAL = "neutral"      # No significant impact


@dataclass
class DivisionTradeData:
    """Operational data for a specific ALMuhalab division."""
    division: Division
    period: str                           # e.g. "2026-Q2"
    contract_volume: float = 0.0          # Active contract value (KWD)
    contract_change_pct: float = 0.0
    inventory_level: float = 0.0          # Current inventory (normalized 0-1)
    supplier_lead_days: float = 0.0       # Average supplier lead time
    revenue_ytd: float = 0.0             # Year-to-date revenue
    expense_ytd: float = 0.0            # Year-to-date expenses
    operational_issues: list[str] = field(default_factory=list)
    custom_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class DroneInsight:
    """Summarized drone inspection insight for correlation."""
    inspection_id: str
    drone_id: str
    timestamp: str
    findings_count: int = 0
    critical_findings: int = 0
    asset_condition: str = "unknown"      # "good", "degraded", "critical"
    supply_chain_risk: str = "low"        # "low", "medium", "high"
    affected_assets: list[str] = field(default_factory=list)


@dataclass
class CorrelatedFactor:
    """A single correlated factor between market and division data."""
    factor_name: str
    market_value: float
    division_value: float
    direction: CorrelationDirection
    severity: ImpactSeverity
    description: str
    description_ar: str
    confidence: float = 0.8              # 0.0 to 1.0


@dataclass
class CorrelationResult:
    """Complete correlation result for a division."""
    division: Division
    period: str
    factors: list[CorrelatedFactor] = field(default_factory=list)
    overall_impact: ImpactSeverity = ImpactSeverity.NEUTRAL
    overall_direction: CorrelationDirection = CorrelationDirection.NEUTRAL
    summary_en: str = ""
    summary_ar: str = ""
    correlated_at: str = ""
    oil_price: float | None = None
    gold_price: float | None = None
    boursa_index: float | None = None

    @property
    def critical_factors(self) -> list[CorrelatedFactor]:
        return [f for f in self.factors if f.severity == ImpactSeverity.CRITICAL]

    @property
    def risk_factors(self) -> list[CorrelatedFactor]:
        return [f for f in self.factors
                if f.severity in (ImpactSeverity.CRITICAL, ImpactSeverity.HIGH)]


# ── Correlation Logic ──────────────────────────────────────────────────

# Correlation thresholds
OIL_HIGH_THRESHOLD = 85.0       # USD — above this, good for gas_oil
OIL_LOW_THRESHOLD = 60.0        # USD — below this, risk for gas_oil
GOLD_HIGH_THRESHOLD = 95.0      # KWD — above this, opportunity for trading
BOURSA_DECLINE_THRESHOLD = -1.0  # pct — below this, broad risk
BOURSA_RISE_THRESHOLD = 1.0      # pct — above this, broad opportunity


class TradeDataCorrelator:
    """Correlates market data with ALMuhalab division trade data.

    Produces CorrelationResult objects with per-factor analysis and
    severity ratings. Supports optional drone inspection insights
    for Gas & Oil division.
    """

    def __init__(self, rbac: Any = None) -> None:
        self.rbac = rbac

    async def correlate(
        self,
        boursa_index: float | None = None,
        boursa_change_pct: float | None = None,
        oil_price: float | None = None,
        oil_change_pct: float | None = None,
        gold_price_kwd: float | None = None,
        gold_change_pct: float | None = None,
        division_data: DivisionTradeData | None = None,
        drone_insights: list[DroneInsight] | None = None,
    ) -> CorrelationResult:
        """Correlate market movements with division-specific factors.

        Args:
            boursa_index: Boursa Kuwait main index value.
            boursa_change_pct: Boursa index daily change percentage.
            oil_price: Brent crude price in USD.
            oil_change_pct: Oil price daily change percentage.
            gold_price_kwd: Gold price in KWD per troy ounce.
            gold_change_pct: Gold price daily change percentage.
            division_data: Operational data for the division.
            drone_insights: Optional drone inspection summaries.

        Returns:
            CorrelationResult with factor-level analysis.
        """
        division = division_data.division if division_data else Division.GENERAL_TRADING
        period = division_data.period if division_data else datetime.now(UTC).strftime("%Y-%m")

        result = CorrelationResult(
            division=division,
            period=period,
            correlated_at=datetime.now(UTC).isoformat(),
            oil_price=oil_price,
            gold_price=gold_price_kwd,
            boursa_index=boursa_index,
        )

        # Correlate based on division
        if division == Division.GAS_OIL:
            self._correlate_gas_oil(result, oil_price, oil_change_pct, division_data, drone_insights)
        elif division == Division.TOURISM:
            self._correlate_tourism(result, boursa_index, boursa_change_pct, gold_price_kwd, division_data)
        elif division == Division.GENERAL_TRADING:
            self._correlate_general(result, boursa_index, boursa_change_pct,
                                    oil_price, gold_price_kwd, gold_change_pct, division_data)

        # Compute overall impact
        self._compute_overall(result)
        return result

    def _correlate_gas_oil(
        self,
        result: CorrelationResult,
        oil_price: float | None,
        oil_change_pct: float | None,
        division_data: DivisionTradeData | None,
        drone_insights: list[DroneInsight] | None,
    ) -> None:
        """Gas & Oil specific correlations."""
        if oil_price is not None:
            # Brent crude vs contract volume
            if oil_price >= OIL_HIGH_THRESHOLD:
                result.factors.append(CorrelatedFactor(
                    factor_name="brent_vs_contracts",
                    market_value=oil_price,
                    division_value=division_data.contract_volume if division_data else 0,
                    direction=CorrelationDirection.POSITIVE,
                    severity=ImpactSeverity.MEDIUM,
                    description=f"Brent crude at ${oil_price:.1f} — favorable for contract pricing",
                    description_ar=f"برنت عند ${oil_price:.1f} — مفيد لتعهدات الأسعار",
                    confidence=0.9,
                ))
            elif oil_price <= OIL_LOW_THRESHOLD:
                result.factors.append(CorrelatedFactor(
                    factor_name="brent_vs_contracts",
                    market_value=oil_price,
                    division_value=division_data.contract_volume if division_data else 0,
                    direction=CorrelationDirection.NEGATIVE,
                    severity=ImpactSeverity.HIGH,
                    description=f"Brent crude at ${oil_price:.1f} — below threshold, contract risk",
                    description_ar=f"برنت عند ${oil_price:.1f} — تحت الحد، مخاطر التعهدات",
                    confidence=0.85,
                ))

            # Oil price volatility
            if oil_change_pct is not None and abs(oil_change_pct) > 3.0:
                result.factors.append(CorrelatedFactor(
                    factor_name="oil_volatility",
                    market_value=oil_change_pct,
                    division_value=0,
                    direction=CorrelationDirection.NEGATIVE,
                    severity=ImpactSeverity.HIGH,
                    description=f"Oil price volatility: {oil_change_pct:+.1f}% — supply chain risk",
                    description_ar=f"تقلبات أسعار النفط: {oil_change_pct:+.1f}% — خطر سلسلة التوريد",
                    confidence=0.8,
                ))

        # Drone inspection insights
        if drone_insights:
            critical_count = sum(d.critical_findings for d in drone_insights)
            if critical_count > 0:
                result.factors.append(CorrelatedFactor(
                    factor_name="drone_critical_findings",
                    market_value=float(critical_count),
                    division_value=0,
                    direction=CorrelationDirection.NEGATIVE,
                    severity=ImpactSeverity.CRITICAL if critical_count >= 3 else ImpactSeverity.HIGH,
                    description=f"{critical_count} critical inspection findings — operational disruption risk",
                    description_ar=f"{critical_count} نتائج فحص حرجة — خطر تعطيل عمليات",
                    confidence=0.95,
                ))

            degraded = [d for d in drone_insights if d.asset_condition == "degraded"]
            if degraded:
                assets = ", ".join(d.drone_id for d in degraded[:5])
                result.factors.append(CorrelatedFactor(
                    factor_name="asset_degradation",
                    market_value=float(len(degraded)),
                    division_value=0,
                    direction=CorrelationDirection.NEGATIVE,
                    severity=ImpactSeverity.MEDIUM,
                    description=f"Asset degradation detected: {assets}",
                    description_ar=f"تدهور الأصول مكتشف: {assets}",
                    confidence=0.85,
                ))

    def _correlate_tourism(
        self,
        result: CorrelationResult,
        boursa_index: float | None,
        boursa_change_pct: float | None,
        gold_price_kwd: float | None,
        division_data: DivisionTradeData | None,
    ) -> None:
        """Tourism division specific correlations."""
        if boursa_change_pct is not None:
            # Market confidence affects tourism bookings
            if boursa_change_pct >= BOURSA_RISE_THRESHOLD:
                result.factors.append(CorrelatedFactor(
                    factor_name="market_confidence_tourism",
                    market_value=boursa_change_pct,
                    division_value=division_data.contract_volume if division_data else 0,
                    direction=CorrelationDirection.POSITIVE,
                    severity=ImpactSeverity.LOW,
                    description=f"Boursa up {boursa_change_pct:+.1f}% — positive market sentiment for tourism",
                    description_ar=f"البورصة ارتفعت {boursa_change_pct:+.1f}% — معنويات سوق إيجابية للسياحة",
                    confidence=0.7,
                ))
            elif boursa_change_pct <= BOURSA_DECLINE_THRESHOLD:
                result.factors.append(CorrelatedFactor(
                    factor_name="market_confidence_tourism",
                    market_value=boursa_change_pct,
                    division_value=division_data.contract_volume if division_data else 0,
                    direction=CorrelationDirection.NEGATIVE,
                    severity=ImpactSeverity.MEDIUM,
                    description=f"Boursa down {boursa_change_pct:+.1f}% — reduced tourism demand expected",
                    description_ar=f"البورصة انخفضت {boursa_change_pct:+.1f}% — توقع انخفاض الطلب السياحي",
                    confidence=0.75,
                ))

        # Gold price correlation (luxury tourism indicator)
        if gold_price_kwd is not None and gold_price_kwd >= GOLD_HIGH_THRESHOLD:
            result.factors.append(CorrelatedFactor(
                factor_name="luxury_tourism_indicator",
                market_value=gold_price_kwd,
                division_value=0,
                direction=CorrelationDirection.POSITIVE,
                severity=ImpactSeverity.LOW,
                description=f"Gold at KWD {gold_price_kwd:.1f} — luxury tourism demand signal",
                description_ar=f"الذهب عند {gold_price_kwd:.1f} د.ك — إشارة طلب سياحة فاخرة",
                confidence=0.6,
            ))

    def _correlate_general(
        self,
        result: CorrelationResult,
        boursa_index: float | None,
        boursa_change_pct: float | None,
        oil_price: float | None,
        gold_price_kwd: float | None,
        gold_change_pct: float | None,
        division_data: DivisionTradeData | None,
    ) -> None:
        """General Trading division correlations."""
        # Boursa broad market impact
        if boursa_change_pct is not None:
            if abs(boursa_change_pct) > 1.0:
                direction = (CorrelationDirection.POSITIVE if boursa_change_pct > 0
                             else CorrelationDirection.NEGATIVE)
                result.factors.append(CorrelatedFactor(
                    factor_name="boursa_broad_market",
                    market_value=boursa_change_pct,
                    division_value=division_data.contract_volume if division_data else 0,
                    direction=direction,
                    severity=ImpactSeverity.MEDIUM,
                    description=f"Boursa {boursa_change_pct:+.1f}% — broad market movement affects trading",
                    description_ar=f"البورصة {boursa_change_pct:+.1f}% — حركة سوق عامة تؤثر على التجارة",
                    confidence=0.75,
                ))

        # Inventory vs commodity prices
        if division_data and oil_price is not None:
            if division_data.inventory_level > 0.7 and oil_price < OIL_LOW_THRESHOLD:
                result.factors.append(CorrelatedFactor(
                    factor_name="inventory_price_mismatch",
                    market_value=oil_price,
                    division_value=division_data.inventory_level,
                    direction=CorrelationDirection.NEGATIVE,
                    severity=ImpactSeverity.MEDIUM,
                    description=f"High inventory ({division_data.inventory_level:.0%}) with low oil price",
                    description_ar=f"مخزون مرتفع ({division_data.inventory_level:.0%}) مع سعر نفط منخفض",
                    confidence=0.8,
                ))

        # Supplier lead times vs gold volatility
        if division_data and gold_change_pct is not None and abs(gold_change_pct) > 2.0:
            if division_data.supplier_lead_days > 14:
                result.factors.append(CorrelatedFactor(
                    factor_name="supply_chain_volatility",
                    market_value=gold_change_pct,
                    division_value=division_data.supplier_lead_days,
                    direction=CorrelationDirection.NEGATIVE,
                    severity=ImpactSeverity.HIGH,
                    description=f"Long lead times ({division_data.supplier_lead_days:.0f}d) with gold volatility {gold_change_pct:+.1f}%",
                    description_ar=f"أوقات توريد طويلة ({division_data.supplier_lead_days:.0f} يوم) مع تقلبات ذهب {gold_change_pct:+.1f}%",
                    confidence=0.7,
                ))

    def _compute_overall(self, result: CorrelationResult) -> None:
        """Compute overall impact and direction from individual factors."""
        if not result.factors:
            result.overall_impact = ImpactSeverity.NEUTRAL
            result.overall_direction = CorrelationDirection.NEUTRAL
            return

        # Overall direction: majority wins
        pos = sum(1 for f in result.factors if f.direction == CorrelationDirection.POSITIVE)
        neg = sum(1 for f in result.factors if f.direction == CorrelationDirection.NEGATIVE)

        if neg > pos:
            result.overall_direction = CorrelationDirection.NEGATIVE
        elif pos > neg:
            result.overall_direction = CorrelationDirection.POSITIVE
        else:
            result.overall_direction = CorrelationDirection.NEUTRAL

        # Overall severity: worst wins
        severity_order = {
            ImpactSeverity.CRITICAL: 4,
            ImpactSeverity.HIGH: 3,
            ImpactSeverity.MEDIUM: 2,
            ImpactSeverity.LOW: 1,
            ImpactSeverity.NEUTRAL: 0,
        }
        worst = max(result.factors, key=lambda f: severity_order.get(f.severity, 0))
        result.overall_impact = worst.severity

        # Division-specific summaries
        division_name = result.division.value
        direction_ar = {
            "positive": "إيجابي",
            "negative": "سلبي",
            "neutral": "محايد",
        }
        impact_ar = {
            "critical": "حرج",
            "high": "مرتفع",
            "medium": "متوسط",
            "low": "منخفض",
            "neutral": "محايد",
        }
        result.summary_en = (
            f"Overall impact for {division_name}: {result.overall_impact.value} "
            f"({result.overall_direction.value}), {len(result.factors)} factors"
        )
        result.summary_ar = (
            f"التأثير العام لـ {division_name}: "
            f"{impact_ar.get(result.overall_impact.value, result.overall_impact.value)} "
            f"({direction_ar.get(result.overall_direction.value, '')}) — "
            f"{len(result.factors)} عوامل"
        )
