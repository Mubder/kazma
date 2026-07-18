#!/usr/bin/env python3
"""Kazma serve script - starts the WebUI server."""

import os
import subprocess
import sys
import time

# Use the same Python that's running this script
python_exe = sys.executable

# Can override the app factory via environment variable
app_factory = "kazma_ui.app:create_app"

# Bind to all interfaces by default so the server is reachable from the
# Windows host (via localhost/127.0.0.1) as well as inside WSL. Override with
# KAZMA_HOST if you need to restrict to a single interface.
host = os.environ.get("KAZMA_HOST", "0.0.0.0")

# Set a default secret when binding to 0.0.0.0 so app.py doesn't warn about an
# unsecured public bind. Override with your own strong value if desired.
if host == "0.0.0.0" and not os.environ.get("KAZMA_SECRET"):
    os.environ["KAZMA_SECRET"] = "kazma-local-dev-secret"

try:
    # Start the server
    proc = subprocess.Popen(
        [python_exe, "-m", "uvicorn", app_factory, "--factory", "--host", host, "--port", "9090"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    print(f"Server started with PID {proc.pid}")
    print("Open http://localhost:9090 in your browser")
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
