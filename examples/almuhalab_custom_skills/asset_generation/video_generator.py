"""Video Summary Generator — Branded video generation from drone footage and reports.

Generates inspection summary videos and market update videos with
division branding, detection overlays, GPS maps, and optional
Kuwaiti Arabic narration.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from almuhalab_custom_skills.asset_generation.image_generator import DivisionImageGenerator
from almuhalab_custom_skills.branding.almuhalab_guidelines import BrandGuidelines

logger = logging.getLogger(__name__)


@dataclass
class VideoFrame:
    """A single frame in a generated video."""

    frame_index: int
    timestamp_ms: int
    image_id: str | None = None  # Reference to GeneratedImage
    overlay_text: str | None = None
    overlay_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedVideo:
    """A generated video with branding metadata."""

    video_id: str
    division: str
    video_type: str          # "inspection_summary", "market_update"
    duration_seconds: int
    width: int
    height: int
    fps: int
    frame_count: int
    frames: list[VideoFrame] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    file_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    narration_text: str | None = None
    narration_language: str | None = None

    @property
    def total_overlay_frames(self) -> int:
        return sum(1 for f in self.frames if f.overlay_text)


class VideoSummaryGenerator:
    """Generates branded video summaries from drone footage and reports.

    Produces:
    - Inspection summary videos with annotated highlights, detection
      overlays, GPS map visualization, and optional Kuwaiti Arabic narration.
    - Market update videos with animated charts, key metrics, and
      division branding throughout.

    Uses DivisionImageGenerator for frame-level image generation
    and BrandGuidelines for consistent branding.
    """

    def __init__(self, image_generator: DivisionImageGenerator) -> None:
        self.image_gen = image_generator
        self.guidelines = BrandGuidelines

    def _generate_id(self, prefix: str) -> str:
        ts = int(time.time() * 1000)
        rand = uuid.uuid4().hex[:8]
        return f"{prefix}-{ts}-{rand}"

    def _compute_gps_bounds(
        self, gps_track: list[dict[str, float]]
    ) -> dict[str, float]:
        """Compute bounding box for GPS track."""
        if not gps_track:
            return {"min_lat": 0, "max_lat": 0, "min_lon": 0, "max_lon": 0}

        lats = [p["latitude"] for p in gps_track]
        lons = [p["longitude"] for p in gps_track]
        return {
            "min_lat": min(lats),
            "max_lat": max(lats),
            "min_lon": min(lons),
            "max_lon": max(lons),
        }

    def _build_inspection_narration(
        self,
        division: str,
        report: Any,
        detection_count: int,
        critical_count: int,
    ) -> str:
        """Build Kuwaiti Arabic narration text for inspection summary."""
        brand = self.guidelines.get_branding(division)
        if critical_count > 0:
            return (
                f"تقرير فحص {brand.name_ar}. "
                f"تم اكتشاف {detection_count} نتيجة فحص، "
                f"منها {critical_count} حالة حرجة تتطلب تدخل فوري. "
                f"يُرجى مراجعة التفاصيل في الفيديو."
            )
        return (
            f"تقرير فحص {brand.name_ar}. "
            f"تم اكتشاف {detection_count} نتيجة فحص. "
            f"الحالة العامة مستقرة."
        )

    def _build_market_narration(
        self, division: str, report: Any
    ) -> str:
        """Build Kuwaiti Arabic narration for market update video."""
        brand = self.guidelines.get_branding(division)
        parts = [f"تحديث سوق {brand.name_ar}."]
        if hasattr(report, "overall_severity"):
            severity_ar = {
                "critical": "الحالة حرجة",
                "high": "المخاطر مرتفعة",
                "medium": "الوضع متوسط",
                "low": "الوضع مستقر",
                "neutral": "الوضع محايد",
            }
            sev = severity_ar.get(report.overall_severity.value, "")
            if sev:
                parts.append(sev)
        if hasattr(report, "risks") and report.risks:
            parts.append(f"{len(report.risks)} مخاطر مسجلة.")
        if hasattr(report, "opportunities") and report.opportunities:
            parts.append(f"{len(report.opportunities)} فرص متاحة.")
        return " ".join(parts)

    async def generate_inspection_summary(
        self,
        division: str,
        detection_batches: list[Any],
        telemetry_history: list[dict],
        report: Any,
        duration_seconds: int = 60,
    ) -> GeneratedVideo:
        """Generate inspection summary video.

        Produces a video with:
        - Annotated drone footage highlights
        - Overlay of detection results
        - GPS map visualization
        - Narration in Kuwaiti Arabic (optional)

        Args:
            division: Division identifier.
            detection_batches: List of DetectionBatch objects.
            telemetry_history: List of telemetry dicts with lat/lon.
            report: InspectionReport dataclass.
            duration_seconds: Target video duration.

        Returns:
            GeneratedVideo with frames and metadata.
        """
        if division not in self.guidelines.DIVISIONS:
            raise ValueError(
                f"Unknown division '{division}'. "
                f"Valid: {self.guidelines.get_all_divisions()}"
            )

        brand = self.guidelines.get_branding(division)
        fps = 30
        frame_count = duration_seconds * fps
        width, height = 1920, 1080

        # Build GPS track from telemetry
        gps_track = [
            {"latitude": t.get("latitude", 0.0), "longitude": t.get("longitude", 0.0)}
            for t in telemetry_history
        ]
        gps_bounds = self._compute_gps_bounds(gps_track)

        # Count detections across all batches
        total_detections = 0
        critical_count = 0
        for batch in detection_batches:
            for det in batch.detections:
                total_detections += 1
                if det.severity == "critical":
                    critical_count += 1

        # Generate key frames at detection events
        frames: list[VideoFrame] = []
        event_interval = max(1, frame_count // max(1, total_detections + 1))
        frame_idx = 0

        for batch_idx, batch in enumerate(detection_batches):
            for det in batch.detections:
                if frame_idx >= frame_count:
                    break
                overlay = (
                    f"{det.class_name} ({det.confidence:.0%}) — "
                    f"severity: {det.severity}"
                )
                frames.append(VideoFrame(
                    frame_index=frame_idx,
                    timestamp_ms=int(frame_idx * 1000 / fps),
                    overlay_text=overlay,
                    overlay_data={
                        "bbox": det.bbox,
                        "class_name": det.class_name,
                        "confidence": det.confidence,
                        "severity": det.severity,
                    },
                ))
                frame_idx += event_interval

        # Add GPS map overlay frames
        if gps_track:
            gps_frame_idx = frame_count // 2  # Middle of video
            frames.append(VideoFrame(
                frame_index=gps_frame_idx,
                timestamp_ms=int(gps_frame_idx * 1000 / fps),
                overlay_text="GPS Track Overview",
                overlay_data={
                    "gps_bounds": gps_bounds,
                    "track_points": len(gps_track),
                },
            ))

        # Build narration
        narration = self._build_inspection_narration(
            division, report, total_detections, critical_count
        )

        video_id = self._generate_id(f"vid-insp-{division}")
        logger.info(
            "Generated inspection video %s: %d frames, %d detections, %d critical",
            video_id, frame_count, total_detections, critical_count,
        )

        return GeneratedVideo(
            video_id=video_id,
            division=division,
            video_type="inspection_summary",
            duration_seconds=duration_seconds,
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            frames=frames,
            narration_text=narration,
            narration_language="ar",
            metadata={
                "colors": self.guidelines.get_palette(division),
                "tagline": brand.tagline_ar,
                "gps_bounds": gps_bounds,
                "total_detections": total_detections,
                "critical_count": critical_count,
                "report_id": report.report_id,
                "watermarked": True,
            },
        )

    async def generate_market_update_video(
        self,
        division: str,
        report: Any,
        duration_seconds: int = 30,
    ) -> GeneratedVideo:
        """Generate market update video for social media.

        Produces a video with:
        - Animated charts
        - Key metrics highlighted
        - Division branding throughout

        Args:
            division: Division identifier.
            report: TradingIntelReport dataclass.
            duration_seconds: Target video duration.

        Returns:
            GeneratedVideo with frames and metadata.
        """
        if division not in self.guidelines.DIVISIONS:
            raise ValueError(
                f"Unknown division '{division}'. "
                f"Valid: {self.guidelines.get_all_divisions()}"
            )

        brand = self.guidelines.get_branding(division)
        fps = 30
        frame_count = duration_seconds * fps
        width, height = 1080, 1080  # Square for social media

        frames: list[VideoFrame] = []

        # Opening frame with branding
        frames.append(VideoFrame(
            frame_index=0,
            timestamp_ms=0,
            overlay_text=f"{brand.name_en} — Market Update",
            overlay_data={"section": "intro"},
        ))

        # Market overview chart frames
        chart_interval = frame_count // 4
        if hasattr(report, "oil_price") and report.oil_price:
            frames.append(VideoFrame(
                frame_index=chart_interval,
                timestamp_ms=int(chart_interval * 1000 / fps),
                overlay_text=f"Brent Crude: ${report.oil_price:.1f}",
                overlay_data={
                    "chart_type": "oil_price",
                    "value": report.oil_price,
                },
            ))

        if hasattr(report, "boursa_index") and report.boursa_index:
            frames.append(VideoFrame(
                frame_index=chart_interval * 2,
                timestamp_ms=int(chart_interval * 2 * 1000 / fps),
                overlay_text=f"Boursa Kuwait: {report.boursa_index:.0f}",
                overlay_data={
                    "chart_type": "boursa_index",
                    "value": report.boursa_index,
                },
            ))

        # Risks and opportunities summary
        if hasattr(report, "risks") and report.risks:
            risk_text = f"{len(report.risks)} Risks Identified"
            frames.append(VideoFrame(
                frame_index=chart_interval * 3,
                timestamp_ms=int(chart_interval * 3 * 1000 / fps),
                overlay_text=risk_text,
                overlay_data={"section": "risks", "count": len(report.risks)},
            ))

        if hasattr(report, "opportunities") and report.opportunities:
            frames.append(VideoFrame(
                frame_index=int(chart_interval * 3.5),
                timestamp_ms=int(chart_interval * 3.5 * 1000 / fps),
                overlay_text=f"{len(report.opportunities)} Opportunities",
                overlay_data={
                    "section": "opportunities",
                    "count": len(report.opportunities),
                },
            ))

        # Closing frame with tagline
        frames.append(VideoFrame(
            frame_index=frame_count - 1,
            timestamp_ms=duration_seconds * 1000 - 1,
            overlay_text=f"{brand.tagline_en} | {brand.tagline_ar}",
            overlay_data={"section": "outro"},
        ))

        # Build narration
        narration = self._build_market_narration(division, report)

        video_id = self._generate_id(f"vid-market-{division}")
        logger.info(
            "Generated market update video %s: %d frames, %d seconds",
            video_id, frame_count, duration_seconds,
        )

        return GeneratedVideo(
            video_id=video_id,
            division=division,
            video_type="market_update",
            duration_seconds=duration_seconds,
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            frames=frames,
            narration_text=narration,
            narration_language="ar",
            metadata={
                "colors": self.guidelines.get_palette(division),
                "tagline": brand.tagline_ar,
                "report_id": report.report_id if hasattr(report, "report_id") else None,
                "overall_severity": (
                    report.overall_severity.value
                    if hasattr(report, "overall_severity")
                    else "unknown"
                ),
                "watermarked": True,
            },
        )
