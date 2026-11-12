"""The boot drill for Kazma's own container engine (Windows).

Stage 2 of ``docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md`` asks, as its
gate, for the drill done by hand on 2026-11-12, automated: a Full install on
the clean drill VM, a restart with nobody signed in, every container part
ready within five minutes. This script runs it from the host PC against the
Hyper-V VM (``KazmaVMs``, reached by SSH as ``kazma-drill``):

1. revert the VM to its clean checkpoint and wait for SSH;
2. copy one commit of this checkout (``git archive``) and ``uv`` there, sync
   the base packages;
3. run ``kazma setup install --mode full -y [--docker desktop]`` DETACHED
   (``Win32_Process.Create``: an SSH drop would kill a child of the SSH
   session) and wait for it; when it asks for Windows to restart (WSL), the
   VM is restarted and setup runs again, as a person would;
4. set the strict code profile (``security.execution.container_required``,
   the live install's), so Kazma's readiness probes the code sandbox's
   engine and says which one it waited for;
5. restart Windows and let nobody sign in;
6. every ``POLL_EVERY_S``, read Kazma's own readiness (``/health/ready``),
   ``kazma_guard.py --status`` and SearXNG's ``/healthz`` on the VM, with the
   time since boot and whether anyone is signed in (``query user``);
7. judge each container part -- the engine, the code sandbox, the document
   worker, private search -- ready or not within ``BUDGET_S`` of boot, and
   write the readings (JSON) and a report (Markdown) the plan cites.

Negative control: ``--docker desktop``, the same drill with Docker Desktop,
which starts only when someone signs in. Nothing may come up: the verdict
must be ``not_up``. (Changing the ``KazmaEngine`` task's trigger is no
control: the guard runs that task on demand, and holds a keep-alive of its
own when it cannot, so Kazma's engine comes back whatever the trigger --
which is the design, AGENTS.md section 63.)

Nobody signing in is a premise, checked at every reading: a reading with a
session in ``query user`` makes the verdict ``invalid``.

Usage (from the checkout, on the host)::

    .venv/Scripts/python.exe scripts/engine_boot_drill.py run [--docker desktop]
    .venv/Scripts/python.exe scripts/engine_boot_drill.py boot [--docker desktop]  # 4-7 on an installed VM

Standard library only. The judging (``parse_status``, ``part_readings``,
``judge``, ``render_report``) is pure, and held by
``tests/test_engine_boot_drill.py`` against the guard's own ``--status`` lines.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

VM_NAME = "KazmaVMs"
CHECKPOINT = "clean Windows + SSH + nested virtualization (2026-11-12)"
SSH_HOST = "kazma-drill"
#: On the VM: the install, and the drill's own folder (probe, logs).
VM_ROOT = r"C:\kazma-drill"
VM_DRILL = r"C:\kazma-drill-probe"
#: Every container part ready within this many seconds of boot.
BUDGET_S = 300.0
#: Readings go on this long past the budget, so a late part is still timed.
OBSERVE_PAST_BUDGET_S = 120.0
POLL_EVERY_S = 15.0
#: A Full install's longest round (downloads included) before the drill gives up.
INSTALL_TIMEOUT_S = 4 * 3600.0
INSTALL_ROUNDS = 4
#: What setup says when Windows must restart before it can go on (WSL).
RESTART_SAID = "Restart Windows to finish WSL"

#: The container parts and how each is read. Order is the report's.
PARTS = ("engine", "code_sandbox", "document_worker", "private_search")
ENGINE_NAMES = {"kazma": "Kazma's container engine", "desktop": "Docker Desktop"}
PART_TITLES = {
    "engine": "Container engine (kazma_guard.py --status: docker)",
    "code_sandbox": "Code sandbox (readiness: code_execution)",
    "document_worker": "Document worker (kazma_guard.py --status: doc broker)",
    "private_search": "Private search (SearXNG /healthz)",
}


# ── reading what the VM said (pure) ─────────────────────────────────────

_STATUS_LINE = re.compile(r"^([a-z][a-z ]*?)\s*:\s?(.*)$")


def parse_status(text: str) -> dict[str, str]:
    """The top-level lines of ``kazma_guard.py --status`` (``server``,
    ``docker``, ``doc broker`` ...) as {name: value}; indented detail lines
    are left out. The first of a repeated name wins (``disk`` lines)."""
    fields: dict[str, str] = {}
    for line in (text or "").splitlines():
        if not line or line[0].isspace():
            continue
        match = _STATUS_LINE.match(line.rstrip())
        if match and match.group(1) not in fields:
            fields[match.group(1)] = match.group(2).strip()
    return fields


def part_readings(reading: dict[str, Any], engine: str) -> dict[str, tuple[bool, str]]:
    """Each container part in one reading: (ready, what was read).

    *engine* is the engine the drill installed (``kazma``, ``desktop``): the
    code sandbox counts as ready only on that engine, as readiness names it.
    """
    status = parse_status(str(reading.get("status") or ""))
    out: dict[str, tuple[bool, str]] = {}

    docker = status.get("docker", "")
    out["engine"] = (docker.startswith("answers"), docker or "no docker line in --status")

    body = (reading.get("ready") or {}).get("body")
    check = ((body or {}).get("checks") or {}).get("code_execution") if isinstance(body, dict) else None
    if not isinstance(check, dict):
        error = (reading.get("ready") or {}).get("error")
        out["code_sandbox"] = (False, f"readiness did not answer ({error})" if error
                               else "readiness has no code_execution check")
    else:
        named = check.get("engine")
        ok = check.get("status") == "ok" and named == engine
        said = check.get("error") or f"{check.get('status')} on engine {named}"
        out["code_sandbox"] = (ok, str(said))

    broker = status.get("doc broker", "")
    out["document_worker"] = (broker.startswith("running"), broker or "no doc broker line in --status")

    search = reading.get("searxng") or {}
    out["private_search"] = (search.get("http") == 200,
                             f"HTTP {search.get('http')}" if search.get("http") is not None
                             else str(search.get("error") or "no address"))
    return out


@dataclass
class Verdict:
    verdict: str                      # pass | not_up | invalid
    parts: dict[str, dict[str, Any]]  # part -> ready_at_s, ready_in_budget, last
    signed_in: list[float]            # uptimes at which someone was signed in
    kazma_answered_at: float | None
    readings: int
    notes: list[str] = field(default_factory=list)


def judge(readings: list[dict[str, Any]], engine: str, budget_s: float = BUDGET_S) -> Verdict:
    """The drill's verdict from its readings, in order.

    ``pass``: nobody signed in at any reading, and every part was ready at a
    reading within *budget_s* of boot and still ready at the last one.
    ``not_up``: some part was not (what the controls must give).
    ``invalid``: someone was signed in, or there was no reading at all -- the
    premise does not hold and the drill says nothing about it.
    """
    parts: dict[str, dict[str, Any]] = {p: {"ready_at_s": None, "ready_in_budget": False,
                                            "ready_at_end": False, "last": ""} for p in PARTS}
    signed_in: list[float] = []
    kazma_at: float | None = None
    for reading in readings:
        up = float(reading.get("uptime_s") or 0.0)
        if reading.get("signed_in"):
            signed_in.append(up)
        if kazma_at is None and (reading.get("ready") or {}).get("http") in (200, 503):
            kazma_at = up
        for part, (ok, said) in part_readings(reading, engine).items():
            rec = parts[part]
            rec["last"] = said
            rec["ready_at_end"] = ok
            if ok and rec["ready_at_s"] is None:
                rec["ready_at_s"] = round(up, 1)
                rec["ready_in_budget"] = up <= budget_s
    notes: list[str] = []
    if not readings:
        return Verdict("invalid", parts, signed_in, kazma_at, 0, ["no reading was taken"])
    if signed_in:
        notes.append(f"someone was signed in at {len(signed_in)} reading(s): the premise does not hold")
        return Verdict("invalid", parts, signed_in, kazma_at, len(readings), notes)
    good = all(rec["ready_in_budget"] and rec["ready_at_end"] for rec in parts.values())
    for part, rec in parts.items():
        if rec["ready_at_s"] is not None and not rec["ready_at_end"]:
            notes.append(f"{part} was ready at {rec['ready_at_s']} s and not at the last reading")
    return Verdict("pass" if good else "not_up", parts, signed_in, kazma_at, len(readings), notes)


def render_report(*, label: str, engine: str, control: bool, verdict: Verdict, meta: dict[str, Any],
                  evidence_name: str, budget_s: float = BUDGET_S) -> str:
    """The drill's report in Markdown: what ran, each part's time, the verdict."""
    expected = "not_up" if control else "pass"
    held = verdict.verdict == expected
    lines = [
        f"# Engine boot drill: {label}",
        "",
        f"Run {meta.get('finished', '')} by `scripts/engine_boot_drill.py` "
        f"(`docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md`, Stage 2).",
        "",
        "| | |",
        "|---|---|",
        f"| Commit | `{meta.get('commit', '')}` |",
        f"| VM | `{meta.get('vm', VM_NAME)}`, reverted to `{meta.get('checkpoint', CHECKPOINT)}` |",
        f"| Engine installed | {ENGINE_NAMES.get(engine, engine)} (`--docker {engine}`) |",
        f"| Kind | {'negative control: nothing may come up' if control else 'the drill: everything must come up'} |",
        f"| Budget | every container part ready within {int(budget_s)} s of boot, nobody signed in |",
        f"| Readings | {verdict.readings}, every {int(POLL_EVERY_S)} s |",
        f"| Kazma answered readiness | "
        f"{'at ' + str(round(verdict.kazma_answered_at)) + ' s' if verdict.kazma_answered_at is not None else 'never'} |",
        f"| Verdict | **{verdict.verdict}** (expected {expected}: "
        f"{'held' if held else 'NOT HELD'}) |",
        "",
    ]
    for step in meta.get("install", []):
        lines.append(f"- Install round {step.get('round')}: exit {step.get('exit')} after "
                     f"{step.get('seconds')} s{'; Windows restarted for WSL' if step.get('restart') else ''}.")
    for extra in meta.get("steps", []):
        lines.append(f"- {extra}")
    lines += ["", "| Part | Ready at (s after boot) | In budget | Ready at the end | Last reading |",
              "|---|---|---|---|---|"]
    for part in PARTS:
        rec = verdict.parts[part]
        at = "never" if rec["ready_at_s"] is None else str(rec["ready_at_s"])
        last = str(rec["last"]).replace("|", "/")[:160]
        lines.append(f"| {PART_TITLES[part]} | {at} | {'yes' if rec['ready_in_budget'] else 'no'} | "
                     f"{'yes' if rec['ready_at_end'] else 'no'} | {last} |")
    if verdict.notes:
        lines += ["", *[f"- {note}" for note in verdict.notes]]
    lines += ["", f"Every reading: `docs/audits/evidence/{evidence_name}`.", ""]
    return "\n".join(lines)


# ── the VM ─────────────────────────────────────────────────────────────


def _say(text: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {text}", flush=True)


def _ssh_exe() -> str:
    native = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "ssh.exe"
    return str(native) if native.is_file() else "ssh"


def _scp_exe() -> str:
    native = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "scp.exe"
    return str(native) if native.is_file() else "scp"


def _host_powershell(script: str, timeout: float = 600.0) -> subprocess.CompletedProcess:
    """A PowerShell script on the host (the Hyper-V cmdlets)."""
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL, check=False)


class Vm:
    def __init__(self, name: str = VM_NAME, host: str = SSH_HOST) -> None:
        self.name, self.host = name, host

    def ssh(self, script: str, timeout: float = 300.0) -> subprocess.CompletedProcess:
        """A PowerShell script on the VM, sent encoded (no quoting through
        sshd's shell)."""
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return subprocess.run(
            [_ssh_exe(), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.host,
             "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand " + encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            stdin=subprocess.DEVNULL, check=False)

    def copy(self, local: Path, remote: str, timeout: float = 1800.0) -> None:
        dest = remote.replace("\\", "/")
        got = subprocess.run([_scp_exe(), "-o", "BatchMode=yes", "-q", str(local), f"{self.host}:{dest}"],
                             capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL, check=False)
        if got.returncode != 0:
            raise RuntimeError(f"scp {local.name} failed: {(got.stderr or got.stdout).strip()[-300:]}")

    def answers(self) -> bool:
        try:
            got = self.ssh("'ok'", timeout=40)
        except subprocess.TimeoutExpired:
            return False
        return got.returncode == 0 and "ok" in got.stdout

    def heartbeat_ok(self) -> bool:
        """Hyper-V's heartbeat: Windows on the VM is up (whatever SSH says)."""
        got = _host_powershell(f"(Get-VMIntegrationService -VMName '{self.name}' -Name Heartbeat)"
                               ".PrimaryStatusDescription", timeout=60)
        return got.stdout.strip() == "OK"

    def wait_ssh(self, timeout: float = 1800.0, *, kick_after: float = 180.0) -> None:
        """Wait for SSH. A VM restored from a checkpoint may keep an address
        the Default Switch no longer serves: when Windows is up (heartbeat)
        and SSH still does not answer *kick_after* later, the VM is restarted
        once. A slow boot (updates being applied) has no heartbeat yet and is
        waited for, never cut short."""
        start, kicked, up_since = time.monotonic(), False, None
        while time.monotonic() - start < timeout:
            if self.answers():
                return
            if self.heartbeat_ok():
                up_since = up_since or time.monotonic()
            else:
                up_since = None
            if not kicked and up_since is not None and time.monotonic() - up_since > kick_after:
                kicked = True
                _say("Windows is up and SSH does not answer: restarting the VM once "
                     "(a stale Default Switch address)")
                _host_powershell(f"Restart-VM -Name '{self.name}' -Type Reboot -Force -Wait -For Heartbeat",
                                 timeout=900)
                up_since = None
            time.sleep(10)
        raise RuntimeError(f"the VM did not answer SSH in {int(timeout)} s")

    def uptime_s(self) -> float | None:
        # Windows PowerShell 5.1 runs on .NET Framework: no TickCount64.
        got = self.ssh("[int]((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime)"
                       ".TotalSeconds", timeout=60)
        try:
            return float(got.stdout.strip().splitlines()[-1].replace(",", "."))
        except (ValueError, IndexError):
            return None

    def revert(self, checkpoint: str) -> None:
        got = _host_powershell(
            f"Restore-VMCheckpoint -VMName '{self.name}' -Name '{checkpoint}' -Confirm:$false; "
            f"if ((Get-VM -Name '{self.name}').State -ne 'Running') {{ Start-VM -Name '{self.name}' }}; "
            f"(Get-VM -Name '{self.name}').State")
        if got.returncode != 0 or "Running" not in got.stdout:
            raise RuntimeError(f"revert failed: {(got.stderr or got.stdout).strip()[-400:]}")

    def restart(self) -> None:
        """Restart Windows on the VM (a guest reboot, as an update does) and
        wait until SSH answers from the NEW boot; nobody signs in."""
        asked = time.monotonic()
        self.ssh("shutdown.exe /r /t 0 /f", timeout=60)
        while True:
            time.sleep(20)
            self.wait_ssh()
            up = self.uptime_s()
            if up is not None and up <= time.monotonic() - asked + 5:
                return

    def start_detached(self, command: str, cwd: str) -> None:
        """Start *command* on the VM outside the SSH session (WMI)."""
        script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
                  f"@{{CommandLine={_ps_quote(command)}; CurrentDirectory={_ps_quote(cwd)}}}; "
                  "\"rc=$($r.ReturnValue) pid=$($r.ProcessId)\"")
        got = self.ssh(script, timeout=120)
        if "rc=0" not in got.stdout:
            raise RuntimeError(f"could not start {command!r}: {got.stdout.strip()} {got.stderr.strip()}")


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


# ── the files the VM runs ───────────────────────────────────────────────

PROBE = r'''"""One reading of the drill VM, as JSON (engine_boot_drill.py)."""
import ctypes, json, os, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(sys.argv[1])
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

def env_value(name):
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""

def get(url, timeout=8):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:200]

def run(argv, timeout=60):
    try:
        got = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                             env={**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"})
        return got.returncode, (got.stdout or "") + (got.stderr or "")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:200]

reading = {"at": time.time(), "uptime_s": ctypes.windll.kernel32.GetTickCount64() / 1000.0}
code, users = run(["query", "user"], timeout=20)
reading["users"] = users.strip()[:400]
reading["signed_in"] = code == 0 and len([l for l in users.splitlines() if l.strip()]) > 1
port = env_value("KAZMA_PORT") or "9090"
code, text = get(f"http://127.0.0.1:{port}/health/ready")
ready = {"http": code}
try:
    ready["body"] = json.loads(text) if code is not None else None
except ValueError:
    ready["body"] = None
if code is None:
    ready["error"] = text
reading["ready"] = ready
_, status = run([sys.executable, str(ROOT / "scripts" / "service" / "kazma_guard.py"), "--status"], timeout=90)
reading["status"] = status[-6000:]
url = env_value("KAZMA_SEARXNG_URL")
code, text = get(url.rstrip("/") + "/healthz") if url else (None, "KAZMA_SEARXNG_URL not set")
reading["searxng"] = {"url": url, "http": code, **({"error": text} if code is None else {})}
_, task = run(["schtasks", "/Query", "/TN", "KazmaEngine", "/FO", "LIST"], timeout=20)
reading["engine_task"] = " ".join(l.strip() for l in task.splitlines() if l.strip().startswith("Status"))
_, procs = run(["tasklist", "/FI", "IMAGENAME eq Docker Desktop.exe", "/NH"], timeout=20)
reading["docker_desktop_running"] = "Docker Desktop.exe" in procs
print(json.dumps(reading))
'''

PREPARE = r"""param([Parameter(Mandatory)][string]$Zip)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = '%(root)s'
$work = Join-Path $env:TEMP 'kazma-drill-src'
foreach ($d in @($root, $work)) { if (Test-Path $d) { Remove-Item $d -Recurse -Force } }
New-Item -ItemType Directory -Force $work | Out-Null
Expand-Archive $Zip -DestinationPath $work -Force
Move-Item (Join-Path $work 'kazma') $root
$bin = Join-Path $env:USERPROFILE '.local\bin'
$env:PATH = "$bin;$env:PATH"
$ErrorActionPreference = 'Continue'
& cmd.exe /c "uv sync --inexact --python 3.12 --project `"$root`" > `"%(drill)s\uv-sync.log`" 2>&1"
"uv exit $LASTEXITCODE"
"python: " + (Test-Path (Join-Path $root '.venv\Scripts\python.exe'))
"""

RUN_INSTALL = r"""param([int]$Round, [string]$Docker = 'kazma')
$ErrorActionPreference = 'Continue'
$drill = '%(drill)s'
$env:PATH = (Join-Path $env:USERPROFILE '.local\bin') + ";$env:PATH"
$env:NO_COLOR = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location '%(root)s'
$start = Get-Date
& cmd.exe /c ".venv\Scripts\python.exe -m kazma_cli setup install --mode full -y --docker $Docker > `"$drill\install-$Round.log`" 2>&1"
$code = $LASTEXITCODE
@{exit = $code; seconds = [int]((Get-Date) - $start).TotalSeconds} | ConvertTo-Json | Set-Content "$drill\install-$Round.done" -Encoding ascii
"""

STRICT_PROFILE = (
    "from kazma_core.env_files import load_env_files; load_env_files(); "
    "from kazma_core.config_store import get_config_store; "
    "s = get_config_store(); "
    "s.set('security.execution.container_required', True, category='security'); "
    "print('container_required =', s.get('security.execution.container_required'))"
)


def _fill(template: str) -> str:
    return template % {"root": VM_ROOT, "drill": VM_DRILL}


# ── the drill ───────────────────────────────────────────────────────────


def _commit(ref: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", ref], capture_output=True, text=True,
                          check=True).stdout.strip()


def prepare(vm: Vm, ref: str, work: Path) -> str:
    """Revert the VM, copy *ref* and uv there, sync the base packages."""
    commit = _commit(ref)
    _say(f"Reverting {vm.name} to {CHECKPOINT!r}")
    vm.revert(CHECKPOINT)
    vm.wait_ssh()
    archive = work / "kazma.zip"
    subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", "--prefix=kazma/", "-o", str(archive),
                    commit], check=True)
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is not on this PC's PATH: the VM gets its copy from here")
    (work / "probe.py").write_text(PROBE, encoding="utf-8")
    (work / "prepare.ps1").write_text(_fill(PREPARE), encoding="utf-8")
    (work / "run-install.ps1").write_text(_fill(RUN_INSTALL), encoding="utf-8")
    vm.ssh(f"New-Item -ItemType Directory -Force '{VM_DRILL}', "
           "(Join-Path $env:USERPROFILE '.local\\bin') | Out-Null", timeout=60)
    _say(f"Copying commit {commit[:10]} and uv to the VM")
    vm.copy(archive, VM_DRILL + r"\kazma.zip")
    vm.copy(Path(uv), ".local/bin/uv.exe")  # relative: the account's home
    for name in ("probe.py", "prepare.ps1", "run-install.ps1"):
        vm.copy(work / name, VM_DRILL + "\\" + name)
    _say("Syncing the base packages on the VM (uv sync)")
    got = vm.ssh(f"& '{VM_DRILL}\\prepare.ps1' -Zip '{VM_DRILL}\\kazma.zip'", timeout=1800)
    if "python: True" not in got.stdout:
        raise RuntimeError(f"prepare failed: {got.stdout[-600:]} {got.stderr[-600:]}")
    return commit


