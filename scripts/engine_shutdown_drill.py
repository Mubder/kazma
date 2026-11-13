"""The shutdown drill for Kazma's own container engine (Windows).

What it proves: a Windows restart stops the engine's containers cleanly, so
nothing they wrote in their last seconds is lost
(docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md, "a clean stop at a Windows
shutdown"; AGENTS.md section 63). Run from the host PC against the Hyper-V VM
(``KazmaVMs``, reached by SSH as ``kazma-drill``), as the boot drill is:

1. revert the VM to its clean checkpoint, copy one commit of this checkout
   (``git archive``) and ``uv`` there, sync the base packages
   (``engine_boot_drill.prepare``);
2. run ``kazma setup engine -y`` DETACHED, restart Windows when it asks (WSL)
   and run it again, until the engine is set up;
3. start a writer container on the engine: it appends a short numbered line
   every 100 ms with no fsync, as most programs do, to a file of its own for
   each distro boot; on SIGTERM it writes ``TERM``, takes 8 s to stop (as
   Neo4j does) and writes ``DONE``. Restart policy ``unless-stopped``, so it
   comes back at the next boot only if its stop was a daemon's, never a
   ``docker stop``;
4. ``ROUNDS`` times: restart Windows (``shutdown /r /t 0 /f``, as Windows
   Update does), wait for the writer to start again in the new boot, and read
   the file of the boot before -- its NUL bytes, ``TERM`` and ``DONE`` -- and
   Windows' own record: when the shutdown began (event 1074), what the
   ``KazmaEngineShutdown`` service said (Application log), when the event log
   stopped (6006);
5. judge: ``clean`` only when every round's file has ``TERM`` and ``DONE`` and
   no NUL byte, and the writer came back.

Negative control: ``--ref`` a commit before the shutdown service (a8df8cc46,
the engine's Stage 3). Windows ends WSL's VM like a power cut there: no
``TERM`` reaches the writer, and its last writes come back as NUL bytes (the
first measurement, 2026-11-13: 760 NUL bytes). The verdict must be
``unclean``.

Usage (from the checkout, on the host)::

    .venv/Scripts/python.exe scripts/engine_shutdown_drill.py run [--ref <commit>] [--rounds 2]
    .venv/Scripts/python.exe scripts/engine_shutdown_drill.py rounds  # 3-5 on an installed VM

Standard library only. The judging (``parse_files``, ``held_seconds``,
``judge``, ``render_report``) is pure, and held by ``tests/test_engine_shutdown_drill.py``.
"""

from __future__ import annotations

import argparse
import json
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

ROUNDS = 2
WRITER = "kazma-shutdown-drill-writer"
VOLUME = "kazma-shutdown-drill"
IMAGE = "alpine:3.20"
#: The writer's stop, in tenths of a second (8 s: Neo4j logged "Stopping..."
#: and needed longer than WSL gave it, the engine-move drill, 2026-11-13).
STOP_TENTHS = 80
#: The writer (``/drill/writer.sh`` in the container): a file per distro boot
#: (the kernel's boot id), a line every 100 ms with no fsync, and on SIGTERM
#: TERM, an 8 s stop, DONE.
WRITER_SCRIPT = f"""#!/bin/sh
f=/data/w-$(cat /proc/sys/kernel/random/boot_id).log
term() {{
  echo "TERM $(date +%s)" >> "$f"
  i=0
  while [ $i -lt {STOP_TENTHS} ]; do i=$((i+1)); echo "stopping $i" >> "$f"; sleep 0.1; done
  echo "DONE $(date +%s)" >> "$f"
  exit 0
}}
trap term TERM
echo "START $(date +%s)" >> "$f"
n=0
while :; do n=$((n+1)); echo "line $n $(date +%s)" >> "$f"; sleep 0.1; done
"""
#: One line per writer file (``/drill/read.sh``): when last written, name,
#: size, NUL bytes, TERM and DONE seen, last line.
READ_SCRIPT = r"""#!/bin/sh
for f in /data/w-*.log; do
  [ -f "$f" ] || continue
  s=$(wc -c < "$f"); z=$(tr -cd '\000' < "$f" | wc -c)
  t=$(tr -d '\000' < "$f" | grep -c '^TERM '); d=$(tr -d '\000' < "$f" | grep -c '^DONE ')
  l=$(tr -d '\000' < "$f" | tail -n 1)
  echo "FILE $(stat -c %Y "$f") $(basename "$f") $s $z $t $d $l"
done
"""
#: Where the VM's drill folder is seen from the engine (drives at /c).
DRILL_IN_ENGINE = "/c/kazma-drill-probe"

