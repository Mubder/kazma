"""Drone Telemetry Ingestion Engine for FPV drone data streams.

Streams and parses FPV drone telemetry data from MQTT or other sources.
Feeds the Gas & Oil Trading division's asset inspection workflows.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class StreamProtocol(str, Enum):
    MQTT = "mqtt"
    WEBSOCKET = "websocket"
    STDIO = "stdio"


class TelemetryValidationError(Exception):
    """Raised when telemetry data fails validation."""

    pass


class DroneTelemetryIngestor:
    """Streams and parses FPV drone telemetry data.

    Supports MQTT, WebSocket, and stdio protocols for ingesting
    real-time telemetry from FPV drones used in Gas & Oil inspections.
    """

    TELEMETRY_FIELDS = {
        "timestamp": "ISO 8601",
        "drone_id": "string",
        "latitude": "float",
        "longitude": "float",
        "altitude_m": "float",
        "speed_ms": "float",
        "battery_pct": "float",
        "gimbal_pitch": "float",
        "gimbal_yaw": "float",
        "camera_fps": "int",
        "signal_strength_dbm": "int",
    }

    REQUIRED_FIELDS = {"timestamp", "drone_id", "latitude", "longitude", "altitude_m"}

    MAX_BUFFER_SIZE = 10_000

    def __init__(
        self,
        stream_source: str = "mqtt://localhost:1883",
        max_buffer_size: int = MAX_BUFFER_SIZE,
    ) -> None:
        self.source = stream_source
        self.max_buffer_size = max_buffer_size
        self._buffer: deque[dict] = deque(maxlen=max_buffer_size)
        self._latest_by_drone: dict[str, dict] = {}
        self._streaming = False
        self._stream_task: asyncio.Task[None] | None = None
        self._callbacks: list[Callable[[dict], Any]] = []
        self._protocol = self._parse_protocol(stream_source)

    @staticmethod
    def _parse_protocol(source: str) -> StreamProtocol:
        if source.startswith("mqtt://") or source.startswith("mqtts://"):
            return StreamProtocol.MQTT
        if source.startswith("ws://") or source.startswith("wss://"):
            return StreamProtocol.WEBSOCKET
        return StreamProtocol.STDIO

    @staticmethod
    def validate_telemetry(data: dict) -> dict:
        """Validate and normalize telemetry data.

        Raises TelemetryValidationError if required fields are missing.
        Returns the normalized data dict.
        """
        missing = DroneTelemetryIngestor.REQUIRED_FIELDS - set(data.keys())
        if missing:
            raise TelemetryValidationError(
                f"Missing required telemetry fields: {sorted(missing)}"
            )

        # Normalize timestamp to ISO 8601
        ts = data.get("timestamp")
        if isinstance(ts, (int, float)):
            data["timestamp"] = (
                datetime.fromtimestamp(ts, tz=UTC).isoformat()
            )
        elif isinstance(ts, str):
            try:
                datetime.fromisoformat(ts)
            except ValueError:
                raise TelemetryValidationError(
                    f"Invalid timestamp format: {ts}"
                )

        # Ensure drone_id is string
        data["drone_id"] = str(data["drone_id"])

        # Validate numeric fields
        float_fields = {
            "latitude",
            "longitude",
            "altitude_m",
            "speed_ms",
            "battery_pct",
            "gimbal_pitch",
            "gimbal_yaw",
        }
        int_fields = {"camera_fps", "signal_strength_dbm"}

        for field in float_fields:
            if field in data:
                try:
                    data[field] = float(data[field])
                except (TypeError, ValueError):
                    raise TelemetryValidationError(
                        f"Field '{field}' must be numeric, got {type(data[field]).__name__}"
                    )

        for field in int_fields:
            if field in data:
                try:
                    data[field] = int(data[field])
                except (TypeError, ValueError):
                    raise TelemetryValidationError(
                        f"Field '{field}' must be int, got {type(data[field]).__name__}"
                    )

        return data

    def _store_telemetry(self, telemetry: dict) -> None:
        """Store telemetry in buffer and update per-drone latest."""
        self._buffer.append(telemetry)
        drone_id = telemetry["drone_id"]
        self._latest_by_drone[drone_id] = telemetry

    async def start_stream(
        self,
        callback: Callable[[dict], Any],
    ) -> None:
        """Start streaming telemetry from drone source.

        The callback is invoked for each validated telemetry message.
        For MQTT, subscribes to 'drones/+/telemetry' topic.
        """
        self._streaming = True
        self._callbacks.append(callback)
        logger.info("Telemetry stream started from %s", self.source)

        if self._protocol == StreamProtocol.MQTT:
            self._stream_task = asyncio.create_task(self._mqtt_loop(callback))
        elif self._protocol == StreamProtocol.WEBSOCKET:
            self._stream_task = asyncio.create_task(self._ws_loop(callback))
        else:
            self._stream_task = asyncio.create_task(self._stdio_loop(callback))

    async def stop_stream(self) -> None:
        """Gracefully stop telemetry stream."""
        self._streaming = False
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        logger.info("Telemetry stream stopped")
        self._callbacks.clear()

    def get_latest_telemetry(self, drone_id: str) -> dict | None:
        """Get most recent telemetry for a drone."""
        return self._latest_by_drone.get(drone_id)

    def get_all_latest(self) -> dict[str, dict]:
        """Get latest telemetry for all known drones."""
        return dict(self._latest_by_drone)

    def get_buffer(self, limit: int | None = None) -> list[dict]:
        """Return buffer contents, optionally limited to most recent N."""
        if limit is None:
            return list(self._buffer)
        return list(self._buffer)[-limit:]

    def clear_buffer(self) -> None:
        """Clear the telemetry buffer."""
        self._buffer.clear()
        self._latest_by_drone.clear()

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    @property
    def is_streaming(self) -> bool:
        return self._streaming

    def process_message(self, raw_message: str) -> dict:
        """Parse a raw JSON message and store validated telemetry.

        Returns the validated telemetry dict.
        """
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError as e:
            raise TelemetryValidationError(f"Invalid JSON: {e}")

        validated = self.validate_telemetry(data)
        self._store_telemetry(validated)
        return validated

    def process_message_dict(self, data: dict) -> dict:
        """Process a pre-parsed telemetry dict.

        Returns the validated telemetry dict.
        """
        validated = self.validate_telemetry(data)
        self._store_telemetry(validated)
        return validated

    # --- Protocol loops (abstracted for testability) ---

    async def _mqtt_loop(
        self, callback: Callable[[dict], Any]
    ) -> None:
        """Simulated MQTT loop. In production, uses paho-mqtt."""
        try:
            import paho.mqtt.client as mqtt  # type: ignore

            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            # Parse host/port from source
            from urllib.parse import urlparse

            parsed = urlparse(self.source)
            host = parsed.hostname or "localhost"
            port = parsed.port or 1883

            def on_message(client: Any, userdata: Any, msg: Any) -> None:
                if not self._streaming:
                    return
                try:
                    telemetry = self.process_message(msg.payload.decode())
                    for cb in self._callbacks:
                        cb(telemetry)
                except Exception as e:
                    logger.error("Error processing MQTT message: %s", e)

            client.on_message = on_message
            try:
                client.connect(host, port)
            except OSError as e:
                logger.warning("MQTT connect failed (%s), using simulated loop", e)
                await self._simulated_loop(callback)
                return

            client.subscribe("drones/+/telemetry")
            client.loop_start()

            while self._streaming:
                await asyncio.sleep(0.1)

            client.loop_stop()
            client.disconnect()
        except ImportError:
            logger.warning("paho-mqtt not installed, using simulated MQTT")
            await self._simulated_loop(callback)

    async def _ws_loop(
        self, callback: Callable[[dict], Any]
    ) -> None:
        """WebSocket loop stub."""
        await self._simulated_loop(callback)

    async def _stdio_loop(
        self, callback: Callable[[dict], Any]
    ) -> None:
        """Stdio loop for testing."""
        await self._simulated_loop(callback)

    async def _simulated_loop(
        self, callback: Callable[[dict], Any]
    ) -> None:
        """Simulated loop that waits until streaming is stopped."""
        while self._streaming:
            await asyncio.sleep(0.1)