def install(vm: Vm, docker: str) -> list[dict[str, Any]]:
    """Run the Full install as a person would: restart Windows when it asks
    (WSL) and run it again; run it once more after a round that ended with
    something not done ("Run setup again to finish"). Returns each round's
    exit and time."""
    rounds: list[dict[str, Any]] = []
    again_after_failure = True
    for number in range(1, INSTALL_ROUNDS + 1):
        _say(f"Install round {number} (--mode full --docker {docker}), detached")
        vm.start_detached(f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File {VM_DRILL}\\run-install.ps1 "
                          f"-Round {number} -Docker {docker}", VM_ROOT)
        done = _wait_for_file(vm, f"{VM_DRILL}\\install-{number}.done", INSTALL_TIMEOUT_S)
        log = vm.ssh(f"Get-Content '{VM_DRILL}\\install-{number}.log' -Tail 60 -Encoding utf8", timeout=60).stdout
        restart = RESTART_SAID in log
        rounds.append({"round": number, "exit": done.get("exit"), "seconds": done.get("seconds"),
                       "restart": restart, "tail": log[-3000:]})
        _say(f"Round {number}: exit {done.get('exit')} after {done.get('seconds')} s"
             f"{' -- Windows must restart (WSL)' if restart else ''}")
        if restart:
            vm.restart()
            continue
        if done.get("exit") != 0 and again_after_failure:
            again_after_failure = False
            continue
        break
    return rounds


