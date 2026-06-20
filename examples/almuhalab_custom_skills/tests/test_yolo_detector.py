"""Tests for YOLODetector."""

from __future__ import annotations

import asyncio
import os
from typing import List
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


from almuhalab_custom_skills.drone_inspection.yolo_detector import (
    Detection,
    DetectionBatch,
    ModelNotLoadedError,
    YOLODetector,
)


class MockBox:
    """Mock YOLO box result."""

    def __init__(self, xyxy: list, conf: float, cls: int):
        self.xyxy = MagicMock()
        self.xyxy.__getitem__ = lambda self, i: MagicMock(
            tolist=lambda: xyxy
        )
        self.conf = MagicMock()
        self.conf.__getitem__ = lambda self, i: conf
        self.cls = MagicMock()
        self.cls.__getitem__ = lambda self, i: cls


class MockBoxes:
    """Mock YOLO boxes collection."""

    def __init__(self, boxes: list):
        self._boxes = boxes

    def __len__(self):
        return len(self._boxes)

    def __getitem__(self, idx):
        return self._boxes[idx]


class MockResult:
    """Mock YOLO result object."""

    def __init__(self, boxes=None):
        self.boxes = boxes


class MockModel:
    """Mock YOLO model for testing."""

    def __init__(self, results=None):
        self._results = results or []

    def predict(self, frame, verbose=False):
        return self._results


def make_detection_result(class_id: int, conf: float, bbox: list) -> MockResult:
    """Create a mock result with one detection."""
    box = MockBox(xyxy=bbox, conf=conf, cls=class_id)
    return MockResult(boxes=MockBoxes([box]))


def make_empty_result() -> MockResult:
    """Create a mock result with no detections."""
    return MockResult(boxes=MockBoxes([]))


class TestDetectionDataclass:
    """Test Detection dataclass."""

    def test_detection_creation(self):
        det = Detection(
            bbox=(10, 20, 100, 200),
            class_id=0,
            class_name="pipeline_leak",
            confidence=0.95,
            severity="critical",
        )
        assert det.bbox == (10, 20, 100, 200)
        assert det.class_name == "pipeline_leak"
        assert det.severity == "critical"

    def test_detection_to_dict(self):
        det = Detection(
            bbox=(10, 20, 100, 200),
            class_id=1,
            class_name="valve_damage",
            confidence=0.85,
            severity="high",
        )
        d = det.to_dict()
        assert d["class_id"] == 1
        assert d["severity"] == "high"
        assert "timestamp" in d

    def test_detection_immutable(self):
        det = Detection(
            bbox=(0, 0, 10, 10),
            class_id=0,
            class_name="test",
            confidence=0.5,
            severity="low",
        )
        with pytest.raises(AttributeError):
            det.class_name = "changed"


class TestDetectionBatch:
    """Test DetectionBatch."""

    def test_empty_batch(self):
        batch = DetectionBatch(detections=[], frame_id=0)
        assert batch.has_critical is False
        assert batch.by_severity == {}

    def test_batch_has_critical(self):
        det = Detection(
            bbox=(0, 0, 10, 10),
            class_id=0,
            class_name="pipeline_leak",
            confidence=0.9,
            severity="critical",
        )
        batch = DetectionBatch(detections=[det], frame_id=0)
        assert batch.has_critical is True

    def test_batch_by_severity(self):
        dets = [
            Detection(bbox=(0, 0, 10, 10), class_id=0, class_name="a", confidence=0.9, severity="critical"),
            Detection(bbox=(0, 0, 10, 10), class_id=1, class_name="b", confidence=0.8, severity="high"),
            Detection(bbox=(0, 0, 10, 10), class_id=0, class_name="a", confidence=0.9, severity="critical"),
        ]
        batch = DetectionBatch(detections=dets, frame_id=1)
        by_sev = batch.by_severity
        assert len(by_sev["critical"]) == 2
        assert len(by_sev["high"]) == 1


class TestYOLODetectorInit:
    """Test YOLODetector initialization."""

    def test_default_init(self):
        det = YOLODetector()
        assert det.confidence == 0.7
        assert det.is_loaded is False

    def test_custom_confidence(self):
        det = YOLODetector(confidence=0.5)
        assert det.confidence == 0.5

    def test_model_targets(self):
        assert len(YOLODetector.OIL_GAS_TARGETS) == 6
        assert YOLODetector.OIL_GAS_TARGETS["pipeline_leak"]["severity"] == "critical"
        assert YOLODetector.OIL_GAS_TARGETS["fire_smoke"]["severity"] == "critical"
        assert YOLODetector.OIL_GAS_TARGETS["ground_disturbance"]["severity"] == "low"

    def test_class_names_mapping(self):
        assert YOLODetector.CLASS_NAMES[0] == "pipeline_leak"
        assert YOLODetector.CLASS_NAMES[5] == "fire_smoke"


