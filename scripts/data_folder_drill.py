"""The data-folder drill: Kazma's database moved off C:, then found again (Windows).

What it proves (2026-11-21, AGENTS.md section 21 G3): on a real Windows with
a real PostgreSQL service, ``kazma setup data-folder <X>:\\Kazma`` moves the
database, its WAL archive and base backups onto a second drive (the service's
``-D`` pointed there, the archive written there), and after PostgreSQL is
removed from the machine -- what a reset of Windows does to it -- ``kazma
setup install`` attaches the kept database to a new service instead of making
an empty one.

Run from the host PC against the Hyper-V VM (``KazmaVMs``, by SSH as
``kazma-drill``), as the other drills are:

1. revert the VM to its clean checkpoint, copy one commit and ``uv`` there,
   sync the base packages (``engine_boot_drill.prepare``);
2. give it a second disk (a new VHDX on this PC, attached over SCSI,
   formatted inside the VM);
3. install Kazma with PostgreSQL and its service (Custom, as the restart
   drill does), wait for it, write a mark into its settings (Postgres);
4. ``kazma setup data-folder <X>:\\Kazma -y``; read the service's ``-D``, the
   mark, ``postmaster.pid`` in the new folder, and a WAL segment archived into
   the new archive after ``pg_switch_wal()``;
5. hold Kazma, remove PostgreSQL with its own uninstaller, unattended (the
   service and the programs go, the data on the second drive stays), run
   ``kazma setup install`` again; read the service's ``-D``, the mark and a
   new archived segment once more;
6. detach and delete the second disk, whatever happened.

Kazma's container engine is not drilled here: it needs WSL and a restart on
the VM (about an hour); ``tests/test_data_folder.py`` holds its move on a fake
machine.

Usage (from the checkout, on the host, after the change is committed)::

    .venv/Scripts/python.exe scripts/data_folder_drill.py run [--ref <commit>]

Standard library only. The judging (``judge``) is pure, held by
``tests/test_data_folder_drill.py``.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine_boot_drill as boot  # noqa: E402  (the VM, its checkpoint, prepare)
import restart_drill as restart  # noqa: E402  (the Custom install with PostgreSQL)

#: Where this PC keeps the drill's second disk while it runs.
DISK_DIR = Path(r"G:\Tools\kazma-drill-disks")
#: Dynamic: it takes on this PC only what the VM writes. Above the room a
#: first install asks for in the folder (data_folder.FRESH_FREE_BYTES).
DISK_BYTES = 40 * 2**30
MOVE_TIMEOUT_S = 1800.0

#: Read from the install folder, so the install's .env is the one loaded.
MARK = (
    "import json, sys; from pathlib import Path; "
    "from kazma_core.env_files import load_env_files; loaded = load_env_files(); "
    "assert any(Path(f).parent == Path.cwd() for f in loaded), f'not the install env: {loaded}'; "
    "from kazma_core.config_store import get_config_store; s = get_config_store(); "
    "value = sys.argv[1] if len(sys.argv) > 1 else None; "
    "value and s.set('drill.data_folder.mark', value, category='drill'); "
    "print('MARK ' + json.dumps({'mark': s.get('drill.data_folder.mark')}))"
)

#: The service's command line and what is in the data folder now.
STATE = r"""param([string]$Folder)
$ErrorActionPreference = 'SilentlyContinue'
$qc = (& sc.exe qc postgresql-x64-18) -join "`n"
$found = [regex]::Match($qc, '-D\s+"([^"]+)"')
$archive = Join-Path $Folder 'postgres-18\wal_archive'
'STATE ' + (@{
    data_dir = $found.Groups[1].Value
    service = [bool](Get-Service postgresql-x64-18 -ErrorAction SilentlyContinue)
    pid_in_folder = (Test-Path (Join-Path $Folder 'postgres-18\data\postmaster.pid'))
    archived = @(Get-ChildItem $archive -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^[0-9A-F]{24}$' }).Count
} | ConvertTo-Json -Compress)
"""

#: A new WAL segment archived, as the superuser (its password file is the install's).
SWITCH_WAL = r"""$env:PGPASSWORD = (Get-Content '%(root)s\.kazma\postgres_superuser.pass' -Raw).Trim()
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -w -h 127.0.0.1 -U postgres -d kazma -X -q -c "CREATE TABLE IF NOT EXISTS drill_wal(x int); INSERT INTO drill_wal VALUES (1); SELECT pg_switch_wal();" 2>&1 | Out-Null
Start-Sleep -Seconds 20
"""

#: Run as the install does, detached (an SSH drop must not end it).
#: The command itself is in ``<drill>\<Name>.cmd``: no quoting through WMI
#: and PowerShell.
RUN = r"""param([string]$Name)
$ErrorActionPreference = 'Continue'
$env:PATH = (Join-Path $env:USERPROFILE '.local\bin') + ";$env:PATH"
$env:NO_COLOR = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location '%(root)s'
$start = Get-Date
& cmd.exe /c "`"%(drill)s\$Name.cmd`" > `"%(drill)s\$Name.log`" 2>&1"
$code = $LASTEXITCODE
@{exit = $code; seconds = [int]((Get-Date) - $start).TotalSeconds} | ConvertTo-Json | Set-Content "%(drill)s\$Name.done" -Encoding ascii
"""


def _fill(template: str) -> str:
    return template % {"root": boot.VM_ROOT, "drill": boot.VM_DRILL}


# ── the second disk ──────────────────────────────────────────────────────


def attach_disk(vm: boot.Vm, stamp: str) -> tuple[Path, str]:
    """A new VHDX attached to the VM and formatted there; (its path here, the
    drive letter the VM gave it)."""
    DISK_DIR.mkdir(parents=True, exist_ok=True)
    disk = DISK_DIR / f"data-folder-drill-{stamp}.vhdx"
    got = boot._host_powershell(
        f"New-VHD -Path '{disk}' -SizeBytes {DISK_BYTES} -Dynamic | Out-Null; "
        f"Add-VMHardDiskDrive -VMName '{vm.name}' -ControllerType SCSI -Path '{disk}'; 'attached'")
    if "attached" not in got.stdout:
        raise RuntimeError(f"the second disk was not attached: {(got.stderr or got.stdout).strip()[-400:]}")
    got = vm.ssh("$d = Get-Disk | Where-Object PartitionStyle -eq 'RAW' | Select-Object -First 1; "
                 "if (-not $d) { 'NO RAW DISK'; exit 1 }; "
                 "Initialize-Disk -Number $d.Number -PartitionStyle GPT | Out-Null; "
                 "$p = New-Partition -DiskNumber $d.Number -UseMaximumSize -AssignDriveLetter; "
                 "Format-Volume -DriveLetter $p.DriveLetter -FileSystem NTFS -NewFileSystemLabel KazmaData "
                 "-Confirm:$false | Out-Null; 'LETTER ' + $p.DriveLetter", timeout=300)
    letters = [line.split()[-1] for line in got.stdout.splitlines() if line.startswith("LETTER ")]
    if not letters:
        raise RuntimeError(f"the second disk was not formatted: {(got.stdout + got.stderr).strip()[-400:]}")
    return disk, letters[-1]


def detach_disk(vm: boot.Vm, disk: Path) -> None:
    boot._host_powershell(
        f"Get-VMHardDiskDrive -VMName '{vm.name}' | Where-Object Path -eq '{disk}' | Remove-VMHardDiskDrive; "
        f"Remove-Item -Force '{disk}' -ErrorAction SilentlyContinue")


# ── on the VM ───────────────────────────────────────────────────────────


def run_detached(vm: boot.Vm, work: Path, name: str, command: str, timeout: float) -> dict[str, Any]:
    script = work / "data-folder-run.ps1"
    script.write_text(_fill(RUN), encoding="utf-8")
    vm.copy(script, boot.VM_DRILL + r"\data-folder-run.ps1")
    batch = work / f"{name}.cmd"
    batch.write_text("@echo off\r\n" + command + "\r\n", encoding="ascii")
    vm.copy(batch, boot.VM_DRILL + "\\" + batch.name)
    vm.start_detached(f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File {boot.VM_DRILL}\\data-folder-run.ps1 "
                      f"-Name {name}", boot.VM_ROOT)
    done = boot._wait_for_file(vm, f"{boot.VM_DRILL}\\{name}.done", timeout)
    tail = vm.ssh(f"Get-Content '{boot.VM_DRILL}\\{name}.log' -Tail 60 -Encoding utf8", timeout=60).stdout
    boot._say(f"{name}: exit {done.get('exit')} after {done.get('seconds')} s")
    return {"exit": done.get("exit"), "seconds": done.get("seconds"), "tail": tail[-4000:]}


def mark(vm: boot.Vm, value: str = "") -> str | None:
    got = vm.ssh(f"Set-Location '{boot.VM_ROOT}'; & '{boot.VM_ROOT}\\.venv\\Scripts\\python.exe' -c \"{MARK}\""
                 + (f" {value}" if value else ""), timeout=180)
    said = [line for line in got.stdout.splitlines() if line.startswith("MARK ")]
    if not said:
        raise RuntimeError(f"the mark could not be read: {(got.stdout + got.stderr).strip()[-400:]}")
    return json.loads(said[-1][len("MARK "):]).get("mark")


def state(vm: boot.Vm, work: Path, folder: str) -> dict[str, Any]:
    script = work / "data-folder-state.ps1"
    script.write_text(STATE, encoding="utf-8")
    vm.copy(script, boot.VM_DRILL + r"\data-folder-state.ps1")
    got = vm.ssh(f"& '{boot.VM_DRILL}\\data-folder-state.ps1' -Folder '{folder}'", timeout=120)
    said = [line for line in got.stdout.splitlines() if line.startswith("STATE ")]
    if not said:
        raise RuntimeError(f"the state could not be read: {(got.stdout + got.stderr).strip()[-400:]}")
    return json.loads(said[-1][len("STATE "):])


def switch_wal(vm: boot.Vm) -> None:
    vm.ssh(_fill(SWITCH_WAL), timeout=180)


def guard(vm: boot.Vm, *args: str) -> str:
    got = vm.ssh(f"Set-Location '{boot.VM_ROOT}'; & '{boot.VM_ROOT}\\.venv\\Scripts\\python.exe' "
                 f"scripts\\service\\kazma_guard.py {' '.join(args)}", timeout=900)
    return (got.stdout + got.stderr).strip()[-800:]


# ── the verdict ─────────────────────────────────────────────────────────


@dataclass
class Verdict:
    verdict: str
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _under(path: str, folder: str) -> bool:
    return bool(path) and path.lower().rstrip("\\").startswith(folder.lower().rstrip("\\") + "\\")


def _archived(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return int(after.get("archived") or 0) > int(before.get("archived") or 0)


def judge(folder: str, seeded: str, ev: dict[str, Any]) -> Verdict:
    """``moved`` when the move put the database in *folder*, kept its data,
    and the server archives a new segment into the folder's archive;
    ``passed`` when, after PostgreSQL was removed and set up again, the new
    service runs the kept database and archives there too."""
    after_move, after_reset = ev.get("after_move") or {}, ev.get("after_reset") or {}
    checks = {
        "move_exit_0": (ev.get("move") or {}).get("exit") == 0,
        "service_runs_from_the_folder": _under(str(after_move.get("data_dir") or ""), folder),
        "it_runs_there": bool(after_move.get("pid_in_folder")),
        "the_data_came_along": ev.get("mark_after_move") == seeded,
        "a_new_segment_is_archived_there": _archived(after_move, ev.get("after_move_switch") or {}),
        "postgresql_was_removed": not (ev.get("after_uninstall") or {}).get("service", True),
        "reinstall_exit_0": (ev.get("reinstall") or {}).get("exit") == 0,
        "the_new_service_runs_the_kept_database": _under(str(after_reset.get("data_dir") or ""), folder),
        "the_data_is_still_there": ev.get("mark_after_reset") == seeded,
        "it_archives_there_again": _archived(after_reset, ev.get("after_reset_switch") or {}),
    }
    moved_ok = all(checks[k] for k in list(checks)[:5])
    found_ok = all(checks[k] for k in list(checks)[5:])
    verdict = "passed" if moved_ok and found_ok else "moved_only" if moved_ok else "failed"
    notes = [name for name, ok in checks.items() if not ok]
    return Verdict(verdict, checks, notes)


def judge_fresh(folder: str, seeded: str, ev: dict[str, Any]) -> Verdict:
    """``passed`` when a first install with the folder chosen beforehand put
    the database there (the installer's ``--datadir``), it runs from it,
    keeps what Kazma writes and archives into the folder's archive."""
    after = ev.get("after_install") or {}
    checks = {
        "install_exit_0": (ev.get("install") or {}).get("exit") == 0,
        "service_runs_from_the_folder": _under(str(after.get("data_dir") or ""), folder),
        "it_runs_there": bool(after.get("pid_in_folder")),
        "the_data_is_kept": ev.get("mark") == seeded,
        "a_new_segment_is_archived_there": _archived(after, ev.get("after_switch") or {}),
    }
    notes = [name for name, ok in checks.items() if not ok]
    return Verdict("passed" if not notes else "failed", checks, notes)


def render_report(*, verdict: Verdict, meta: dict[str, Any], evidence_name: str) -> str:
    kind = f" ({meta['kind']})" if meta.get("kind") else ""
    lines = [f"# Data-folder drill{kind}: {verdict.verdict}", "",
             f"Commit {meta.get('commit', '')[:10]}, finished {meta.get('finished')}. Evidence: `{evidence_name}`.",
             "", "| Check | |", "|---|---|"]
    lines += [f"| {name.replace('_', ' ')} | {'yes' if ok else '**no**'} |" for name, ok in verdict.checks.items()]
    lines += ["", "Kazma's container engine was not drilled here (it needs WSL and a restart on the VM);",
              "`tests/test_data_folder.py` holds its move on a fake machine.", ""]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    vm = boot.Vm()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%MZ")
    evidence: dict[str, Any] = {}
    disk: Path | None = None
    with tempfile.TemporaryDirectory(prefix="kazma-data-folder-drill-") as tmp:
        work = Path(tmp)
        try:
            commit = boot.prepare(vm, args.ref, work)
            disk, letter = attach_disk(vm, stamp)
            folder = f"{letter}:\\Kazma"
            evidence.update(commit=commit, folder=folder, install=restart.install(vm, work))
            restart.wait_ready(vm)
            seeded = secrets.token_hex(8)
            evidence["seeded"] = mark(vm, seeded)
            evidence["move"] = run_detached(
                vm, work, "data-folder-move",
                f".venv\\Scripts\\python.exe -m kazma_cli setup data-folder {folder} -y", MOVE_TIMEOUT_S)
            restart.wait_ready(vm)
            evidence["after_move"] = state(vm, work, folder)
            switch_wal(vm)
            evidence["after_move_switch"] = state(vm, work, folder)
            evidence["mark_after_move"] = mark(vm)
            evidence["pause"] = guard(vm, "--pause", "--stop")
            boot._say("Removing PostgreSQL as a reset of Windows does (its uninstaller); its data stays on "
                      f"{letter}:")
            # The EDB uninstaller itself, unattended: it removes the service
            # and the programs and leaves a data folder it did not make.
            evidence["uninstall"] = run_detached(
                vm, work, "data-folder-uninstall",
                r'"C:\Program Files\PostgreSQL\18\uninstall-postgresql.exe" --mode unattended', 1800)
            evidence["after_uninstall"] = state(vm, work, folder)
            evidence["reinstall"] = run_detached(
                vm, work, "data-folder-reinstall",
                ".venv\\Scripts\\python.exe -m kazma_cli setup install --mode custom --with postgres "
                + " ".join(f"--without {p}" for p in restart.WITHOUT) + " -y --no-browser", restart.INSTALL_TIMEOUT_S)
            evidence["resume"] = guard(vm, "--resume")
            restart.wait_ready(vm)
            evidence["after_reset"] = state(vm, work, folder)
            switch_wal(vm)
            evidence["after_reset_switch"] = state(vm, work, folder)
            evidence["mark_after_reset"] = mark(vm)
        finally:
            if disk is not None:
                detach_disk(vm, disk)
    return _report(args, stamp, "move", judge(evidence["folder"], seeded, evidence), evidence)


def fresh(args: argparse.Namespace) -> int:
    """A first install with the folder chosen beforehand: ``kazma setup
    install --data-folder <X>:\\Kazma`` on a clean VM."""
    vm = boot.Vm()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%MZ")
    evidence: dict[str, Any] = {}
    disk: Path | None = None
    with tempfile.TemporaryDirectory(prefix="kazma-data-folder-drill-") as tmp:
        work = Path(tmp)
        try:
            commit = boot.prepare(vm, args.ref, work)
            disk, letter = attach_disk(vm, stamp)
            folder = f"{letter}:\\Kazma"
            evidence.update(commit=commit, folder=folder)
            evidence["install"] = run_detached(
                vm, work, "data-folder-fresh-install",
                ".venv\\Scripts\\python.exe -m kazma_cli setup install --mode custom --with postgres "
                + " ".join(f"--without {p}" for p in restart.WITHOUT)
                + f" --data-folder {folder} -y --no-browser", restart.INSTALL_TIMEOUT_S)
            restart.wait_ready(vm)
            seeded = secrets.token_hex(8)
            evidence["seeded"] = mark(vm, seeded)
            evidence["after_install"] = state(vm, work, folder)
            switch_wal(vm)
            evidence["after_switch"] = state(vm, work, folder)
            evidence["mark"] = mark(vm)
        finally:
            if disk is not None:
                detach_disk(vm, disk)
    return _report(args, stamp, "fresh", judge_fresh(evidence["folder"], seeded, evidence), evidence)


def _report(args: argparse.Namespace, stamp: str, kind: str, verdict: Verdict, evidence: dict[str, Any]) -> int:
    meta = {"kind": kind, "commit": evidence["commit"],
            "finished": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"data_folder_drill_{kind}_{stamp}.json"
    (out / name).write_text(json.dumps({"meta": meta, "evidence": evidence, "verdict": verdict.__dict__},
                                       indent=1), encoding="utf-8", newline="\n")
    report = out / name.replace(".json", ".md")
    report.write_text(render_report(verdict=verdict, meta=meta, evidence_name=name), encoding="utf-8", newline="\n")
    boot._say(f"Verdict: {verdict.verdict}. Report: {report}")
    return 0 if verdict.verdict == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run", help="revert, install, move, remove PostgreSQL, reinstall, judge")
    go.add_argument("--ref", default="HEAD")
    go.add_argument("--out", default=str(ROOT / "docs" / "audits" / "evidence" / "data_folder_drill"))
    first = sub.add_parser("fresh", help="revert, a first install with --data-folder, judge")
    first.add_argument("--ref", default="HEAD")
    first.add_argument("--out", default=str(ROOT / "docs" / "audits" / "evidence" / "data_folder_drill"))
    args = parser.parse_args(argv)
    return fresh(args) if args.command == "fresh" else run(args)


if __name__ == "__main__":
    sys.exit(main())
