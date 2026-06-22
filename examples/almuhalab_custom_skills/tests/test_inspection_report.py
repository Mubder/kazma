"""Tests for InspectionReportGenerator."""

from __future__ import annotations

import pytest

from almuhalab_custom_skills.drone_inspection.inspection_report import (
    KUWAITI_ARABIC_RECOMMENDATIONS,
    SEVERITY_COLORS,
    InspectionReportGenerator,
)
from almuhalab_custom_skills.drone_inspection.yolo_detector import Detection, DetectionBatch


def make_detection(
    class_id: int = 0,
    class_name: str = "pipeline_leak",
    severity: str = "critical",
    confidence: float = 0.95,
) -> Detection:
    return Detection(
        bbox=(100, 200, 300, 400),
        class_id=class_id,
        class_name=class_name,
        confidence=confidence,
        severity=severity,
    )


SAMPLE_TELEMETRY = {
    "timestamp": "2026-06-20T21:00:00+00:00",
    "drone_id": "drone-001",
    "latitude": 29.3759,
    "longitude": 47.9774,
    "altitude_m": 120.5,
    "speed_ms": 15.3,
    "battery_pct": 87.5,
}


class TestReportGeneration:
    """Test inspection report generation."""

    @pytest.mark.asyncio
    async def test_generate_empty_report(self):
        gen = InspectionReportGenerator()
        report = await gen.generate_report(
            detections=[],
            telemetry=SAMPLE_TELEMETRY,
            drone_id="drone-001",
        )
        assert report.total_detections == 0
        assert len(report.findings) == 0
        assert report.report_id.startswith("IR-")

    @pytest.mark.asyncio
    async def test_generate_report_with_findings(self):
        gen = InspectionReportGenerator()
        dets = [
            make_detection(class_id=0, class_name="pipeline_leak", severity="critical"),
            make_detection(class_id=1, class_name="valve_damage", severity="high"),
        ]
        report = await gen.generate_report(
            detections=dets,
            telemetry=SAMPLE_TELEMETRY,
            drone_id="drone-001",
        )
        assert report.total_detections == 2
        assert report.critical_count == 1
        assert report.high_count == 1
        assert report.drone_id == "drone-001"
        assert report.division == "gas_oil"

    @pytest.mark.asyncio
    async def test_findings_contain_gps(self):
        gen = InspectionReportGenerator()
        dets = [make_detection()]
        report = await gen.generate_report(
            detections=dets,
            telemetry=SAMPLE_TELEMETRY,
            drone_id="drone-001",
        )
        finding = report.findings[0]
        assert finding.latitude == 29.3759
        assert finding.longitude == 47.9774
        assert finding.altitude_m == 120.5

    @pytest.mark.asyncio
    async def test_findings_contain_arabic_recommendation(self):
        gen = InspectionReportGenerator()
        dets = [make_detection(class_name="pipeline_leak")]
        report = await gen.generate_report(
            detections=dets,
            telemetry=SAMPLE_TELEMETRY,
            drone_id="drone-001",
        )
        finding = report.findings[0]
        assert "تسريب" in finding.recommendation_ar
        assert "pipeline" in finding.recommendation_en.lower() or "leak" in finding.recommendation_en.lower()

    @pytest.mark.asyncio
    async def test_gps_center_computed(self):
        gen = InspectionReportGenerator()
        dets = [make_detection(), make_detection()]
        report = await gen.generate_report(
            detections=dets,
            telemetry=SAMPLE_TELEMETRY,
            drone_id="drone-001",
        )
        assert report.gps_center is not None
        assert report.gps_center["latitude"] == 29.3759

    @pytest.mark.asyncio
    async def test_severity_counts(self):
        gen = InspectionReportGenerator()
        dets = [
            make_detection(severity="critical"),
            make_detection(severity="critical"),
            make_detection(class_id=2, class_name="corrosion_spot", severity="medium"),
            make_detection(class_id=3, class_name="ground_disturbance", severity="low"),
        ]
        report = await gen.generate_report(
            detections=dets,
            telemetry=SAMPLE_TELEMETRY,
            drone_id="drone-001",
        )
        assert report.critical_count == 2
        assert report.medium_count == 1
        assert report.low_count == 1

    @pytest.mark.asyncio
    async def test_report_id_unique(self):
        gen = InspectionReportGenerator()
        r1 = await gen.generate_report([], SAMPLE_TELEMETRY, "d1")
        r2 = await gen.generate_report([], SAMPLE_TELEMETRY, "d1")
        assert r1.report_id != r2.report_id

    @pytest.mark.asyncio
    async def test_report_to_dict(self):
        gen = InspectionReportGenerator()
        dets = [make_detection()]
        report = await gen.generate_report(
            dets, SAMPLE_TELEMETRY, "drone-001"
        )
        d = report.to_dict()
        assert "report_id" in d
        assert d["total_detections"] == 1
        assert d["critical_count"] == 1

    @pytest.mark.asyncio
    async def test_recommendations_summary(self):
        gen = InspectionReportGenerator()
        dets = [
            make_detection(class_name="pipeline_leak", severity="critical"),
            make_detection(class_name="fire_smoke", class_id=5, severity="critical"),
        ]
        report = await gen.generate_report(
            dets, SAMPLE_TELEMETRY, "drone-001"
        )
        assert len(report.recommendations_summary_ar) > 0
        assert len(report.recommendations_summary_en) > 0

    @pytest.mark.asyncio
    async def test_custom_division(self):
        gen = InspectionReportGenerator()
        report = await gen.generate_report(
            [], SAMPLE_TELEMETRY, "d1", division="petrochemical"
        )
        assert report.division == "petrochemical"


