"""Kazma's Windows installer, drilled on a clean Windows VM (REMAINING 2.7).

    python scripts/windows_installer_drill.py --installer dist/kazma-0.21.2-windows-setup.exe --out <dir>

From this PC (the host), on the Hyper-V VM ``KazmaVMs`` reached over SSH
(``engine_boot_drill.Vm``): the VM is reverted to a clean checkpoint, the
installer copied over and run as Windows would run it for a person who
pressed Next (``/VERYSILENT``: Express, the default, every step otherwise as
in the wizard). When setup asks for a restart (WSL) the VM restarts and
setup is run again as its sign-in entry would. Then, checked from the VM:

- the folder is a release install (its marker names the version);
- Kazma answers ``/health/live`` on its port;
- ``kazma update --check`` calls it a release install;
- the Start-menu shortcuts are there, and Apps lists Kazma;
- the uninstaller removes the service and Apps' entry, closes the port and
  keeps the data folder and ``.env``.

The report (``report.json`` and the verdict) goes to ``--out``. The default
checkpoint has no nested virtualization, so no container part can install
and no restart is asked; ``--checkpoint`` names the one with it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_boot_drill import Vm, _ps_quote, _say  # noqa: E402

CHECKPOINT = "clean Windows + SSH (2026-11-08)"
PUBLIC = r"C:\Users\Public"
SETUP_EXE = PUBLIC + r"\kazma-setup.exe"
INSTALL_TIMEOUT_S = 3 * 3600.0
#: Where the installer puts Kazma for the VM's account ({localappdata}\Programs\Kazma).
APP = r"$env:LOCALAPPDATA\Programs\Kazma"

RUNNER = r'''
param([string]$Exe, [string]$Done, [string]$Log)
$start = Get-Date
# Not -Wait: it waits for every process the installer started, and setup
# leaves Kazma's service running (the first drill waited 3 hours for it).
$p = Start-Process -FilePath $Exe -ArgumentList $Log -PassThru
$p.WaitForExit()
$runonce = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -ErrorAction SilentlyContinue).KazmaSetup
@{ exit = $p.ExitCode; seconds = [int]((Get-Date) - $start).TotalSeconds; restart = [bool]$runonce } |
  ConvertTo-Json -Compress | Set-Content -LiteralPath $Done -Encoding utf8
'''


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    started: str
    installer: str
    checkpoint: str
    rounds: list[dict] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)


def _run_detached(vm: Vm, exe: str, args: str, tag: str) -> dict:
    """*exe* with *args* on the VM outside the SSH session; its exit, time and
    whether it left a restart entry, once it ends."""
    done = f"{PUBLIC}\\kazma-{tag}.done"
    vm.ssh(f"Remove-Item -LiteralPath {_ps_quote(done)} -ErrorAction SilentlyContinue", timeout=60)
    vm.ssh(f"Set-Content -LiteralPath '{PUBLIC}\\kazma-run.ps1' -Encoding utf8 -Value {_ps_quote(RUNNER)}",
           timeout=60)
    vm.start_detached(f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File {PUBLIC}\\kazma-run.ps1 "
                      f"-Exe \"{exe}\" -Done \"{done}\" -Log \"{args}\"", PUBLIC)
    start = time.monotonic()
    while time.monotonic() - start < INSTALL_TIMEOUT_S:
        time.sleep(60)
        try:
            got = vm.ssh(f"if (Test-Path {_ps_quote(done)}) {{ Get-Content {_ps_quote(done)} -Raw }}", timeout=60)
        except Exception:  # noqa: BLE001 - the VM may be busy; ask again
            continue
        text = got.stdout.strip().lstrip("\ufeff")
        if text.startswith("{"):
            return json.loads(text)
        minutes = int((time.monotonic() - start) // 60)
        if minutes % 10 == 0:
            _say(f"  still running ({minutes} min)")
    raise RuntimeError(f"{tag} did not finish in {int(INSTALL_TIMEOUT_S)} s")


def install(vm: Vm, report: Report, *, already_ran: bool = False) -> None:
    if already_ran:
        # The installer ran in an earlier drill (--continue): did it leave a restart entry?
        restart = vm.ssh("[bool](Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce' "
                         "-ErrorAction SilentlyContinue).KazmaSetup", timeout=60).stdout.strip() == "True"
        result = {"exit": None, "seconds": None, "restart": restart}
    else:
        _say("Running the installer (/VERYSILENT: Express, as the wizard's defaults)")
        result = _run_detached(vm, SETUP_EXE,
                               f"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG={PUBLIC}\\kazma-setup.log", "install")
    report.rounds.append({"step": "installer", **result})
    _say(f"Installer: exit {result['exit']} after {result['seconds']} s"
         f"{' -- restart asked (WSL)' if result.get('restart') else ''}")
    if result.get("restart"):
        vm.restart()
        # The RunOnce entry runs at the next sign-in; the drill runs it itself.
        result = _run_detached(vm, f"{APP_EXPANDED}\\setup.cmd", "-Mode express -Yes", "setup")
        report.rounds.append({"step": "setup after restart", **result})
        _say(f"Setup after the restart: exit {result['exit']} after {result['seconds']} s")


APP_EXPANDED = ""  # filled from the VM in main()


def check(vm: Vm, report: Report) -> None:
    app = APP_EXPANDED
    py = f"{app}\\.venv\\Scripts\\python.exe"
    marker = vm.ssh(f"Get-Content -LiteralPath '{app}\\.kazma-release.json' -Raw -ErrorAction SilentlyContinue",
                    timeout=60).stdout.strip().lstrip("\ufeff")
    try:
        version = json.loads(marker).get("version", "")
    except ValueError:
        version = ""
    report.checks.append(Check("a release install (its marker names the version)", bool(version), version))

    port = vm.ssh(f"$l = Select-String -LiteralPath '{app}\\.env' -Pattern '^KAZMA_PORT=(\\d+)' -ErrorAction "
                  "SilentlyContinue | Select-Object -Last 1; if ($l) { $l.Matches[0].Groups[1].Value } else { 9090 }",
                  timeout=60).stdout.strip() or "9090"
    live = ""
    for _ in range(20):
        live = vm.ssh(f"try {{ (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 "
                      f"http://127.0.0.1:{port}/health/live).StatusCode }} catch {{ 'no answer' }}",
                      timeout=60).stdout.strip()
        if live == "200":
            break
        time.sleep(30)
    report.checks.append(Check(f"Kazma answers /health/live on port {port}", live == "200", live))

    upd = vm.ssh(f"Set-Location -LiteralPath '{app}'; & '{py}' -m kazma_cli update --check 2>&1 | Out-String",
                 timeout=300).stdout
    report.checks.append(Check("kazma update calls it a release install", "Install type: release" in upd,
                               upd.strip()[-400:]))

    links = vm.ssh("Get-ChildItem \"$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\Kazma\" "
                   "-ErrorAction SilentlyContinue | ForEach-Object Name", timeout=60).stdout.split()
    report.checks.append(Check("the Start-menu shortcuts are there", any("Kazma.lnk" == n for n in links),
                               " ".join(links)))

    apps = _apps_entry(vm)
    report.checks.append(Check("Apps lists Kazma", bool(apps), apps))

    _say("Uninstalling")
    uninstaller = f"{app}\\unins000.exe"
    result = _run_detached(vm, uninstaller, "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART", "uninstall")
    report.rounds.append({"step": "uninstall", **result})
    time.sleep(30)
    task = vm.ssh("schtasks /Query /TN KazmaAgent 2>&1 | Out-String", timeout=60).stdout
    report.checks.append(Check("the uninstaller removed the service task", "KazmaAgent" not in task
                               or "cannot find" in task.lower(), task.strip()[-200:]))
    closed = vm.ssh(f"try {{ (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "
                    f"http://127.0.0.1:{port}/health/live).StatusCode }} catch {{ 'closed' }}", timeout=60).stdout.strip()
    report.checks.append(Check("the port is closed after uninstalling", closed == "closed", closed))
    kept = vm.ssh(f"(Test-Path -LiteralPath '{app}\\kazma-data'), (Test-Path -LiteralPath '{app}\\.env')",
                  timeout=60).stdout.split()
    report.checks.append(Check("the data folder and .env are kept", kept == ["True", "True"], " ".join(kept)))
    report.checks.append(Check("Apps no longer lists Kazma", not _apps_entry(vm), _apps_entry(vm)))


def _apps_entry(vm: Vm) -> str:
    return vm.ssh("Get-ChildItem 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall', "
                  "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall' -ErrorAction SilentlyContinue | "
                  "Get-ItemProperty | Where-Object DisplayName -eq 'Kazma' | ForEach-Object DisplayVersion",
                  timeout=60).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    global APP_EXPANDED
    # The VM's answers hold box-drawing characters: a log file redirected on
    # Windows is cp1252, and the first drill's printing failed on them.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--installer", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--checkpoint", default=CHECKPOINT)
    parser.add_argument("--no-revert", action="store_true", help="use the VM as it is")
    parser.add_argument("--continue", dest="cont", action="store_true",
                        help="the installer already ran on the VM: restart if it asked, then check")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    vm = Vm()
    report = Report(started=datetime.now(UTC).isoformat(timespec="seconds"),
                    installer=Path(args.installer).name, checkpoint=args.checkpoint)
    if not (args.no_revert or args.cont):
        _say(f"Reverting {vm.name} to '{args.checkpoint}'")
        vm.revert(args.checkpoint)
    vm.wait_ssh()
    APP_EXPANDED = vm.ssh(f"\"{APP}\"", timeout=60).stdout.strip()
    if not args.cont:
        _say(f"Copying {Path(args.installer).name} to the VM")
        vm.copy(Path(args.installer), SETUP_EXE)
    try:
        install(vm, report, already_ran=args.cont)
        check(vm, report)
    except Exception as exc:  # noqa: BLE001 - recorded in the report
        report.checks.append(Check("the drill ran to its end", False, f"{type(exc).__name__}: {exc}"))
    data = {"started": report.started, "installer": report.installer, "checkpoint": report.checkpoint,
            "passed": report.passed, "rounds": report.rounds,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in report.checks]}
    (out / "report.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    for c in report.checks:
        _say(f"{'PASS' if c.ok else 'FAIL'}  {c.name}  {c.detail[:160]}")
    _say(f"Verdict: {'PASSED' if report.passed else 'FAILED'}  ({out / 'report.json'})")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
