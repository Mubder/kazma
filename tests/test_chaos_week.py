"""The weekly chaos drill (resilience plan J): the steps' logic, and its
refusal to touch the live install.

The faults themselves (a stopped database, a killed server, Docker stopped,
the network cut, a full disk, a reload) run on a drill machine; here each
step's judgement is driven by a fake target, a fake clock and a guard log
written the way the guard writes it, and each has its negative control.

The drill judged "was Kazma restarted?" by a ``restarts`` field of the
guard's state file, which the guard never writes, and stopped a container
named ``kazma-db`` on an install whose database is the OS service ``kazma
setup postgres`` makes (2026-11-06): the restart checks always passed, and
the database step failed on the setup the plan recommends. The gate below
holds every field and event the drill reads to the guard's own source.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "service" / "kazma_guard.py"
#: The drill before the fix (the negative control).
OLD_DRILL_COMMIT = "ee0c37127"
T0 = 1_800_000_000.0


def _load(name: str, source: str | None = None):
    if source is None:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "chaos_week.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    module = type(sys)(name)
    sys.modules[name] = module
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)  # noqa: S102 -- the repo's own file
    return module


chaos = _load("_chaos_week")


def _old_drill():
    source = subprocess.run(["git", "show", f"{OLD_DRILL_COMMIT}:scripts/chaos_week.py"], capture_output=True,
                            text=True, encoding="utf-8", cwd=ROOT, check=True).stdout
    return _load("_chaos_week_old", source)


class _Target:
    """A fake install: answers by a script of (from_second, status) per path;
    commands answer exit 0 unless *fail* names them (argv[0..1])."""

    def __init__(self, script, fail=None, missing=()):
        self.t = 0.0
        self.script = script
        self.fail = fail or {}
        self.missing = set(missing)
        self.ran = []

    def now(self):
        return self.t

    def wall(self):
        return T0 + self.t

    def sleep(self, s):
        self.t += s

    def get(self, path):
        status = 0
        for since, code in self.script.get(path, []):
            if self.t >= since:
                status = code
        return status, {}

    def run(self, argv, **kw):
        self.ran.append(list(argv))
        if argv[0] in self.missing:
            raise FileNotFoundError(argv[0])
        key = " ".join(argv[:2])
        code = self.fail.get(key, 0)
        if callable(code):
            code = code(self)
        return subprocess.CompletedProcess(argv, code, "", "Access is denied." if code else "")


def _guard_log(install: Path, events: list[tuple[float, str, dict]]) -> None:
    """Write *events* (second after T0, event, fields) as the guard's JSONL log."""
    (install / ".kazma").mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"ts": datetime.fromtimestamp(T0 + at, UTC).isoformat(), "level": "info",
                         "event": event, **fields}) for at, event, fields in events]
    (install / ".kazma" / "guard.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _guard_state(install: Path, **fields) -> None:
    (install / ".kazma").mkdir(parents=True, exist_ok=True)
    (install / ".kazma" / "guard.state.json").write_text(json.dumps(fields), encoding="utf-8")


def _service_record(install: Path, os_name: str = "windows") -> None:
    data = install / "kazma-data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "setup_record.json").write_text(json.dumps(
        {"version": 1, "postgres": {"os": os_name, "service": "postgresql-x64-16", "port": 5432}}), encoding="utf-8")


def _drill(module, target, install):
    return module.Drill("http://127.0.0.1:9191", install, run=target.run, sleep=target.sleep,
                        now=target.now, get=target.get, **({"wall": target.wall} if module is chaos else {}))


OUTAGE = {"/health/ready": [(0, 200), (10, 503), (230, 200)]}


# -- refusals ----------------------------------------------------------------


def test_the_live_install_is_refused(tmp_path):
    assert "live port" in chaos.refusal("http://127.0.0.1:9090", tmp_path)
    assert "chaos-ok" in chaos.refusal("http://127.0.0.1:9191", tmp_path)
    (tmp_path / ".kazma").mkdir()
    (tmp_path / ".kazma" / "chaos-ok").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    assert chaos.refusal("http://127.0.0.1:9191", tmp_path) == ""
    assert "live install" in chaos.refusal("http://127.0.0.1:9191", tmp_path, live_env=tmp_path / ".env")