#: Windows' record of the restart, read after the boot.
WINDOWS_RECORD = r"""
$since = (Get-Date).AddMinutes(-30)
Get-WinEvent -FilterHashtable @{LogName='System'; Id=1074,6006,6005; StartTime=$since} -ErrorAction SilentlyContinue |
  Sort-Object TimeCreated | ForEach-Object { "SYS {0} {1}" -f $_.TimeCreated.ToUniversalTime().ToString('o'), $_.Id }
Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='KazmaEngineShutdown'; StartTime=$since} -ErrorAction SilentlyContinue |
  Sort-Object TimeCreated | ForEach-Object { "SVC {0} {1}" -f $_.TimeCreated.ToUniversalTime().ToString('o'), ($_.Message -replace '\s+', ' ') }
"""


# ── reading what the VM said (pure) ─────────────────────────────────────


@dataclass
class WriterFile:
    name: str
    mtime: int
    size: int
    nul_bytes: int
    term: bool
    done: bool
    last_line: str


def parse_files(text: str) -> list[WriterFile]:
    """The writer's files, from ``READ_FILES``' lines, oldest first."""
    files = []
    for line in text.splitlines():
        parts = line.strip().split(" ", 7)
        if len(parts) < 7 or parts[0] != "FILE":
            continue
        try:
            files.append(WriterFile(parts[2], int(parts[1]), int(parts[3]), int(parts[4]), int(parts[5]) > 0,
                                    int(parts[6]) > 0, parts[7] if len(parts) > 7 else ""))
        except ValueError:
            continue
    return sorted(files, key=lambda f: (f.mtime, f.name))


@dataclass
class Round:
    """One restart: the writer's file of the boot that ended, the files seen
    after it, and what Windows recorded."""

    number: int
    before: WriterFile | None
    came_back: bool
    windows: list[str] = field(default_factory=list)
    held_s: float | None = None


def held_seconds(windows: list[str]) -> float | None:
    """From the shutdown's start (1074) to the event log's stop (6006): how
    long Windows took, the service's hold included."""
    started = stopped = None
    for line in windows:
        parts = line.split(" ", 3)
        if len(parts) < 3 or parts[0] != "SYS":
            continue
        try:
            at = datetime.fromisoformat(parts[1].replace("Z", "+00:00"))
        except ValueError:
            continue
        if parts[2] == "1074":
            started, stopped = at, None
        elif parts[2] == "6006" and started is not None and stopped is None:
            stopped = at
    return (stopped - started).total_seconds() if started and stopped else None


@dataclass
class Verdict:
    verdict: str  # clean | unclean | invalid
    reasons: list[str] = field(default_factory=list)


def judge(rounds: list[Round]) -> Verdict:
    """``clean`` only when every round's writer got its stop signal, finished
    its stop (``DONE``), left no NUL byte, and came back after the boot."""
    if not rounds:
        return Verdict("invalid", ["no restart was measured"])
    reasons = []
    for r in rounds:
        if r.before is None:
            return Verdict("invalid", [f"round {r.number}: no writer file of the boot before the restart"])
        f = r.before
        if f.nul_bytes:
            reasons.append(f"round {r.number}: {f.nul_bytes} NUL bytes in {f.name} (writes lost as after a power cut)")
        if not f.term:
            reasons.append(f"round {r.number}: the writer never got its stop signal (no TERM)")
        elif not f.done:
            reasons.append(f"round {r.number}: the writer was cut off while stopping (TERM, no DONE)")
        if not r.came_back:
            reasons.append(f"round {r.number}: the writer did not start again after the boot")
    return Verdict("unclean" if reasons else "clean", reasons)


