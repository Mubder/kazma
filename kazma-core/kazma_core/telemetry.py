"""Hardware Telemetry Engine — Async CPU/RAM/GPU monitoring.

Polls system metrics without blocking the event loop:
  - CPU & RAM via psutil (sync calls wrapped in executor)
  - GPU & VRAM via nvidia-smi subprocess (pure asyncio)
  - Graceful fallback when nvidia-smi is unavailable

Usage::

    monitor = HardwareMonitor()
    stats = await monitor.get_stats()
    # {"cpu": 45.2, "ram_used_gb": 16.4, "ram_total_gb": 32.0,
    #  "gpu": 88.0, "vram_used_gb": 14.2, "vram_total_gb": 24.0}

    async for snapshot in monitor.stream(interval=1.0):
        send_to_client(snapshot)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Data model
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class TelemetrySnapshot:
    """A single hardware telemetry reading."""

    cpu: float = 0.0
    """CPU utilization percentage (0-100)."""

    ram_used_gb: float = 0.0
    """RAM used in GB."""

    ram_total_gb: float = 0.0
    """Total RAM in GB."""

    gpu: float = 0.0
    """GPU utilization percentage (0-100). 0 if no NVIDIA GPU."""

    vram_used_gb: float = 0.0
    """VRAM used in GB. 0 if no NVIDIA GPU."""

    vram_total_gb: float = 0.0
    """Total VRAM in GB. 0 if no NVIDIA GPU."""

    timestamp: float = 0.0
    """Unix timestamp of the reading."""

    error: str = ""
    """Non-empty if any subsystem failed."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the normalized dict format."""
        return {
            "cpu": round(self.cpu, 1),
            "ram_used_gb": round(self.ram_used_gb, 2),
            "ram_total_gb": round(self.ram_total_gb, 2),
            "gpu": round(self.gpu, 1),
            "vram_used_gb": round(self.vram_used_gb, 2),
            "vram_total_gb": round(self.vram_total_gb, 2),
            "timestamp": self.timestamp,
            **({"error": self.error} if self.error else {}),
        }


# ══════════════════════════════════════════════════════════════════════════
# nvidia-smi parser
# ══════════════════════════════════════════════════════════════════════════


def parse_nvidia_smi_output(raw: str) -> tuple[float, float, float]:
    """Parse nvidia-smi CSV output into (gpu_util%, vram_used_mb, vram_total_mb).

    Expected input format (from ``--format=csv,noheader,nounits``)::

        88, 14200, 24576

    Multiple GPU lines are averaged.

    Args:
        raw: Raw stdout from nvidia-smi.

    Returns:
        Tuple of (gpu_percent, vram_used_mb, vram_total_mb).
        Returns (0.0, 0.0, 0.0) on parse failure.
    """
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    if not lines:
        return (0.0, 0.0, 0.0)

    gpu_utils: list[float] = []
    vram_used: list[float] = []
    vram_total: list[float] = []

    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            gpu_utils.append(float(parts[0]))
            vram_used.append(float(parts[1]))
            vram_total.append(float(parts[2]))
        except (ValueError, IndexError):
            continue

    if not gpu_utils:
        return (0.0, 0.0, 0.0)

    avg_gpu = sum(gpu_utils) / len(gpu_utils)
    total_vram_used = sum(vram_used)
    total_vram_total = sum(vram_total)

    return (avg_gpu, total_vram_used, total_vram_total)


# ══════════════════════════════════════════════════════════════════════════
# HardwareMonitor
# ══════════════════════════════════════════════════════════════════════════


