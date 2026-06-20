"""Drone Fleet Manager for FPV inspection operations.

Manages multiple FPV drones, their telemetry streams,
inspection missions, and emergency landing protocols.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from almuhalab_custom_skills.drone_inspection.telemetry import (
    DroneTelemetryIngestor,
    TelemetryValidationError,
)

logger = logging.getLogger(__name__)


class DroneStatus(str, Enum):
    IDLE = "idle"
    STREAMING = "streaming"
    INSPECTING = "inspecting"
    EMERGENCY_LANDING = "emergency_landing"
    LANDED = "landed"
    OFFLINE = "offline"
    ERROR = "error"


class MissionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABORTED = "aborted"


@dataclass
class DroneState:
    """Current state of a drone in the fleet."""

    drone_id: str
    status: DroneStatus = DroneStatus.IDLE
    config: Dict[str, Any] = field(default_factory=dict)
    registered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_telemetry: Optional[dict] = None
    current_mission_id: Optional[str] = None
    battery_pct: float = 100.0
    signal_strength_dbm: int = -50

    @property
    def is_available(self) -> bool:
        return self.status in (DroneStatus.IDLE, DroneStatus.LANDED)

    @property
    def is_airborne(self) -> bool:
        return self.status in (
            DroneStatus.STREAMING,
            DroneStatus.INSPECTING,
        )

    def to_dict(self) -> dict:
        return {
            "drone_id": self.drone_id,
            "status": self.status.value,
            "battery_pct": self.battery_pct,
            "signal_strength_dbm": self.signal_strength_dbm,
            "current_mission_id": self.current_mission_id,
            "registered_at": self.registered_at,
            "last_telemetry": self.last_telemetry,
        }


@dataclass
class InspectionMission:
    """An inspection mission assigned to a drone."""

    mission_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    area_coordinates: List[Dict[str, float]] = field(default_factory=list)
    targets: List[str] = field(default_factory=list)
    max_duration_seconds: int = 3600
    status: MissionStatus = MissionStatus.PENDING
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    drone_id: Optional[str] = None
    findings_count: int = 0

    def to_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "name": self.name,
            "status": self.status.value,
            "drone_id": self.drone_id,
            "targets": self.targets,
            "findings_count": self.findings_count,
            "started_at": self.started_at,
        }


@dataclass
class FleetStatus:
    """Status overview of the entire drone fleet."""

    total_drones: int = 0
    idle: int = 0
    airborne: int = 0
    inspecting: int = 0
    emergency: int = 0
    offline: int = 0
    active_missions: int = 0
    total_battery_avg: float = 0.0
    drones: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_drones": self.total_drones,
            "idle": self.idle,
            "airborne": self.airborne,
            "inspecting": self.inspecting,
            "emergency": self.emergency,
            "offline": self.offline,
            "active_missions": self.active_missions,
            "total_battery_avg": self.total_battery_avg,
        }


class DroneFleetManager:
    """Manages multiple FPV drones and their telemetry streams.

    Handles drone registration, inspection missions, fleet status,
    and emergency landing protocols for Gas & Oil operations.
    """

    def __init__(self) -> None:
        self.active_drones: Dict[str, DroneState] = {}
        self.ingestors: Dict[str, DroneTelemetryIngestor] = {}
        self.missions: Dict[str, InspectionMission] = {}
        self._telemetry_callbacks: List[Callable[[str, dict], Any]] = []

    def on_telemetry(self, callback: Callable[[str, dict], Any]) -> None:
        """Register a callback for telemetry updates (drone_id, telemetry)."""
        self._telemetry_callbacks.append(callback)

    async def register_drone(self, drone_id: str, config: dict) -> DroneState:
        """Register a new drone in the fleet.

        Creates a telemetry ingestor for the drone using the stream_source
        from config, or defaults to mqtt://localhost:1883.
        """
        if drone_id in self.active_drones:
            logger.warning("Drone %s already registered, updating config", drone_id)
            self.active_drones[drone_id].config = config
            return self.active_drones[drone_id]

        stream_source = config.get("stream_source", "mqtt://localhost:1883")
        ingestor = DroneTelemetryIngestor(stream_source=stream_source)

        state = DroneState(
            drone_id=drone_id,
            status=DroneStatus.IDLE,
            config=config,
        )

        self.active_drones[drone_id] = state
        self.ingestors[drone_id] = ingestor

        logger.info("Registered drone %s (source: %s)", drone_id, stream_source)
        return state

    async def unregister_drone(self, drone_id: str) -> bool:
        """Remove a drone from the fleet."""
        if drone_id not in self.active_drones:
            return False

        # Stop any active telemetry stream
        ingestor = self.ingestors.get(drone_id)
        if ingestor and ingestor.is_streaming:
            await ingestor.stop_stream()

        del self.active_drones[drone_id]
        self.ingestors.pop(drone_id, None)

        # Abort any active mission for this drone
        for mission in self.missions.values():
            if mission.drone_id == drone_id and mission.status == MissionStatus.ACTIVE:
                mission.status = MissionStatus.ABORTED

        logger.info("Unregistered drone %s", drone_id)
        return True

    async def start_inspection(
        self, drone_id: str, mission: InspectionMission
    ) -> str:
        """Start inspection mission on a drone.

        Returns the mission_id.
        Raises ValueError if drone is not available.
        """
        if drone_id not in self.active_drones:
            raise ValueError(f"Drone {drone_id} not registered")

        state = self.active_drones[drone_id]
        if not state.is_available:
            raise ValueError(
                f"Drone {drone_id} is not available (status: {state.status.value})"
            )

        mission.drone_id = drone_id
        mission.status = MissionStatus.ACTIVE
        mission.started_at = datetime.now(timezone.utc).isoformat()

        state.status = DroneStatus.INSPECTING
        state.current_mission_id = mission.mission_id

        self.missions[mission.mission_id] = mission

        # Start telemetry stream
        ingestor = self.ingestors.get(drone_id)
        if ingestor:

            def on_telemetry(telemetry: dict) -> None:
                state.last_telemetry = telemetry
                state.battery_pct = telemetry.get("battery_pct", state.battery_pct)
                state.signal_strength_dbm = telemetry.get(
                    "signal_strength_dbm", state.signal_strength_dbm
                )
                for cb in self._telemetry_callbacks:
                    cb(drone_id, telemetry)

            await ingestor.start_stream(on_telemetry)

        logger.info(
            "Started mission %s on drone %s", mission.mission_id, drone_id
        )
        return mission.mission_id

    async def complete_mission(
        self, mission_id: str, findings_count: int = 0
    ) -> Optional[InspectionMission]:
        """Mark a mission as completed."""
        mission = self.missions.get(mission_id)
        if not mission:
            return None

        mission.status = MissionStatus.COMPLETED
        mission.completed_at = datetime.now(timezone.utc).isoformat()
        mission.findings_count = findings_count

        # Return drone to idle
        if mission.drone_id and mission.drone_id in self.active_drones:
            state = self.active_drones[mission.drone_id]
            state.status = DroneStatus.IDLE
            state.current_mission_id = None

            ingestor = self.ingestors.get(mission.drone_id)
            if ingestor and ingestor.is_streaming:
                await ingestor.stop_stream()

        logger.info("Completed mission %s (%d findings)", mission_id, findings_count)
        return mission

    async def get_fleet_status(self) -> FleetStatus:
        """Get status of all drones in fleet."""
        total = len(self.active_drones)
        idle = 0
        airborne = 0
        inspecting = 0
        emergency = 0
        offline = 0
        battery_sum = 0.0

        drone_dicts = []
        for drone_id, state in self.active_drones.items():
            if state.status == DroneStatus.IDLE or state.status == DroneStatus.LANDED:
                idle += 1
            elif state.status in (DroneStatus.STREAMING, DroneStatus.INSPECTING):
                airborne += 1
                if state.status == DroneStatus.INSPECTING:
                    inspecting += 1
            elif state.status == DroneStatus.EMERGENCY_LANDING:
                emergency += 1
            elif state.status == DroneStatus.OFFLINE:
                offline += 1
            battery_sum += state.battery_pct
            drone_dicts.append(state.to_dict())

        active_missions = sum(
            1
            for m in self.missions.values()
            if m.status == MissionStatus.ACTIVE
        )

        return FleetStatus(
            total_drones=total,
            idle=idle,
            airborne=airborne,
            inspecting=inspecting,
            emergency=emergency,
            offline=offline,
            active_missions=active_missions,
            total_battery_avg=battery_sum / total if total > 0 else 0.0,
            drones=drone_dicts,
        )

    async def emergency_land(self, drone_id: str) -> dict:
        """Emergency landing protocol.

        Immediately stops all streams and transitions drone to
        emergency landing state. Returns landing status.
        """
        if drone_id not in self.active_drones:
            raise ValueError(f"Drone {drone_id} not registered")

        state = self.active_drones[drone_id]
        state.status = DroneStatus.EMERGENCY_LANDING

        # Stop telemetry stream
        ingestor = self.ingestors.get(drone_id)
        if ingestor and ingestor.is_streaming:
            await ingestor.stop_stream()

        # Abort active mission
        if state.current_mission_id:
            mission = self.missions.get(state.current_mission_id)
            if mission and mission.status == MissionStatus.ACTIVE:
                mission.status = MissionStatus.ABORTED

        state.current_mission_id = None

        logger.warning("EMERGENCY LANDING: drone %s", drone_id)

        return {
            "drone_id": drone_id,
            "status": "emergency_landing",
            "message": "Emergency landing initiated. All streams stopped.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_drone(self, drone_id: str) -> Optional[DroneState]:
        """Get state of a specific drone."""
        return self.active_drones.get(drone_id)

    def get_mission(self, mission_id: str) -> Optional[InspectionMission]:
        """Get a specific mission."""
        return self.missions.get(mission_id)

    def get_drone_missions(self, drone_id: str) -> List[InspectionMission]:
        """Get all missions for a drone."""
        return [
            m for m in self.missions.values() if m.drone_id == drone_id
        ]
