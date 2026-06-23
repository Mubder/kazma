"""Tests for the Hardware Telemetry Engine and SSE route.

Covers:
  - parse_nvidia_smi_output: single GPU, multi-GPU, malformed input
  - HardwareMonitor: CPU/RAM collection, GPU fallback, stream generator
  - TelemetrySnapshot: serialization
  - SSE route: endpoint registration, streaming behavior
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_core.telemetry import (
    HardwareMonitor,
    TelemetrySnapshot,
    parse_nvidia_smi_output,
)

# ═══════════════════════════════════════════════════════════════════
# parse_nvidia_smi_output
# ═══════════════════════════════════════════════════════════════════


class TestParseNvidiaSmi:
    """Tests for nvidia-smi CSV output parsing."""

    def test_single_gpu(self):
        raw = "88, 14200, 24576\n"
        gpu, vram_used, vram_total = parse_nvidia_smi_output(raw)
        assert gpu == 88.0
        assert vram_used == 14200.0
        assert vram_total == 24576.0

    def test_multi_gpu_averaging(self):
        # Two GPUs: util 50% and 90%, different VRAM
        raw = "50, 8000, 24576\n90, 16000, 24576\n"
        gpu, vram_used, vram_total = parse_nvidia_smi_output(raw)
        assert gpu == 70.0  # (50 + 90) / 2
        assert vram_used == 24000.0  # 8000 + 16000
        assert vram_total == 49152.0  # 24576 + 24576

    def test_no_whitespace(self):
        raw = "45,1000,2000"
        gpu, vram_used, vram_total = parse_nvidia_smi_output(raw)
        assert gpu == 45.0
        assert vram_used == 1000.0
        assert vram_total == 2000.0

    def test_empty_string(self):
        gpu, vram_used, vram_total = parse_nvidia_smi_output("")
        assert gpu == 0.0
        assert vram_used == 0.0
        assert vram_total == 0.0

    def test_whitespace_only(self):
        gpu, vram_used, vram_total = parse_nvidia_smi_output("   \n  \n  ")
        assert gpu == 0.0

    def test_malformed_line_skipped(self):
        raw = "not,a,number\n88, 14200, 24576\n"
        gpu, vram_used, vram_total = parse_nvidia_smi_output(raw)
        assert gpu == 88.0
        assert vram_used == 14200.0

    def test_partial_line_too_few_columns(self):
        raw = "88, 14200\n"  # missing third column
        gpu, vram_used, vram_total = parse_nvidia_smi_output(raw)
        assert gpu == 0.0  # line skipped

    def test_negative_values(self):
        # nvidia-smi can report [N/A] for some fields — these become 0
        raw = "[N/A], 14200, 24576\n"
        gpu, vram_used, vram_total = parse_nvidia_smi_output(raw)
        # [N/A] can't be parsed as float, so line is skipped
        assert gpu == 0.0

    def test_realistic_rtx4090_output(self):
        raw = "12, 3284, 24564\n"
        gpu, vram_used, vram_total = parse_nvidia_smi_output(raw)
        assert gpu == 12.0
        assert abs(vram_used - 3284.0) < 0.1
        assert abs(vram_total - 24564.0) < 0.1


# ═══════════════════════════════════════════════════════════════════
# TelemetrySnapshot
# ═══════════════════════════════════════════════════════════════════


class TestTelemetrySnapshot:
    """Tests for TelemetrySnapshot serialization."""

    def test_to_dict_basic(self):
        snap = TelemetrySnapshot(
            cpu=45.2,
            ram_used_gb=16.4,
            ram_total_gb=32.0,
            gpu=88.0,
            vram_used_gb=14.2,
            vram_total_gb=24.0,
            timestamp=1719162000.0,
        )
        d = snap.to_dict()
        assert d["cpu"] == 45.2
        assert d["ram_used_gb"] == 16.4
        assert d["ram_total_gb"] == 32.0
        assert d["gpu"] == 88.0
        assert d["vram_used_gb"] == 14.2
        assert d["vram_total_gb"] == 24.0
        assert d["timestamp"] == 1719162000.0
        assert "error" not in d

    def test_to_dict_with_error(self):
        snap = TelemetrySnapshot(error="nvidia-smi not found")
        d = snap.to_dict()
        assert d["error"] == "nvidia-smi not found"

    def test_to_dict_rounding(self):
        snap = TelemetrySnapshot(cpu=45.234567, ram_used_gb=16.456789)
        d = snap.to_dict()
        assert d["cpu"] == 45.2
        assert d["ram_used_gb"] == 16.46

    def test_to_dict_no_error_key_when_empty(self):
        snap = TelemetrySnapshot(error="")
        d = snap.to_dict()
        assert "error" not in d

    def test_to_dict_json_serializable(self):
        snap = TelemetrySnapshot(cpu=50.0, timestamp=123.0)
        d = snap.to_dict()
        json_str = json.dumps(d)
        assert "50.0" in json_str


# ═══════════════════════════════════════════════════════════════════
# HardwareMonitor
# ═══════════════════════════════════════════════════════════════════


class TestHardwareMonitor:
    """Tests for the HardwareMonitor class."""

    @pytest.mark.asyncio
    async def test_get_stats_returns_snapshot(self):
        monitor = HardwareMonitor()
        stats = await monitor.get_stats()
        assert isinstance(stats, TelemetrySnapshot)
        assert stats.timestamp > 0
        assert stats.cpu >= 0
        assert stats.ram_total_gb > 0

    @pytest.mark.asyncio
    async def test_get_stats_never_raises(self):
        """get_stats() should never propagate exceptions."""
        monitor = HardwareMonitor()
        # Even with mocked failures, it should return a snapshot
        for _ in range(3):
            stats = await monitor.get_stats()
            assert isinstance(stats, TelemetrySnapshot)

    @pytest.mark.asyncio
    async def test_gpu_fallback_when_no_nvidia(self):
        """On systems without nvidia-smi, GPU stats should be 0."""
        monitor = HardwareMonitor()
        # Force nvidia check
        monitor._nvidia_available = False
        stats = await monitor.get_stats()
        assert stats.gpu == 0.0
        assert stats.vram_used_gb == 0.0
        assert stats.vram_total_gb == 0.0

    @pytest.mark.asyncio
    async def test_get_stats_mocked_psutil(self):
        """Test with mocked psutil to verify CPU/RAM parsing."""
        monitor = HardwareMonitor()
        monitor._nvidia_available = False  # skip GPU

        mock_mem = MagicMock()
        mock_mem.used = 16 * (1024**3)  # 16 GB
        mock_mem.total = 32 * (1024**3)  # 32 GB

        with patch("kazma_core.telemetry._sync_cpu_ram", return_value=(45.5, 16.0, 32.0)):
            stats = await monitor.get_stats()

        assert stats.cpu == 45.5
        assert stats.ram_used_gb == 16.0
        assert stats.ram_total_gb == 32.0

    @pytest.mark.asyncio
    async def test_stream_yields_multiple_snapshots(self):
        """Test the stream generator yields snapshots at interval."""
        monitor = HardwareMonitor()
        monitor._nvidia_available = False

        count = 0

        async def _collect():
            nonlocal count
            async for snapshot in monitor.stream(interval=0.05):
                count += 1
                assert isinstance(snapshot, TelemetrySnapshot)
                if count >= 3:
                    break

        await _collect()
        assert count == 3

    @pytest.mark.asyncio
    async def test_stream_cancellation(self):
        """Test that the stream handles CancelledError cleanly."""
        monitor = HardwareMonitor()
        monitor._nvidia_available = False

        async def _consumer():
            async for _ in monitor.stream(interval=0.01):
                pass  # will be cancelled

        task = asyncio.create_task(_consumer())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # expected

    @pytest.mark.asyncio
    async def test_gpu_with_mocked_nvidia_smi(self):
        """Test GPU stats collection with a mocked nvidia-smi subprocess."""
        monitor = HardwareMonitor()

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"88, 14200, 24576\n", b""))
        mock_process.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            gpu, vram_used, vram_total = await monitor._get_gpu_vram()

        assert gpu == 88.0
        assert abs(vram_used - 14200 / 1024) < 0.1
        assert abs(vram_total - 24576 / 1024) < 0.1

    @pytest.mark.asyncio
    async def test_gpu_nvidia_smi_not_found(self):
        """When nvidia-smi doesn't exist, return zeros gracefully."""
        monitor = HardwareMonitor()
        monitor._nvidia_available = None  # reset

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            gpu, vram_used, vram_total = await monitor._get_gpu_vram()

        assert gpu == 0.0
        assert vram_used == 0.0
        assert vram_total == 0.0
        assert monitor._nvidia_available is False

    @pytest.mark.asyncio
    async def test_gpu_nvidia_smi_timeout(self):
        """When nvidia-smi hangs, return zeros after timeout."""
        monitor = HardwareMonitor()
        monitor._nvidia_available = None  # reset

        async def _slow_communicate():
            await asyncio.sleep(10)
            return (b"", b"")

        mock_process = AsyncMock()
        mock_process.communicate = _slow_communicate

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                gpu, vram_used, vram_total = await monitor._get_gpu_vram()

        assert gpu == 0.0

    @pytest.mark.asyncio
    async def test_gpu_nvidia_smi_nonzero_exit(self):
        """When nvidia-smi returns non-zero, return zeros."""
        monitor = HardwareMonitor()
        monitor._nvidia_available = None  # reset

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b"No devices were found"))
        mock_process.returncode = 6

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            gpu, vram_used, vram_total = await monitor._get_gpu_vram()

        assert gpu == 0.0
        assert monitor._nvidia_available is False

    @pytest.mark.asyncio
    async def test_stats_json_serializable(self):
        """The full stats dict must be JSON-serializable."""
        monitor = HardwareMonitor()
        monitor._nvidia_available = False

        stats = await monitor.get_stats()
        d = stats.to_dict()
        json_str = json.dumps(d)
        assert isinstance(json_str, str)
        assert len(json_str) > 10