def test_dry_run_lists_every_step_and_an_unknown_one_is_refused(capsys):
    assert chaos.main(["--target", "http://127.0.0.1:9191", "--install", ".", "--dry-run"]) == 0
    listed = [line.split(":")[0] for line in capsys.readouterr().out.splitlines()]
    assert listed == list(chaos.STEPS)
    assert chaos.main(["--target", "http://127.0.0.1:9191", "--install", ".", "--only", "nope"]) == 2


def test_an_unready_target_is_refused_before_any_fault(tmp_path, monkeypatch, capsys):
    (tmp_path / ".kazma").mkdir()
    (tmp_path / ".kazma" / "chaos-ok").write_text("", encoding="utf-8")
    monkeypatch.setattr(chaos, "_is_admin", lambda: True)
    monkeypatch.setattr(chaos.Drill, "answer", lambda self, path: (0, {}))
    assert chaos.main(["--target", "http://127.0.0.1:9191", "--install", str(tmp_path)]) == 2
    assert "not ready" in capsys.readouterr().out


# -- the database --------------------------------------------------------------


def test_a_container_outage_ridden_out_passes(tmp_path):
    target = _Target(OUTAGE)
    result = chaos.step_postgres(_drill(chaos, target, tmp_path))
    assert result.ok, result
    assert ["docker", "stop", "kazma-db"] in target.ran and ["docker", "start", "kazma-db"] in target.ran


def test_a_crash_restart_during_the_outage_fails(tmp_path):
    _guard_log(tmp_path, [(60, "guard.restarting", {"reason": "unhealthy (3 probes)"})])
    result = chaos.step_postgres(_drill(chaos, _Target(OUTAGE), tmp_path))
    assert not result.ok and "crash restarts: 1" in result.observed


def test_negative_control_the_old_drill_missed_that_restart(tmp_path):
    # The real guard's state file has no "restarts": the old check saw none.
    old = _old_drill()
    _guard_log(tmp_path, [(60, "guard.restarting", {"reason": "unhealthy (3 probes)"})])
    _guard_state(tmp_path, child_pid=4242, heartbeat=T0)
    assert old.step_postgres(_drill(old, _Target(OUTAGE), tmp_path)).ok


def test_a_service_database_is_stopped_as_a_service_and_the_guard_must_start_it(tmp_path):
    _service_record(tmp_path)
    _guard_log(tmp_path, [(75, "dbsvc.start_tried", {"service": "postgresql-x64-16", "accepted": True})])
    target = _Target(OUTAGE)
    result = chaos.step_postgres(_drill(chaos, target, tmp_path))
    assert result.ok, result
    assert ["sc", "stop", "postgresql-x64-16"] in target.ran
    assert not any(argv[0] == "docker" for argv in target.ran)
    assert "the guard started the service: True" in result.observed


def test_a_service_the_guard_never_started_fails(tmp_path):
    _service_record(tmp_path)
    _guard_log(tmp_path, [(75, "dbsvc.start_tried", {"service": "postgresql-x64-16", "accepted": False})])
    result = chaos.step_postgres(_drill(chaos, _Target(OUTAGE), tmp_path))
    assert not result.ok and "the guard started the service: False" in result.observed


def test_negative_control_the_old_drill_stopped_a_container_the_service_install_lacks(tmp_path):
    _service_record(tmp_path)
    target = _Target(OUTAGE)
    _old_drill().step_postgres(_drill(_old_drill(), target, tmp_path))
    assert ["docker", "stop", "kazma-db"] in target.ran
    assert not any(argv[0] == "sc" for argv in target.ran)


def test_a_stop_that_was_refused_says_so_instead_of_timing_out(tmp_path):
    _service_record(tmp_path)
    target = _Target(OUTAGE, fail={"sc stop": 5})
    result = chaos.step_postgres(_drill(chaos, target, tmp_path))
    assert not result.ok and "could not stop the postgresql-x64-16 service" in result.observed
    assert ["sc", "start", "postgresql-x64-16"] not in target.ran


