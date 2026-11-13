"""Drill ``kazma setup postgres --upgrade`` on a real Windows machine (the
drill VM, as ``engine_boot_drill.py`` drives it).

1. The VM reverted to its clean checkpoint; *--from* (a commit whose Postgres
   is 16, default ``origin/main``) copied there and set up with ``kazma setup
   postgres -y``: Postgres 16 as a Windows service, Kazma's role, ``.env``,
   WAL archiving.
2. Kazma-shaped data written as ``kazma_app`` (a settings table with the boot
   stamp, a table with a sequence, a trigger and its function, Arabic text).
3. The code of *--to* (default ``HEAD``) put over the install's packages,
   and ``kazma setup postgres
   --upgrade -y`` run (detached, as a person would run it).
4. Read back: the record (18, the old server under ``previous``), both
   services (18 running on 5432 and at boot; 16 stopped, not at boot), the
   data (every table and sequence as written), ``kazma_app`` signing in with
   the unchanged ``.env``, the new WAL archive taking a segment, the first
   base backup, and a second ``--upgrade`` with nothing to do.

    .venv\\Scripts\\python.exe scripts/postgres_upgrade_drill.py [--from REF]

Writes ``docs/audits/evidence/postgres_upgrade_drill_<date>.json``. Run it
from the dev checkout on the PC that hosts the VM.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_engine_boot_drill", ROOT / "scripts" / "engine_boot_drill.py")
boot = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = boot
_spec.loader.exec_module(boot)

VM_ROOT, VM_DRILL = boot.VM_ROOT, boot.VM_DRILL

SEED = r'''
import os, sys
from pathlib import Path
from kazma_core.env_files import load_env_files
load_env_files()
import psycopg
url = os.environ["KAZMA_DATABASE_URL"]
with psycopg.connect(url, autocommit=True) as conn:
    conn.execute("""
CREATE TABLE IF NOT EXISTS kazma_settings (key text PRIMARY KEY, value text);
INSERT INTO kazma_settings VALUES ('system.lifecycle.last_boot_epoch', '1790000000.5') ON CONFLICT DO NOTHING;
CREATE TABLE kazma_chat_sessions (id serial PRIMARY KEY, title text);
INSERT INTO kazma_chat_sessions (title) SELECT 'chat ' || g FROM generate_series(1, 5000) g;
INSERT INTO kazma_chat_sessions (title) VALUES ('محادثة عربية');
CREATE TABLE document_audit_events (id bigserial PRIMARY KEY, note text);
CREATE FUNCTION kazma_document_audit_guard() RETURNS trigger LANGUAGE plpgsql AS
  $$ BEGIN RAISE EXCEPTION 'append only'; END $$;
CREATE TRIGGER document_audit_guard BEFORE UPDATE ON document_audit_events
  FOR EACH ROW EXECUTE FUNCTION kazma_document_audit_guard();
