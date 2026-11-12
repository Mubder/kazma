"""The boot drill's judging: what it reads, and the verdict it gives.

``scripts/engine_boot_drill.py`` runs on the host against the drill VM (a
Full install, a restart with nobody signed in, every container part ready
within five minutes; docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md, Stage
2). The VM part cannot run here; what it concludes can. These tests hold its
reading of ``kazma_guard.py --status`` to the guard's own lines (a guard
that words them differently fails here, not in a drill hours long), and its
verdict to the rule: ``pass`` only with every part up in time and nobody
signed in; the Docker Desktop control, a late part, a part that fell over
and a signed-in session each give another verdict.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


drill = _load("engine_boot_drill_under_test", ROOT / "scripts" / "engine_boot_drill.py")
guard = _load("kazma_guard_for_drill_test", ROOT / "scripts" / "service" / "kazma_guard.py")


def _status_text(*, engine_up: bool | None, broker: str, context: str = "kazma-engine") -> str:
    """``--status`` as the guard prints it, from its own line builders."""
    state = {
        "dependencies": {"engine_up": engine_up, "state": "ok" if engine_up else "engine_starting",
                         "engine": "kazma", "context": context, "containers": ["kazma-searxng"]},
        "document_broker": {"state": broker, "why": "wanted", "url": "tcp://127.0.0.1:20527",
                            "log": "C:/kazma-drill/.kazma/document_broker.log"},
    }
    lines = ["supervision : active", "server      : up, needs setup in Settings -> Providers (llm_provider)"]
    lines += guard._dependency_status_lines(state)
    lines += guard._broker_status_lines(state, alive=False)
    lines += ["disk        : ok (60.0% / 70.0 GB free on C:, data)"]
    return "\n".join(lines)


def _reading(up: float, *, engine_up=True, broker="running", sandbox: str | None = "ok",
             sandbox_engine="kazma", searxng=200, signed_in=False) -> dict[str, Any]:
    checks: dict[str, Any] = {"llm_provider": {"status": "degraded", "needs_setup": "provider"}}
    if sandbox is not None:
        checks["code_execution"] = (
            {"status": "ok", "engine": sandbox_engine, "backend": "docker"} if sandbox == "ok" else
            {"status": "failed", "engine": sandbox_engine, "waits_for": "docker",
             "error": "No available isolated execution backend: Docker Desktop is not running"})
    return {
        "uptime_s": up, "signed_in": signed_in, "users": "",
        "ready": {"http": 503, "body": {"status": "not_ready", "checks": checks}},
        "status": _status_text(engine_up=engine_up, broker=broker),
        "searxng": {"url": "http://127.0.0.1:8888", "http": searxng},
    }


# ── reading ───────────────────────────────────────────────────────────


def test_the_drill_reads_the_guard_s_own_status_lines():
    fields = drill.parse_status(_status_text(engine_up=True, broker="running"))
    assert fields["docker"].startswith("answers"), fields
    assert fields["doc broker"].startswith("running"), fields
    assert fields["server"].startswith("up, needs setup")
    assert "engine" not in fields, "indented detail lines are not top-level fields"


def test_each_part_is_read_up_when_it_is():
    parts = drill.part_readings(_reading(60.0), "kazma")
    assert {p: ok for p, (ok, _said) in parts.items()} == dict.fromkeys(drill.PARTS, True)


@pytest.mark.parametrize("change, part", [
    ({"engine_up": False}, "engine"),
    ({"engine_up": None}, "engine"),
    ({"broker": "docker_unavailable"}, "document_worker"),
    ({"broker": "starting"}, "document_worker"),
    ({"sandbox": "waits"}, "code_sandbox"),
    ({"sandbox": None}, "code_sandbox"),
    ({"sandbox_engine": "desktop"}, "code_sandbox"),
    ({"searxng": None}, "private_search"),
    ({"searxng": 502}, "private_search"),
])
def test_each_part_is_read_down_when_it_is(change, part):
    parts = drill.part_readings(_reading(60.0, **change), "kazma")
    assert parts[part][0] is False, (part, parts[part])
    assert all(ok for p, (ok, _s) in parts.items() if p != part), "only that part"


def test_kazma_not_answering_is_read_as_no_code_sandbox():
    reading = _reading(40.0)
    reading["ready"] = {"http": None, "body": None, "error": "ConnectionRefusedError"}
    ok, said = drill.part_readings(reading, "kazma")["code_sandbox"]
    assert not ok and "ConnectionRefusedError" in said


# ── the verdict ───────────────────────────────────────────────────────


def _boot(*ups_and_changes) -> list[dict[str, Any]]:
    return [_reading(up, **change) for up, change in ups_and_changes]


def test_everything_up_in_time_with_nobody_signed_in_passes():
    readings = _boot((45, {"engine_up": False, "broker": "idle", "sandbox": None, "searxng": None}),
                     (90, {"broker": "starting", "sandbox": "waits"}),
                     (135, {}), (300, {}), (420, {}))
    verdict = drill.judge(readings, "kazma")
    assert verdict.verdict == "pass", verdict
    assert verdict.parts["engine"]["ready_at_s"] == 90
    assert verdict.parts["document_worker"]["ready_at_s"] == 135
    assert verdict.kazma_answered_at == 45


def test_the_docker_desktop_control_is_not_up():
    """Docker Desktop with nobody signed in: the engine never answers."""
    down = {"engine_up": False, "broker": "docker_unavailable", "sandbox": "waits",
            "sandbox_engine": "desktop", "searxng": None}
    verdict = drill.judge(_boot(*[(t, down) for t in (60, 180, 300, 420)]), "desktop")
    assert verdict.verdict == "not_up"
    assert all(rec["ready_at_s"] is None for rec in verdict.parts.values())


def test_a_part_up_only_after_the_budget_is_not_up():
    late = {"broker": "starting"}
    verdict = drill.judge(_boot((60, late), (290, late), (330, {}), (420, {})), "kazma")
    assert verdict.verdict == "not_up"
    assert verdict.parts["document_worker"] == {**verdict.parts["document_worker"],
                                                 "ready_at_s": 330, "ready_in_budget": False}


def test_a_part_that_falls_over_before_the_end_is_not_up():
    verdict = drill.judge(_boot((60, {}), (200, {}), (420, {"searxng": None})), "kazma")
    assert verdict.verdict == "not_up"
    assert any("private_search" in note for note in verdict.notes)


def test_someone_signed_in_makes_the_drill_invalid():
    """Negative control of the premise: with a session, Docker Desktop could
    have started -- the drill says nothing then."""
    verdict = drill.judge(_boot((60, {}), (120, {"signed_in": True}), (420, {})), "kazma")
    assert verdict.verdict == "invalid" and verdict.signed_in == [120]


def test_no_reading_is_invalid():
    assert drill.judge([], "kazma").verdict == "invalid"


def test_the_report_says_whether_the_expected_verdict_held():
    verdict = drill.judge(_boot((60, {}), (420, {})), "kazma")
    text = drill.render_report(label="kazma", engine="kazma", control=False, verdict=verdict,
                               meta={"commit": "abc", "install": [{"round": 1, "exit": 0, "seconds": 9,
                                                                   "restart": True}]},
                               evidence_name="x.json")
    assert "**pass** (expected pass: held)" in text
    assert "Windows restarted for WSL" in text and "Kazma's container engine" in text
    control = drill.render_report(label="desktop", engine="desktop", control=True, verdict=verdict,
                                  meta={}, evidence_name="y.json")
    assert "**pass** (expected not_up: NOT HELD)" in control


# ── what the drill sends to the VM ────────────────────────────────────


def test_the_strict_profile_is_set_only_through_the_install_s_env(tmp_path):
    """The first drill wrote the profile from the VM account's home folder:
    no ``.env`` loaded, the setting landed in a store of its own and read
    back True, and the server's Postgres never had it -- readiness had no
    code sandbox check at all. Run anywhere but an install folder, the write
    refuses before it touches a store."""
    import os
    import subprocess

    # Its own stores: no database address, a data folder and home of the test's.
    env = {k: v for k, v in os.environ.items()
           if k not in ("KAZMA_ENV_FILE", "KAZMA_DATABASE_URL", "DATABASE_URL")}
    env.update(KAZMA_DATA_DIR=str(tmp_path / "data"), KAZMA_USER_HOME=str(tmp_path / "home"))
    got = subprocess.run([sys.executable, "-c", drill.STRICT_PROFILE], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert got.returncode != 0 and "not the install env" in got.stderr, got.stderr[-400:]
    (tmp_path / ".env").write_text("KAZMA_DRILL_PROBE=1\n", encoding="utf-8")
    got = subprocess.run([sys.executable, "-c", drill.STRICT_PROFILE], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert "container_required = True" in got.stdout, (got.stdout + got.stderr)[-400:]


def test_the_files_the_vm_runs_are_complete():
    compile(drill.PROBE, "probe.py", "exec")
    for template in (drill.PREPARE, drill.RUN_INSTALL):
        filled = drill._fill(template)
        assert drill.VM_ROOT in filled and "%(" not in filled
    assert "--mode full -y --docker $Docker" in drill._fill(drill.RUN_INSTALL)
    assert drill.RESTART_SAID in (ROOT / "kazma-cli" / "kazma_cli" / "install.py").read_text(encoding="utf-8"), (
        "the drill restarts Windows on the words setup says")
