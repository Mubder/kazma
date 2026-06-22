"""Report Store — SQLite-backed storage for trading intelligence reports.

Stores, retrieves, and searches TradingIntelReport objects with
filtering by division, date range, and severity.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB = str(Path(__file__).resolve().parent.parent.parent / "kazma-data" / "reports.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trading_reports (
    report_id TEXT PRIMARY KEY,
    division TEXT NOT NULL,
    division_ar TEXT NOT NULL,
    period TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'kw',
    generated_at TEXT NOT NULL,
    overall_severity TEXT NOT NULL DEFAULT 'neutral',
    oil_price REAL,
    gold_price_kwd REAL,
    boursa_index REAL,
    summary_en TEXT NOT NULL DEFAULT '',
    summary_ar TEXT NOT NULL DEFAULT '',
    market_overview_en TEXT NOT NULL DEFAULT '',
    market_overview_ar TEXT NOT NULL DEFAULT '',
    risks_json TEXT NOT NULL DEFAULT '[]',
    opportunities_json TEXT NOT NULL DEFAULT '[]',
    actions_json TEXT NOT NULL DEFAULT '[]',
    report_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_reports_division ON trading_reports(division);
CREATE INDEX IF NOT EXISTS idx_reports_generated ON trading_reports(generated_at);
CREATE INDEX IF NOT EXISTS idx_reports_severity ON trading_reports(overall_severity);
CREATE INDEX IF NOT EXISTS idx_reports_period ON trading_reports(period);
"""


class ReportStoreError(Exception):
    """Raised when report storage operations fail."""

    pass


class ReportStore:
    """Stores and retrieves trading intelligence reports.

    Uses SQLite with ajson-serialized risk/opportunity/action arrays
    for efficient filtering.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    async def store(self, report: Any) -> str:
        """Store a TradingIntelReport, return report_id.

        The report object must have attributes matching TradingIntelReport.
        """
        report_id = report.report_id

        # Serialize nested objects
        risks_json = json.dumps(
            [
                {
                    "title": r.title,
                    "title_ar": r.title_ar,
                    "description": r.description,
                    "description_ar": r.description_ar,
                    "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                    "likelihood": r.likelihood,
                    "mitigation": r.mitigation,
                    "mitigation_ar": r.mitigation_ar,
                }
                for r in (report.risks or [])
            ],
            ensure_ascii=False,
        )

        opps_json = json.dumps(
            [
                {
                    "title": o.title,
                    "title_ar": o.title_ar,
                    "description": o.description,
                    "description_ar": o.description_ar,
                    "potential_value_kwd": o.potential_value_kwd,
                    "urgency": o.urgency.value if hasattr(o.urgency, "value") else str(o.urgency),
                    "confidence": o.confidence,
                }
                for o in (report.opportunities or [])
            ],
            ensure_ascii=False,
        )

        actions_json = json.dumps(
            [
                {
                    "action": a.action,
                    "action_ar": a.action_ar,
                    "urgency": a.urgency.value if hasattr(a.urgency, "value") else str(a.urgency),
                    "responsible_division": a.responsible_division,
                    "responsible_division_ar": a.responsible_division_ar,
                    "expected_outcome": a.expected_outcome,
                    "expected_outcome_ar": a.expected_outcome_ar,
                }
                for a in (report.actions or [])
            ],
            ensure_ascii=False,
        )

        # Full report as JSON
        report_full = {
            "report_id": report_id,
            "division": report.division,
            "division_ar": report.division_ar,
            "period": report.period,
            "language": report.language,
            "generated_at": report.generated_at,
            "overall_severity": report.overall_severity.value
            if hasattr(report.overall_severity, "value")
            else str(report.overall_severity),
            "oil_price": report.oil_price,
            "gold_price_kwd": report.gold_price_kwd,
            "boursa_index": report.boursa_index,
            "summary_en": report.summary_en,
            "summary_ar": report.summary_ar,
        }

        severity_val = (
            report.overall_severity.value if hasattr(report.overall_severity, "value") else str(report.overall_severity)
        )

        sql = """
            INSERT OR REPLACE INTO trading_reports
            (report_id, division, division_ar, period, language, generated_at,
             overall_severity, oil_price, gold_price_kwd, boursa_index,
             summary_en, summary_ar, market_overview_en, market_overview_ar,
             risks_json, opportunities_json, actions_json, report_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            report_id,
            report.division,
            report.division_ar,
            report.period,
            report.language,
            report.generated_at,
            severity_val,
            report.oil_price,
            report.gold_price_kwd,
            report.boursa_index,
            report.summary_en,
            report.summary_ar,
            report.market_overview_en,
            report.market_overview_ar,
            risks_json,
            opps_json,
            actions_json,
            json.dumps(report_full, ensure_ascii=False),
        )

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(sql, params)
            conn.commit()
            logger.info("Stored report %s for %s", report_id, report.division)
            return report_id
        except sqlite3.Error as exc:
            raise ReportStoreError(f"Failed to store report {report_id}: {exc}") from exc
        finally:
            conn.close()

    async def get_latest(self, division: str) -> dict[str, Any] | None:
        """Get the most recent report for a division."""
        sql = """
            SELECT report_json FROM trading_reports
            WHERE division = ?
            ORDER BY generated_at DESC
            LIMIT 1
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(sql, (division,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    async def search(
        self,
        division: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        severity: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search reports with optional filters.

        Args:
            division: Filter by division name.
            date_from: ISO date string lower bound.
            date_to: ISO date string upper bound.
            severity: Filter by severity level.
            limit: Maximum results.

        Returns:
            List of report dicts.
        """
        conditions = []
        params: list[Any] = []

        if division:
            conditions.append("division = ?")
            params.append(division)
        if date_from:
            conditions.append("generated_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("generated_at <= ?")
            params.append(date_to)
        if severity:
            conditions.append("overall_severity = ?")
            params.append(severity)

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"""
            SELECT report_json FROM trading_reports
            WHERE {where}
            ORDER BY generated_at DESC
            LIMIT ?
        """
        params.append(limit)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    async def get_report_by_id(self, report_id: str) -> dict[str, Any] | None:
        """Get a specific report by ID."""
        sql = "SELECT report_json FROM trading_reports WHERE report_id = ?"
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(sql, (report_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    async def count(self, division: str | None = None) -> int:
        """Count reports, optionally filtered by division."""
        if division:
            sql = "SELECT COUNT(*) FROM trading_reports WHERE division = ?"
            params = (division,)
        else:
            sql = "SELECT COUNT(*) FROM trading_reports"
            params = ()
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    async def delete_old(self, days: int = 90) -> int:
        """Delete reports older than N days. Returns count deleted."""
        from datetime import timedelta

        cutoff_dt = datetime.now(UTC) - timedelta(days=days)
        cutoff = cutoff_dt.isoformat()
        sql = "DELETE FROM trading_reports WHERE generated_at < ?"
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(sql, (cutoff,))
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()
