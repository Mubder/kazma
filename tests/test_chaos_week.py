"""The weekly chaos drill (resilience plan J): the steps' logic, and its
refusal to touch the live install.

The faults themselves (a stopped database, a killed server, Docker stopped,
the network cut, a full disk, a reload under load) run on a drill machine;
here each step's judgement is driven by a fake target and a fake clock, and
each has its negative control.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_chaos_week", ROOT / "scripts" / "chaos_week.py")
chaos = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = chaos
spec.loader.exec_module(chaos)


class _Target:
    """A fake install: answers by a script of (from_second, status) per path."""

    def __init__(self, script, restarts=(0, 0)):
        self.t = 0.0
        self.script = script
        self.restarts = list(restarts)
        self.ran = []

    def now(self):
        return self.t

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
        return None


def _drill(target, tmp_path, state=None):
    d = chaos.Drill("http://127.0.0.1:9191", tmp_path, run=target.run, sleep=target.sleep,
                    now=target.now, get=target.get)
    states = iter(state or [{"restarts": 0}, {"restarts": 0}])
    d.guard_state = lambda: next(states, {"restarts": 0})
    return d


def test_the_live_install_is_refused(tmp_path):
    assert "live port" in chaos.refusal("http://127.0.0.1:9090", tmp_path)
    assert "chaos-ok" in chaos.refusal("http://127.0.0.1:9191", tmp_path)
    (tmp_path / ".kazma").mkdir()
    (tmp_path / ".kazma" / "chaos-ok").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    assert chaos.refusal("http://127.0.0.1:9191", tmp_path) == ""
    assert "live install" in chaos.refusal("http://127.0.0.1:9191", tmp_path, live_env=tmp_path / ".env")


def test_a_database_outage_ridden_out_passes(tmp_path):
    # Not ready at 10 s, ready again 100 s after the database is back (at ~230 s).
    target = _Target({"/health/ready": [(0, 200), (10, 503), (230, 200)]})
    result = chaos.step_postgres(_drill(target, tmp_path))
    assert result.ok, result
    assert ["docker", "stop", "kazma-db"] in target.ran and ["docker", "start", "kazma-db"] in target.ran


def test_negative_control_a_restart_during_the_outage_fails(tmp_path):
    target = _Target({"/health/ready": [(0, 200), (10, 503), (230, 200)]})
    result = chaos.step_postgres(_drill(target, tmp_path, state=[{"restarts": 0}, {"restarts": 1}]))
    assert not result.ok and "restarted: True" in result.observed


def test_a_server_that_never_comes_back_fails_the_kill_step(tmp_path):
    target = _Target({"/health/ready": [(0, 0)]})
    drill = _drill(target, tmp_path, state=[{"child_pid": 4242}])
    result = chaos.step_kill(drill)
    assert not result.ok and "None" in result.observed
    target = _Target({"/health/ready": [(0, 0), (90, 200)]})
    assert chaos.step_kill(_drill(target, tmp_path, state=[{"child_pid": 4242}])).ok


def test_dry_run_lists_every_step_and_an_unknown_one_is_refused(capsys):
    assert chaos.main(["--target", "http://127.0.0.1:9191", "--install", ".", "--dry-run"]) == 0
    listed = [line.split(":")[0] for line in capsys.readouterr().out.splitlines()]
    assert listed == list(chaos.STEPS)
    assert chaos.main(["--target", "http://127.0.0.1:9191", "--install", ".", "--only", "nope"]) == 2
