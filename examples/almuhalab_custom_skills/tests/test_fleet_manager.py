"""Tests for DroneFleetManager."""

from __future__ import annotations

import pytest

from almuhalab_custom_skills.drone_inspection.fleet_manager import (
    DroneFleetManager,
    DroneState,
    DroneStatus,
    InspectionMission,
    MissionStatus,
)


class TestDroneRegistration:
    """Test drone registration and unregistration."""

    @pytest.mark.asyncio
    async def test_register_drone(self):
        fleet = DroneFleetManager()
        state = await fleet.register_drone("drone-001", {"stream_source": "mqtt://broker:1883"})
        assert state.drone_id == "drone-001"
        assert state.status == DroneStatus.IDLE
        assert "drone-001" in fleet.active_drones

    @pytest.mark.asyncio
    async def test_register_duplicate_updates(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {"type": "survey"})
        state = await fleet.register_drone("drone-001", {"type": "inspection"})
        assert state.config["type"] == "inspection"
        assert len(fleet.active_drones) == 1

    @pytest.mark.asyncio
    async def test_unregister_drone(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        result = await fleet.unregister_drone("drone-001")
        assert result is True
        assert "drone-001" not in fleet.active_drones

    @pytest.mark.asyncio
    async def test_unregister_nonexistent(self):
        fleet = DroneFleetManager()
        result = await fleet.unregister_drone("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_register_creates_ingestor(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {"stream_source": "mqtt://broker:1883"})
        assert "drone-001" in fleet.ingestors

    @pytest.mark.asyncio
    async def test_get_drone(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        state = fleet.get_drone("drone-001")
        assert state is not None
        assert state.drone_id == "drone-001"

    @pytest.mark.asyncio
    async def test_get_drone_nonexistent(self):
        fleet = DroneFleetManager()
        assert fleet.get_drone("nonexistent") is None


class TestDroneState:
    """Test DroneState properties."""

    def test_idle_is_available(self):
        state = DroneState(drone_id="d1", status=DroneStatus.IDLE)
        assert state.is_available is True
        assert state.is_airborne is False

    def test_landed_is_available(self):
        state = DroneState(drone_id="d1", status=DroneStatus.LANDED)
        assert state.is_available is True

    def test_streaming_is_airborne(self):
        state = DroneState(drone_id="d1", status=DroneStatus.STREAMING)
        assert state.is_available is False
        assert state.is_airborne is True

    def test_inspecting_is_airborne(self):
        state = DroneState(drone_id="d1", status=DroneStatus.INSPECTING)
        assert state.is_airborne is True

    def test_emergency_not_available(self):
        state = DroneState(drone_id="d1", status=DroneStatus.EMERGENCY_LANDING)
        assert state.is_available is False
        assert state.is_airborne is False

    def test_to_dict(self):
        state = DroneState(drone_id="d1", battery_pct=85.0)
        d = state.to_dict()
        assert d["drone_id"] == "d1"
        assert d["battery_pct"] == 85.0
        assert d["status"] == "idle"


class TestInspectionMission:
    """Test InspectionMission lifecycle."""

    def test_mission_creation(self):
        mission = InspectionMission(name="Pipeline Survey")
        assert mission.name == "Pipeline Survey"
        assert mission.status == MissionStatus.PENDING
        assert len(mission.mission_id) == 8

    def test_mission_to_dict(self):
        mission = InspectionMission(name="Test")
        d = mission.to_dict()
        assert d["name"] == "Test"
        assert d["status"] == "pending"

    def test_mission_unique_ids(self):
        m1 = InspectionMission()
        m2 = InspectionMission()
        assert m1.mission_id != m2.mission_id


class TestStartInspection:
    """Test inspection mission start."""

    @pytest.mark.asyncio
    async def test_start_inspection(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        mission = InspectionMission(name="Survey A")
        mission_id = await fleet.start_inspection("drone-001", mission)
        assert mission_id == mission.mission_id
        assert mission.status == MissionStatus.ACTIVE
        assert fleet.active_drones["drone-001"].status == DroneStatus.INSPECTING

    @pytest.mark.asyncio
    async def test_start_inspection_unregistered_raises(self):
        fleet = DroneFleetManager()
        mission = InspectionMission()
        with pytest.raises(ValueError, match="not registered"):
            await fleet.start_inspection("nonexistent", mission)

    @pytest.mark.asyncio
    async def test_start_inspection_busy_drone_raises(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        m1 = InspectionMission()
        await fleet.start_inspection("drone-001", m1)
        m2 = InspectionMission()
        with pytest.raises(ValueError, match="not available"):
            await fleet.start_inspection("drone-001", m2)

    @pytest.mark.asyncio
    async def test_start_inspection_stores_mission(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        mission = InspectionMission()
        await fleet.start_inspection("drone-001", mission)
        assert mission.mission_id in fleet.missions


class TestCompleteMission:
    """Test mission completion."""

    @pytest.mark.asyncio
    async def test_complete_mission(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        mission = InspectionMission()
        await fleet.start_inspection("drone-001", mission)
        result = await fleet.complete_mission(mission.mission_id, findings_count=5)
        assert result is not None
        assert result.status == MissionStatus.COMPLETED
        assert result.findings_count == 5
        assert fleet.active_drones["drone-001"].status == DroneStatus.IDLE

    @pytest.mark.asyncio
    async def test_complete_nonexistent_mission(self):
        fleet = DroneFleetManager()
        result = await fleet.complete_mission("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_complete_clears_current_mission(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        mission = InspectionMission()
        await fleet.start_inspection("drone-001", mission)
        await fleet.complete_mission(mission.mission_id)
        assert fleet.active_drones["drone-001"].current_mission_id is None


class TestFleetStatus:
    """Test fleet status reporting."""

    @pytest.mark.asyncio
    async def test_empty_fleet(self):
        fleet = DroneFleetManager()
        status = await fleet.get_fleet_status()
        assert status.total_drones == 0
        assert status.idle == 0

    @pytest.mark.asyncio
    async def test_fleet_with_drones(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        await fleet.register_drone("drone-002", {})
        status = await fleet.get_fleet_status()
        assert status.total_drones == 2
        assert status.idle == 2

    @pytest.mark.asyncio
    async def test_fleet_with_inspecting_drone(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        await fleet.register_drone("drone-002", {})
        mission = InspectionMission()
        await fleet.start_inspection("drone-001", mission)
        status = await fleet.get_fleet_status()
        assert status.total_drones == 2
        assert status.inspecting == 1
        assert status.active_missions == 1

    @pytest.mark.asyncio
    async def test_fleet_status_to_dict(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        status = await fleet.get_fleet_status()
        d = status.to_dict()
        assert d["total_drones"] == 1

    @pytest.mark.asyncio
    async def test_fleet_battery_average(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        await fleet.register_drone("drone-002", {})
        fleet.active_drones["drone-001"].battery_pct = 100.0
        fleet.active_drones["drone-002"].battery_pct = 80.0
        status = await fleet.get_fleet_status()
        assert status.total_battery_avg == 90.0

    @pytest.mark.asyncio
    async def test_fleet_drones_list(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        status = await fleet.get_fleet_status()
        assert len(status.drones) == 1
        assert status.drones[0]["drone_id"] == "drone-001"


class TestEmergencyLanding:
    """Test emergency landing protocol."""

    @pytest.mark.asyncio
    async def test_emergency_land(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        result = await fleet.emergency_land("drone-001")
        assert result["status"] == "emergency_landing"
        assert fleet.active_drones["drone-001"].status == DroneStatus.EMERGENCY_LANDING

    @pytest.mark.asyncio
    async def test_emergency_land_unregistered_raises(self):
        fleet = DroneFleetManager()
        with pytest.raises(ValueError, match="not registered"):
            await fleet.emergency_land("nonexistent")

    @pytest.mark.asyncio
    async def test_emergency_land_aborts_mission(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        mission = InspectionMission()
        await fleet.start_inspection("drone-001", mission)
        await fleet.emergency_land("drone-001")
        assert fleet.missions[mission.mission_id].status == MissionStatus.ABORTED
        assert fleet.active_drones["drone-001"].current_mission_id is None

    @pytest.mark.asyncio
    async def test_emergency_land_clears_mission(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        mission = InspectionMission()
        await fleet.start_inspection("drone-001", mission)
        result = await fleet.emergency_land("drone-001")
        assert "timestamp" in result


class TestMissionQueries:
    """Test mission query methods."""

    @pytest.mark.asyncio
    async def test_get_mission(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        mission = InspectionMission()
        await fleet.start_inspection("drone-001", mission)
        result = fleet.get_mission(mission.mission_id)
        assert result is not None
        assert result.name == mission.name

    @pytest.mark.asyncio
    async def test_get_mission_nonexistent(self):
        fleet = DroneFleetManager()
        assert fleet.get_mission("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_drone_missions(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        m1 = InspectionMission(name="Mission 1")
        m2 = InspectionMission(name="Mission 2")
        await fleet.start_inspection("drone-001", m1)
        await fleet.complete_mission(m1.mission_id)
        await fleet.start_inspection("drone-001", m2)
        missions = fleet.get_drone_missions("drone-001")
        assert len(missions) == 2


class TestTelemetryCallbacks:
    """Test telemetry callback system."""

    @pytest.mark.asyncio
    async def test_telemetry_callback_registered(self):
        fleet = DroneFleetManager()
        received = []
        fleet.on_telemetry(lambda did, t: received.append((did, t)))
        assert len(fleet._telemetry_callbacks) == 1


class TestMultipleConcurrentDrones:
    """Test fleet handling multiple concurrent drones."""

    @pytest.mark.asyncio
    async def test_multiple_drones_registered(self):
        fleet = DroneFleetManager()
        for i in range(5):
            await fleet.register_drone(f"drone-{i:03d}", {})
        assert len(fleet.active_drones) == 5
        status = await fleet.get_fleet_status()
        assert status.total_drones == 5
        assert status.idle == 5

    @pytest.mark.asyncio
    async def test_concurrent_missions(self):
        fleet = DroneFleetManager()
        for i in range(3):
            await fleet.register_drone(f"drone-{i:03d}", {})
            mission = InspectionMission(name=f"Mission {i}")
            await fleet.start_inspection(f"drone-{i:03d}", mission)

        status = await fleet.get_fleet_status()
        assert status.inspecting == 3
        assert status.active_missions == 3

    @pytest.mark.asyncio
    async def test_mixed_fleet_states(self):
        fleet = DroneFleetManager()
        await fleet.register_drone("drone-001", {})
        await fleet.register_drone("drone-002", {})
        await fleet.register_drone("drone-003", {})

        # drone-001 inspecting
        m = InspectionMission()
        await fleet.start_inspection("drone-001", m)

        # drone-003 emergency
        await fleet.emergency_land("drone-003")

        status = await fleet.get_fleet_status()
        assert status.total_drones == 3
        assert status.inspecting == 1
        assert status.emergency == 1
        assert status.idle == 1
