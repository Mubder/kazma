#!/usr/bin/env python3
"""Kazma serve script - starts the WebUI server."""

import subprocess
import sys
from pathlib import Path

# Use the same Python that's running this script
python_exe = sys.executable

# Can override the app factory via environment variable
app_factory = "kazma_ui.app:create_app"

proc = subprocess.Popen(
    [python_exe, "-m", "uvicorn", app_factory, "--factory", "--host", "0.0.0.0", "--port", "8000"],
)

print(f"Server started with PID {proc.pid}")
print("Open http://localhost:8000 in your browser")
print("Press Ctrl+C to stop")

try:
    proc.wait()
except KeyboardInterrupt:
    proc.terminate()
    print("\nServer stopped")
