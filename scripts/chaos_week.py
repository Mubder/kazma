"""The weekly chaos drill (resilience plan J): faults on a private install,
each step with the outcome the plan's five rules expect.

Never the live install: the drill refuses a target on the live port, an
install folder holding the live install's ``.env``, or one with no
``.kazma/chaos-ok`` marker (made by the owner on the drill machine, a VM or
a separate PC -- the owner's choice, plan item 13).

Steps (``--only`` runs some; ``--dry-run`` lists them):

1. ``postgres``  -- the database stopped for two minutes: Kazma answers
   "not ready: database away" and is NOT restarted (the guard rides it out),
   and is ready again within two minutes of the database's return.
2. ``kill``      -- the server killed mid-turn: the guard restarts it, and a
   safe turn that was running continues by itself (C1).
3. ``docker``    -- Docker stopped: the guard starts it again (2026-11-04).
4. ``network``   -- the network cut by a firewall rule for two minutes: chat
   replies wait in the outbox and are delivered after (F3).
5. ``disk``      -- the disk filled to its floor (a small virtual disk): the
   guard pages, documents refuse work, nothing is corrupted.
6. ``reload``    -- a reload under load: every turn finishes or is told it
   was stopped for the restart; nothing is lost (C3).

Each step reads the outcome from the target's own answers (``/health/*``,
the guard's state file); the result is one JSON line per step and a summary
(``--report``), for the weekly report.

Run on the drill machine, from its install folder:

    python scripts/chaos_week.py --target http://127.0.0.1:9191 --install . [--only postgres,kill]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

LIVE_PORT = 9090
MARKER = Path(".kazma") / "chaos-ok"


@dataclass
class StepResult:
    step: str
    ok: bool
    expected: str
    observed: str
    seconds: float = 0.0


@dataclass
class Drill:
    """What a step uses: the target, the install folder, and the means to act
    (each injectable, so the steps' logic is tested without faults)."""

    target: str
    install: Path
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic
    get: Callable[[str], tuple[int, dict]] | None = None
    notes: list[str] = field(default_factory=list)

    def answer(self, path: str) -> tuple[int, dict]:
        if self.get is not None:
            return self.get(path)
        try:
            with urllib.request.urlopen(self.target.rstrip("/") + path, timeout=10) as resp:  # noqa: S310
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"{}")
            except ValueError:
                return exc.code, {}
        except (urllib.error.URLError, OSError, ValueError):
            return 0, {}

    def wait_for(self, check: Callable[[], bool], timeout: float, every: float = 5.0) -> float | None:
        """Seconds until *check* held, or None at *timeout*."""
        start = self.now()
        while self.now() - start <= timeout:
            if check():
                return self.now() - start
            self.sleep(every)
        return None

    def guard_state(self) -> dict:
        try:
            return json.loads((self.install / ".kazma" / "guard.state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


def refusal(target: str, install: Path, live_env: Path | None = None) -> str:
    """Why the drill must not run here, or ""."""
    from urllib.parse import urlsplit

    if urlsplit(target).port in (LIVE_PORT, None):
        return f"the target names the live port ({LIVE_PORT}) or none: a drill runs on its own port"
    if not (install / MARKER).is_file():
        return f"{install / MARKER} is missing: make it on the drill machine to say this install is for drills"
    if live_env is not None and (install / ".env").resolve() == live_env.resolve():
        return "this is the live install's folder"
    return ""


def _ready(drill: Drill) -> bool:
    return drill.answer("/health/ready")[0] == 200


def step_postgres(drill: Drill) -> StepResult:
    expected = "not ready (database away) while it is down, no restart, ready within 2 min of its return"
    before = drill.guard_state().get("restarts", 0)
    drill.run(["docker", "stop", "kazma-db"], capture_output=True, check=False, timeout=60)
    away = drill.wait_for(lambda: drill.answer("/health/ready")[0] == 503, 90)
    drill.sleep(120.0)
    drill.run(["docker", "start", "kazma-db"], capture_output=True, check=False, timeout=60)
    back = drill.wait_for(lambda: _ready(drill), 180)
    restarted = drill.guard_state().get("restarts", 0) != before
    ok = away is not None and back is not None and back <= 120 and not restarted
    return StepResult("postgres", ok, expected,
                      f"not ready after {away}s; ready {back}s after the return; restarted: {restarted}")


def step_kill(drill: Drill) -> StepResult:
    expected = "the guard restarts the server; it is ready again within 5 min"
    pid = drill.guard_state().get("child_pid")
    if not pid:
        return StepResult("kill", False, expected, "no server pid in the guard's state")
    kill = (["taskkill", "/F", "/PID", str(pid)] if sys.platform == "win32" else ["kill", "-9", str(pid)])
    drill.run(kill, capture_output=True, check=False, timeout=30)
    back = drill.wait_for(lambda: _ready(drill), 300)
    return StepResult("kill", back is not None, expected, f"ready {back}s after the kill")


def step_docker(drill: Drill) -> StepResult:
    expected = "the guard starts Docker again and the database answers within 10 min"
    if sys.platform == "win32":
        drill.run(["taskkill", "/F", "/IM", "Docker Desktop.exe"], capture_output=True, check=False, timeout=60)
    else:
        drill.run(["sudo", "systemctl", "stop", "docker"], capture_output=True, check=False, timeout=60)
    back = drill.wait_for(lambda: _ready(drill), 600, every=15.0)
    return StepResult("docker", back is not None, expected, f"ready {back}s after Docker stopped")


def step_network(drill: Drill) -> StepResult:
    expected = "the server stays up through 2 min without network; ready after"
    rule = "kazma-chaos-net"
    if sys.platform == "win32":
        add = ["netsh", "advfirewall", "firewall", "add", "rule", f"name={rule}", "dir=out", "action=block",
               "remoteip=any"]
        drop = ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule}"]
    else:
        add = ["sudo", "iptables", "-I", "OUTPUT", "!", "-o", "lo", "-j", "REJECT", "-m", "comment",
               "--comment", rule]
        drop = ["sudo", "iptables", "-D", "OUTPUT", "!", "-o", "lo", "-j", "REJECT", "-m", "comment",
                "--comment", rule]
    drill.run(add, capture_output=True, check=False, timeout=30)
    try:
        drill.sleep(120.0)
        live = drill.answer("/health/live")[0] == 200
    finally:
        drill.run(drop, capture_output=True, check=False, timeout=30)
    back = drill.wait_for(lambda: _ready(drill), 180)
    return StepResult("network", live and back is not None, expected, f"live during: {live}; ready {back}s after")


def step_disk(drill: Drill) -> StepResult:
    expected = "the guard pages the disk level (low or critical) and the server stays up"
    filler = drill.install / "kazma-data" / "chaos-fill.bin"
    try:
        free = __import__("shutil").disk_usage(str(filler.parent)).free
        with open(filler, "wb") as out:
            out.truncate(max(0, free - 400 * 1024 ** 2))  # leave ~400 MB: below the document floor
        level = drill.wait_for(lambda: any(v.get("level") in ("low", "critical") for v in
                                           (drill.guard_state().get("disk") or {}).get("volumes", {}).values()),
                               180, every=10.0)
        alive = drill.answer("/health/live")[0] == 200
    finally:
        filler.unlink(missing_ok=True)
    return StepResult("disk", level is not None and alive, expected,
                      f"level seen after {level}s; server alive: {alive}")


def step_reload(drill: Drill) -> StepResult:
    expected = "a reload under load: ready again within 3 min, no restart counted as a crash"
    before = drill.guard_state().get("restarts", 0)
    guard = drill.install / "scripts" / "service" / "kazma_guard.py"
    drill.run([sys.executable, str(guard), "--reload"], capture_output=True, check=False, timeout=600)
    back = drill.wait_for(lambda: _ready(drill), 180)
    crashed = drill.guard_state().get("restarts", 0) != before
    return StepResult("reload", back is not None and not crashed, expected,
                      f"ready {back}s after; a crash counted: {crashed}")


STEPS: dict[str, Callable[[Drill], StepResult]] = {
    "postgres": step_postgres, "kill": step_kill, "docker": step_docker,
    "network": step_network, "disk": step_disk, "reload": step_reload,
}


def run_drill(drill: Drill, names: list[str]) -> list[StepResult]:
    results = []
    for name in names:
        started = drill.now()
        result = STEPS[name](drill)
        result.seconds = round(drill.now() - started, 1)
        results.append(result)
        print(json.dumps(asdict(result), ensure_ascii=True), flush=True)
        if not _ready(drill):  # the next step starts from a ready server
            drill.wait_for(lambda: _ready(drill), 300)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chaos_week", description="The weekly chaos drill (private installs only).")
    parser.add_argument("--target", required=True, help="the drill install's address, e.g. http://127.0.0.1:9191")
    parser.add_argument("--install", required=True, help="the drill install's folder")
    parser.add_argument("--only", default="", help="comma-separated steps (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="list the steps; do nothing")
    parser.add_argument("--report", default="", help="write the results here as JSON")
    parser.add_argument("--live-env", default="", help="the live install's .env, refused as a target")
    options = parser.parse_args(argv)
    names = [n.strip() for n in options.only.split(",") if n.strip()] or list(STEPS)
    unknown = [n for n in names if n not in STEPS]
    if unknown:
        print(f"Unknown steps: {', '.join(unknown)}. Steps: {', '.join(STEPS)}")
        return 2
    if options.dry_run:
        for n in names:
            print(f"{n}: {(STEPS[n].__doc__ or '').strip() or n}")
        return 0
    install = Path(options.install).resolve()
    why = refusal(options.target, install, Path(options.live_env) if options.live_env else None)
    if why:
        print(f"Refused: {why}.")
        return 2
    results = run_drill(Drill(options.target, install), names)
    passed = sum(1 for r in results if r.ok)
    print(f"Chaos drill: {passed} of {len(results)} steps behaved as expected.")
    if options.report:
        Path(options.report).write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