class TestYOLODetectorLoading:
    """Test model loading."""

    def test_load_model_missing_file(self):
        det = YOLODetector(model_path="/nonexistent/path/model.pt")
        det.load_model()  # Should not raise, just warn
        assert det.is_loaded is True
        assert det._model is None

    def test_inject_model(self):
        det = YOLODetector()
        mock = MockModel()
        det.inject_model(mock)
        assert det.is_loaded is True
        assert det._model is mock

    def test_load_called_automatically(self):
        det = YOLODetector(model_path="/nonexistent/model.pt")
        assert det.is_loaded is False
        det._ensure_loaded()
        assert det.is_loaded is True


class TestYOLODetectorDetection:
    """Test detection methods."""

    def _make_detector_with_mock(self, results):
        det = YOLODetector()
        mock_model = MockModel(results=results)
        det.inject_model(mock_model)
        return det

    def test_detect_single_object(self):
        result = make_detection_result(
            class_id=0, conf=0.95, bbox=[100, 200, 300, 400]
        )
        detector = self._make_detector_with_mock([result])
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = detector.detect_sync(frame)
        assert len(detections) == 1
        assert detections[0].class_name == "pipeline_leak"
        assert detections[0].severity == "critical"
        assert detections[0].confidence == 0.95
        assert detections[0].bbox == (100, 200, 300, 400)

    def test_detect_below_confidence_filtered(self):
        result = make_detection_result(
            class_id=1, conf=0.3, bbox=[10, 20, 50, 60]
        )
        detector = self._make_detector_with_mock([result])
        detector.confidence = 0.7  # Default threshold
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = detector.detect_sync(frame)
        assert len(detections) == 0

    def test_detect_multiple_objects(self):
        result1 = make_detection_result(class_id=0, conf=0.95, bbox=[10, 10, 50, 50])
        result2 = make_detection_result(class_id=2, conf=0.8, bbox=[100, 100, 200, 200])
        detector = self._make_detector_with_mock([result1, result2])
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = detector.detect_sync(frame)
        assert len(detections) == 2
        names = {d.class_name for d in detections}
        assert "pipeline_leak" in names
        assert "corrosion_spot" in names

    def test_detect_stub_mode(self):
        det = YOLODetector(model_path="/nonexistent/model.pt")
        det.load_model()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = det.detect_sync(frame)
        assert detections == []

    def test_detect_empty_result(self):
        result = make_empty_result()
        detector = self._make_detector_with_mock([result])
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = detector.detect_sync(frame)
        assert len(detections) == 0

    def test_detect_none_boxes(self):
        result = MockResult(boxes=None)
        detector = self._make_detector_with_mock([result])
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = detector.detect_sync(frame)
        assert len(detections) == 0


class TestYOLODetectorAsync:
    """Test async detection methods."""

    @pytest.mark.asyncio
    async def test_async_detect(self):
        result = make_detection_result(
            class_id=5, conf=0.99, bbox=[50, 50, 150, 150]
        )
        det = YOLODetector()
        det.inject_model(MockModel([result]))
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = await det.detect(frame)
        assert len(detections) == 1
        assert detections[0].class_name == "fire_smoke"
        assert detections[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_detect_stream(self):
        result1 = make_detection_result(class_id=0, conf=0.9, bbox=[10, 10, 50, 50])
        result2 = make_detection_result(class_id=3, conf=0.75, bbox=[20, 20, 60, 60])

        call_count = [0]
        results_list = [result1, result2]

        class CallCountModel:
            def predict(self, frame, verbose=False):
                idx = min(call_count[0], len(results_list) - 1)
                call_count[0] += 1
                return [results_list[idx]]

        det = YOLODetector()
        det.inject_model(CallCountModel())

        async def frame_generator():
            for _ in range(2):
                yield np.zeros((480, 640, 3), dtype=np.uint8)

        batches = []
        async for batch in det.detect_stream(frame_generator()):
            batches.append(batch)

        assert len(batches) == 2
        assert batches[0].frame_id == 0
        assert batches[1].frame_id == 1
        assert batches[0].inference_time_ms >= 0


class TestYOLODetectorTargets:
    """Test target info retrieval."""

    def test_get_target_info(self):
        det = YOLODetector()
        info = det.get_target_info("pipeline_leak")
        assert info is not None
        assert info["severity"] == "critical"

    def test_get_target_info_unknown(self):
        det = YOLODetector()
        assert det.get_target_info("nonexistent") is None

    def test_all_severities_present(self):
        severities = {
            info["severity"]
            for info in YOLODetector.OIL_GAS_TARGETS.values()
        }
        assert severities == {"critical", "high", "medium", "low"}