class TestVideoSummary:
    """Test video summary generation."""

    @pytest.mark.asyncio
    async def test_empty_summary(self):
        gen = InspectionReportGenerator()
        summary = await gen.generate_video_summary([], [])
        assert summary.total_frames == 0
        assert summary.total_detections == 0
        assert summary.unique_targets == []

    @pytest.mark.asyncio
    async def test_summary_with_batches(self):
        gen = InspectionReportGenerator()
        det1 = make_detection(class_name="pipeline_leak", severity="critical")
        det2 = make_detection(
            class_id=2, class_name="corrosion_spot", severity="medium"
        )
        batches = [
            DetectionBatch(detections=[det1], frame_id=0),
            DetectionBatch(detections=[det2], frame_id=1),
            DetectionBatch(detections=[det1], frame_id=2),
        ]
        telemetry = [
            {"latitude": 29.3759, "longitude": 47.9774, "timestamp": "2026-06-20T21:00:00+00:00"},
            {"latitude": 29.3760, "longitude": 47.9775, "timestamp": "2026-06-20T21:00:01+00:00"},
        ]
        summary = await gen.generate_video_summary(batches, telemetry)
        assert summary.total_frames == 3
        assert summary.total_detections == 3
        assert "pipeline_leak" in summary.unique_targets
        assert "corrosion_spot" in summary.unique_targets
        assert len(summary.critical_events) == 2  # Two pipeline_leak detections

    @pytest.mark.asyncio
    async def test_summary_gps_track(self):
        gen = InspectionReportGenerator()
        telemetry = [
            {"latitude": 29.3759, "longitude": 47.9774},
            {"latitude": 29.3760, "longitude": 47.9775},
        ]
        summary = await gen.generate_video_summary([], telemetry)
        assert len(summary.gps_track) == 2

    @pytest.mark.asyncio
    async def test_summary_to_dict(self):
        gen = InspectionReportGenerator()
        summary = await gen.generate_video_summary([], [])
        d = summary.to_dict()
        assert d["total_frames"] == 0
        assert d["critical_events_count"] == 0

    @pytest.mark.asyncio
    async def test_summary_recommendations_arabic(self):
        gen = InspectionReportGenerator()
        det = make_detection(class_name="fire_smoke", class_id=5, severity="critical")
        batches = [DetectionBatch(detections=[det], frame_id=0)]
        summary = await gen.generate_video_summary(batches, [])
        assert "نار" in summary.recommendations_ar or "دخان" in summary.recommendations_ar


class TestFrameAnnotation:
    """Test frame annotation with bounding boxes."""

    def test_annotate_frame(self):
        import numpy as np

        gen = InspectionReportGenerator()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        dets = [make_detection()]
        annotated = gen.annotate_frame(frame, dets)
        assert annotated.shape == frame.shape
        # Bounding box area should be colored
        assert annotated[200, 100].sum() > 0  # Inside bbox

    def test_annotate_empty_frame(self):
        import numpy as np

        gen = InspectionReportGenerator()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        annotated = gen.annotate_frame(frame, [])
        assert np.array_equal(annotated, frame)


class TestArabicRecommendations:
    """Test Kuwaiti Arabic recommendations."""

    def test_all_targets_have_recommendations(self):
        from almuhalab_custom_skills.drone_inspection.yolo_detector import YOLODetector

        for class_name in YOLODetector.OIL_GAS_TARGETS:
            assert class_name in KUWAITI_ARABIC_RECOMMENDATIONS, (
                f"Missing Arabic recommendation for {class_name}"
            )

    def test_recommendations_have_both_languages(self):
        for name, rec in KUWAITI_ARABIC_RECOMMENDATIONS.items():
            assert "ar" in rec, f"Missing Arabic text for {name}"
            assert "en" in rec, f"Missing English text for {name}"
            assert "priority" in rec, f"Missing priority for {name}"
            assert len(rec["ar"]) > 0
            assert len(rec["en"]) > 0

    def test_severity_colors_defined(self):
        for sev in ["critical", "high", "medium", "low", "unknown"]:
            assert sev in SEVERITY_COLORS
