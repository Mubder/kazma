import subprocess, time, sys

proc = subprocess.Popen(
    ["/home/balfaris/kazma/.venv/bin/python", "-m", "uvicorn", "kazma_ui.app:create_app", 
     "--factory", "--host", "0.0.0.0", "--port", "8000"],
    cwd="/home/balfaris/kazma",
)

print(f"Server started with PID {proc.pid}")
print("Open http://localhost:8000 in your browser")
print("Press Ctrl+C to stop")

try:
    proc.wait()
except KeyboardInterrupt:
    proc.terminate()
    print("\nServer stopped")
