#!/usr/bin/env python3
"""
Load Test Runner for Kazma

Usage:
    python run_loadtests.py --scenario=swarm --users=50 --spawn-rate=5 --run-time=60s
    python run_loadtests.py --scenario=websocket --users=30 --spawn-rate=3 --run-time=120s
    python run_loadtests.py --scenario=all --users=100 --spawn-rate=10 --run-time=300s --headless
"""

import argparse
import subprocess
import sys
import os
import time
from datetime import datetime
from pathlib import Path


def run_command(cmd, cwd=None, env=None, capture_output=False):
    """Run a shell command."""
    print(f"\n[RUN] {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        capture_output=capture_output,
        text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] Command failed with exit code {result.returncode}")
        if capture_output:
            print(f"stdout: {result.stdout}")
            print(f"stderr: {result.stderr}")
    return result


def check_server(host, timeout=30):
    """Check if Kazma server is running."""
    import requests
    for i in range(timeout):
        try:
            resp = requests.get(f"{host}/health", timeout=2)
            if resp.status_code == 200:
                print(f"[OK] Server ready at {host}")
                return True
        except Exception:
            pass
        time.sleep(1)
    print(f"[ERROR] Server not ready at {host} after {timeout}s")
    return False


def run_locust_scenario(scenario, host, users, spawn_rate, run_time, headless, output_dir):
    """Run a Locust scenario."""
    locustfiles = {
        "swarm": "locustfile_swarm.py",
        "websocket": "locustfile_websocket.py",
        "mixed": "locustfile_swarm.py",  # Use SwarmDispatchUser which has mixed tasks
    }
    
    if scenario not in locustfiles:
        print(f"[ERROR] Unknown scenario: {scenario}")
        return False
    
    locustfile = locustfiles[scenario]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    cmd = [
        "locust",
        "-f", f"loadtests/{locustfile}",
        "--host", host,
        "--users", str(users),
        "--spawn-rate", str(spawn_rate),
        "--run-time", run_time,
    ]
    
    if headless:
        cmd.append("--headless")
        
        # HTML report
        html_report = output_dir / f"locust_{scenario}_{timestamp}.html"
        cmd.extend(["--html", str(html_report)])
        
        # CSV export
        csv_prefix = output_dir / f"locust_{scenario}_{timestamp}"
        cmd.extend(["--csv", str(csv_prefix)])
        
        # Log file
        log_file = output_dir / f"locust_{scenario}_{timestamp}.log"
        cmd.extend(["--logfile", str(log_file), "--loglevel", "INFO"])
    
    # Set LOCUST_LOCUSTFILE for locust-plugins
    env = {"LOCUST_LOCUSTFILE": f"loadtests/{locustfile}"}
    
    result = run_command(cmd, env=env)
    return result.returncode == 0