def test_a_launchd_service_is_skipped_not_failed(tmp_path):
    _service_record(tmp_path, os_name="macos")
    result = chaos.step_postgres(_drill(chaos, _Target(OUTAGE), tmp_path))
    assert result.skipped and result.ok


def test_old_events_before_the_step_do_not_count(tmp_path):
    _guard_log(tmp_path, [(-3600, "guard.restarting", {"reason": "yesterday"})])
    assert chaos.step_postgres(_drill(chaos, _Target(OUTAGE), tmp_path)).ok


# -- the server, Docker, the network, the disk, a reload -----------------------


def test_a_server_that_never_comes_back_fails_the_kill_step(tmp_path):
    _guard_state(tmp_path, child_pid=4242)
    result = chaos.step_kill(_drill(chaos, _Target({"/health/ready": [(0, 0)]}), tmp_path))
    assert not result.ok and "None" in result.observed
    assert chaos.step_kill(_drill(chaos, _Target({"/health/ready": [(0, 0), (90, 200)]}), tmp_path)).ok


def test_a_kill_that_was_refused_says_so(tmp_path):
    _guard_state(tmp_path, child_pid=4242)
    target = _Target({"/health/ready": [(0, 200)]}, fail={"taskkill /F": 1, "kill -9": 1})
    result = chaos.step_kill(_drill(chaos, target, tmp_path))
    assert not result.ok and "could not kill pid 4242" in result.observed


def test_docker_is_skipped_when_kazma_uses_no_container(tmp_path):
    _guard_state(tmp_path, dependencies={"state": "idle"})
    result = chaos.step_docker(_drill(chaos, _Target({"/health/ready": [(0, 200)]}), tmp_path))
    assert result.skipped and result.ok


def test_docker_is_skipped_when_there_is_no_docker(tmp_path):
    _guard_state(tmp_path, dependencies={"state": "ok"})
    target = _Target({"/health/ready": [(0, 200)]}, missing={"docker"})
    assert chaos.step_docker(_drill(chaos, target, tmp_path)).skipped


def test_docker_stopped_and_started_by_the_guard_passes(tmp_path):
    _guard_state(tmp_path, dependencies={"state": "ok"})
    _guard_log(tmp_path, [(130, "deps.docker_launched", {"attempt": 1})])
    stopped_at = {}

    def version(target):
        stopped = any(argv[:3] == ["docker", "desktop", "stop"] or argv[-1] == "docker" and argv[0] == "sudo"
                      for argv in target.ran)
        if stopped and "t" not in stopped_at:
            stopped_at["t"] = target.t
        return 1 if stopped and target.t < stopped_at["t"] + 200 else 0

    target = _Target({"/health/ready": [(0, 200)]}, fail={"docker version": version})
    result = chaos.step_docker(_drill(chaos, target, tmp_path))
    assert result.ok, result
    assert "the guard launched it: True" in result.observed


def test_docker_that_never_comes_back_fails(tmp_path):
    _guard_state(tmp_path, dependencies={"state": "ok"})

    def version(target):
        stopped = any(argv[:3] == ["docker", "desktop", "stop"] or argv[0] == "sudo" for argv in target.ran)
        return 1 if stopped else 0

    target = _Target({"/health/ready": [(0, 200)]}, fail={"docker version": version})
    assert not chaos.step_docker(_drill(chaos, target, tmp_path)).ok


def test_a_network_cut_that_could_not_be_made_fails(tmp_path):
    target = _Target({"/health/live": [(0, 200)], "/health/ready": [(0, 200)]},
                     fail={"netsh advfirewall": 1, "sudo iptables": 1})
    result = chaos.step_network(_drill(chaos, target, tmp_path))
    assert not result.ok and "could not cut the network" in result.observed


def test_the_network_step_passes_and_removes_its_rule(tmp_path):
    target = _Target({"/health/live": [(0, 200)], "/health/ready": [(0, 200)]})
    result = chaos.step_network(_drill(chaos, target, tmp_path))
    assert result.ok, result
    assert any("delete" in argv or "-D" in argv for argv in target.ran)


