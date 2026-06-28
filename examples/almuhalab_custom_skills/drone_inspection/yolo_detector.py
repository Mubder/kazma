"""YOLOv11 Detection Pipeline for Gas & Oil inspection targets.

Provides real-time object detection on FPV drone video frames
using YOLOv11 with domain-specific inspection targets.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    """A single detection result from YOLO inference."""

    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2
    class_id: int
    class_name: str
    confidence: float
    severity: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "bbox": self.bbox,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "severity": self.severity,
            "timestamp": self.timestamp,
        }


@dataclass
class DetectionBatch:
    """A batch of detections from processing one or more frames."""

    detections: list[Detection]
    frame_id: int
    timestamp: float = field(default_factory=time.time)
    inference_time_ms: float = 0.0

    @property
    def has_critical(self) -> bool:
        return any(d.severity == "critical" for d in self.detections)

    @property
    def by_severity(self) -> dict[str, list[Detection]]:
        result: dict[str, list[Detection]] = {}
        for det in self.detections:
            result.setdefault(det.severity, []).append(det)
        return result


class ModelNotLoadedError(Exception):
    """Raised when detection is attempted before model is loaded."""

    pass


class YOLODetector:
    """Real-time object detection using YOLOv11.

    Specialized for Gas & Oil infrastructure inspection with
    domain-specific target classes and severity classification.
    """

    OIL_GAS_TARGETS: dict[str, dict] = {
        "pipeline_leak": {"class_id": 0, "severity": "critical"},
        "valve_damage": {"class_id": 1, "severity": "high"},
        "corrosion_spot": {"class_id": 2, "severity": "medium"},
        "ground_disturbance": {"class_id": 3, "severity": "low"},
        "vehicle_intrusion": {"class_id": 4, "severity": "medium"},
        "fire_smoke": {"class_id": 5, "severity": "critical"},
    }

    CLASS_NAMES: dict[int, str] = {
        0: "pipeline_leak",
        1: "valve_damage",
        2: "corrosion_spot",
        3: "ground_disturbance",
        4: "vehicle_intrusion",
        5: "fire_smoke",
    }

    def __init__(
        self,
        model_path: str = "models/yolov11-oilgas.pt",
        confidence: float = 0.7,
    ) -> None:
        self.model_path = model_path
        self.confidence = confidence
        self._model: Any = None
        self._loaded = False

    def load_model(self) -> None:
        """Load the YOLOv11 model from disk.

        In production, this loads the actual ultralytics YOLO model.
        For testing, use inject_model() to provide a mock.
        """
        if self._loaded:
            return

        if not os.path.exists(self.model_path):
            logger.warning(
                "Model file not found at %s — running in stub mode. "
                "Detection will return empty results.",
                self.model_path,
            )
            self._loaded = True
            return

        try:
            from ultralytics import YOLO  # type: ignore

            self._model = YOLO(self.model_path)
            self._loaded = True
            logger.info("YOLO model loaded from %s", self.model_path)
        except ImportError:
            logger.warning(
                "ultralytics not installed — running in stub mode"
            )
            self._loaded = True
        except Exception as e:
            logger.error("Failed to load YOLO model: %s", e)
            raise

    def inject_model(self, model: Any) -> None:
        """Inject a mock or custom model for testing.

        The model must have a predict() method returning results
        compatible with ultralytics YOLO output format.
        """
        self._model = model
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load_model()

    def _map_detections(self, raw_results: Any) -> list[Detection]:
        """Map raw YOLO results to Detection objects."""
        detections: list[Detection] = []

        if raw_results is None:
            return detections

        # Handle ultralytics Results objects
        for result in raw_results:
            if not hasattr(result, "boxes") or result.boxes is None:
                continue

            boxes = result.boxes
            for i in range(len(boxes)):
                box = boxes[i]
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])

                if conf < self.confidence:
                    continue

                class_name = self.CLASS_NAMES.get(cls_id, f"unknown_{cls_id}")
                target_info = self.OIL_GAS_TARGETS.get(class_name, {})
                severity = target_info.get("severity", "unknown")

                detections.append(
                    Detection(
                        bbox=(x1, y1, x2, y2),
                        class_id=cls_id,
                        class_name=class_name,
                        confidence=conf,
                        severity=severity,
                    )
                )

        return detections

    async def detect(self, frame: np.ndarray) -> list[Detection]:
        """Run YOLOv11 inference on a single frame.

        Returns List[Detection] with bounding boxes, class info,
        confidence scores, and severity classification.
        """
        self._ensure_loaded()

        if self._model is None:
            # Stub mode — return empty detections
            return []

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, lambda: self._model.predict(frame, verbose=False)
        )

        return self._map_detections(results)

    async def detect_stream(
        self, video_stream: AsyncIterator[np.ndarray]
    ) -> AsyncIterator[DetectionBatch]:
        """Continuous detection on video stream.

        Yields DetectionBatch for each frame processed.
        """
        frame_id = 0
        self._ensure_loaded()

        async for frame in video_stream:
            t_start = time.monotonic()
            detections = await self.detect(frame)
            inference_time = (time.monotonic() - t_start) * 1000

            batch = DetectionBatch(
                detections=detections,
                frame_id=frame_id,
                inference_time_ms=inference_time,
            )

            frame_id += 1
            yield batch

    def detect_sync(self, frame: np.ndarray) -> list[Detection]:
        """Synchronous detection for non-async contexts."""
        self._ensure_loaded()

        if self._model is None:
            return []

        results = self._model.predict(frame, verbose=False)
        return self._map_detections(results)

    def get_target_info(self, class_name: str) -> dict | None:
        """Get info about a specific inspection target."""
        return self.OIL_GAS_TARGETS.get(class_name)

    @property
    def is_loaded(self) -> bool:
        return self._loaded
