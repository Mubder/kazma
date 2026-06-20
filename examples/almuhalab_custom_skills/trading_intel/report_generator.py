"""Intelligence Report Generator — Produces actionable trading intel reports.

Generates structured reports in Kuwaiti Arabic with market overview,
division impact analysis, risk assessment, opportunities, and
recommended actions. Supports Majlis Mode formatting.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from almuhalab_custom_skills.trading_intel.correlator import (
    CorrelatedFactor,
    CorrelationResult,
    CorrelationDirection,
    Division,
    ImpactSeverity,
)

logger = logging.getLogger(__name__)


# ── Report Models ──────────────────────────────────────────────────────

class UrgencyTag(str, Enum):
    IMMEDIATE = "immediate"       # فوري — act now
    THIS_WEEK = "this_week"       # هذا الأسبوع — within 7 days
    THIS_MONTH = "this_month"     # هذا الشهر — within 30 days
    MONITOR = "monitor"           # مراقبة — watch and wait


class ReportSection(str, Enum):
    MARKET_OVERVIEW = "market_overview"            # صورة عامة عن السوق
    DIVISION_IMPACT = "division_impact"             # تأثير الأقسام
    RISK_ASSESSMENT = "risk_assessment"             # تقييم المخاطر
    OPPORTUNITIES = "opportunities"                 # الفرص
    RECOMMENDED_ACTIONS = "recommended_actions"     # الإجراءات المقترحة


@dataclass
class RiskItem:
    """A single risk in the risk assessment section."""
    title: str
    title_ar: str
    description: str
    description_ar: str
    severity: ImpactSeverity
    likelihood: float              # 0.0 to 1.0
    mitigation: str
    mitigation_ar: str


@dataclass
class OpportunityItem:
    """A single opportunity in the opportunities section."""
    title: str
    title_ar: str
    description: str
    description_ar: str
    potential_value_kwd: float
    urgency: UrgencyTag
    confidence: float              # 0.0 to 1.0


@dataclass
class ActionItem:
    """A single recommended action."""
    action: str
    action_ar: str
    urgency: UrgencyTag
    responsible_division: str
    responsible_division_ar: str
    expected_outcome: str
    expected_outcome_ar: str


@dataclass
class TradingIntelReport:
    """Complete trading intelligence report."""
    report_id: str
    division: str
    division_ar: str
    period: str
    generated_at: str
    language: str = "kw"                   # "kw" for Kuwaiti Arabic

    # Market Overview section
    market_overview_en: str = ""
    market_overview_ar: str = ""
    oil_price: Optional[float] = None
    gold_price_kwd: Optional[float] = None
    boursa_index: Optional[float] = None

    # Risk Assessment section
    risks: List[RiskItem] = field(default_factory=list)

    # Opportunities section
    opportunities: List[OpportunityItem] = field(default_factory=list)

    # Recommended Actions section
    actions: List[ActionItem] = field(default_factory=list)

    # Overall
    overall_severity: ImpactSeverity = ImpactSeverity.NEUTRAL
    summary_en: str = ""
    summary_ar: str = ""
    correlation_summary: str = ""
    correlation_summary_ar: str = ""


# ── Report Generator ───────────────────────────────────────────────────

# Market overview templates (Kuwaiti Arabic)
_MARKET_TEMPLATES: Dict[str, Dict[str, str]] = {
    "gas_oil": {
        "oil_high": (
            "سعر النفط مرتفع عند ${price:.1f} — بيئة م有利 للتعهدات. "
            "يجب مراجعة أسعار البيع لضمان هوامش الربح."
        ),
        "oil_low": (
            "سعر النفط منخفض عند ${price:.1f} — ضغط على هوامش الربح. "
            "يجب مراجعة نموذج التسعير وتقليل المخزون."
        ),
        "oil_normal": (
            "سعر النفط مستقر عند ${price:.1f} — الظروف م有利 للعمل اليومي."
        ),
    },
    "tourism": {
        "market_up": (
            "البورصة ترتفع — معنويات السوق إيجابية. "
            "وقت مناسب لزيادة التسويق والحجوزات."
        ),
        "market_down": (
            "البورصة تنخفض — قد يؤثر على الطلب السياحي. "
            "ركز على العروض والخصومات."
        ),
        "market_stable": (
            "السوق مستقر — استمر في الخطة التسويقية الحالية."
        ),
    },
    "general_trading": {
        "volatile": (
            "تقلبات في السوق — مخاطر مرتفعة. "
            "قلل المخزون وراقب أسعار السلع."
        ),
        "stable": (
            "السوق مستقر — ظروف م有利 للتجارة."
        ),
    },
}


class TradingIntelReportGenerator:
    """Generates actionable trading intelligence reports in Kuwaiti Arabic.

    Takes CorrelationResult and produces structured reports with:
    - Market overview (صورة عامة عن السوق)
    - Division impact analysis (تأثير الأقسام)
    - Risk assessment (تقييم المخاطر)
    - Opportunities (الفرص)
    - Recommended actions (الإجراءات المقترحة)
    """

    REPORT_SECTIONS = [s.value for s in ReportSection]

    def __init__(self, tracer: Any = None) -> None:
        self.tracer = tracer

    async def generate(
        self,
        correlation: CorrelationResult,
        division: str = "general_trading",
        language: str = "kw",
    ) -> TradingIntelReport:
        """Generate a complete trading intelligence report.

        Args:
            correlation: CorrelationResult from TradeDataCorrelator.
            division: Division identifier string.
            language: Report language code (default "kw" for Kuwaiti Arabic).

        Returns:
            TradingIntelReport with all sections populated.
        """
        report_id = f"rpt-{int(time.time())}-{division}"
        division_enum = Division(division) if division in [d.value for d in Division] else Division.GENERAL_TRADING
        division_ar_map = {
            "gas_oil": "الغاز والنفط",
            "tourism": "السياحة",
            "general_trading": "التجارة العامة",
        }

        report = TradingIntelReport(
            report_id=report_id,
            division=division,
            division_ar=division_ar_map.get(division, division),
            period=correlation.period,
            generated_at=datetime.now(timezone.utc).isoformat(),
            language=language,
            oil_price=correlation.oil_price,
            gold_price_kwd=correlation.gold_price,
            boursa_index=correlation.boursa_index,
            overall_severity=correlation.overall_impact,
            correlation_summary=correlation.summary_en,
            correlation_summary_ar=correlation.summary_ar,
        )

        # 1. Market Overview
        self._build_market_overview(report, correlation, division_enum)

        # 2. Risks from correlated factors
        self._build_risks(report, correlation)

        # 3. Opportunities
        self._build_opportunities(report, correlation, division_enum)

        # 4. Recommended Actions
        self._build_actions(report, correlation)

        # 5. Overall summary
        self._build_summary(report, correlation)

        logger.info(
            "Generated report %s for %s: %d risks, %d opportunities, %d actions",
            report_id, division, len(report.risks),
            len(report.opportunities), len(report.actions),
        )
        return report

    def _build_market_overview(
        self, report: TradingIntelReport, corr: CorrelationResult, division: Division
    ) -> None:
        """Build the market overview section."""
        parts_en = []
        parts_ar = []

        if corr.oil_price:
            parts_en.append(f"Brent crude: ${corr.oil_price:.1f}")
            parts_ar.append(f"برنت: ${corr.oil_price:.1f}")

        if corr.gold_price:
            parts_en.append(f"Gold: KWD {corr.gold_price:.1f}")
            parts_ar.append(f"الذهب: {corr.gold_price:.1f} د.ك")

        if corr.boursa_index:
            parts_en.append(f"Boursa Kuwait: {corr.boursa_index:.0f}")
            parts_ar.append(f"البورصة الكويتية: {corr.boursa_index:.0f}")

        # Division-specific narrative
        if division == Division.GAS_OIL and corr.oil_price:
            if corr.oil_price >= 85:
                narrative_ar = _MARKET_TEMPLATES["gas_oil"]["oil_high"].format(price=corr.oil_price)
                narrative_en = f"Brent at ${corr.oil_price:.1f} — favorable for contract pricing"
            elif corr.oil_price <= 60:
                narrative_ar = _MARKET_TEMPLATES["gas_oil"]["oil_low"].format(price=corr.oil_price)
                narrative_en = f"Brent at ${corr.oil_price:.1f} — margin pressure"
            else:
                narrative_ar = _MARKET_TEMPLATES["gas_oil"]["oil_normal"].format(price=corr.oil_price)
                narrative_en = f"Brent at ${corr.oil_price:.1f} — stable conditions"
            parts_en.append(narrative_en)
            parts_ar.append(narrative_ar)
        elif division == Division.TOURISM:
            if corr.boursa_index and corr.boursa_index > 0:
                parts_ar.append(_MARKET_TEMPLATES["tourism"]["market_stable"])
            parts_en.append("Tourism market conditions assessed")
        else:
            parts_ar.append(_MARKET_TEMPLATES["general_trading"]["stable"])

        report.market_overview_en = " | ".join(parts_en) if parts_en else "Market data pending"
        report.market_overview_ar = " | ".join(parts_ar) if parts_ar else "بيانات السوق قيد الانتظار"

    def _build_risks(self, report: TradingIntelReport, corr: CorrelationResult) -> None:
        """Build risk assessment from negative correlated factors."""
        for factor in corr.factors:
            if factor.direction == CorrelationDirection.NEGATIVE and factor.severity != ImpactSeverity.NEUTRAL:
                likelihood = 0.9 if factor.severity == ImpactSeverity.CRITICAL else 0.7 if factor.severity == ImpactSeverity.HIGH else 0.5
                report.risks.append(RiskItem(
                    title=factor.description,
                    title_ar=factor.description_ar,
                    description=f"Market value: {factor.market_value}, Direction: {factor.direction.value}",
                    description_ar=f"القيمة السوقية: {factor.market_value}",
                    severity=factor.severity,
                    likelihood=likelihood,
                    mitigation=f"Review and adjust strategy for {factor.factor_name}",
                    mitigation_ar=f"مراجعة وتعديل الاستراتيجية لـ {factor.factor_name}",
                ))

    def _build_opportunities(
        self, report: TradingIntelReport, corr: CorrelationResult, division: Division
    ) -> None:
        """Build opportunities from positive correlated factors."""
        for factor in corr.factors:
            if factor.direction == CorrelationDirection.POSITIVE:
                urgency = UrgencyTag.THIS_WEEK if factor.severity in (ImpactSeverity.HIGH, ImpactSeverity.MEDIUM) else UrgencyTag.THIS_MONTH
                report.opportunities.append(OpportunityItem(
                    title=factor.description,
                    title_ar=factor.description_ar,
                    description=f"Leverage {factor.factor_name} for competitive advantage",
                    description_ar=f"استغلال {factor.factor_name} للحصول على ميزة تنافسية",
                    potential_value_kwd=factor.market_value * 100,  # rough estimate
                    urgency=urgency,
                    confidence=factor.confidence,
                ))

    def _build_actions(self, report: TradingIntelReport, corr: CorrelationResult) -> None:
        """Build recommended actions based on risks and opportunities."""
        div_ar = {
            "gas_oil": "الغاز والنفط",
            "tourism": "السياحة",
            "general_trading": "التجارة العامة",
        }
        division_ar = div_ar.get(corr.division.value, corr.division.value)

        # For each critical/high risk, create an action
        for risk in report.risks:
            if risk.severity in (ImpactSeverity.CRITICAL, ImpactSeverity.HIGH):
                urgency = UrgencyTag.IMMEDIATE if risk.severity == ImpactSeverity.CRITICAL else UrgencyTag.THIS_WEEK
                report.actions.append(ActionItem(
                    action=f"Address: {risk.title}",
                    action_ar=f"معالجة: {risk.title_ar}",
                    urgency=urgency,
                    responsible_division=corr.division.value,
                    responsible_division_ar=division_ar,
                    expected_outcome=f"Mitigate risk: {risk.title}",
                    expected_outcome_ar=f"تخفيف المخاطر: {risk.title_ar}",
                ))

        # For each high-confidence opportunity, create an action
        for opp in report.opportunities:
            if opp.confidence >= 0.7:
                report.actions.append(ActionItem(
                    action=f"Pursue: {opp.title}",
                    action_ar=f"المضي قدماً: {opp.title_ar}",
                    urgency=opp.urgency,
                    responsible_division=corr.division.value,
                    responsible_division_ar=division_ar,
                    expected_outcome=f"Capture opportunity worth ~KWD {opp.potential_value_kwd:.0f}",
                    expected_outcome_ar=f"استغلال فرصة بقيمة ~{opp.potential_value_kwd:.0f} د.ك",
                ))

    def _build_summary(self, report: TradingIntelReport, corr: CorrelationResult) -> None:
        """Build overall summary."""
        severity_ar = {
            "critical": "حرج — إجراء فوري مطلوب",
            "high": "مرتفع — مراقبة وثيقة",
            "medium": "متوسط — مراجعة دورية",
            "low": "منخفض — استمرار روتيني",
            "neutral": "محايد — لا إجراء مطلوب",
        }
        report.summary_en = (
            f"Trading Intel for {report.division}: "
            f"{len(report.risks)} risks, {len(report.opportunities)} opportunities, "
            f"{len(report.actions)} actions recommended. "
            f"Overall: {report.overall_severity.value}"
        )
        report.summary_ar = (
            f"الاستخبارات التجارية لـ {report.division_ar}: "
            f"{len(report.risks)} مخاطر، {len(report.opportunities)} فرص، "
            f"{len(report.actions)} إجراءات مقترحة. "
            f"الم整体: {severity_ar.get(report.overall_severity.value, report.overall_severity.value)}"
        )

    async def format_for_majlis(self, report: TradingIntelReport) -> str:
        """Format report for Majlis Mode presentation.

        Returns a clean, readable text format suitable for
        presentation in a Majlis (meeting) setting.
        """
        lines = []
        lines.append(f"═══ تقرير الاستخبارات التجارية ═══")
        lines.append(f"القسم: {report.division_ar}")
        lines.append(f"الفترة: {report.period}")
        lines.append(f"التاريخ: {report.generated_at[:10]}")
        lines.append("")

        # Market Overview
        lines.append("── صورة عامة عن السوق ──")
        lines.append(report.market_overview_ar)
        lines.append("")

        # Risks
        if report.risks:
            lines.append("── تقييم المخاطر ──")
            for i, risk in enumerate(report.risks, 1):
                severity_sym = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(risk.severity.value, "⚪")
                lines.append(f"{severity_sym} {i}. {risk.title_ar}")
                lines.append(f"   الاحتمال: {risk.likelihood:.0%}")
                lines.append(f"   الحد: {risk.mitigation_ar}")
            lines.append("")

        # Opportunities
        if report.opportunities:
            lines.append("── الفرص ──")
            for i, opp in enumerate(report.opportunities, 1):
                lines.append(f"💡 {i}. {opp.title_ar}")
                lines.append(f"   القيمة: ~{opp.potential_value_kwd:.0f} د.ك")
                lines.append(f"   الأولوية: {opp.urgency.value}")
            lines.append("")

        # Actions
        if report.actions:
            lines.append("── الإجراءات المقترحة ──")
            for i, action in enumerate(report.actions, 1):
                urgency_sym = {"immediate": "⚡", "this_week": "📅", "this_month": "📆", "monitor": "👁"}.get(action.urgency.value, "•")
                lines.append(f"{urgency_sym} {i}. {action.action_ar}")
                lines.append(f"   المسؤول: {action.responsible_division_ar}")
            lines.append("")

        # Summary
        lines.append(f"── الملخص ──")
        lines.append(report.summary_ar)

        return "\n".join(lines)
