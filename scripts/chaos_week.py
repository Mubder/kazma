"""The weekly chaos drill (resilience plan J): faults on a private install,
each step with the outcome the plan's five rules expect.

Never the live install: the drill refuses a target on the live port, an
install folder holding the live install's ``.env``, or one with no
``.kazma/chaos-ok`` marker (made by the owner on the drill machine, a VM or
a separate PC -- the owner's choice, plan item 13).

Steps (``--only`` runs some; ``--dry-run`` lists them):

1. ``postgres``  -- the database stopped: Kazma answers "not ready" and is
   NOT restarted (the guard rides it out, §44), and is ready again within
   two minutes of the database's return. The database is what the install
   runs: the OS service ``kazma setup postgres`` recorded (then the guard
   must start it itself, A1), else the container ``--db-container`` names.
2. ``kill``      -- the server killed: the guard starts it again and it is
   ready within five minutes.
3. ``docker``    -- Docker Desktop stopped: the guard starts it again and
   Docker answers within ten minutes. Skipped when Kazma uses no container
   on this machine (the guard then has nothing to start Docker for) or
   there is no Docker.
4. ``network``   -- the network cut by a firewall rule for two minutes: the
   server stays up, and is ready after.
5. ``disk``      -- the data folder's disk filled to ~400 MB free: the guard
   records the disk level (low or critical) and the server stays up.
6. ``reload``    -- an operator reload: the guard reloads (not a crash
   restart) and the server is ready again within three minutes. A turn and a
   document job in flight across a real reload are CI's drill
   (``tests/test_drill_graceful_reload.py``).

Each step reads the outcome from the target's own answers (``/health/*``)
and the guard's own record of what it did (its state file and its log,
``<install>/.kazma/guard.state.json`` and ``guard.log``) -- only fields and
events the guard writes (``tests/test_chaos_week.py`` holds the drill to
them). The result is one JSON line per step and a summary (``--report``).

Run on the drill machine, from its install folder, in an elevated shell on
Windows (stopping a service, the guard's server and a firewall rule need
it):

    .venv\\Scripts\\python.exe scripts\\chaos_week.py --target http://127.0.0.1:9191 --install .
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
from datetime import datetime
from pathlib import Path

LIVE_PORT = 9090
MARKER = Path(".kazma") / "chaos-ok"
#: Steps that stop a service, kill an elevated process or add a firewall rule.
ADMIN_STEPS = ("postgres", "kill", "network")
#: Guard log events: a restart the guard counted as a crash.
CRASH_EVENTS = ("guard.restarting", "guard.crash_loop")
#: Fields of the guard's state file the drill reads.
STATE_FIELDS = ("child_pid", "dependencies", "disk")
#: Guard log events the drill reads.
GUARD_EVENTS = CRASH_EVENTS + ("guard.operator_reload", "dbsvc.start_tried", "deps.docker_launched")


@dataclass
class StepResult:
    step: str
    ok: bool
    expected: str
    observed: str
    seconds: float = 0.0
    skipped: bool = False


@dataclass
class Outcome:
    """What a command did."""

    ok: bool
    detail: str = ""
    missing: bool = False


@dataclass
class Drill:
    """What a step uses: the target, the install folder, and the means to act
    (each injectable, so the steps' logic is tested without faults)."""

    target: str
    install: Path
    data_dir: Path | None = None
    db_container: str = "kazma-db"
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = time.time
    get: Callable[[str], tuple[int, dict]] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def data(self) -> Path:
        return self.data_dir or self.install / "kazma-data"

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

    def act(self, argv: list[str], timeout: float = 60) -> Outcome:
        """Run *argv*; never raises."""
        try:
            got = self.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
        except FileNotFoundError:
            return Outcome(False, f"{argv[0]} not found", missing=True)
        except (OSError, subprocess.SubprocessError) as exc:
            return Outcome(False, f"{type(exc).__name__}: {exc}"[:300])
        said = " ".join(str(part).strip() for part in (got.stdout, got.stderr) if part).strip()
        return Outcome(got.returncode == 0, f"exit {got.returncode}: {said}"[:300])

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

    def guard_events(self, since: float) -> list[dict]:
        """The guard's log events written at or after *since* (epoch seconds),
        the rotated file included."""
        found: list[dict] = []
        for name in ("guard.log.1", "guard.log"):
            try:
                lines = (self.install / ".kazma" / name).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    rec = json.loads(line)
                    when = datetime.fromisoformat(str(rec.get("ts"))).timestamp()
                except (ValueError, TypeError, AttributeError):
                    continue
                if when >= since:
                    found.append(rec)
        return found

    def uses_postgres(self) -> bool:
        """Whether the install keeps its data in Postgres: ``KAZMA_DATABASE_URL``
        set in its ``.env`` or this process's environment (the name only; its
        value is never read out)."""
        import os

        if str(os.environ.get("KAZMA_DATABASE_URL") or "").strip():
            return True
        try:
            text = (self.install / ".env").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        for line in text.splitlines():
            name, _, value = line.strip().partition("=")
            if name.strip() == "KAZMA_DATABASE_URL" and value.strip().strip('"').strip("'"):
                return True
        return False

    def setup_record(self) -> dict:
        """The database service ``kazma setup postgres`` recorded, or {}."""
        try:
            data = json.loads((self.data / "setup_record.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        rec = data.get("postgres") if isinstance(data, dict) else None
        return rec if isinstance(rec, dict) else {}


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


def _events(drill: Drill, since: float, *names: str) -> list[dict]:
    return [e for e in drill.guard_events(since) if e.get("event") in names]


def _database_control(drill: Drill) -> tuple[list[str], list[str], str, bool] | str:
    """(stop, start, what, a service) for the database the install runs, or why not."""
    rec = drill.setup_record()
    service, os_name = str(rec.get("service") or ""), str(rec.get("os") or "")
    if service:
        if os_name == "windows":
            return ["sc", "stop", service], ["sc", "start", service], f"the {service} service", True
        if os_name == "macos":
            return f"the {service} service is a launchd job: launchd starts it again at once"
        return (["sudo", "systemctl", "stop", service], ["sudo", "systemctl", "start", service],
                f"the {service} service", True)
    if not drill.uses_postgres():
        # An Express install keeps its data in SQLite: there is no database
        # server to stop, and stopping a container it never had is no test
        # (the drill VM, 2026-11-09: "could not stop the kazma-db container").
        return "this install keeps its data in SQLite (no KAZMA_DATABASE_URL): no database server to stop"
    name = drill.db_container
    return ["docker", "stop", name], ["docker", "start", name], f"the {name} container", False


def step_postgres(drill: Drill) -> StepResult:
    expected = ("not ready while the database is down, no restart, ready within 2 min of its "
                "return; a service is started again by the guard")
    control = _database_control(drill)
    if isinstance(control, str):
        return StepResult("postgres", True, expected, f"skipped: {control}", skipped=True)
    stop, start, what, service = control
    since = drill.wall()
    stopped = drill.act(stop)
    if not stopped.ok:
        return StepResult("postgres", False, expected, f"could not stop {what}: {stopped.detail}")
    away = drill.wait_for(lambda: drill.answer("/health/ready")[0] == 503, 90)
    drill.sleep(120.0)
    drill.act(start)  # refused when the guard already started it: that is the point
    back = drill.wait_for(lambda: _ready(drill), 180)
    restarts = len(_events(drill, since, *CRASH_EVENTS))
    started = any(e.get("accepted") for e in _events(drill, since, "dbsvc.start_tried"))
    ok = away is not None and back is not None and back <= 120 and not restarts
    if service:
        ok = ok and started
    observed = (f"{what}: not ready after {away}s; ready {back}s after the return; "
                f"crash restarts: {restarts}")
    if service:
        observed += f"; the guard started the service: {started}"
    return StepResult("postgres", ok, expected, observed)


def step_kill(drill: Drill) -> StepResult:
    expected = "the guard starts the server again; it is ready within 5 min"
    pid = drill.guard_state().get("child_pid")
    if not pid:
        return StepResult("kill", False, expected, "no server pid in the guard's state")
    kill = (["taskkill", "/F", "/PID", str(pid)] if sys.platform == "win32" else ["kill", "-9", str(pid)])
    killed = drill.act(kill, 30)
    if not killed.ok:
        return StepResult("kill", False, expected, f"could not kill pid {pid}: {killed.detail}")
    back = drill.wait_for(lambda: _ready(drill), 300)
    return StepResult("kill", back is not None, expected, f"pid {pid} killed; ready {back}s after")


def _docker_answers(drill: Drill) -> Outcome:
    return drill.act(["docker", "version", "--format", "{{.Server.Version}}"], 30)


def step_docker(drill: Drill) -> StepResult:
    expected = "the guard starts Docker again; Docker answers and the server is ready within 10 min"
    deps = drill.guard_state().get("dependencies") or {}
    if deps.get("state") == "idle":
        return StepResult("docker", True, expected,
                          "skipped: Kazma uses no container on this machine, so the guard has "
                          "nothing to start Docker for", skipped=True)
    before = _docker_answers(drill)
    if before.missing:
        return StepResult("docker", True, expected, "skipped: no Docker on this machine", skipped=True)
    if not before.ok:
        return StepResult("docker", False, expected, f"Docker did not answer before the step: {before.detail}")
    since = drill.wall()
    stop = (["docker", "desktop", "stop"] if sys.platform == "win32"
            else ["sudo", "systemctl", "stop", "docker.socket", "docker"])
    stopped = drill.act(stop, 120)
    if not stopped.ok:
        return StepResult("docker", False, expected, f"could not stop Docker: {stopped.detail}")
    down = drill.wait_for(lambda: not _docker_answers(drill).ok, 120)
    back = drill.wait_for(lambda: _docker_answers(drill).ok and _ready(drill), 600, every=15.0)
    launched = bool(_events(drill, since, "deps.docker_launched"))
    return StepResult("docker", down is not None and back is not None, expected,
                      f"Docker down after {down}s; answering and ready {back}s after; "
                      f"the guard launched it: {launched}")


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
    cut = drill.act(add, 30)
    if not cut.ok:
        return StepResult("network", False, expected, f"could not cut the network: {cut.detail}")
    try:
        drill.sleep(120.0)
        live = drill.answer("/health/live")[0] == 200
    finally:
        restored = drill.act(drop, 30)
    if not restored.ok:
        drill.notes.append(f"the firewall rule {rule} may still be there: {restored.detail}")
    back = drill.wait_for(lambda: _ready(drill), 180)
    return StepResult("network", live and back is not None and restored.ok, expected,
                      f"live during: {live}; rule removed: {restored.ok}; ready {back}s after")


def _disk_level_seen(drill: Drill) -> bool:
    volumes = (drill.guard_state().get("disk") or {}).get("volumes") or {}
    return any(isinstance(v, dict) and v.get("level") in ("low", "critical") for v in volumes.values())


def step_disk(drill: Drill) -> StepResult:
    expected = "the guard records the disk level (low or critical) and the server stays up"
    if not drill.data.is_dir():
        return StepResult("disk", False, expected, f"no data folder at {drill.data} (pass --data-dir)")
    filler = drill.data / "chaos-fill.bin"
    try:
        free = __import__("shutil").disk_usage(str(drill.data)).free
        with open(filler, "wb") as out:
            out.truncate(max(0, free - 400 * 1024 ** 2))  # leave ~400 MB: below the document floor
        level = drill.wait_for(lambda: _disk_level_seen(drill), 180, every=10.0)
        alive = drill.answer("/health/live")[0] == 200
    finally:
        filler.unlink(missing_ok=True)
    return StepResult("disk", level is not None and alive, expected,
                      f"level seen after {level}s; server alive: {alive}")


def step_reload(drill: Drill) -> StepResult:
    expected = "an operator reload, not a crash restart: ready again within 3 min"
    since = drill.wall()
    guard = drill.install / "scripts" / "service" / "kazma_guard.py"
    asked = drill.act([sys.executable, str(guard), "--reload"], 600)
    if not asked.ok:
        return StepResult("reload", False, expected, f"the reload was not taken: {asked.detail}")
    back = drill.wait_for(lambda: _ready(drill), 180)
    reloaded = bool(_events(drill, since, "guard.operator_reload"))
    crashes = len(_events(drill, since, *CRASH_EVENTS))
    return StepResult("reload", back is not None and reloaded and not crashes, expected,
                      f"ready {back}s after; the guard reloaded: {reloaded}; crash restarts: {crashes}")


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


def summary(results: list[StepResult]) -> str:
    ran = [r for r in results if not r.skipped]
    passed = sum(1 for r in ran if r.ok)
    line = f"Chaos drill: {passed} of {len(ran)} steps behaved as expected."
    skipped = [r.step for r in results if r.skipped]
    if skipped:
        line += f" Skipped: {', '.join(skipped)}."
    return line


def _is_admin() -> bool:
    if sys.platform != "win32":
        return True                       # the steps ask sudo themselves
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chaos_week", description="The weekly chaos drill (private installs only).")
    parser.add_argument("--target", required=True, help="the drill install's address, e.g. http://127.0.0.1:9191")
    parser.add_argument("--install", required=True, help="the drill install's folder")
    parser.add_argument("--data-dir", default="", help="the install's data folder (default: <install>/kazma-data)")
    parser.add_argument("--db-container", default="kazma-db",
                        help="the database container, when no database service was set up")
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
    needs_admin = [n for n in names if n in ADMIN_STEPS]
    if needs_admin and not _is_admin():
        print(f"Refused: run from an elevated shell ({', '.join(needs_admin)} need it), "
              f"or leave them out with --only.")
        return 2
    drill = Drill(options.target, install, data_dir=Path(options.data_dir) if options.data_dir else None,
                  db_container=options.db_container)
    if not _ready(drill):
        print(f"Refused: {options.target} is not ready; start the drill install first.")
        return 2
    results = run_drill(drill, names)
    print(summary(results))
    for note in drill.notes:
        print(f"Note: {note}")
    if options.report:
        Path(options.report).write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