def test_the_disk_step_reads_the_guards_disk_record(tmp_path, monkeypatch):
    (tmp_path / "kazma-data").mkdir()
    monkeypatch.setattr("shutil.disk_usage", lambda p: type("U", (), {"free": 10 * 1024 ** 2})())
    _guard_state(tmp_path, disk={"volumes": {"C:": {"role": "data", "level": "critical"}}})
    result = chaos.step_disk(_drill(chaos, _Target({"/health/live": [(0, 200)]}), tmp_path))
    assert result.ok, result
    assert not (tmp_path / "kazma-data" / "chaos-fill.bin").exists()
    _guard_state(tmp_path, disk={"volumes": {"C:": {"role": "data", "level": "ok"}}})
    assert not chaos.step_disk(_drill(chaos, _Target({"/health/live": [(0, 200)]}), tmp_path)).ok


def test_a_reload_must_be_the_guards_reload_not_a_crash(tmp_path):
    _guard_log(tmp_path, [(5, "guard.operator_reload", {"reason": "operator reload", "graceful": True})])
    target = _Target({"/health/ready": [(0, 200)]})
    assert chaos.step_reload(_drill(chaos, target, tmp_path)).ok
    _guard_log(tmp_path, [(5, "guard.restarting", {"reason": "process exited (code 1)"})])
    result = chaos.step_reload(_drill(chaos, target, tmp_path))
    assert not result.ok and "the guard reloaded: False" in result.observed


def test_skipped_steps_are_named_not_counted():
    results = [chaos.StepResult("postgres", True, "", ""), chaos.StepResult("docker", True, "", "", skipped=True)]
    assert chaos.summary(results) == "Chaos drill: 1 of 1 steps behaved as expected. Skipped: docker."


# -- the gate: the drill reads only what the guard writes ----------------------


def _guard_writes(source: str) -> tuple[set[str], set[str]]:
    """(state fields, log events) the guard's own source writes."""
    fields = set(re.findall(r"_update_state\(\s*(\w+)\s*=", source))
    fields |= set(re.findall(r"_update_state\([^)]*?,\s*(\w+)\s*=", source))
    fields |= {"child_pid"} if "child_pid=pid" in source else set()
    events = set(re.findall(r'self\.log\(\s*"(?:info|warn|error|debug)"[^,]*,\s*"([\w.]+)"', source))
    events |= set(re.findall(r'\blog\(\s*"(?:info|warn|error|debug)"\s*,\s*"([\w.]+)"', source))
    events |= set(re.findall(r'"warn" if \w+ else "error",\s*"([\w.]+)"', source))
    return fields, events


def _drill_reads(module) -> tuple[set[str], set[str]]:
    return set(module.STATE_FIELDS), set(module.GUARD_EVENTS)


def test_the_drill_reads_only_fields_and_events_the_guard_writes():
    fields, events = _guard_writes(GUARD.read_text(encoding="utf-8"))
    reads_fields, reads_events = _drill_reads(chaos)
    assert reads_fields <= fields, reads_fields - fields
    assert reads_events <= events, reads_events - events
    source = (ROOT / "scripts" / "chaos_week.py").read_text(encoding="utf-8")
    state_reads = set(re.findall(r'guard_state\(\)\.get\("(\w+)"', source))
    assert state_reads <= set(chaos.STATE_FIELDS), state_reads - set(chaos.STATE_FIELDS)
    event_reads = set(re.findall(r'_events\(drill, since, "([\w.]+)"', source))
    assert event_reads <= set(chaos.GUARD_EVENTS), event_reads - set(chaos.GUARD_EVENTS)


def test_negative_control_the_old_drill_read_a_field_the_guard_never_writes():
    fields, _ = _guard_writes(GUARD.read_text(encoding="utf-8"))
    old = subprocess.run(["git", "show", f"{OLD_DRILL_COMMIT}:scripts/chaos_week.py"], capture_output=True,
                         text=True, encoding="utf-8", cwd=ROOT, check=True).stdout
    read = set(re.findall(r'guard_state\(\)\.get\("(\w+)"', old))
    assert "restarts" in read and "restarts" not in fields