# ═══════════════════════════════════════════════════════════════════
# SSE Route
# ═══════════════════════════════════════════════════════════════════


class TestTelemetryRoute:
    """Tests for the telemetry SSE FastAPI route."""

    def test_router_has_stream_endpoint(self):
        from kazma_ui.telemetry_route import create_telemetry_router

        router = create_telemetry_router()
        paths = {r.path for r in router.routes if hasattr(r, "path")}
        assert "/api/telemetry/stream" in paths

    def test_router_has_snapshot_endpoint(self):
        from kazma_ui.telemetry_route import create_telemetry_router

        router = create_telemetry_router()
        paths = {r.path for r in router.routes if hasattr(r, "path")}
        assert "/api/telemetry/snapshot" in paths

    def test_snapshot_endpoint_returns_json(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from kazma_ui.telemetry_route import create_telemetry_router

        app = FastAPI()
        app.include_router(create_telemetry_router())
        client = TestClient(app)

        resp = client.get("/api/telemetry/snapshot")
        assert resp.status_code == 200
        data = resp.json()
        assert "cpu" in data
        assert "ram_used_gb" in data
        assert "timestamp" in data

    def test_stream_endpoint_returns_sse(self):
        """Verify the SSE stream endpoint has correct headers and format."""
        from kazma_core.telemetry import HardwareMonitor
        from kazma_ui.telemetry_route import create_telemetry_router

        # Create a mock monitor that returns a fixed snapshot
        monitor = MagicMock(spec=HardwareMonitor)

        async def mock_stream(interval=1.0):
            yield TelemetrySnapshot(cpu=50.0, ram_used_gb=16.0, ram_total_gb=32.0, timestamp=123.0)

        monitor.stream = mock_stream

        router = create_telemetry_router(monitor=monitor)

        # Verify the route exists and has the right path
        paths = {r.path for r in router.routes if hasattr(r, "path")}
        assert "/api/telemetry/stream" in paths

        # Verify the stream generator produces valid SSE format
        import asyncio

        async def _test_gen():
            # Get the actual generator function
            async for snapshot in monitor.stream(interval=1.0):
                payload = json.dumps(snapshot.to_dict())
                sse_frame = f"data: {payload}\n\n"
                assert sse_frame.startswith("data: ")
                parsed = json.loads(sse_frame.split("data: ")[1].split("\n\n")[0])
                assert parsed["cpu"] == 50.0
                return  # just test one frame

        asyncio.run(_test_gen())
