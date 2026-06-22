"""Inspection Report Generator for Gas & Oil drone inspections.

Generates visual inspection reports from YOLO detection results
with annotated images, GPS coordinates, and Kuwaiti Arabic recommendations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from almuhalab_custom_skills.drone_inspection.yolo_detector import Detection, DetectionBatch

logger = logging.getLogger(__name__)

# Kuwaiti Arabic recommendations per detection class
KUWAITI_ARABIC_RECOMMENDATIONS: dict[str, dict] = {
    "pipeline_leak": {
        "ar": "تسريب في خط الأنابيب — تدخل فوري مطلوب. أوقف التدفق واحضر فريق الإصلاح.",
        "en": "Pipeline leak detected — immediate intervention required. Shut off flow and dispatch repair team.",
        "priority": 1,
    },
    "valve_damage": {
        "ar": "تلف في الصمام — فحص وإصلاح مطلوب. قد يؤدي إلى تسريب إذا لم يتم معالجته.",
        "en": "Valve damage — inspection and repair needed. May lead to leak if untreated.",
        "priority": 2,
    },
    "corrosion_spot": {
        "ar": "بقعة تآكل — تقييم وصيانة وقائية مطلوبة.",
        "en": "Corrosion spot — assessment and preventive maintenance required.",
        "priority": 3,
    },
    "ground_disturbance": {
        "ar": "اضطراب في التربة — تحقق من عدم وجود تسريب تحت الأرضي.",
        "en": "Ground disturbance — verify no underground leak present.",
        "priority": 4,
    },
    "vehicle_intrusion": {
        "ar": "اختراق مركبة — فرق أمنية مطلوبة لتأمين المنطقة.",
        "en": "Vehicle intrusion — security team needed to secure the area.",
        "priority": 3,
    },
    "fire_smoke": {
        "ar": "نار أو دخان — خطر حرج! أوقف كل النشاطات واستدعِ الإطفاء فوراً.",
        "en": "Fire or smoke — critical hazard! Halt all operations and call fire services immediately.",
        "priority": 0,
    },
}

SEVERITY_COLORS: dict[str, tuple] = {
    "critical": (255, 0, 0),  # Red
    "high": (255, 165, 0),    # Orange
    "medium": (255, 255, 0),  # Yellow
    "low": (0, 255, 0),       # Green
    "unknown": (128, 128, 128),  # Gray
}


@dataclass
class Finding:
    """A single finding in an inspection report."""

    detection: Detection
    latitude: float
    longitude: float
    altitude_m: float
    recommendation_ar: str
    recommendation_en: str
    priority: int


@dataclass
class InspectionReport:
    """A complete inspection report with findings and metadata."""

    report_id: str
    drone_id: str
    division: str
    timestamp: str
    findings: list[Finding]
    total_detections: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    gps_center: dict[str, float] | None = None
    recommendations_summary_ar: str = ""
    recommendations_summary_en: str = ""

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "drone_id": self.drone_id,
            "division": self.division,
            "timestamp": self.timestamp,
            "total_detections": self.total_detections,
            "critical_count": self.critical_count,
            "high_count": self.high_count,
            "medium_count": self.medium_count,
            "low_count": self.low_count,
            "gps_center": self.gps_center,
            "findings_count": len(self.findings),
            "recommendations_summary_ar": self.recommendations_summary_ar,
            "recommendations_summary_en": self.recommendations_summary_en,
        }


@dataclass
class VideoSummary:
    """Summary of detections across a video segment."""

    total_frames: int
    total_detections: int
    unique_targets: list[str]
    duration_seconds: float
    critical_events: list[dict]
    gps_track: list[dict[str, float]]
    recommendations_ar: str
    recommendations_en: str

    def to_dict(self) -> dict:
        return {
            "total_frames": self.total_frames,
            "total_detections": self.total_detections,
            "unique_targets": self.unique_targets,
            "duration_seconds": self.duration_seconds,
            "critical_events_count": len(self.critical_events),
            "gps_track_length": len(self.gps_track),
        }


class InspectionReportGenerator:
    """Generates visual inspection reports from detection results.

    Produces annotated images with bounding boxes, GPS coordinates,
    severity classification, and recommendations in Kuwaiti Arabic.
    """

    def __init__(self, tracer: Any = None) -> None:
        self.tracer = tracer
        self._report_counter = 0

    def _generate_report_id(self) -> str:
        self._report_counter += 1
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        return f"IR-{ts}-{self._report_counter:04d}"

    def _get_recommendation(self, class_name: str) -> dict:
        return KUWAITI_ARABIC_RECOMMENDATIONS.get(
            class_name,
            {
                "ar": "غير معروف — تحقق يدوياً.",
                "en": "Unknown — manual inspection required.",
                "priority": 5,
            },
        )

    def _compute_gps_center(
        self, findings: list[Finding]
    ) -> dict[str, float] | None:
        if not findings:
            return None
        lat = sum(f.latitude for f in findings) / len(findings)
        lon = sum(f.longitude for f in findings) / len(findings)
        return {"latitude": lat, "longitude": lon}

    def _build_summary(
        self, findings: list[Finding]
    ) -> tuple[str, str]:
        """Build summary recommendations in Arabic and English."""
        if not findings:
            return ("لا توجد توصيات — لا توجد مشاهدات.", "No recommendations — no findings.")

        # Sort by priority
        sorted_findings = sorted(findings, key=lambda f: f.priority)
        top = sorted_findings[:3]

        ar_lines = []
        en_lines = []
        for f in top:
            ar_lines.append(
                f"{f.recommendation_ar} (الإحداثيات: {f.latitude:.6f}, {f.longitude:.6f})"
            )
            en_lines.append(
                f"{f.recommendation_en} (GPS: {f.latitude:.6f}, {f.longitude:.6f})"
            )

        return (" | ".join(ar_lines), " | ".join(en_lines))

    def _annotate_frame(
        self, frame: Any, detections: list[Detection]
    ) -> Any:
        """Draw bounding boxes and labels on frame.

        Uses numpy to draw directly on the frame array.
        Returns annotated frame.
        """
        try:
            import numpy as np
        except ImportError:
            return frame

        annotated = frame.copy()
        for det in detections:
            x1, y1, x2, y2 = det.bbox
            color = SEVERITY_COLORS.get(det.severity, (128, 128, 128))

            # Draw bounding box (4px border)
            for thickness in range(4):
                annotated[
                    max(0, y1 - thickness) : min(annotated.shape[0], y2 + thickness),
                    max(0, x1 - thickness) : min(annotated.shape[1], x2 + thickness),
                ] = color

        return annotated

    async def generate_report(
        self,
        detections: list[Detection],
        telemetry: dict,
        drone_id: str,
        division: str = "gas_oil",
    ) -> InspectionReport:
        """Generate inspection report with annotated images and GPS coordinates.

        Produces findings with:
        - Bounding box annotations
        - GPS coordinates from telemetry
        - Severity classification
        - Recommended actions in Kuwaiti Arabic
        """
        report_id = self._generate_report_id()

        findings: list[Finding] = []
        lat = telemetry.get("latitude", 0.0)
        lon = telemetry.get("longitude", 0.0)
        alt = telemetry.get("altitude_m", 0.0)

        for det in detections:
            rec = self._get_recommendation(det.class_name)
            finding = Finding(
                detection=det,
                latitude=lat,
                longitude=lon,
                altitude_m=alt,
                recommendation_ar=rec["ar"],
                recommendation_en=rec["en"],
                priority=rec["priority"],
            )
            findings.append(finding)

        # Count by severity
        critical = sum(1 for f in findings if f.detection.severity == "critical")
        high = sum(1 for f in findings if f.detection.severity == "high")
        medium = sum(1 for f in findings if f.detection.severity == "medium")
        low = sum(1 for f in findings if f.detection.severity == "low")

        gps_center = self._compute_gps_center(findings)
        ar_summary, en_summary = self._build_summary(findings)

        report = InspectionReport(
            report_id=report_id,
            drone_id=drone_id,
            division=division,
            timestamp=datetime.now(UTC).isoformat(),
            findings=findings,
            total_detections=len(findings),
            critical_count=critical,
            high_count=high,
            medium_count=medium,
            low_count=low,
            gps_center=gps_center,
            recommendations_summary_ar=ar_summary,
            recommendations_summary_en=en_summary,
        )

        logger.info(
            "Generated report %s: %d findings (%d critical)",
            report_id,
            len(findings),
            critical,
        )
        return report

    async def generate_video_summary(
        self,
        detection_batches: list[DetectionBatch],
        telemetry_history: list[dict],
    ) -> VideoSummary:
        """Generate time-lapse summary with overlaid detections.

        Aggregates all detection batches and telemetry history
        into a single summary with critical events highlighted.
        """
        total_frames = len(detection_batches)
        all_detections: list[Detection] = []
        unique_targets: set = set()
        critical_events: list[dict] = []
        gps_track: list[dict[str, float]] = []

        for batch in detection_batches:
            for det in batch.detections:
                all_detections.append(det)
                unique_targets.add(det.class_name)
                if det.severity == "critical":
                    critical_events.append(
                        {
                            "class_name": det.class_name,
                            "confidence": det.confidence,
                            "timestamp": det.timestamp,
                            "bbox": det.bbox,
                        }
                    )

        for telemetry in telemetry_history:
            lat = telemetry.get("latitude", 0.0)
            lon = telemetry.get("longitude", 0.0)
            gps_track.append({"latitude": lat, "longitude": lon})

        # Compute duration from timestamps if available
        duration = 0.0
        if telemetry_history and len(telemetry_history) >= 2:
            ts_first = telemetry_history[0].get("timestamp", "")
            ts_last = telemetry_history[-1].get("timestamp", "")
            try:
                t1 = datetime.fromisoformat(ts_first)
                t2 = datetime.fromisoformat(ts_last)
                duration = (t2 - t1).total_seconds()
            except (ValueError, TypeError):
                duration = float(total_frames) * 0.033  # ~30fps fallback

        # Build recommendations from unique targets found
        ar_recs = []
        en_recs = []
        for target in sorted(unique_targets):
            rec = self._get_recommendation(target)
            ar_recs.append(rec["ar"])
            en_recs.append(rec["en"])

        summary = VideoSummary(
            total_frames=total_frames,
            total_detections=len(all_detections),
            unique_targets=sorted(unique_targets),
            duration_seconds=duration,
            critical_events=critical_events,
            gps_track=gps_track,
            recommendations_ar=" | ".join(ar_recs) if ar_recs else "لا توجد توصيات.",
            recommendations_en=" | ".join(en_recs) if en_recs else "No recommendations.",
        )

        logger.info(
            "Video summary: %d frames, %d detections, %d critical events",
            total_frames,
            len(all_detections),
            len(critical_events),
        )
        return summary

    def annotate_frame(
        self, frame: Any, detections: list[Detection]
    ) -> Any:
        """Public sync wrapper for frame annotation."""
        return self._annotate_frame(frame, detections)