def render_report(*, label: str, control: bool, verdict: Verdict, rounds: list[Round], meta: dict[str, Any],
                  evidence_name: str) -> str:
    lines = [f"# Engine shutdown drill: {label}", "",
             f"- Commit: `{meta.get('commit', '?')}`; VM `{meta.get('vm')}` from `{meta.get('checkpoint')}`",
             f"- Finished: {meta.get('finished', '?')}",
             f"- {'Negative control: the verdict must be `unclean`.' if control else 'The verdict must be `clean`.'}",
             f"- Verdict: **{verdict.verdict}**", ""]
    lines += [f"  - {reason}" for reason in verdict.reasons]
    lines += ["", "| Round | Writer file of the boot that ended | NUL bytes | TERM | DONE | Last line | Came back "
              "| Shutdown took |", "|---|---|---|---|---|---|---|---|"]
    for r in rounds:
        f = r.before
        took = f"{r.held_s:.1f} s" if r.held_s is not None else "?"
        if f is None:
            lines.append(f"| {r.number} | (none) | | | | | {'yes' if r.came_back else 'no'} | {took} |")
            continue
        lines.append(f"| {r.number} | `{f.name}` ({f.size} bytes) | {f.nul_bytes} | {'yes' if f.term else 'no'} "
                     f"| {'yes' if f.done else 'no'} | `{f.last_line[:40]}` | {'yes' if r.came_back else 'no'} "
                     f"| {took} |")
    lines += ["", "What the KazmaEngineShutdown service said, each round (Application log, UTC):", ""]
    for r in rounds:
        said = [line[4:] for line in r.windows if line.startswith("SVC ")
                and any(word in line for word in ("shutting down", "stopped after", "did not", "no holder",
                                                  "powered off", "failed"))]
        lines.append(f"- Round {r.number}: " + ("; ".join(said[-4:]) if said else "nothing (no service)"))
    lines += ["", f"Readings: `{evidence_name}`.", ""]
    return "\n".join(lines)


# ── the VM ─────────────────────────────────────────────────────────────


INSTALL_ENGINE = r"""param([int]$Round)
$ErrorActionPreference = 'Continue'
$drill = '%(drill)s'
$env:PATH = (Join-Path $env:USERPROFILE '.local\bin') + ";$env:PATH"
$env:NO_COLOR = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location '%(root)s'
$start = Get-Date
& cmd.exe /c ".venv\Scripts\python.exe -m kazma_cli setup engine -y > `"$drill\engine-$Round.log`" 2>&1"
$code = $LASTEXITCODE
@{exit = $code; seconds = [int]((Get-Date) - $start).TotalSeconds} | ConvertTo-Json | Set-Content "$drill\engine-$Round.done" -Encoding ascii
"""


def _docker(vm: boot.Vm, args: str, timeout: float = 300) -> str:
    docker = r"$env:LOCALAPPDATA\Kazma\engine\bin\docker.exe"
    got = vm.ssh(f"& \"{docker}\" --context kazma-engine {args} 2>&1", timeout=timeout)
    return got.stdout


def install_engine(vm: boot.Vm, work: Path) -> list[dict[str, Any]]:
    (work / "install-engine.ps1").write_text(boot._fill(INSTALL_ENGINE), encoding="utf-8")
    vm.copy(work / "install-engine.ps1", boot.VM_DRILL + r"\install-engine.ps1")
    rounds = []
    for number in range(1, 4):
        boot._say(f"kazma setup engine, round {number}, detached")
        vm.start_detached(f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File {boot.VM_DRILL}\\install-engine.ps1 "
                          f"-Round {number}", boot.VM_ROOT)
        done = boot._wait_for_file(vm, f"{boot.VM_DRILL}\\engine-{number}.done", 3600)
        log = vm.ssh(f"Get-Content '{boot.VM_DRILL}\\engine-{number}.log' -Tail 40 -Encoding utf8", timeout=60).stdout
        rounds.append({"round": number, "exit": done.get("exit"), "seconds": done.get("seconds"), "tail": log[-2000:]})
        if "Restart Windows" in log:
            vm.restart()
            continue
        if done.get("exit") == 0:
            return rounds
    raise RuntimeError("kazma setup engine did not finish: " + json.dumps(rounds)[-800:])