def _wait_for_file(vm: Vm, path: str, timeout: float) -> dict[str, Any]:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        time.sleep(60)
        try:
            got = vm.ssh(f"if (Test-Path '{path}') {{ Get-Content '{path}' -Raw }}", timeout=60)
        except subprocess.TimeoutExpired:
            continue
        text = got.stdout.strip()
        if got.returncode == 0 and text.startswith("{"):
            return json.loads(text)
        minutes = int((time.monotonic() - start) // 60)
        if minutes and minutes % 10 == 0:
            _say(f"  still installing ({minutes} min)")
    raise RuntimeError(f"{path} did not appear in {int(timeout)} s")


def strict_profile(vm: Vm) -> str:
    got = vm.ssh(f"& '{VM_ROOT}\\.venv\\Scripts\\python.exe' -c \"{STRICT_PROFILE}\"", timeout=300)
    return (got.stdout + got.stderr).strip()[-300:]


def measure_boot(vm: Vm, engine: str) -> list[dict[str, Any]]:
    """Restart Windows, let nobody sign in, read the VM until the budget
    and the observation time have passed. Only readings of the new boot
    count (its uptime is below the time since the restart was asked)."""
    _say("Restarting Windows; nobody signs in")
    asked = time.monotonic()
    vm.ssh("shutdown.exe /r /t 0 /f", timeout=60)
    time.sleep(20)
    readings: list[dict[str, Any]] = []
    deadline = time.monotonic() + BUDGET_S + OBSERVE_PAST_BUDGET_S + 900.0
    while time.monotonic() < deadline:
        try:
            got = vm.ssh(f"& '{VM_ROOT}\\.venv\\Scripts\\python.exe' '{VM_DRILL}\\probe.py' '{VM_ROOT}'",
                         timeout=150)
        except subprocess.TimeoutExpired:
            got = None
        if got is not None and got.returncode == 0 and got.stdout.strip().startswith("{"):
            reading = json.loads(got.stdout.strip().splitlines()[-1])
            up = reading["uptime_s"]
            if up > time.monotonic() - asked + 5:
                time.sleep(5)
                continue  # the boot before the restart, still shutting down
            readings.append(reading)
            parts = part_readings(reading, engine)
            _say(f"  {up:6.0f} s: " + ", ".join(f"{p}={'up' if ok else 'down'}" for p, (ok, _s) in parts.items())
                 + (" [SIGNED IN]" if reading.get("signed_in") else ""))
            if up > BUDGET_S + OBSERVE_PAST_BUDGET_S:
                break
        time.sleep(POLL_EVERY_S)
    return readings


def write_results(*, label: str, engine: str, control: bool, readings: list[dict[str, Any]],
                  meta: dict[str, Any], out_dir: Path, report: Path | None) -> Verdict:
    verdict = judge(readings, engine)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%MZ")
    evidence_name = f"engine_boot_drill_{label}_{stamp}.json"
    meta["finished"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / evidence_name).write_text(json.dumps(
        {"label": label, "engine": engine, "control": control, "meta": meta,
         "verdict": verdict.__dict__, "readings": readings}, indent=1), encoding="utf-8", newline="\n")
    text = render_report(label=label, engine=engine, control=control, verdict=verdict, meta=meta,
                         evidence_name=evidence_name)
    target = report or out_dir / evidence_name.replace(".json", ".md")
    target.write_text(text, encoding="utf-8", newline="\n")
    _say(f"Verdict: {verdict.verdict}. Report: {target}")
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="what", required=True)
    run = sub.add_parser("run", help="revert, Full install, restart with nobody signed in, judge")
    run.add_argument("--docker", choices=("kazma", "desktop"), default="kazma")
    run.add_argument("--ref", default="HEAD", help="the commit of this checkout to install")
    boot = sub.add_parser("boot", help="steps 4-7 on a VM the drill installed")
    boot.add_argument("--docker", choices=("kazma", "desktop"), default="kazma")
    for one in (run, boot):
        one.add_argument("--out", type=Path, default=ROOT / "docs" / "audits" / "evidence")
        one.add_argument("--report", type=Path, default=None)
    options = parser.parse_args(argv)
    vm = Vm()
    meta: dict[str, Any] = {"vm": VM_NAME, "checkpoint": CHECKPOINT, "steps": []}
    control = options.docker == "desktop"
    label = options.docker if options.what == "run" else f"{options.docker}-boot"
    with tempfile.TemporaryDirectory(prefix="engine-drill-") as tmp:
        if options.what == "run":
            meta["commit"] = prepare(vm, options.ref, Path(tmp))
            meta["install"] = install(vm, options.docker)
        else:
            vm.wait_ssh()
        meta["steps"].append(f"Strict code profile set on the VM: {strict_profile(vm)}")
        readings = measure_boot(vm, options.docker)
    verdict = write_results(label=label, engine=options.docker, control=control, readings=readings,
                            meta=meta, out_dir=options.out, report=options.report)
    expected = "not_up" if control else "pass"
    return 0 if verdict.verdict == expected else 1


if __name__ == "__main__":
    sys.exit(main())
