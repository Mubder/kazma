"""The restart drill: Kazma stops cleanly when Windows restarts (Windows).

What it proves (2026-11-20, AGENTS.md section 63, "A Windows shutdown stops
Kazma first"): a Windows restart, made as Windows Update makes one, gives
Kazma's server its own graceful stop -- it records that it stopped
(``system.lifecycle.last_shutdown_epoch``), so the next start card says the
last run stopped cleanly -- because the ``KazmaEngineShutdown`` service tells
it at Windows' preshutdown notice, before the database service stops.

Run from the host PC against the Hyper-V VM (``KazmaVMs``, reached by SSH as
``kazma-drill``), as the engine drills are:

1. revert the VM to its clean checkpoint, copy one commit of this checkout
   (``git archive``) and ``uv`` there, sync the base packages
   (``engine_boot_drill.prepare``);
2. install Kazma with PostgreSQL and its service only (Custom: every other
   part left out, no containers), detached;
3. wait for Kazma to answer; read the boot it recorded;
4. restart Windows (``shutdown /r /t 0 /f``) and wait for Kazma to answer
   in the new boot;
5. read what was recorded: the stop stamp in Kazma's settings, Kazma's log
   lines about the stop, what the ``KazmaEngineShutdown`` service said
   (Application log) and when Windows stopped (event 6006);
6. judge: ``clean`` only when the stop stamp falls between the boot before
   the restart and the moment Windows stopped, and the server logged its own
   stop.

Negative control: ``--ref`` a commit before the fix (9b96c2b8c, 20 Nov). The
service there knows nothing of Kazma, the guard is ended by Task Scheduler
and the server is cut off: no stop stamp, verdict ``unclean``.

Usage (from the checkout, on the host)::

    .venv/Scripts/python.exe scripts/restart_drill.py run [--ref <commit>] [--label fix|control]

Standard library only. The judging (``judge``, ``render_report``) is pure, and
held by ``tests/test_restart_drill.py``.
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

#: Every part a Windows Express install holds but the service: left out so
#: the drill installs Kazma, PostgreSQL and the service alone (no containers,
#: no 4 GB model).
WITHOUT = ("meaning_search", "documents", "tests", "code_index", "databases", "push", "metrics", "ocr",
           "neural_ocr", "office", "malware_scan", "fonts", "node", "docker", "private_search", "graph_neo4j")
INSTALL_TIMEOUT_S = 2 * 3600.0
READY_TIMEOUT_S = 900.0
#: A stop stamp this long after Windows' event log stopped is not this stop's.
STAMP_SLACK_S = 5.0

RUN_INSTALL = r"""param([int]$Round)
$ErrorActionPreference = 'Continue'
$drill = '%(drill)s'
$env:PATH = (Join-Path $env:USERPROFILE '.local\bin') + ";$env:PATH"
$env:NO_COLOR = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location '%(root)s'
$start = Get-Date
& cmd.exe /c ".venv\Scripts\python.exe -m kazma_cli setup install --mode custom --with postgres %(without)s -y --no-browser > `"$drill\restart-install-$Round.log`" 2>&1"
$code = $LASTEXITCODE
@{exit = $code; seconds = [int]((Get-Date) - $start).TotalSeconds} | ConvertTo-Json | Set-Content "$drill\restart-install-$Round.done" -Encoding ascii
"""

#: Run from the install folder, so the install's .env is the one loaded
#: (engine_boot_drill.STRICT_PROFILE's lesson).
LIFECYCLE = (
    "import json; from pathlib import Path; "
    "from kazma_core.env_files import load_env_files; loaded = load_env_files(); "
    "assert any(Path(f).parent == Path.cwd() for f in loaded), f'not the install env: {loaded}'; "
    "from kazma_core.config_store import get_config_store; s = get_config_store(); "
    "print('LIFECYCLE ' + json.dumps({'boot': s.get('system.lifecycle.last_boot_epoch'), "
    "'stop': s.get('system.lifecycle.last_shutdown_epoch')}))"
)

#: What Windows and the service recorded since *since* (epoch seconds), and
#: Kazma's log lines about stopping, as one JSON line.
EVIDENCE = r"""param([double]$Since)
$ErrorActionPreference = 'SilentlyContinue'
$from = [DateTimeOffset]::FromUnixTimeSeconds([long]$Since).LocalDateTime
function Epoch($t) { [DateTimeOffset]::new($t).ToUnixTimeMilliseconds() / 1000.0 }
$system = @(Get-WinEvent -FilterHashtable @{LogName='System'; Id=1074,6006,6005; StartTime=$from} |
    ForEach-Object { @{id = $_.Id; at = (Epoch $_.TimeCreated)} })
$service = @(Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='KazmaEngineShutdown'; StartTime=$from} |
    ForEach-Object { @{at = (Epoch $_.TimeCreated); text = $_.Message} })
$log = Join-Path '%(root)s' '.kazma\kazma.log'
$lines = @()
if (Test-Path $log) {
    $lines = @(Select-String -Path $log -Pattern 'shutdown-notice|\[shutdown\] the stop took|Shutdown signal sent|last_shutdown_epoch|last_boot_epoch' |
        ForEach-Object { $_.Line })
}
'EVIDENCE ' + (@{system = $system; service = $service; log = $lines} | ConvertTo-Json -Depth 4 -Compress)
"""


def _fill(template: str) -> str:
    without = " ".join(f"--without {part}" for part in WITHOUT)
    return template % {"root": boot.VM_ROOT, "drill": boot.VM_DRILL, "without": without}


# ── judging (pure) ──────────────────────────────────────────────────────


@dataclass
class Verdict:
    verdict: str  # clean | unclean | invalid
    reasons: list[str] = field(default_factory=list)
    held_s: float | None = None  # how long Windows waited after the restart was asked


def _log_message(line: str) -> tuple[float | None, str]:
    """A kazma.log line's time and message (JSON lines; a plain line as is)."""
    try:
        record = json.loads(line)
    except ValueError:
        return None, line
    stamp = record.get("timestamp") or ""
    try:
        at = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        at = None
    return at, str(record.get("message") or "")


def judge(before: dict[str, Any], after: dict[str, Any], evidence: dict[str, Any]) -> Verdict:
    """*before*: the lifecycle stamps read before the restart and the time it
    was asked (``asked``). *after*: the stamps read in the new boot.
    *evidence*: Windows' and the service's events and Kazma's log lines."""
    reasons: list[str] = []
    asked = float(before.get("asked") or 0)
    stopped = [float(e["at"]) for e in evidence.get("system") or [] if e.get("id") == 6006 and float(e["at"]) >= asked]
    if not stopped:
        return Verdict("invalid", ["Windows' event log never stopped after the restart was asked (no event 6006)"])
    windows_stopped = min(stopped)
    held = round(windows_stopped - asked, 1)
    prior_boot = float(before.get("boot") or 0)
    stamp = after.get("stop")
    if not after.get("boot") or float(after["boot"]) <= windows_stopped:
        return Verdict("invalid", ["Kazma did not record a boot after the restart"], held)
    clean_stamp = stamp is not None and prior_boot <= float(stamp) <= windows_stopped + STAMP_SLACK_S
    if clean_stamp:
        reasons.append(f"the server recorded its stop {float(stamp) - asked:.1f} s after the restart was asked")
    else:
        reasons.append("no stop stamp from this restart: the server never ran its stop "
                       f"(stamp {stamp!r}, boot before {prior_boot!r})")
    messages = [_log_message(line) for line in evidence.get("log") or []]
    own_stop = [m for at, m in messages if at is not None and asked <= at <= windows_stopped + STAMP_SLACK_S
                and m.startswith("[shutdown] the stop took")]
    if own_stop:
        reasons.append("the server logged its stop: " + own_stop[-1][:160])
    else:
        reasons.append("the server logged no stop of its own before Windows stopped")
    said = [str(e.get("text") or "") for e in evidence.get("service") or [] if float(e.get("at") or 0) >= asked]
    told = [s for s in said if "Kazma stopped after" in s]
    reasons.append("the KazmaEngineShutdown service said: " + (told[-1] if told else
                                                                ("; ".join(said)[-200:] or "nothing")))
    return Verdict("clean" if clean_stamp and own_stop else "unclean", reasons, held)


def render_report(*, label: str, verdict: Verdict, meta: dict[str, Any], evidence_name: str) -> str:
    lines = [
        f"# Restart drill: {label}",
        "",
        f"- Verdict: **{verdict.verdict}**",
        f"- Commit: `{meta.get('commit', '')[:12]}`",
        f"- Windows held the restart for: {verdict.held_s} s" if verdict.held_s is not None else
        "- Windows held the restart for: unknown",
        f"- Finished: {meta.get('finished', '')}",
        "",
        "## What was read",
        "",
        *[f"- {reason}" for reason in verdict.reasons],
        "",
        f"Evidence: `{evidence_name}`.",
        "",
    ]
    return "\n".join(lines)


# ── on the VM ───────────────────────────────────────────────────────────


def install(vm: boot.Vm, work: Path) -> dict[str, Any]:
    script = work / "restart-install.ps1"
    script.write_text(_fill(RUN_INSTALL), encoding="utf-8")
    vm.copy(script, boot.VM_DRILL + r"\restart-install.ps1")
    boot._say("Installing Kazma with PostgreSQL and its service (Custom), detached")
    vm.start_detached(f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File {boot.VM_DRILL}\\restart-install.ps1 "
                      "-Round 1", boot.VM_ROOT)
    done = boot._wait_for_file(vm, f"{boot.VM_DRILL}\\restart-install-1.done", INSTALL_TIMEOUT_S)
    tail = vm.ssh(f"Get-Content '{boot.VM_DRILL}\\restart-install-1.log' -Tail 40 -Encoding utf8", timeout=60).stdout
    boot._say(f"Install: exit {done.get('exit')} after {done.get('seconds')} s")
    return {"exit": done.get("exit"), "seconds": done.get("seconds"), "tail": tail[-3000:]}


def wait_ready(vm: boot.Vm, *, timeout: float = READY_TIMEOUT_S) -> dict[str, Any]:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        got = vm.ssh("try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 http://127.0.0.1:9090/health/live)"
                     ".Content } catch { 'down' }", timeout=60)
        text = got.stdout.strip()
        if text.startswith("{"):
            return json.loads(text.splitlines()[-1])
        time.sleep(15)
    raise RuntimeError(f"Kazma did not answer within {int(timeout)} s")


def lifecycle(vm: boot.Vm) -> dict[str, Any]:
    got = vm.ssh(f"Set-Location '{boot.VM_ROOT}'; & '{boot.VM_ROOT}\\.venv\\Scripts\\python.exe' -c \"{LIFECYCLE}\"",
                 timeout=180)
    said = [line for line in got.stdout.splitlines() if line.startswith("LIFECYCLE ")]
    if not said:
        raise RuntimeError(f"the lifecycle stamps could not be read: {(got.stdout + got.stderr).strip()[-400:]}")
    return json.loads(said[-1][len("LIFECYCLE "):])


def evidence(vm: boot.Vm, since: float, work: Path) -> dict[str, Any]:
    script = work / "restart-evidence.ps1"
    script.write_text(_fill(EVIDENCE), encoding="utf-8")
    vm.copy(script, boot.VM_DRILL + r"\restart-evidence.ps1")
    got = vm.ssh(f"& '{boot.VM_DRILL}\\restart-evidence.ps1' -Since {int(since) - 60}", timeout=180)
    said = [line for line in got.stdout.splitlines() if line.startswith("EVIDENCE ")]
    if not said:
        raise RuntimeError(f"the evidence could not be read: {(got.stdout + got.stderr).strip()[-400:]}")
    return json.loads(said[-1][len("EVIDENCE "):])


def vm_now(vm: boot.Vm) -> float:
    got = vm.ssh("[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()", timeout=60)
    return int(got.stdout.strip().splitlines()[-1]) / 1000.0


def run(args: argparse.Namespace) -> int:
    vm = boot.Vm()
    with tempfile.TemporaryDirectory(prefix="kazma-restart-drill-") as tmp:
        work = Path(tmp)
        commit = boot.prepare(vm, args.ref, work)
        meta: dict[str, Any] = {"commit": commit, "ref": args.ref, "install": install(vm, work)}
        meta["first_boot"] = wait_ready(vm)
        time.sleep(30)  # the first boot's stamps and the notice's connection
        before = lifecycle(vm)
        before["asked"] = vm_now(vm)  # the VM's own clock: it lags the host
        boot._say("Restarting Windows as an update does (shutdown /r /t 0 /f)")
        vm.restart()
        meta["second_boot"] = wait_ready(vm)
        time.sleep(30)
        after = lifecycle(vm)
        found = evidence(vm, before["asked"], work)
    verdict = judge(before, after, found)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%MZ")
    meta["finished"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"restart_drill_{args.label}_{stamp}.json"
    (out / name).write_text(json.dumps({"label": args.label, "meta": meta, "before": before, "after": after,
                                        "evidence": found, "verdict": verdict.__dict__}, indent=1),
                            encoding="utf-8", newline="\n")
    report = out / name.replace(".json", ".md")
    report.write_text(render_report(label=args.label, verdict=verdict, meta=meta, evidence_name=name),
                      encoding="utf-8", newline="\n")
    boot._say(f"Verdict: {verdict.verdict}. Report: {report}")
    return 0 if verdict.verdict == "clean" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run", help="revert, install, restart Windows, judge")
    go.add_argument("--ref", default="HEAD")
    go.add_argument("--label", default="fix")
    go.add_argument("--out", default=str(ROOT / "docs" / "audits" / "evidence" / "restart_drill"))
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
