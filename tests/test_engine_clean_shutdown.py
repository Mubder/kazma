"""Kazma's container engine stops cleanly when Windows shuts down (2026-11-13).

Measured on the drill VM: Windows ends WSL's VM about 2 s into a restart with
no signal to anything in it (WslService and vmcompute take no shutdown
notice), and a container's last writes came back as NUL bytes -- the Neo4j
config line of the engine-move drill, 760 bytes of a writer here. Task
Scheduler ends a running task and everything it started at the very start of
a shutdown (event 111), and starts none (ERROR_SHUTDOWN_IN_PROGRESS); WSL
refuses LocalSystem.

So setup installs (``container_engine_setup``, the engine:shutdown step):
- ``kazma-engine-stop`` and ``kazma-engine-hold`` in the distro
  (``engine-stop.sh``);
- ``keep-engine.ps1``, which starts the holder through WMI -- outside Task
  Scheduler's reach -- with its stdin and stdout on the service's pipes;
- the ``KazmaEngineShutdown`` service (``engine-shutdown.cs``), which takes
  the preshutdown notice, writes "stop" to the holder, reads its answer,
  writes "poweroff", and holds Windows meanwhile.

These tests hold each piece, behaviourally where a real program can run here
(the distro's scripts under bash with stand-in systemctl, findmnt and sync;
the service under the .NET Framework's compiler on Windows), each with its
negative control: the first versions of the scripts, which the drill caught.
``scripts/engine_shutdown_drill.py`` is the end-to-end proof on the VM.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kazma_core import container_engine_setup as ces

from tests.test_container_engine_setup import make_machine

ASSETS = Path(ces.__file__).parent / "container_engine_assets"


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """The fake Windows machine of the engine's setup tests, engine complete."""
    return make_machine(monkeypatch, tmp_path)


def _heredoc(script: str, marker: str) -> str:
    """The body of ``cat > ... <<'MARKER'`` in *script*."""
    match = re.search(rf"<<'{marker}'\n(.*?)\n{marker}\n", script, re.S)
    assert match, f"no {marker} heredoc"
    return match.group(1) + "\n"


def _bash() -> str | None:
    found = shutil.which("bash")
    if not found:
        return None
    # Windows' own bash.exe is WSL's launcher, not a shell for these tests.
    if os.name == "nt" and Path(found).parent.name.lower() == "system32":
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return found


needs_bash = pytest.mark.skipif(_bash() is None, reason="bash is not on this machine")


# ── what setup sees, and does ───────────────────────────────────────────


def test_an_engine_without_the_clean_stop_is_missing_it(machine):
    """The negative control of the whole feature: before it, a complete
    engine with no shutdown service was found whole."""
    machine.service = None
    found = ces.check(machine)
    assert found.ok is False and found.missing == ["shutdown"]
    assert "KazmaEngineShutdown" in found.detail
    assert [s.action for s in ces.install_steps(found)] == ["engine:shutdown", "engine:record"]


def test_a_stopped_service_is_missing_it_too(machine):
    machine.service = "STOPPED"
    assert ces.check(machine).missing == ["shutdown"]


def test_an_older_keep_engine_is_replaced(machine):
    """An install whose keep-engine.ps1 predates the piped holder has no way to
    stop the engine at a shutdown; `kazma setup engine` must notice it."""
    (ces._engine_dir() / "keep-engine.ps1").write_text("# keep-engine.ps1 of an older setup\n", encoding="utf-8")
    assert ces.check(machine).missing == ["shutdown"]


def test_older_helpers_in_the_distro_are_replaced(machine):
    ces._stop_helpers_stamp().write_text("0" * 64 + "\n", encoding="ascii")
    assert ces.check(machine).missing == ["shutdown"]
    ces._stop_helpers_stamp().unlink()
    assert ces.check(machine).missing == ["shutdown"], "no stamp: helpers of an unknown build"


