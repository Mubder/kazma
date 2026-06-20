"""Tests for Video Summary Generator."""
from __future__ import annotations

import pytest

from almuhalab_custom_skills.branding.almuhalab_guidelines import BrandGuidelines
from almuhalab_custom_skills.asset_generation.image_generator import DivisionImageGenerator
from almuhalab_custom_skills.asset_generation.video_generator import (
    VideoSummaryGenerator,
    GeneratedVideo,
    VideoFrame,
)
from almuhalab_custom_skills.drone_inspection.yolo_detector import Detection, DetectionBatch


@pytest.fixture
def image_generator():
    return DivisionImageGenerator(backend="mock")


@pytest.fixture
def video_generator(image_generator):
    return VideoSummaryGenerator(image_generator=image_generator)


@pytest.fixture
def sample_detection_batches():
    """Create sample detection batches with various severities."""
    batches = []
    detections_1 = [
        Detection(
            bbox=(100, 100, 200, 200),
            class_id=0,
            class_name="pipeline_leak",
            confidence=0.92,
            severity="critical",
            timestamp=1000.0,
        ),
        Detection(
            bbox=(300, 150, 400, 250),
            class_id=1,
            class_name="valve_damage",
            confidence=0.85,
            severity="high",
            timestamp=1001.0,
        ),
    ]
    batches.append(DetectionBatch(detections=detections_1, frame_id=1))

    detections_2 = [
        Detection(
            bbox=(500, 200, 600, 300),
            class_id=2,
            class_name="corrosion_spot",
            confidence=0.78,
            severity="medium",
            timestamp=1002.0,
        ),
    ]
    batches.append(DetectionBatch(detections=detections_2, frame_id=2))
    return batches


@pytest.fixture
def sample_telemetry():
    return [
        {"latitude": 29.3759, "longitude": 47.9774, "altitude_m": 50.0, "timestamp": "2026-06-20T10:00:00"},
        {"latitude": 29.3760, "longitude": 47.9775, "altitude_m": 55.0, "timestamp": "2026-06-20T10:01:00"},
        {"latitude": 29.3761, "longitude": 47.9776, "altitude_m": 60.0, "timestamp": "2026-06-20T10:02:00"},
    ]


@pytest.fixture
def sample_inspection_report():
    class StubReport:
        report_id = "IR-20260620-0042"
        total_detections = 3
        critical_count = 1
        high_count = 1
        medium_count = 1
        low_count = 0
    return StubReport()


@pytest.fixture
def sample_trading_report():
    class StubTradingReport:
        report_id = "rpt-1234-gas_oil"
        division = "gas_oil"
        overall_severity = type("S", (), {"value": "high"})()
        risks = [1, 2]  # 2 risks
        opportunities = [1]  # 1 opportunity
        oil_price = 78.5
        boursa_index = 6200.0
        gold_price = 88.3
    return StubTradingReport()