def start_writer(vm: boot.Vm, work: Path) -> None:
    for name, text in (("writer.sh", WRITER_SCRIPT), ("read.sh", READ_SCRIPT)):
        (work / name).write_text(text, encoding="utf-8", newline="\n")
        vm.copy(work / name, boot.VM_DRILL + "\\" + name)
    _docker(vm, f"pull {IMAGE}", timeout=600)
    _docker(vm, f"rm -f {WRITER}")
    out = _docker(vm, f"run -d --name {WRITER} --restart unless-stopped --stop-timeout 30 -v {VOLUME}:/data "
                      f"-v {DRILL_IN_ENGINE}:/drill:ro {IMAGE} sh /drill/writer.sh")
    time.sleep(5)
    running = _docker(vm, f"ps --filter name={WRITER} --format '{{{{.Status}}}}'")
    if "Up" not in running:
        raise RuntimeError(f"the writer did not start: {out.strip()[-300:]} {running.strip()[-200:]}")


def writer_files(vm: boot.Vm) -> list[WriterFile]:
    return parse_files(_docker(vm, f"run --rm -v {VOLUME}:/data -v {DRILL_IN_ENGINE}:/drill:ro {IMAGE} "
                                   "sh /drill/read.sh"))


def measure_rounds(vm: boot.Vm, count: int) -> list[Round]:
    rounds = []
    for number in range(1, count + 1):
        time.sleep(20)  # the writer writes a while in this boot
        seen = {f.name for f in writer_files(vm)}
        boot._say(f"Round {number}: restarting Windows (as Windows Update does)")
        vm.restart()
        after: list[WriterFile] = []
        for _ in range(30):  # the writer starts again with the engine
            after = writer_files(vm)
            if any(f.name not in seen for f in after):
                break
            time.sleep(10)
        new = [f for f in after if f.name not in seen]
        old = [f for f in after if f.name in seen]
        record = vm.ssh(WINDOWS_RECORD, timeout=120).stdout.splitlines()
        r = Round(number, old[-1] if old else None, bool(new), [line.strip() for line in record if line.strip()])
        r.held_s = held_seconds(r.windows)
        boot._say(f"  {r.before.name if r.before else '?'}: NUL {r.before.nul_bytes if r.before else '?'}, "
                  f"TERM {r.before.term if r.before else '?'}, DONE {r.before.done if r.before else '?'}, "
                  f"back {r.came_back}, shutdown took {r.held_s}")
        rounds.append(r)
    return rounds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="what", required=True)
    run = sub.add_parser("run", help="revert, set the engine up, restart Windows ROUNDS times, judge")
    run.add_argument("--ref", default="HEAD", help="the commit of this checkout to install")
    run.add_argument("--control", action="store_true", help="a commit without the clean stop: must be unclean")
    again = sub.add_parser("rounds", help="steps 3-5 on a VM this drill set up")
    again.add_argument("--control", action="store_true")
    for one in (run, again):
        one.add_argument("--rounds", type=int, default=ROUNDS)
        one.add_argument("--label", default="")
        one.add_argument("--out", type=Path, default=ROOT / "docs" / "audits" / "evidence")
    options = parser.parse_args(argv)
    vm = boot.Vm()
    meta: dict[str, Any] = {"vm": boot.VM_NAME, "checkpoint": boot.CHECKPOINT}
    with tempfile.TemporaryDirectory(prefix="engine-shutdown-drill-") as tmp:
        if options.what == "run":
            meta["commit"] = boot.prepare(vm, options.ref, Path(tmp))
            meta["install"] = install_engine(vm, Path(tmp))
        else:
            vm.wait_ssh()
        start_writer(vm, Path(tmp))
        rounds = measure_rounds(vm, options.rounds)
    verdict = judge(rounds)
    label = options.label or ("control" if options.control else "fix")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%MZ")
    evidence = f"engine_shutdown_drill_{label}_{stamp}.json"
    meta["finished"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    options.out.mkdir(parents=True, exist_ok=True)
    (options.out / evidence).write_text(json.dumps(
        {"label": label, "control": options.control, "meta": meta, "verdict": verdict.__dict__,
         "rounds": [{**r.__dict__, "before": r.before.__dict__ if r.before else None} for r in rounds]},
        indent=1), encoding="utf-8", newline="\n")
    (options.out / evidence.replace(".json", ".md")).write_text(
        render_report(label=label, control=options.control, verdict=verdict, rounds=rounds, meta=meta,
                      evidence_name=evidence), encoding="utf-8", newline="\n")
    boot._say(f"Verdict: {verdict.verdict}. " + "; ".join(verdict.reasons))
    expected = "unclean" if options.control else "clean"
    return 0 if verdict.verdict == expected else 1


if __name__ == "__main__":
    sys.exit(main())