def run_k6_scenario(scenario, host, users, run_time, output_dir):
    """Run a k6 scenario."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    env = {
        "KAZMA_HOST": host,
        "KAZMA_WS_HOST": host.replace("http", "ws"),
        "SCENARIO": scenario,
    }
    
    # k6 options
    cmd = [
        "k6", "run",
        f"loadtests/k6_swarm.js",
        "--vus", str(users),
        "--duration", run_time,
    ]
    
    # Output reports
    html_report = output_dir / f"k6_{scenario}_{timestamp}.html"
    json_report = output_dir / f"k6_{scenario}_{timestamp}.json"
    junit_report = output_dir / f"k6_{scenario}_{timestamp}.xml"
    
    cmd.extend([
        "--out", f"html={html_report}",
        "--out", f"json={json_report}",
        "--out", f"junit={junit_report}",
    ])
    
    result = run_command(cmd, env=env)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Kazma Load Test Runner")
    parser.add_argument("--scenario", choices=["swarm", "websocket", "mixed", "all", "k6"], 
                        default="swarm", help="Test scenario to run")
    parser.add_argument("--host", default="http://localhost:9090", 
                        help="Target host (default: http://localhost:9090)")
    parser.add_argument("--users", type=int, default=50, 
                        help="Number of concurrent users (default: 50)")
    parser.add_argument("--spawn-rate", type=float, default=5, 
                        help="Users per second to spawn (default: 5)")
    parser.add_argument("--run-time", default="60s", 
                        help="Test duration (e.g., 60s, 5m, 1h) (default: 60s)")
    parser.add_argument("--headless", action="store_true", 
                        help="Run in headless mode (no Web UI)")
    parser.add_argument("--output-dir", default="loadtest_reports",
                        help="Output directory for reports (default: loadtest_reports)")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="List available scenarios and exit")
    parser.add_argument("--no-server-check", action="store_true",
                        help="Skip server health check")
    parser.add_argument("--start-server", action="store_true",
                        help="Start Kazma server before test (requires .venv)")
    parser.add_argument("--tool", choices=["locust", "k6", "both"], default="locust",
                        help="Load testing tool to use (default: locust)")
    
    args = parser.parse_args()
    
    # Handle list-scenarios
    if args.list_scenarios:
        print("Available Locust scenarios:")
        print("  swarm      - Swarm dispatch load test (locustfile_swarm.py)")
        print("  websocket  - WebSocket/SSE/HITL test (locustfile_websocket.py)")
        print("  mixed      - Combined workload")
        print("  all        - Run all scenarios")
        print()
        print("Available k6 scenarios:")
        print("  swarm      - Swarm dispatch + polling")
        print("  sse        - SSE stream consumption")
        print("  hitl       - HITL approval flow")
        print("  mixed      - Combined (default for k6)")
        return 0
    
    # Setup output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 60)
    print("KAZMA LOAD TEST RUNNER")
    print("=" * 60)
    print(f"Scenario: {args.scenario}")
    print(f"Host: {args.host}")
    print(f"Users: {args.users}")
    print(f"Spawn Rate: {args.spawn_rate}/s")
    print(f"Duration: {args.run_time}")
    print(f"Headless: {args.headless}")
    print(f"Tool: {args.tool}")
    print(f"Output: {output_dir.absolute()}")
    print("=" * 60)
    
    # Start server if requested
    server_process = None
    if args.start_server:
        print("\n[START] Starting Kazma server...")
        server_cmd = [sys.executable, "-m", "uvicorn", "kazma_ui.app:create_app", 
                      "--factory", "--host", "0.0.0.0", "--port", "9090"]
        server_process = subprocess.Popen(server_cmd)
        print(f"[START] Server started (PID: {server_process.pid})")
        time.sleep(5)  # Give server time to start
    
    try:
        # Check server
        if not args.no_server_check:
            print("\n[CHECK] Verifying server...")
            if not check_server(args.host):
                print("[ERROR] Server not accessible. Exiting.")
                return 1
        
        success = True
        
        if args.tool in ("locust", "both"):
            print("\n" + "=" * 60)
            print("RUNNING LOCUST TESTS")
            print("=" * 60)
            
            scenarios = []
            if args.scenario == "all":
                scenarios = ["swarm", "websocket", "mixed"]
            else:
                scenarios = [args.scenario]
            
            for scenario in scenarios:
                print(f"\n--- Running Locust scenario: {scenario} ---")
                ok = run_locust_scenario(
                    scenario, args.host, args.users, args.spawn_rate,
                    args.run_time, args.headless, output_dir
                )
                if not ok:
                    success = False
                    print(f"[FAIL] Scenario {scenario} failed")
                else:
                    print(f"[PASS] Scenario {scenario} completed")
        
        if args.tool in ("k6", "both"):
            print("\n" + "=" * 60)
            print("RUNNING K6 TESTS")
            print("=" * 60)
            
            scenarios = []
            if args.scenario in ("all", "k6"):
                scenarios = ["swarm", "sse", "hitl", "mixed"]
            else:
                # Map locust scenario to k6 scenario
                scenario_map = {
                    "swarm": "swarm",
                    "websocket": "mixed",  # k6 mixed includes websocket
                    "mixed": "mixed",
                }
                scenarios = [scenario_map.get(args.scenario, "swarm")]
            
            for scenario in scenarios:
                print(f"\n--- Running k6 scenario: {scenario} ---")
                ok = run_k6_scenario(
                    scenario, args.host, args.users, args.run_time, output_dir
                )
                if not ok:
                    success = False
                    print(f"[FAIL] Scenario {scenario} failed")
                else:
                    print(f"[PASS] Scenario {scenario} completed")
        
        print("\n" + "=" * 60)
        if success:
            print("ALL TESTS PASSED")
        else:
            print("SOME TESTS FAILED")
        print("=" * 60)
        print(f"Reports available in: {output_dir.absolute()}")
        
        return 0 if success else 1
        
    finally:
        if server_process:
            print("\n[STOP] Stopping Kazma server...")
            server_process.terminate()
            server_process.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())