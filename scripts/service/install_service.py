#!/usr/bin/env python3
"""Install Kazma as a supervised service on any supported platform.

Kazma ships orchestration for infrastructure it is rarely run on -- a
Dockerfile, Kubernetes manifests, a fly.toml -- and nothing at all for the
way it is actually run: as a long-lived process on someone's machine. This
closes that gap.

One health contract, five supervisors
-------------------------------------
The supervisor differs per platform. The liveness contract does not: every
platform runs ``kazma_guard.py``, which owns health-gated restart, backoff,
crash-loop protection and notification. The OS supervisor's only job is to
keep the guard alive. Without that split, each platform would grow its own
restart semantics and drift -- the same way chat delivery drifted across
transports and caused the incidents this work came out of.

    Windows   Scheduled Task, "run whether user is logged on or not"
    Linux     systemd unit (user or system)
    macOS     launchd daemon or agent
    WSL       systemd inside the distro + a Windows task to hold it up
    Docker    restart: unless-stopped + HEALTHCHECK

Usage
-----
    python scripts/service/install_service.py --print          # show the unit
    python scripts/service/install_service.py --install        # install it
    python scripts/service/install_service.py --uninstall
    python scripts/service/install_service.py --status
    python scripts/service/install_service.py --platform linux --print

``--print`` never touches the system, so the unit for any platform can be
generated from any platform and committed or reviewed. On Windows,
``--status`` compares the registered task with what ``--install`` registers
and names each difference.
"""

from __future__ import annotations

import argparse
import json
import ntpath
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "scripts" / "service" / "kazma_guard.py"

TASK_NAME = "KazmaAgent"
SERVICE_NAME = "kazma"
LAUNCHD_LABEL = "com.kazma.agent"

# What --install registers on Windows; --status checks the task against it.
# Priority 4 is an interactive program's. A task's default, 7, is Task
# Scheduler's background priority -- below every normal program on the
# machine -- and the server the guard starts inherits it.
TASK_PRIORITY = 4
# The repeating trigger that starts a guard that is not running.
KEEP_ALIVE_MINUTES = 5


# -- platform detection -----------------------------------------------


def detect_platform() -> str:
    """Return one of: windows, wsl, macos, linux."""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        # WSL needs both an in-distro supervisor AND a Windows-side task to
        # keep the distro running; it is not just "Linux".
        release = platform.release().lower()
        if "microsoft" in release or "wsl" in release:
            return "wsl"
        if Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists():
            return "wsl"
        return "linux"
    return system or "unknown"


