#!/usr/bin/env python3
"""Kazma serve script - starts the WebUI server."""

import subprocess
import sys
import time

# Use the same Python that's running this script
python_exe = sys.executable

# Can override the app factory via environment variable
app_factory = "kazma_ui.app:create_app"

try:
    # Start the server
    proc = subprocess.Popen(
        [python_exe, "-m", "uvicorn", app_factory, "--factory", "--host", "0.0.0.0", "--port", "8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    print(f"Server started with PID {proc.pid}")
    print("Open http://localhost:8000 in your browser")
    print("Press Ctrl+C to stop\n")

    # Wait for server to start
    time.sleep(2)

    # Check if process is still running
    if proc.poll() is not None:
        stdout, stderr = proc.communicate()
        if stderr:
            print(f"❌ Server failed to start:\n{stderr.decode()}")
            sys.exit(1)

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("Server stopped")

except FileNotFoundError:
    print("❌ Error: uvicorn not found")
    print("Install with: pip install uvicorn[standard]")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