class TestInspectionSummaryVideo:
    """Test inspection summary video generation."""

    @pytest.mark.asyncio
    async def test_basic_generation(
        self, video_generator, sample_detection_batches,
        sample_telemetry, sample_inspection_report
    ):
        video = await video_generator.generate_inspection_summary(
            division="gas_oil",
            detection_batches=sample_detection_batches,
            telemetry_history=sample_telemetry,
            report=sample_inspection_report,
            duration_seconds=60,
        )
        assert isinstance(video, GeneratedVideo)
        assert video.division == "gas_oil"
        assert video.video_type == "inspection_summary"
        assert video.duration_seconds == 60
        assert video.fps == 30
        assert video.frame_count == 1800  # 60 * 30

    @pytest.mark.asyncio
    async def test_detection_overlays_present(
        self, video_generator, sample_detection_batches,
        sample_telemetry, sample_inspection_report
    ):
        video = await video_generator.generate_inspection_summary(
            division="gas_oil",
            detection_batches=sample_detection_batches,
            telemetry_history=sample_telemetry,
            report=sample_inspection_report,
        )
        # Should have overlay frames for detections + GPS
        overlay_frames = [f for f in video.frames if f.overlay_text]
        assert len(overlay_frames) > 0

    @pytest.mark.asyncio
    async def test_critical_detection_highlighted(
        self, video_generator, sample_detection_batches,
        sample_telemetry, sample_inspection_report
    ):
        video = await video_generator.generate_inspection_summary(
            division="gas_oil",
            detection_batches=sample_detection_batches,
            telemetry_history=sample_telemetry,
            report=sample_inspection_report,
        )
        # Check that pipeline_leak (critical) is in overlays
        all_overlays = " ".join(f.overlay_text or "" for f in video.frames)
        assert "pipeline_leak" in all_overlays
        assert "critical" in all_overlays

    @pytest.mark.asyncio
    async def test_gps_track_frame(
        self, video_generator, sample_detection_batches,
        sample_telemetry, sample_inspection_report
    ):
        video = await video_generator.generate_inspection_summary(
            division="gas_oil",
            detection_batches=sample_detection_batches,
            telemetry_history=sample_telemetry,
            report=sample_inspection_report,
        )
        gps_frames = [
            f for f in video.frames
            if f.overlay_text and "GPS" in f.overlay_text
        ]
        assert len(gps_frames) == 1
        assert gps_frames[0].overlay_data["track_points"] == 3

    @pytest.mark.asyncio
    async def test_narration_arabic(
        self, video_generator, sample_detection_batches,
        sample_telemetry, sample_inspection_report
    ):
        video = await video_generator.generate_inspection_summary(
            division="gas_oil",
            detection_batches=sample_detection_batches,
            telemetry_history=sample_telemetry,
            report=sample_inspection_report,
        )
        assert video.narration_language == "ar"
        assert video.narration_text is not None
        assert len(video.narration_text) > 0
        # Should mention critical findings
        assert "حرجة" in video.narration_text or "critical" in video.narration_text.lower()

    @pytest.mark.asyncio
    async def test_metadata_includes_branding(
        self, video_generator, sample_detection_batches,
        sample_telemetry, sample_inspection_report
    ):
        video = await video_generator.generate_inspection_summary(
            division="gas_oil",
            detection_batches=sample_detection_batches,
            telemetry_history=sample_telemetry,
            report=sample_inspection_report,
        )
        assert "colors" in video.metadata
        assert video.metadata["colors"]["primary"] == "#1B365D"
        assert video.metadata["watermarked"] is True

    @pytest.mark.asyncio
    async def test_metadata_includes_detection_counts(
        self, video_generator, sample_detection_batches,
        sample_telemetry, sample_inspection_report
    ):
        video = await video_generator.generate_inspection_summary(
            division="gas_oil",
            detection_batches=sample_detection_batches,
            telemetry_history=sample_telemetry,
            report=sample_inspection_report,
        )
        assert video.metadata["total_detections"] == 3
        assert video.metadata["critical_count"] == 1

    @pytest.mark.asyncio
    async def test_all_divisions(
        self, video_generator, sample_detection_batches,
        sample_telemetry, sample_inspection_report
    ):
        for div in BrandGuidelines.get_all_divisions():
            video = await video_generator.generate_inspection_summary(
                division=div,
                detection_batches=sample_detection_batches,
                telemetry_history=sample_telemetry,
                report=sample_inspection_report,
            )
            assert video.division == div

    @pytest.mark.asyncio
    async def test_unknown_division_raises(
        self, video_generator, sample_detection_batches,
        sample_telemetry, sample_inspection_report
    ):
        with pytest.raises(ValueError, match="Unknown division"):
            await video_generator.generate_inspection_summary(
                division="nonexistent",
                detection_batches=sample_detection_batches,
                telemetry_history=sample_telemetry,
                report=sample_inspection_report,
            )

    @pytest.mark.asyncio
    async def test_no_detections(
        self, video_generator, sample_telemetry, sample_inspection_report
    ):
        video = await video_generator.generate_inspection_summary(
            division="gas_oil",
            detection_batches=[],
            telemetry_history=sample_telemetry,
            report=sample_inspection_report,
        )
        assert video.metadata["total_detections"] == 0
        assert video.metadata["critical_count"] == 0


