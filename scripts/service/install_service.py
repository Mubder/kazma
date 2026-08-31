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
generated from any platform and committed or reviewed.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "scripts" / "service" / "kazma_guard.py"

TASK_NAME = "KazmaAgent"
SERVICE_NAME = "kazma"
LAUNCHD_LABEL = "com.kazma.agent"


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
    if elevated:
        triggers = """@(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn)
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
        triggers = "@( (New-ScheduledTaskTrigger -AtLogOn) )"
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

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew

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


def install_windows() -> int:
    """Register the task, degrading to a user-level one when not elevated.

    A failed install that leaves nothing behind is worse than a partial one
    that works until someone reboots -- so when elevation is missing we
    register what we can and say plainly what it does not cover.
    """
    svc_dir = REPO_ROOT / "scripts" / "service"
    elevated = _is_elevated()

    script = svc_dir / "install_windows_task.ps1"
    script.write_text(windows_task_ps1(elevated=True), encoding="utf-8")

    if elevated:
        code, out = _run([
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script),
        ])
        print(out)
        return code

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


def do_install(target: str) -> int:
    if target == "windows":
        return install_windows()
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
        code, out = _run([
            "powershell", "-NoProfile", "-Command",
            f"Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue "
            f"| Select-Object TaskName,State | Format-List | Out-String",
        ])
        print(out.strip() or f"task '{TASK_NAME}' is not registered")
        return code
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
    grp.add_argument("--status", action="store_true")
    args = ap.parse_args()

    target = (args.platform or detect_platform()).lower()
    if not args.do_print:
        print(f"platform: {target}   guard: {GUARD}")

    if args.do_print:
        return do_print(target)
    if args.install:
        return do_install(target)
    if args.uninstall:
        return do_uninstall(target)
    return do_status(target)


if __name__ == "__main__":
    raise SystemExit(main())