def python_exe() -> str:
    """The interpreter the service should use -- prefer the project venv."""
    for candidate in (
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",   # Windows venv
        REPO_ROOT / ".venv" / "bin" / "python",           # POSIX venv
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


# -- unit templates ---------------------------------------------------


def systemd_unit() -> str:
    return f"""\
# Kazma agent -- health-gated supervision.
#
# install:  cp kazma.service ~/.config/systemd/user/   (user unit)
#           systemctl --user daemon-reload
#           systemctl --user enable --now {SERVICE_NAME}
#           loginctl enable-linger $USER   # survive logout
#
# For a system unit, drop the "--user" flags and place in
# /etc/systemd/system/. Postgres ordering matters: Kazma's session store
# and checkpointer both need it up first.

[Unit]
Description=Kazma agent
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={REPO_ROOT}
ExecStart={python_exe()} {GUARD}
Restart=always
RestartSec=5
# Crash-loop guard at the systemd layer as well as inside the guard: if the
# guard ITSELF cannot stay up, stop trying rather than spin.
StartLimitIntervalSec=600
StartLimitBurst=5
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=45
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def launchd_plist() -> str:
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!--
  Kazma agent -- health-gated supervision.

  install:  cp {LAUNCHD_LABEL}.plist ~/Library/LaunchAgents/
            launchctl load -w ~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist

  A LaunchAgent stops at logout. For an always-on agent use
  /Library/LaunchDaemons/ instead (requires sudo) so it survives logout
  and starts at boot without a session.
-->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python_exe()}</string>
        <string>{GUARD}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{REPO_ROOT}</string>

    <key>RunAtLoad</key>
    <true/>

    <!-- Restart unless it exited cleanly (a deliberate stop stays stopped). -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <!-- launchd's own crash-loop guard: refuse to respawn faster than this. -->
    <key>ThrottleInterval</key>
    <integer>30</integer>

    <key>StandardOutPath</key>
    <string>{REPO_ROOT / ".kazma" / "launchd.out.log"}</string>
    <key>StandardErrorPath</key>
    <string>{REPO_ROOT / ".kazma" / "launchd.err.log"}</string>
</dict>
</plist>
"""


def windows_task_ps1(*, elevated: bool = True) -> str:
    """Scheduled Task definition.

    ``elevated=True`` is the real deployment: S4U + AtStartup + Highest runs
    the agent whether or not anyone is logged in and brings it back after a
    reboot. Registering it requires an elevated PowerShell.

    ``elevated=False`` is the fallback a normal user can register without a
    UAC prompt. It is genuinely useful -- restart-on-failure works and the
    agent comes back at logon -- but it does NOT survive a reboot into the
    login screen, so it is a stepping stone, not the destination.
    """
    # The repeating trigger is what brings a guard back after it exits.
    # Restart-on-failure does not: Task Scheduler counts a process that ran
    # and exited as a completed run, whatever its exit code. Live 2026-09-26
    # the guard died, the task sat "Ready" with result 1, and Kazma ran with
    # nobody supervising it. With IgnoreNew a running guard is left alone,
    # so the trigger only ever starts a guard that is not there.
    keep_alive = (
        "(New-ScheduledTaskTrigger -Once -At (Get-Date) "
        f"-RepetitionInterval (New-TimeSpan -Minutes {KEEP_ALIVE_MINUTES}))"
    )
    if elevated:
        triggers = f"""@(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn),
    {keep_alive}
)"""
        principal = (
            "New-ScheduledTaskPrincipal -UserId $env:USERNAME "
            "-LogonType S4U -RunLevel Highest"
        )
        header = """\
# Session-independent: behaves like a service without requiring NSSM or
# WinSW - no third-party wrapper to install, update or trust.
#
# Run from an ELEVATED PowerShell:
#     powershell -ExecutionPolicy Bypass -File install_windows_task.ps1"""
    else:
        triggers = f"@( (New-ScheduledTaskTrigger -AtLogOn), {keep_alive} )"
        principal = (
            "New-ScheduledTaskPrincipal -UserId $env:USERNAME "
            "-LogonType Interactive -RunLevel Limited"
        )
        header = """\
# USER-LEVEL fallback - registers without elevation.
#
# Restart-on-failure works and the agent returns at logon, but it will NOT
# start after a reboot until someone logs in. Upgrade with an elevated:
#     powershell -ExecutionPolicy Bypass -File install_windows_task.ps1"""

    return f"""\
# Kazma agent - health-gated supervision via Scheduled Task.
#
{header}

$ErrorActionPreference = "Stop"

$Python = "{python_exe()}"
$Guard  = "{GUARD}"
$Root   = "{REPO_ROOT}"

$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Guard`"" -WorkingDirectory $Root

$triggers = {triggers}

$principal = {principal}

# Priority {TASK_PRIORITY}, an interactive program's. A task's default, 7, is Task
# Scheduler's background priority -- below every normal program on the
# machine -- and the server the guard starts inherits it.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -Priority {TASK_PRIORITY}

Register-ScheduledTask -TaskName "{TASK_NAME}" `
    -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Registered scheduled task '{TASK_NAME}'."
Write-Host "Start it now with:  Start-ScheduledTask -TaskName '{TASK_NAME}'"
"""


def docker_notes() -> str:
    return """\
# Docker -- supervision notes
#
# docker-compose.yml already declares `restart: unless-stopped`, which
# covers process exit. It does NOT cover "running but wedged", because no
# HEALTHCHECK is declared -- so a hung server keeps its container alive and
# Docker never intervenes.
#
# Add to the kazma service in docker-compose.yml:
#
#     healthcheck:
#       test: ["CMD", "python", "-c",
#              "import urllib.request,sys;
#               sys.exit(0 if urllib.request.urlopen(
#                 'http://127.0.0.1:9090/health/ready', timeout=5).status==200 else 1)"]
#       interval: 30s
#       timeout: 10s
#       retries: 3
#       start_period: 180s
#
# With a healthcheck present, an orchestrator (Swarm, Kubernetes, Compose
# with a restart policy plus an external watchdog) can act on unhealthy.
# Plain `docker compose up` marks the container unhealthy but will not
# restart it on its own -- run kazma_guard.py as the container entrypoint if
# you want health-gated restart inside a standalone container.
"""


def wsl_notes() -> str:
    return """\
# WSL -- supervision notes
#
# WSL needs BOTH layers or neither holds:
#
# 1. Inside the distro, enable systemd (WSL2, recent builds) by adding to
#    /etc/wsl.conf:
#
#        [boot]
#        systemd=true
#
#    then `wsl --shutdown` from Windows and reopen. Without this there is no
#    in-distro supervisor at all, and the Linux unit below cannot run.
#
# 2. Install the systemd unit as normal (see kazma.service), plus:
#
#        loginctl enable-linger $USER
#
#    so the unit survives closing the terminal.
#
# 3. On the WINDOWS side, a Scheduled Task must keep the distro itself
#    alive -- a WSL distro with no running process shuts down, taking the
#    unit with it. Register a task that runs at startup:
#
#        wsl.exe -d <distro> -u <user> -- /bin/true
#
#    or keep a long-lived process pinned. This repo already registers a
#    'KazmaWSL' task in scripts/fix-cloudflare-tunnel-tasks.ps1 for exactly
#    this reason.
"""


UNITS = {
    "linux": ("kazma.service", systemd_unit),
    "wsl": ("kazma.service", systemd_unit),
    "macos": (f"{LAUNCHD_LABEL}.plist", launchd_plist),
    "windows": ("install_windows_task.ps1", windows_task_ps1),
}

NOTES = {"docker": docker_notes, "wsl": wsl_notes}


# -- actions ----------------------------------------------------------


def do_print(target: str) -> int:
    if target in NOTES and target not in UNITS:
        print(NOTES[target]())
        return 0
    if target not in UNITS:
        print(f"No unit template for platform: {target}", file=sys.stderr)
        return 2
    name, builder = UNITS[target]
    print(f"# -- {name} --")
    print(builder())
    if target in NOTES:
        print(NOTES[target]())
    return 0


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _is_elevated() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


# -- the registered Windows task ----------------------------------------

# Reads the task --install registers (root folder) and prints it as one JSON
# line. Read-only. "registered" is false when there is no such task and null
# when it could not be read.
_TASK_QUERY_PS1 = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Names and paths may not be ASCII. Without a console (a service) there is no
# encoding to set, and ASCII still reads right.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try {
    $t = Get-ScheduledTask -TaskPath '\' -TaskName '__TASK_NAME__'
} catch {
    if ($_.CategoryInfo.Category -eq 'ObjectNotFound') {
        '{"registered": false}'
    } else {
        @{ registered = $null; error = "$($_.Exception.Message)" } | ConvertTo-Json -Compress
    }
    exit 0
}
$info = $null
try { $info = Get-ScheduledTaskInfo -TaskPath '\' -TaskName '__TASK_NAME__' } catch { }
$action = @($t.Actions)[0]
# $null piped to ForEach-Object runs the block once: filter it out first.
$triggers = @(@($t.Triggers) | Where-Object { $null -ne $_ } | ForEach-Object {
    @{ kind = "$($_.CimClass.CimClassName)"; every = "$($_.Repetition.Interval)"; enabled = [bool]$_.Enabled }
})
@{
    registered  = $true
    state       = "$($t.State)"
    user        = "$($t.Principal.UserId)"
    logon       = "$($t.Principal.LogonType)"
    level       = "$($t.Principal.RunLevel)"
    priority    = [int]$t.Settings.Priority
    instances   = "$($t.Settings.MultipleInstances)"
    execute     = "$($action.Execute)"
    arguments   = "$($action.Arguments)"
    triggers    = $triggers
    last_run    = $(if ($info -and $info.LastRunTime) { $info.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') } else { '' })
    last_result = $(if ($info) { [int64]$info.LastTaskResult } else { $null })
} | ConvertTo-Json -Compress -Depth 4
"""


def _ps_quote(text: str) -> str:
    """A PowerShell single-quoted string literal."""
    return "'" + text.replace("'", "''") + "'"


def _run_powershell_script(script: str, *, timeout: float = 60) -> tuple[int, str, str]:
    """Run a PowerShell script from a temporary file: (code, stdout, stderr).

    A file rather than -Command or -EncodedCommand: nothing to get wrong in
    command-line quoting, and no encoded command line for security tooling
    to flag. The same way the installer runs its registration script.
    """
    with tempfile.TemporaryDirectory(prefix="kazma-task-") as tmp:
        path = Path(tmp) / "query.ps1"
        # Windows PowerShell reads a file without a BOM in the ANSI code page.
        path.write_text(script, encoding="utf-8-sig")
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def registered_task(name: str = TASK_NAME) -> dict:
    """The registered Windows task, read-only.

    ``{"registered": False}`` when there is none; ``{"registered": None,
    "error": ...}`` when it could not be read -- which is not the same as
    "none", and callers must not treat it so.
    """
    code, out, err = _run_powershell_script(
        _TASK_QUERY_PS1.replace("__TASK_NAME__", name.replace("'", "''")))
    for line in reversed(out.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            break
        if isinstance(data, dict) and "registered" in data:
            return data
        break
    reason = next(
        (ln.strip() for ln in (err or out).splitlines()
         if ln.strip() and not ln.startswith("#< CLIXML")),
        f"powershell exited with code {code}",
    )
    return {"registered": None, "error": reason[:300]}


# LogonType values under which a task starts at boot with nobody logged on.
_BOOT_LOGONS = ("S4U", "Password", "ServiceAccount")

_TRIGGER_NAMES = {
    "MSFT_TaskBootTrigger": "at startup",
    "MSFT_TaskLogonTrigger": "at logon",
}

# Task Scheduler's last-run results, in the words an operator needs.
_TASK_RESULTS = {
    0x0: "completed",
    0x41300: "ready to run",
    0x41301: "running",
    0x41303: "has not run yet",
    0x41306: "ended from Task Scheduler (End)",
    0x8004131F: "a start was refused: the guard was already running",
    0x800710E0: "a trigger fired while the guard was running and was ignored (expected)",
    0xC000013A: "the guard stopped on Ctrl+C / Ctrl+Break",
}


def _minutes(interval: str) -> float | None:
    """An ISO-8601 duration from Task Scheduler ("PT5M", "P1DT2H") in minutes."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", interval or "")
    if not m:
        return None
    days, hours, mins, secs = (int(g or 0) for g in m.groups())
    total = days * 1440 + hours * 60 + mins + secs / 60
    return total or None


def explain_task_result(code: object) -> str:
    """A last-run result as hex with what it means."""
    try:
        value = int(code) & 0xFFFFFFFF  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return "unknown"
    text = _TASK_RESULTS.get(value)
    if text is None and 0 < value < 0x100:
        text = f"the guard exited with code {value}"
    return f"0x{value:X}: {text}" if text else f"0x{value:X}"


def _same_path(a: str, b: str) -> bool:
    """Whether two Windows paths name the same file (a task's are Windows
    paths whatever platform reads them)."""
    def norm(p: str) -> str:
        return ntpath.normcase(ntpath.normpath(p.strip().strip('"')))
    return bool(a.strip()) and norm(a) == norm(b)


def task_checks(task: dict) -> list[tuple[str, bool, str]]:
    """``(setting, ok, what the task has)`` for each setting --install registers."""
    checks: list[tuple[str, bool, str]] = []

    state = str(task.get("state") or "?")
    checks.append(("state", state != "Disabled", {
        "Running": "Running: the guard is up",
        "Ready": "Ready: no guard is running under the task",
        "Disabled": "Disabled: nothing starts the guard, at boot or after it exits",
    }.get(state, state)))

    user, logon, level = (str(task.get(k) or "?") for k in ("user", "logon", "level"))
    at_boot = logon in _BOOT_LOGONS and level == "Highest"
    checks.append(("account", at_boot, f"{user}, {logon}, {level}: " + (
        "starts at boot, no logon needed" if at_boot
        else "--install registers S4U with the highest privileges, "
             "which starts at boot with nobody logged on")))

    triggers = [t for t in task.get("triggers") or [] if isinstance(t, dict)]
    have = [name for kind, name in _TRIGGER_NAMES.items()
            if any(t.get("kind") == kind and t.get("enabled", True) for t in triggers)]
    repeats = [m for t in triggers if t.get("enabled", True)
               for m in [_minutes(str(t.get("every") or ""))] if m]
    keep_alive = bool(repeats) and min(repeats) <= KEEP_ALIVE_MINUTES
    if repeats:
        have.append(f"every {min(repeats):g} min")
    missing = [name for name in _TRIGGER_NAMES.values() if name not in have]
    if not keep_alive:
        missing.append(f"every {KEEP_ALIVE_MINUTES} min (brings back a guard that exited)")
    checks.append(("triggers", not missing, (", ".join(have) or "none") + (
        f"; missing: {', '.join(missing)}" if missing else "")))

    instances = str(task.get("instances") or "?")
    checks.append(("if running", instances == "IgnoreNew", {
        "IgnoreNew": "IgnoreNew: a trigger never starts a second guard",
        "Parallel": "Parallel: a trigger starts a second guard beside the running one",
        "Queue": "Queue: a trigger queues a second guard behind the running one",
        "StopExisting": "StopExisting: every trigger stops the running guard",
    }.get(instances, f"{instances}: --install registers IgnoreNew")))

    try:
        priority = int(task.get("priority"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        priority = -1
    if priority == TASK_PRIORITY:
        text = f"{priority}, an interactive program's"
    elif priority >= 7:
        text = (f"{priority}, background: the guard and the server start below "
                f"normal programs; --install registers {TASK_PRIORITY}")
    else:
        text = f"{priority}; --install registers {TASK_PRIORITY}"
    checks.append(("priority", priority == TASK_PRIORITY, text))

    execute = str(task.get("execute") or "")
    arguments = str(task.get("arguments") or "")
    ours = _same_path(execute, python_exe()) and _same_path(arguments, str(GUARD))
    checks.append(("runs", ours, f"{execute} {arguments}".strip() + (
        "" if ours else f"; this folder's guard is {python_exe()} \"{GUARD}\"")))
    return checks


def _elevated_install_hint(python: str = "", script: str = "", flags: str = "--install") -> str:
    python = python or python_exe()
    script = script or str(Path(__file__).resolve())
    return (
        "From an elevated PowerShell (Run as administrator):\n"
        f"  & {_ps_quote(python)} {_ps_quote(script)} {flags}"
    )


def task_install_root(task: dict) -> PureWindowsPath | None:
    """The Kazma folder whose guard the task starts, when it starts one."""
    guard = PureWindowsPath(str(task.get("arguments") or "").strip().strip('"'))
    if guard.name.lower() != "kazma_guard.py" or guard.parent.name.lower() != "service" \
            or guard.parent.parent.name.lower() != "scripts":
        return None
    return guard.parents[2]


def _runs_another_folder(task: dict) -> PureWindowsPath | None:
    """The other Kazma folder the task starts the guard of, if any."""
    if task.get("registered") is not True:
        return None
    root = task_install_root(task)
    if root is None or _same_path(str(root), str(REPO_ROOT)):
        return None
    return root


def _another_folder_text(task: dict, root: PureWindowsPath) -> str:
    """How to update a task that starts another folder's guard -- there."""
    return (
        f"'{TASK_NAME}' starts the guard in {root}, not in this folder ({REPO_ROOT}).\n"
        "To update the task there, run that folder's installer. "
        + _elevated_install_hint(
            str(task.get("execute") or python_exe()),
            str(root / "scripts" / "service" / "install_service.py"))
        + "\nTo move the task to this folder instead, run this folder's --install --move."
    )


def format_task_status(task: dict) -> tuple[str, int]:
    """The status report, and how many settings differ from --install's."""
    if task.get("registered") is False:
        return f"task '{TASK_NAME}' is not registered.\n{_elevated_install_hint()}", 1
    if task.get("registered") is not True:
        return (f"could not read task '{TASK_NAME}': "
                f"{task.get('error') or 'unknown error'}"), 1
    checks = task_checks(task)
    lines = [f"task '{TASK_NAME}':"]
    lines += [f"  {'ok ' if ok else 'FIX'}  {label:<11} {text}" for label, ok, text in checks]
    last_run = str(task.get("last_run") or "")
    if last_run and not last_run.startswith("1999"):
        lines.append(f"       {'last run':<11} {last_run} -- "
                     f"{explain_task_result(task.get('last_result'))}")
    problems = sum(1 for _, ok, _ in checks if not ok)
    lines.append("")
    if problems:
        lines.append(f"{problems} setting{'s differ' if problems > 1 else ' differs'} "
                     "from what --install registers.")
        other = _runs_another_folder(task)
        lines.append(_another_folder_text(task, other) if other else _elevated_install_hint())
    else:
        lines.append("The task matches what --install registers.")
    return "\n".join(lines), problems


def _replacing_would_downgrade(task: dict) -> bool:
    """Whether registering the user-level task would replace a better one.

    True for a task that starts at boot, and for one that could not be read:
    an unknown task is not a missing one.
    """
    if task.get("registered") is False:
        return False
    if task.get("registered") is not True:
        return True
    return not (task.get("logon") == "Interactive" and task.get("level") == "Limited")


def _downgrade_refusal(task: dict) -> str:
    if task.get("registered") is not True:
        why = (f"Could not read the registered task '{TASK_NAME}' "
               f"({task.get('error') or 'unknown error'}), so this shell cannot tell "
               "whether the user-level task would replace one that starts at boot.")
    else:
        why = (f"'{TASK_NAME}' is registered to start at boot ({task.get('user')}, "
               f"{task.get('logon')}, {task.get('level')}). This PowerShell is not "
               "elevated: the user-level task it can register would replace that "
               "one and lose the boot start.")
    return f"{why} Nothing was changed.\n{_elevated_install_hint()}"


def install_windows(*, move: bool = False) -> int:
    """Register the task, degrading to a user-level one when not elevated.

    A failed install that leaves nothing behind is worse than a partial one
    that works until someone reboots -- so when elevation is missing we
    register what we can and say plainly what it does not cover.

    Re-running --install replaces the registered task, so it never does so
    silently for the worse: not with the user-level task over one that
    starts at boot (a shell without admin rights), and not onto this folder
    when the task starts another folder's guard (a second checkout on the
    same machine) unless ``move`` says so.
    """
    svc_dir = REPO_ROOT / "scripts" / "service"
    elevated = _is_elevated()

    script = svc_dir / "install_windows_task.ps1"
    script.write_text(windows_task_ps1(elevated=True), encoding="utf-8")

    existing = registered_task()
    other = _runs_another_folder(existing)
    if other is not None and not move:
        print(f"Nothing was changed.\n{_another_folder_text(existing, other)}",
              file=sys.stderr)
        return 1

    if elevated:
        code, out = _run([
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script),
        ])
        print(out)
        if code == 0:
            report, _problems = format_task_status(registered_task())
            print("\n" + report)
        return code

    if _replacing_would_downgrade(existing):
        print(_downgrade_refusal(existing), file=sys.stderr)
        return 1

    fallback = svc_dir / "install_windows_task_userlevel.ps1"
    fallback.write_text(windows_task_ps1(elevated=False), encoding="utf-8")
    code, out = _run([
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(fallback),
    ])
    print(out)
    if code == 0:
        print(
            "\nNOT ELEVATED -- registered the user-level task instead.\n"
            "  what works: restart-on-failure, health-gated restart, and\n"
            "              recovery at logon\n"
            "  what does NOT: it will not start after a reboot until someone\n"
            "              logs in\n"
            "\nTo upgrade to boot-start, run this ONCE from an elevated "
            "PowerShell:\n"
            f"  powershell -ExecutionPolicy Bypass -File \"{script}\"\n"
        )
    else:
        print(
            "\nCould not register a task at all. Run this from an elevated "
            f"PowerShell:\n  powershell -ExecutionPolicy Bypass -File \"{script}\"\n",
            file=sys.stderr,
        )
    return code


def install_systemd() -> int:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / f"{SERVICE_NAME}.service"
    unit_path.write_text(systemd_unit(), encoding="utf-8")
    print(f"wrote {unit_path}")
    if not shutil.which("systemctl"):
        print("systemctl not found -- unit written but not enabled.", file=sys.stderr)
        return 1
    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", SERVICE_NAME],
    ):
        code, out = _run(cmd)
        print(out or f"ok: {' '.join(cmd)}")
        if code != 0:
            return code
    code, out = _run(["loginctl", "enable-linger", os.environ.get("USER", "")])
    print(out or "linger enabled (unit survives logout)")
    return 0


def install_launchd() -> int:
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{LAUNCHD_LABEL}.plist"
    plist_path.write_text(launchd_plist(), encoding="utf-8")
    print(f"wrote {plist_path}")
    code, out = _run(["launchctl", "load", "-w", str(plist_path)])
    print(out or "loaded")
    print("\nNote: this is a LaunchAgent and stops at logout. For an "
          "always-on agent, move it to /Library/LaunchDaemons/ (sudo).")
    return code


def do_install(target: str, *, move: bool = False) -> int:
    if target == "windows":
        return install_windows(move=move)
    if target in ("linux", "wsl"):
        rc = install_systemd()
        if target == "wsl":
            print("\n" + wsl_notes())
        return rc
    if target == "macos":
        return install_launchd()
    print(f"--install is not supported for '{target}'. Use --print.", file=sys.stderr)
    return 2


def do_uninstall(target: str) -> int:
    if target == "windows":
        code, out = _run([
            "powershell", "-NoProfile", "-Command",
            f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false",
        ])
        print(out or f"removed task {TASK_NAME}")
        return code
    if target in ("linux", "wsl"):
        _run(["systemctl", "--user", "disable", "--now", SERVICE_NAME])
        path = Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
        if path.exists():
            path.unlink()
        _run(["systemctl", "--user", "daemon-reload"])
        print("removed systemd unit")
        return 0
    if target == "macos":
        path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        _run(["launchctl", "unload", "-w", str(path)])
        if path.exists():
            path.unlink()
        print("removed launchd agent")
        return 0
    print(f"--uninstall is not supported for '{target}'.", file=sys.stderr)
    return 2


def do_status(target: str) -> int:
    if target == "windows":
        report, problems = format_task_status(registered_task())
        print(report)
        return 1 if problems else 0
    if target in ("linux", "wsl"):
        code, out = _run(["systemctl", "--user", "status", SERVICE_NAME, "--no-pager"])
        print(out)
        return code
    if target == "macos":
        code, out = _run(["launchctl", "list", LAUNCHD_LABEL])
        print(out or f"{LAUNCHD_LABEL} is not loaded")
        return code
    print(f"--status is not supported for '{target}'.", file=sys.stderr)
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Install Kazma as a supervised service.",
        epilog="Platforms: windows, linux, macos, wsl, docker",
    )
    ap.add_argument("--platform", default=None,
                    help="override auto-detection (windows|linux|macos|wsl|docker)")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--print", dest="do_print", action="store_true",
                     help="print the unit for the platform; changes nothing")
    grp.add_argument("--install", action="store_true")
    grp.add_argument("--uninstall", action="store_true")
    grp.add_argument("--status", action="store_true",
                     help="on Windows, compare the registered task with what "
                          "--install registers")
    ap.add_argument("--move", action="store_true",
                    help="with --install on Windows: move a task that starts "
                         "another folder's guard to this folder")
    args = ap.parse_args()
    if args.move and not args.install:
        ap.error("--move goes with --install")

    target = (args.platform or detect_platform()).lower()
    if not args.do_print:
        print(f"platform: {target}   guard: {GUARD}")

    if args.do_print:
        return do_print(target)
    if args.install:
        return do_install(target, move=args.move)
    if args.uninstall:
        return do_uninstall(target)
    return do_status(target)


if __name__ == "__main__":
    raise SystemExit(main())