class HardwareMonitor:
    """Async hardware telemetry collector.

    Collects CPU/RAM via psutil and GPU/VRAM via nvidia-smi subprocess.
    All I/O is non-blocking.  Falls back gracefully when nvidia-smi
    is unavailable (non-NVIDIA systems).

    Thread-safe: each ``get_stats()`` call is independent.
    """

    def __init__(self) -> None:
        self._nvidia_available: bool | None = None  # None = not yet checked
        self._nvidia_cmd: list[str] = [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]

    # ── Public API ──────────────────────────────────────────────────

    async def get_stats(self) -> TelemetrySnapshot:
        """Collect a single telemetry snapshot.

        Returns:
            TelemetrySnapshot with all metrics.  ``error`` field is
            non-empty if any subsystem failed (but the call never raises).
        """
        snapshot = TelemetrySnapshot(timestamp=time.time())
        errors: list[str] = []

        # CPU & RAM (run psutil in executor to avoid blocking)
        try:
            cpu, ram_used, ram_total = await self._get_cpu_ram()
            snapshot.cpu = cpu
            snapshot.ram_used_gb = ram_used
            snapshot.ram_total_gb = ram_total
        except Exception as exc:
            logger.warning("psutil read failed: %s", exc)
            errors.append(f"cpu_ram: {exc}")

        # GPU & VRAM (async subprocess)
        try:
            gpu, vram_used, vram_total = await self._get_gpu_vram()
            snapshot.gpu = gpu
            snapshot.vram_used_gb = vram_used
            snapshot.vram_total_gb = vram_total
        except Exception as exc:
            logger.debug("GPU telemetry unavailable: %s", exc)
            errors.append(f"gpu: {exc}")

        if errors:
            snapshot.error = "; ".join(errors)

        return snapshot

    async def stream(
        self,
        interval: float = 1.0,
    ) -> AsyncGenerator[TelemetrySnapshot, None]:
        """Continuously yield telemetry snapshots.

        Args:
            interval: Seconds between readings (default 1.0).

        Yields:
            TelemetrySnapshot objects at the configured interval.

        Raises:
            asyncio.CancelledError: When the consumer disconnects.
        """
        while True:
            snapshot = await self.get_stats()
            yield snapshot
            await asyncio.sleep(interval)

    # ── CPU & RAM (psutil) ──────────────────────────────────────────

    async def _get_cpu_ram(self) -> tuple[float, float, float]:
        """Get CPU%, RAM used (GB), RAM total (GB) via psutil.

        Runs the blocking psutil calls in a thread executor.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync_cpu_ram)


    # ── GPU & VRAM (nvidia-smi) ────────────────────────────────────

    async def _get_gpu_vram(self) -> tuple[float, float, float]:
        """Get GPU%, VRAM used (GB), VRAM total (GB) via nvidia-smi.

        Returns (0.0, 0.0, 0.0) if nvidia-smi is not available.
        """
        # Check availability once
        if self._nvidia_available is False:
            return (0.0, 0.0, 0.0)

        try:
            proc = await asyncio.create_subprocess_exec(
                *self._nvidia_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=5.0
            )
        except FileNotFoundError:
            self._nvidia_available = False
            logger.info("nvidia-smi not found — GPU telemetry disabled")
            return (0.0, 0.0, 0.0)
        except asyncio.TimeoutError:
            logger.warning("nvidia-smi timed out after 5s")
            return (0.0, 0.0, 0.0)
        except Exception as exc:
            self._nvidia_available = False
            logger.debug("nvidia-smi subprocess failed: %s", exc)
            return (0.0, 0.0, 0.0)

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            # nvidia-smi returns non-zero if no GPU or driver issue
            self._nvidia_available = False
            logger.info("nvidia-smi exited %d: %s", proc.returncode, err[:200])
            return (0.0, 0.0, 0.0)

        self._nvidia_available = True
        raw = stdout.decode(errors="replace")
        gpu_pct, vram_used_mb, vram_total_mb = parse_nvidia_smi_output(raw)

        return (
            gpu_pct,
            vram_used_mb / 1024.0,   # MB → GB
            vram_total_mb / 1024.0,  # MB → GB
        )


# ══════════════════════════════════════════════════════════════════════════
# Sync helper (runs in thread executor)
# ══════════════════════════════════════════════════════════════════════════


def _sync_cpu_ram() -> tuple[float, float, float]:
    """Blocking call to psutil for CPU and RAM stats.

    Returns:
        (cpu_percent, ram_used_gb, ram_total_gb)
    """
    import psutil

    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    ram_used = mem.used / (1024 ** 3)
    ram_total = mem.total / (1024 ** 3)

    return (cpu, ram_used, ram_total)