INSERT INTO document_audit_events (note) SELECT 'event ' || g FROM generate_series(1, 700) g;
""")
    print("major", conn.execute("SHOW server_version_num").fetchone()[0])
'''

READ = r'''
import json, os, subprocess
from kazma_core.env_files import load_env_files
load_env_files()
import psycopg
from kazma_core import postgres_service as pgs
url = os.environ["KAZMA_DATABASE_URL"]
out = {"record": pgs.read_record()}
with psycopg.connect(url, autocommit=True) as conn:
    out["server_version_num"] = conn.execute("SHOW server_version_num").fetchone()[0]
    out["rows"] = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                   for t in ("kazma_settings", "kazma_chat_sessions", "document_audit_events")}
    out["arabic"] = conn.execute("SELECT title FROM kazma_chat_sessions WHERE id = 5001").fetchone()[0]
    out["next_id"] = conn.execute("SELECT nextval('kazma_chat_sessions_id_seq')").fetchone()[0]
    try:
        conn.execute("UPDATE document_audit_events SET note = 'x' WHERE id = 1")
        out["trigger"] = "missing"
    except psycopg.Error:
        out["trigger"] = "refused the update"
# The archiver's numbers and a segment switch, as the server's superuser.
from pathlib import Path
from urllib.parse import quote
port = int(url.rsplit(":", 1)[1].split("/")[0])
su = Path(".kazma/postgres_superuser.pass").read_text(encoding="utf-8").strip()
admin = f"postgresql://postgres:{quote(su, safe='')}@127.0.0.1:{port}/postgres"
with psycopg.connect(admin, autocommit=True) as conn:
    before = conn.execute("SELECT archived_count FROM pg_stat_archiver").fetchone()[0]
    conn.execute("CREATE TABLE IF NOT EXISTS wal_probe AS SELECT g FROM generate_series(1, 20000) g")
    conn.execute("DROP TABLE wal_probe")
    conn.execute("SELECT pg_switch_wal()")
out["archived_before_switch"] = before
import time
time.sleep(8)
with psycopg.connect(admin, autocommit=True) as conn:
    out["archived_after_switch"] = conn.execute("SELECT archived_count FROM pg_stat_archiver").fetchone()[0]
    out["archive_failed"] = conn.execute("SELECT failed_count FROM pg_stat_archiver").fetchone()[0]
for name in ("postgresql-x64-16", "postgresql-x64-18"):
    q = subprocess.run(["sc", "query", name], capture_output=True, text=True).stdout
    c = subprocess.run(["sc", "qc", name], capture_output=True, text=True).stdout
    out[name] = {"running": "RUNNING" in q, "auto": "AUTO_START" in c}
rec = out["record"].get("postgres") or {}
base = rec.get("base_backup_dir") or ""
out["base_backups"] = sorted(p.name for p in __import__("pathlib").Path(base).iterdir()) if base and __import__("pathlib").Path(base).is_dir() else []
print(json.dumps(out, default=str))
'''


def _ssh_python(vm, code: str, timeout: float = 600) -> subprocess.CompletedProcess:
    """*code* run by the install's Python, from the install folder (its .env)."""
    vm.ssh(f"Set-Content -Path '{VM_DRILL}\\snippet.py' -Value @'\n{code}\n'@ -Encoding utf8", timeout=60)
    return vm.ssh(f"Set-Location '{VM_ROOT}'; $env:PYTHONIOENCODING = 'utf-8'; "
                  f"& .venv\\Scripts\\python.exe -X utf8 '{VM_DRILL}\\snippet.py' 2>&1", timeout=timeout)