def test_a_crlf_copy_of_this_keep_engine_is_current(machine):
    keep = ces._engine_dir() / "keep-engine.ps1"
    keep.write_bytes(ces._asset("keep-engine.ps1").replace("\n", "\r\n").encode("utf-8"))
    assert ces.check(machine).ok is True


@pytest.fixture
def csc(monkeypatch, tmp_path):
    """A stand-in for the .NET Framework's compiler that the machine runs."""
    fake = tmp_path / "csc.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(ces, "_csc", lambda: fake)
    return fake


def _shutdown_step(machine, monkeypatch) -> tuple[bool, str]:
    monkeypatch.setattr(ces.time, "sleep", lambda s: None)
    return ces.run_action("engine:shutdown", run=machine)


def _service_installs(machine):
    """Answer the service script: the service is then running."""
    original = machine._answer

    def answer(argv):
        if Path(argv[0]).name.lower() == "powershell" and "New-Service" in argv[-1]:
            machine.service = "RUNNING"
            return 0, ""
        if Path(argv[0]).name.lower() == "wsl.exe" and argv[-2:] == ["bash", "-s"]:
            return 0, "STOP_READY\n"
        return original(argv)

    machine._answer = answer


def test_the_step_puts_in_every_piece(machine, monkeypatch, csc):
    machine.service = None
    (ces._engine_dir() / "keep-engine.ps1").unlink()
    ces._stop_helpers_stamp().unlink()
    _service_installs(machine)
    ok, detail = _shutdown_step(machine, monkeypatch)
    assert ok, detail
    # The helpers went into the distro as root, from engine-stop.sh on stdin.
    at = next(i for i, a in enumerate(machine.ran) if a[-2:] == ["bash", "-s"])
    assert machine.ran[at][:6] == ["wsl.exe", "-d", ces.DISTRO, "-u", "root", "--"]
    assert machine.stdin[at] == ces._asset("engine-stop.sh").encode("utf-8")
    assert ces._stop_helpers_current() and ces._keep_engine_current()
    # The service was built from this source, with the compiler, into Program Files.
    build = next(a for a in machine.ran if a[0] == str(csc))
    folder = ces._shutdown_service_dir()
    assert f"-out:{folder / 'kazma-engine-shutdown.exe'}" in build
    assert (folder / "kazma-engine-shutdown.cs").read_text(encoding="utf-8") == ces._asset("engine-shutdown.cs")
    script = next(a[-1] for a in machine.ran if a[0] == "powershell" and "New-Service" in a[-1])
    assert "-StartupType Automatic" in script and f"--budget {ces.SHUTDOWN_BUDGET_S}" in script
    assert "Win32_Service" in script, "an existing service is pointed at this build, not left on an old one"
    assert machine.service == "RUNNING"
    assert not any(a[:2] == ["schtasks", "/End"] for a in machine.ran), \
        "a fresh keep-engine.ps1 has no older copy running to replace"


def test_an_older_keep_engine_is_restarted_with_this_one(machine, monkeypatch, csc):
    (ces._engine_dir() / "keep-engine.ps1").write_text("# older\n", encoding="utf-8")
    _service_installs(machine)
    ok, detail = _shutdown_step(machine, monkeypatch)
    assert ok, detail
    ends = [i for i, a in enumerate(machine.ran) if a[:2] == ["schtasks", "/End"]]
    runs = [i for i, a in enumerate(machine.ran) if a[:2] == ["schtasks", "/Run"]]
    assert ends and runs and ends[0] < runs[0], "the running copy holds the old loop"


def test_the_running_service_is_stopped_before_its_program_is_replaced(machine, monkeypatch, csc):
    _service_installs(machine)
    ok, _ = _shutdown_step(machine, monkeypatch)
    assert ok
    stop = next(i for i, a in enumerate(machine.ran) if a[:3] == ["sc.exe", "stop", ces.SHUTDOWN_SERVICE])
    build = next(i for i, a in enumerate(machine.ran) if a[0] == str(csc))
    assert stop < build


