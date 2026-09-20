"""Tests for DroneTelemetryIngestor."""

from __future__ import annotations

import json

import pytest

# Ensure kazma packages are importable
from almuhalab_custom_skills.drone_inspection.telemetry import (
    DroneTelemetryIngestor,
    StreamProtocol,
    TelemetryValidationError,
)

VALID_TELEMETRY = {
    "timestamp": "2026-06-20T21:00:00+00:00",
    "drone_id": "drone-001",
    "latitude": 29.3759,
    "longitude": 47.9774,
    "altitude_m": 120.5,
    "speed_ms": 15.3,
    "battery_pct": 87.5,
    "gimbal_pitch": -30.0,
    "gimbal_yaw": 45.0,
    "camera_fps": 30,
    "signal_strength_dbm": -65,
}


class TestTelemetryValidation:
    """Test telemetry data validation."""

    def test_valid_telemetry_passes(self):
        result = DroneTelemetryIngestor.validate_telemetry(dict(VALID_TELEMETRY))
        assert result["drone_id"] == "drone-001"
        assert result["latitude"] == 29.3759

    def test_missing_required_field_raises(self):
        bad = dict(VALID_TELEMETRY)
        del bad["latitude"]
        with pytest.raises(TelemetryValidationError, match="Missing required"):
            DroneTelemetryIngestor.validate_telemetry(bad)

    def test_missing_drone_id_raises(self):
        bad = dict(VALID_TELEMETRY)
        del bad["drone_id"]
        with pytest.raises(TelemetryValidationError, match="Missing required"):
            DroneTelemetryIngestor.validate_telemetry(bad)

    def test_numeric_coercion(self):
        data = dict(VALID_TELEMETRY)
        data["latitude"] = "29.3759"  # String that can be float
        data["camera_fps"] = "30"  # String that can be int
        result = DroneTelemetryIngestor.validate_telemetry(data)
        assert result["latitude"] == 29.3759
        assert result["camera_fps"] == 30

    def test_invalid_numeric_raises(self):
        data = dict(VALID_TELEMETRY)
        data["latitude"] = "not_a_number"
        with pytest.raises(TelemetryValidationError, match="must be numeric"):
            DroneTelemetryIngestor.validate_telemetry(data)

    def test_invalid_int_field_raises(self):
        data = dict(VALID_TELEMETRY)
        data["camera_fps"] = "abc"
        with pytest.raises(TelemetryValidationError, match="must be int"):
            DroneTelemetryIngestor.validate_telemetry(data)

    def test_timestamp_unix_epoch_coercion(self):
        data = dict(VALID_TELEMETRY)
        data["timestamp"] = 1781981362  # Unix timestamp
        result = DroneTelemetryIngestor.validate_telemetry(data)
        assert "T" in result["timestamp"]  # ISO 8601

    def test_invalid_timestamp_raises(self):
        data = dict(VALID_TELEMETRY)
        data["timestamp"] = "not-a-timestamp"
        with pytest.raises(TelemetryValidationError, match="Invalid timestamp"):
            DroneTelemetryIngestor.validate_telemetry(data)

    def test_drone_id_coerced_to_string(self):
        data = dict(VALID_TELEMETRY)
        data["drone_id"] = 42
        result = DroneTelemetryIngestor.validate_telemetry(data)
        assert result["drone_id"] == "42"