def _run_detached(vm, args: str, name: str, timeout: float) -> tuple[int, str]:
    """``python -m kazma_cli <args>`` on the VM, detached; its exit and log."""
    script = (f"$ErrorActionPreference = 'Continue'\n$env:NO_COLOR = '1'\n$env:PYTHONIOENCODING = 'utf-8'\n"
              f"Set-Location '{VM_ROOT}'\n$start = Get-Date\n"
              f"& cmd.exe /c \".venv\\Scripts\\python.exe -m kazma_cli {args} > `\"{VM_DRILL}\\{name}.log`\" 2>&1\"\n"
              f"@{{exit = $LASTEXITCODE; seconds = [int]((Get-Date) - $start).TotalSeconds}} | ConvertTo-Json | "
              f"Set-Content '{VM_DRILL}\\{name}.done' -Encoding ascii\n")
    vm.ssh(f"Set-Content -Path '{VM_DRILL}\\{name}.ps1' -Value @'\n{script}\n'@ -Encoding utf8", timeout=60)
    vm.start_detached(f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File {VM_DRILL}\\{name}.ps1", VM_ROOT)
    done = boot._wait_for_file(vm, f"{VM_DRILL}\\{name}.done", timeout)
    log = vm.ssh(f"Get-Content '{VM_DRILL}\\{name}.log' -Encoding utf8 -Raw", timeout=60).stdout
    return int(done.get("exit", -1)), log


def _put_new_code(vm, work: Path, ref: str) -> str:
    """*ref*'s packages and scripts over the install's (its .env, data and
    .venv kept)."""
    archive = work / "new.zip"
    commit = boot._commit(ref)
    subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", "--prefix=kazma/", "-o", str(archive),
                    commit], check=True)
    vm.copy(archive, VM_DRILL + r"\new.zip")
    got = vm.ssh(f"$w = Join-Path $env:TEMP 'kazma-new'; if (Test-Path $w) {{ Remove-Item $w -Recurse -Force }}; "
                 f"Expand-Archive '{VM_DRILL}\\new.zip' -DestinationPath $w -Force; "
                 "foreach ($d in 'kazma-core','kazma-cli','kazma-ui','kazma-gateway','kazma-tui','kazma-skills',"
                 "'scripts','deploy') { robocopy (Join-Path $w \"kazma\\$d\") "
                 f"(Join-Path '{VM_ROOT}' $d) /MIR /NFL /NDL /NJH /NJS /NP | Out-Null }}; 'copied'", timeout=900)
    if "copied" not in got.stdout:
        raise RuntimeError(f"copying the new code failed: {got.stdout[-400:]} {got.stderr[-400:]}")
    return commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="ref", default="origin/main", help="a commit whose Postgres is 16")
    parser.add_argument("--to", dest="to", default="HEAD", help="the commit whose --upgrade is drilled")
    options = parser.parse_args(argv)
    vm = boot.Vm()
    report: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S")}
    with tempfile.TemporaryDirectory(prefix="kazma-pg-upgrade-drill-") as tmp:
        work = Path(tmp)
        report["from"] = boot.prepare(vm, options.ref, work)
        boot._say("Setting up PostgreSQL 16 with the old code (kazma setup postgres -y)")
        code, log = _run_detached(vm, "setup postgres -y", "setup16", 3600)
        report["setup16"] = {"exit": code, "tail": log[-1500:]}
        if code != 0:
            boot._say(log[-2000:])
            raise SystemExit("setting up Postgres 16 failed")
        seeded = _ssh_python(vm, SEED)
        report["seed"] = seeded.stdout.strip()[-300:]
        boot._say(f"Seeded: {report['seed']}")
        report["before"] = json.loads(_ssh_python(vm, READ).stdout.strip().splitlines()[-1])
        report["to"] = _put_new_code(vm, work, options.to)
        boot._say("Upgrading (kazma setup postgres --upgrade -y)")
        code, log = _run_detached(vm, "setup postgres --upgrade -y", "upgrade", 3600)
        report["upgrade"] = {"exit": code, "log": log[-6000:]}
        boot._say(log[-3000:])
        report["after"] = json.loads(_ssh_python(vm, READ).stdout.strip().splitlines()[-1])
        code, log = _run_detached(vm, "setup postgres --upgrade -y", "upgrade-again", 600)
        report["again"] = {"exit": code, "tail": log[-600:]}
        code, log = _run_detached(vm, "doctor", "doctor", 600)
        report["doctor"] = log[-2500:]
    before, after = report["before"], report["after"]
    rec = after["record"].get("postgres") or {}
    checks = {
        "upgrade exit 0": report["upgrade"]["exit"] == 0,
        "server is 18": str(after["server_version_num"]).startswith("18"),
        "record names 18, 16 under previous": rec.get("major") == 18 and (rec.get("previous") or {}).get("major") == 16,
        "rows the same": before["rows"] == after["rows"],
        "arabic the same": before["arabic"] == after["arabic"],
        "sequence the same": after["next_id"] == before["next_id"] + 1,
        "trigger kept": after["trigger"] == "refused the update",
        "18 running at boot": after["postgresql-x64-18"] == {"running": True, "auto": True},
        "16 stopped, not at boot": after["postgresql-x64-16"] == {"running": False, "auto": False},
        "WAL archived on 18": after["archived_after_switch"] > after["archived_before_switch"]
                              and not after["archive_failed"],
        "first base backup taken": bool(after["base_backups"]),
        "a second upgrade does nothing": report["again"]["exit"] == 0 and "already runs" in report["again"]["tail"],
    }
    report["checks"] = checks
    report["verdict"] = "pass" if all(checks.values()) else "fail"
    for name, ok in checks.items():
        boot._say(f"  {'ok  ' if ok else 'FAIL'} {name}")
    out = ROOT / "docs" / "audits" / "evidence" / f"postgres_upgrade_drill_{time.strftime('%Y_%m_%d')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    boot._say(f"Verdict: {report['verdict']} ({out.relative_to(ROOT)})")
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