def test_control_helpers_that_do_not_say_ready_fail_the_step(machine, monkeypatch, csc):
    machine.answers[("wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "bash", "-s")] = (0, "something else")
    ok, detail = _shutdown_step(machine, monkeypatch)
    assert not ok and "shutdown stop" in detail
    assert not any(a[0] == str(csc) for a in machine.ran), "nothing else is done"


def test_a_service_that_does_not_run_fails_the_step(machine, monkeypatch, csc):
    machine.service = None  # the service script answers, but nothing runs
    machine.answers[("wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "bash", "-s")] = (0, "STOP_READY\n")
    ok, detail = _shutdown_step(machine, monkeypatch)
    assert not ok and ces.SHUTDOWN_SERVICE in detail


def test_the_docker_step_installs_the_helpers_too(machine, monkeypatch):
    """A distro made again (wsl --unregister by hand) gets them with Docker."""
    machine.answers[("wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "bash", "-s", "--")] = \
        (0, "ENGINE_READY Docker version 29.9.3\n")
    machine.answers[("wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "bash", "-s")] = (0, "STOP_READY\n")
    ces._stop_helpers_stamp().unlink()
    monkeypatch.setattr(ces, "_free_range", lambda run: ces._RANGES[0])
    ok, detail = ces.run_action("engine:docker", run=machine)
    assert ok, detail
    assert ces._stop_helpers_current()


def test_removing_takes_the_service_and_its_program(machine):
    folder = ces._shutdown_service_dir()
    folder.mkdir(parents=True)
    (folder / "kazma-engine-shutdown.exe").write_bytes(b"service")
    ok, detail = ces.remove(machine)
    assert ok, detail
    assert machine.service is None and not folder.exists()


def test_the_service_program_is_under_program_files(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "PF"))
    assert ces._shutdown_service_dir() == tmp_path / "PF" / "Kazma" / "engine-shutdown"
    assert "LOCALAPPDATA" not in str(ces._shutdown_service_dir()), \
        "it runs as LocalSystem: never from a folder the account can write"


# ── the distro's helpers, run ───────────────────────────────────────────


def _stand_ins(tmp_path: Path, mounts: str = "/\n/var/lib/docker\n") -> tuple[Path, Path]:
    """systemctl, findmnt and sync that record their calls; a log of them."""
    bin_dir, log = tmp_path / "bin", tmp_path / "calls.log"
    bin_dir.mkdir()
    for name in ("systemctl", "sync"):
        (bin_dir / name).write_text(f'#!/bin/bash\necho "{name} $*" >> "{log.as_posix()}"\n', encoding="utf-8",
                                    newline="\n")
    (bin_dir / "findmnt").write_text(f'#!/bin/bash\necho "findmnt $*" >> "{log.as_posix()}"\nprintf "%s" '
                                     f'"{mounts}"\n', encoding="utf-8", newline="\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    return bin_dir, log


def _run(script_text: str, tmp_path: Path, bin_dir: Path, *, stdin: str = "", args: tuple[str, ...] = (),
         name: str = "script.sh") -> subprocess.CompletedProcess:
    """*script_text* under bash, the stand-ins first on its PATH."""
    script = tmp_path / name
    script.write_text(f'export PATH="{_posix(bin_dir)}:$PATH"\n' + script_text, encoding="utf-8", newline="\n")
    return subprocess.run([_bash(), _posix(script), *args], input=stdin.encode("utf-8"), capture_output=True,
                          timeout=60, check=False)


def _posix(path: Path) -> str:
    text = path.as_posix()
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        return f"/{text[0].lower()}{text[2:]}"
    return text


def _calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _order_problems(calls: list[str]) -> list[str]:
    """What a stop does wrong: Docker must stop before containerd, both before
    the mask (a masked unit reloads with no order), and the disks are written
    by syncfs per local filesystem, never a plain sync (it hung on the Windows
    drives during a shutdown)."""
    problems = []
    try:
        docker = next(i for i, c in enumerate(calls) if c.startswith("systemctl stop") and "docker.service" in c)
        containerd = next(i for i, c in enumerate(calls) if c.startswith("systemctl stop") and "containerd" in c)
    except StopIteration:
        return ["Docker or containerd is never stopped"]
    masks = [i for i, c in enumerate(calls) if c.startswith("systemctl mask")]
    if "containerd" in calls[docker]:
        problems.append("containerd is stopped in the same call as Docker: no order between them")
    elif containerd < docker:
        problems.append("containerd is stopped before Docker")
    if any(i < max(docker, containerd) for i in masks):
        problems.append("a unit is masked before it is stopped")
    syncs = [c for c in calls if c.split()[0] == "sync"]
    if any(c.strip() == "sync" for c in syncs):
        problems.append("a plain sync (it flushes the Windows drives too)")
    if not any(c.startswith("sync -f") for c in syncs):
        problems.append("the distro's disks are not written")
    return problems


@needs_bash
def test_the_stop_stops_docker_first_and_writes_only_local_disks(tmp_path):
    stop = _heredoc(ces._asset("engine-stop.sh"), "STOP")
    bin_dir, log = _stand_ins(tmp_path)
    got = _run(stop, tmp_path, bin_dir)
    assert got.returncode == 0, got.stderr
    out = got.stdout.decode()
    assert out.startswith("ENGINE_STOPPED in ") and "disks written" in out, out
    calls = _calls(log)
    assert _order_problems(calls) == [], calls
    assert {c for c in calls if c.startswith("sync -f")} == {"sync -f /", "sync -f /var/lib/docker"}
    assert any(c.startswith("findmnt") and "ext4" in c for c in calls), "only the distro's own filesystems"


@needs_bash
def test_control_the_first_stop_masked_first_and_synced_everything(tmp_path):
    """The first version, measured on the VM: it masked first -- containerd
    stopped at once under a Docker still stopping its containers ("Force
    shutdown daemon", 36 s) -- and its plain sync never returned during a
    shutdown. The same checks catch it."""
    first = ("#!/bin/bash\nsystemctl mask --runtime docker.service docker.socket containerd.service\n"
             "systemctl stop docker.socket docker.service containerd.service\nsync\necho ENGINE_STOPPED\n")
    bin_dir, log = _stand_ins(tmp_path)
    _run(first, tmp_path, bin_dir)
    problems = _order_problems(_calls(log))
    assert any("same call" in p for p in problems)
    assert any("masked before" in p for p in problems)
    assert any("plain sync" in p for p in problems)


def _hold(tmp_path: Path) -> str:
    """kazma-engine-hold, calling a stand-in stop and systemctl."""
    hold = _heredoc(ces._asset("engine-stop.sh"), "HOLD")
    stop = tmp_path / "fake-stop"
    stop.write_text('#!/bin/bash\necho "noise first"\necho "ENGINE_STOPPED in 1.0 s (fake)"\n', encoding="utf-8",
                    newline="\n")
    stop.chmod(0o755)
    return hold.replace("/usr/local/sbin/kazma-engine-stop", _posix(stop))


@needs_bash
def test_the_holder_answers_the_stop_then_powers_off_on_the_next_line(tmp_path):
    bin_dir, log = _stand_ins(tmp_path)
    # CRLF, as .NET's WriteLine sends it: bash reads "stop\r".
    got = _run(_hold(tmp_path), tmp_path, bin_dir, stdin="stop\r\npoweroff\n", args=("piped",), name="hold.sh")
    assert got.stdout.decode().strip() == "ENGINE_STOPPED in 1.0 s (fake)", "its answer is the stop's last line"
    assert _calls(log) == ["systemctl poweroff"]


@needs_bash
def test_the_piped_holder_ends_when_the_service_goes_away(tmp_path):
    bin_dir, log = _stand_ins(tmp_path)
    got = _run(_hold(tmp_path), tmp_path, bin_dir, stdin="", args=("piped",), name="hold.sh")
    assert got.returncode == 0 and _calls(log) == [], "it ends; keep-engine.ps1 starts one for the new service"


@needs_bash
def test_control_a_holder_that_compares_the_raw_line_never_stops(tmp_path):
    """The first holder compared "$line" = "stop": .NET's WriteLine sent
    "stop\\r\\n" and nothing stopped (measured on the VM)."""
    first = _hold(tmp_path).replace('"${line%$\'\\r\'}"', '"$line"')
    assert first != _hold(tmp_path)
    bin_dir, log = _stand_ins(tmp_path)
    got = _run(first.replace("case \"$line\" in", 'case "$line" in'), tmp_path, bin_dir,
               stdin="stop\r\n", args=("piped",), name="hold.sh")
    assert "ENGINE_STOPPED" not in got.stdout.decode()


def test_the_helpers_reach_the_distro_as_lf_text():
    script = ces._asset("engine-stop.sh")
    assert "\r" not in script and script.startswith("#!/bin/bash") and "STOP_READY" in script
    assert b"\r" not in (ASSETS / "engine-stop.sh").read_bytes()


# ── keep-engine.ps1 and the service ─────────────────────────────────────


def test_keep_engine_starts_the_holder_outside_task_schedulers_reach():
    """Task Scheduler ended keep-engine.ps1 and its child wsl.exe 60 ms into
    every measured shutdown: the holder is started through WMI, its stdin and
    stdout the service's pipes."""
    keep = ces._asset("keep-engine.ps1")
    assert "Invoke-CimMethod -ClassName Win32_Process -MethodName Create" in keep
    assert "kazma-engine-hold piped" in keep
    assert re.search(r"<\$pipeIn >\$pipeOut", keep), "stdin from the service, stdout to it"
    cs = ces._asset("engine-shutdown.cs")
    for pipe in ("KazmaEngineHold-in", "KazmaEngineHold-out"):
        assert f"\\\\.\\pipe\\{pipe}" in keep and f'"{pipe}"' in cs


def test_keep_engine_starts_nothing_once_windows_is_shutting_down():
    keep = ces._asset("keep-engine.ps1")
    assert "GetSystemMetrics(0x2000)" in keep, "SM_SHUTTINGDOWN"
    loop = keep[keep.index("while ($true) {"):]
    assert loop.index("ShuttingDown") < loop.index("StartPipedHolder") < loop.index("StartPlainHolder")


def test_the_service_takes_the_preshutdown_notice_and_its_time():
    cs = ces._asset("engine-shutdown.cs")
    assert "SERVICE_ACCEPT_PRESHUTDOWN" in cs and "SERVICE_CONTROL_PRESHUTDOWN" in cs
    assert "SERVICE_CONFIG_PRESHUTDOWN_INFO" in cs, "Windows waits for it only as long as its own timeout"
    # stop, its answer, then poweroff: the order the drill needed.
    body = cs[cs.index("static void StopEngine"):]
    assert body.index('"stop\\n"') < body.index("TryTake(out answer") < body.index('"poweroff\\n"')
    assert "NetworkSid" in cs and "AccessControlType.Deny" in cs, "nobody reaches the pipes over the network"


@pytest.mark.skipif(sys.platform != "win32" or not ces._csc().is_file(),
                    reason="the .NET Framework's compiler is a Windows program")
def test_the_service_builds_with_the_compiler_setup_uses(tmp_path):
    source = tmp_path / "kazma-engine-shutdown.cs"
    source.write_text(ces._asset("engine-shutdown.cs"), encoding="utf-8")
    exe = tmp_path / "kazma-engine-shutdown.exe"
    got = subprocess.run([str(ces._csc()), "-nologo", "-optimize+", "-target:exe", f"-out:{exe}", str(source)],
                         capture_output=True, text=True, timeout=120, check=False)
    assert got.returncode == 0, got.stdout + got.stderr
    ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30, check=False)
    assert ran.returncode == 1 and "KazmaEngineShutdown service's program" in ran.stderr, \
        "started outside the service manager, it says what it is and does nothing"