class TestIngestorOperations:
    """Test ingestor buffer and stream operations."""

    def test_process_message_dict(self):
        ingestor = DroneTelemetryIngestor()
        result = ingestor.process_message_dict(dict(VALID_TELEMETRY))
        assert result["drone_id"] == "drone-001"
        assert ingestor.buffer_size == 1

    def test_process_message_json(self):
        ingestor = DroneTelemetryIngestor()
        raw = json.dumps(VALID_TELEMETRY)
        result = ingestor.process_message(raw)
        assert result["drone_id"] == "drone-001"

    def test_invalid_json_raises(self):
        ingestor = DroneTelemetryIngestor()
        with pytest.raises(TelemetryValidationError, match="Invalid JSON"):
            ingestor.process_message("not json {{{")

    def test_buffer_stores_multiple(self):
        ingestor = DroneTelemetryIngestor()
        for i in range(10):
            data = dict(VALID_TELEMETRY)
            data["drone_id"] = f"drone-{i:03d}"
            ingestor.process_message_dict(data)
        assert ingestor.buffer_size == 10

    def test_buffer_max_size(self):
        ingestor = DroneTelemetryIngestor(max_buffer_size=5)
        for i in range(10):
            data = dict(VALID_TELEMETRY)
            data["drone_id"] = f"drone-{i:03d}"
            ingestor.process_message_dict(data)
        assert ingestor.buffer_size == 5
        # Most recent should be retained
        buf = ingestor.get_buffer()
        assert buf[-1]["drone_id"] == "drone-009"

    def test_buffer_handles_1000_messages(self):
        ingestor = DroneTelemetryIngestor(max_buffer_size=10_000)
        for i in range(1000):
            data = dict(VALID_TELEMETRY)
            data["drone_id"] = f"drone-{i:04d}"
            data["latitude"] = 29.0 + (i * 0.001)
            ingestor.process_message_dict(data)
        assert ingestor.buffer_size == 1000
        assert ingestor.get_latest_telemetry("drone-0999") is not None

    def test_get_latest_telemetry(self):
        ingestor = DroneTelemetryIngestor()
        ingestor.process_message_dict(dict(VALID_TELEMETRY))
        latest = ingestor.get_latest_telemetry("drone-001")
        assert latest is not None
        assert latest["battery_pct"] == 87.5

    def test_get_latest_unknown_drone(self):
        ingestor = DroneTelemetryIngestor()
        assert ingestor.get_latest_telemetry("nonexistent") is None

    def test_get_all_latest(self):
        ingestor = DroneTelemetryIngestor()
        for i in range(3):
            data = dict(VALID_TELEMETRY)
            data["drone_id"] = f"drone-{i}"
            ingestor.process_message_dict(data)
        all_latest = ingestor.get_all_latest()
        assert len(all_latest) == 3

    def test_clear_buffer(self):
        ingestor = DroneTelemetryIngestor()
        ingestor.process_message_dict(dict(VALID_TELEMETRY))
        ingestor.clear_buffer()
        assert ingestor.buffer_size == 0
        assert ingestor.get_latest_telemetry("drone-001") is None

    def test_get_buffer_limit(self):
        ingestor = DroneTelemetryIngestor()
        for i in range(5):
            data = dict(VALID_TELEMETRY)
            data["drone_id"] = f"drone-{i}"
            ingestor.process_message_dict(data)
        buf = ingestor.get_buffer(limit=2)
        assert len(buf) == 2


class TestProtocolParsing:
    """Test protocol detection from source URL."""

    def test_mqtt_protocol(self):
        ingestor = DroneTelemetryIngestor(stream_source="mqtt://broker:1883")
        assert ingestor._protocol == StreamProtocol.MQTT

    def test_mqtts_protocol(self):
        ingestor = DroneTelemetryIngestor(stream_source="mqtts://broker:8883")
        assert ingestor._protocol == StreamProtocol.MQTT

    def test_websocket_protocol(self):
        ingestor = DroneTelemetryIngestor(stream_source="ws://server:8080")
        assert ingestor._protocol == StreamProtocol.WEBSOCKET

    def test_stdio_protocol(self):
        ingestor = DroneTelemetryIngestor(stream_source="/dev/stdin")
        assert ingestor._protocol == StreamProtocol.STDIO


class TestStreamLifecycle:
    """Test async stream start/stop."""

    @pytest.mark.asyncio
    async def test_start_stop_stream(self):
        ingestor = DroneTelemetryIngestor()
        callback_results = []

        def cb(telemetry):
            callback_results.append(telemetry)

        await ingestor.start_stream(cb)
        assert ingestor.is_streaming

        await ingestor.stop_stream()
        assert not ingestor.is_streaming

    @pytest.mark.asyncio
    async def test_callback_registered(self):
        ingestor = DroneTelemetryIngestor()
        cb = lambda t: None
        await ingestor.start_stream(cb)
        assert len(ingestor._callbacks) == 1
        await ingestor.stop_stream()

    @pytest.mark.asyncio
    async def test_multiple_start_stop_cycles(self):
        ingestor = DroneTelemetryIngestor()
        for _ in range(3):
            await ingestor.start_stream(lambda t: None)
            assert ingestor.is_streaming
            await ingestor.stop_stream()
            assert not ingestor.is_streaming