class TestMarketUpdateVideo:
    """Test market update video generation."""

    @pytest.mark.asyncio
    async def test_basic_generation(
        self, video_generator, sample_trading_report
    ):
        video = await video_generator.generate_market_update_video(
            division="gas_oil",
            report=sample_trading_report,
            duration_seconds=30,
        )
        assert isinstance(video, GeneratedVideo)
        assert video.division == "gas_oil"
        assert video.video_type == "market_update"
        assert video.duration_seconds == 30
        assert video.fps == 30
        assert video.frame_count == 900

    @pytest.mark.asyncio
    async def test_frame_structure(
        self, video_generator, sample_trading_report
    ):
        video = await video_generator.generate_market_update_video(
            division="gas_oil",
            report=sample_trading_report,
        )
        # Should have intro, charts, risks/opps, outro
        assert len(video.frames) >= 4
        # First frame should be intro
        assert video.frames[0].overlay_data["section"] == "intro"
        # Last frame should be outro
        assert video.frames[-1].overlay_data["section"] == "outro"

    @pytest.mark.asyncio
    async def test_oil_price_chart(
        self, video_generator, sample_trading_report
    ):
        video = await video_generator.generate_market_update_video(
            division="gas_oil",
            report=sample_trading_report,
        )
        oil_frames = [
            f for f in video.frames
            if f.overlay_data.get("chart_type") == "oil_price"
        ]
        assert len(oil_frames) == 1
        assert "78.5" in oil_frames[0].overlay_text

    @pytest.mark.asyncio
    async def test_risk_and_opportunity_frames(
        self, video_generator, sample_trading_report
    ):
        video = await video_generator.generate_market_update_video(
            division="gas_oil",
            report=sample_trading_report,
        )
        risk_frames = [
            f for f in video.frames
            if f.overlay_data.get("section") == "risks"
        ]
        opp_frames = [
            f for f in video.frames
            if f.overlay_data.get("section") == "opportunities"
        ]
        assert len(risk_frames) == 1
        assert len(opp_frames) == 1

    @pytest.mark.asyncio
    async def test_narration(
        self, video_generator, sample_trading_report
    ):
        video = await video_generator.generate_market_update_video(
            division="gas_oil",
            report=sample_trading_report,
        )
        assert video.narration_language == "ar"
        assert video.narration_text is not None
        assert "تحديث سوق" in video.narration_text

    @pytest.mark.asyncio
    async def test_branding_metadata(
        self, video_generator, sample_trading_report
    ):
        video = await video_generator.generate_market_update_video(
            division="gas_oil",
            report=sample_trading_report,
        )
        assert video.metadata["colors"]["primary"] == "#1B365D"
        assert video.metadata["watermarked"] is True
        assert "overall_severity" in video.metadata

    @pytest.mark.asyncio
    async def test_all_divisions(
        self, video_generator, sample_trading_report
    ):
        for div in BrandGuidelines.get_all_divisions():
            video = await video_generator.generate_market_update_video(
                division=div,
                report=sample_trading_report,
            )
            assert video.division == div
            # Check tagline in outro
            outro = video.frames[-1]
            brand = BrandGuidelines.get_branding(div)
            assert brand.tagline_en in outro.overlay_text


class TestVideoFrame:
    """Test VideoFrame data structure."""

    def test_frame_creation(self):
        frame = VideoFrame(
            frame_index=0,
            timestamp_ms=0,
            overlay_text="Test",
            overlay_data={"key": "value"},
        )
        assert frame.frame_index == 0
        assert frame.overlay_text == "Test"

    def test_frame_defaults(self):
        frame = VideoFrame(frame_index=5, timestamp_ms=100)
        assert frame.overlay_text is None
        assert frame.overlay_data == {}
        assert frame.image_id is None
