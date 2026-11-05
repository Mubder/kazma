"""The Dashboard's System Resources read what the telemetry stream sends.

Live 2026-11-05: CPU and RAM showed "–" whatever the machine did, because the
page read field names /api/telemetry/stream never sent. This builds the
payload the stream builds (``TelemetrySnapshot.to_dict()``, the one source of
its fields) and hands it to ``tests/js/test_dashboard_resources.js``, which
runs the page's real code on it. A renamed field on either side fails here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kazma_core.telemetry import TelemetrySnapshot

ROOT = Path(__file__).resolve().parents[1]
JS_TEST = ROOT / "tests" / "js" / "test_dashboard_resources.js"


def _payload() -> dict:
    snap = TelemetrySnapshot(
        cpu=28.9, ram_used_gb=38.39, ram_total_gb=63.77,
        gpu=37.0, vram_used_gb=6.91, vram_total_gb=22.49,
    )
    return snap.to_dict()


def test_the_stream_payload_has_the_fields_the_page_reads():
    payload = _payload()
    page = (ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    for field in ("cpu", "ram_used_gb", "ram_total_gb"):
        assert field in payload, field
        assert f"data.{field}" in page, f"dashboard.js no longer reads data.{field}"
    # Negative control: the names the page read before 2026-11-05.
    for old in ("cpu_percent", "memory_mb", "memory_percent"):
        assert old not in payload


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_shows_the_stream_payload(tmp_path):
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps(_payload()), encoding="utf-8")
    run = subprocess.run(
        ["node", str(JS_TEST), str(payload_file)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert run.returncode == 0, run.stdout + run.stderr
