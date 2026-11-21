"""Move Kazma's database and engine into the data folder (2026-11-21).

``kazma setup data-folder D:\\Kazma`` on an install whose parts live on
Windows' own drive (:mod:`kazma_core.data_folder` says why that lost the
database on the 19 Nov reset). Windows only. In the pattern of the other
moves (``kazma setup postgres --move-from-container`` and ``--upgrade``,
``kazma setup engine --move-from-desktop``):

* every refusal comes before anything changes (:func:`plan_move`): the
  folder's own (:func:`data_folder.folder_problem`, its free space counted
  against what moves), a target that already holds files, a database with a
  tablespace or a link outside its folder, a WSL too old to move a distro;
* Kazma is held through its guard (``setup._MoveHold``) while the parts move,
  and started again at the end and seen ready;
* each stage is written to the journal (``setup_record.json``,
  ``data_folder_move``) before it begins. The guard leaves a part the journal
  names alone (its database watch does not start the service, its engine
  watch does not start the distro), and the service is disabled while its
  data is copied, so no guard -- an older one too -- starts it on half a copy;
* a failure puts the part back as it was (:func:`_put_back`), and so does the
  next run for a run that died (:func:`recover`).

**The database** (:func:`_move_postgres`): its service disabled and stopped;
the data folder, the WAL archive and the base backups copied into the data
folder (each restricted to who needs it first) and compared file by file
(path, size, SHA-256); the copy's ``archive_command`` pointed at the new
archive, on disk; the service's command line pointed at the copy (``-D``);
the service set to start at boot again and started; then it must answer, run
from the copy (its ``postmaster.pid`` is there) and let Kazma sign in. The
record names the new folders last. The old folders are kept, unused: the way
back, removed by the owner.

**The engine** (:func:`_move_engine`): its ``KazmaEngine`` task disabled and
ended, Docker stopped cleanly in the distro, the distro ended; its disk
hashed, moved by WSL itself (``wsl --manage kazma-engine --move``), hashed
again where it landed; the task enabled and run; Docker must answer.

Every function blocks: off the loop.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from kazma_core import data_folder as df

__all__ = [
    "Machine",
    "MovePlan",
    "Part",
    "move",
    "plan_move",
    "recover",
]

Runner = Callable[..., subprocess.CompletedProcess]

#: The journal's stages while a part is out of service (the guard leaves it
#: alone during these, ``scripts/service/kazma_guard.py``: its
#: ``DATA_MOVE_STAGES``, held equal by a test).
ACTIVE_STAGES = ("stopping", "copying", "switching", "starting", "moving")
#: How long from a stage's start the guard leaves the part alone (its
#: ``DATA_MOVE_HOLD_S``): a run that died is not waited for.
GUARD_HOLD_S = 2 * 3600.0
#: How long the engine has to answer after its task runs again.
ENGINE_WAIT_S = 180.0
#: How long the database has to answer after its service starts.
DATABASE_WAIT_S = 120.0
#: The first WSL release with ``wsl --manage <distro> --move``.
WSL_MOVE_MIN = (2, 5)
#: The well-known accounts a Postgres service runs as, by SID.
_ACCOUNT_SIDS = {
    "nt authority\\networkservice": "*S-1-5-20", "nt authority\\network service": "*S-1-5-20",
    "nt authority\\localservice": "*S-1-5-19", "nt authority\\local service": "*S-1-5-19",
    "localsystem": "*S-1-5-18", "nt authority\\system": "*S-1-5-18",
}
_SYSTEM, _ADMINS = "*S-1-5-18", "*S-1-5-32-544"
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _distro_location(name: str) -> tuple[Path, str] | None:
    from kazma_core.docker_cli import wsl_distro_location

    return wsl_distro_location(name)


@dataclass
class Machine:
    """What the move reads and runs (tests replace each piece)."""

    run: Runner = subprocess.run
    disk: df.Disk | None = None
    answers: Callable[[int], bool] | None = None
    sign_in: Callable[[str], str | None] | None = None
    distro_location: Callable[[str], tuple[Path, str] | None] = _distro_location
    hasher: Callable[[Path], str] = _hash_file
    sleep: Callable[[float], None] = time.sleep
    say: Callable[[str], None] = print
    record_path: Path | None = None
    #: The account Kazma runs as (``*<SID>`` for icacls), read when empty.
    user: str = ""

    def __post_init__(self) -> None:
        if self.disk is None:
            self.disk = df.Disk.this_machine()

    def account(self) -> str:
        if not self.user:
            self.user = current_account(self.run)
        return self.user

    def refusal(self, url: str) -> str | None:
        if self.sign_in is not None:
            return self.sign_in(url)
        from kazma_core.db.postgres_pool import refusal_reason

        return refusal_reason(url)


def _out(run: Runner, argv: list[str], *, timeout: float = 120) -> tuple[int, str]:
    try:
        done = run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).replace("\x00", "")


def current_account(run: Runner = subprocess.run) -> str:
    """This process's account as icacls takes it (``*S-1-5-21-...``), from
    ``whoami /user``: a SID, never a name that reads ``WORKGROUP\\x`` over SSH."""
    code, text = _out(run, ["whoami", "/user", "/fo", "csv", "/nh"], timeout=30)
    found = re.search(r'"(S-1-5-[0-9-]+)"', text) if code == 0 else None
    if found:
        return f"*{found.group(1)}"
    return os.environ.get("USERNAME") or ""


def _account_grant(account: str) -> str:
    return _ACCOUNT_SIDS.get((account or "").strip().lower(), account.strip())


# -- what moves --------------------------------------------------------------------------------


@dataclass
class Part:
    """One part of the move: ``postgres`` or ``engine``, where it is and where
    it goes. ``folders`` are (what, from, to) for the database."""

    name: str
    folders: list[tuple[str, Path, Path]] = field(default_factory=list)
    size: int = 0
    service: str = ""
    port: int = 0
    major: int = 0
    vhd: str = ""

    @property
    def title(self) -> str:
        return {"postgres": f"PostgreSQL {self.major}'s data, WAL archive and base backups",
                "engine": "Kazma's container engine (its virtual disk)"}[self.name]


@dataclass
class MovePlan:
    folder: Path
    parts: list[Part] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)


def _size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _holds_files(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return True


def _links_inside(path: Path) -> list[str]:
    """Links and junctions under *path* (a tablespace in ``pg_tblspc`` is
    one): a copy would copy the link, not what it names."""
    def linked(full: str) -> bool:
        try:
            info = os.lstat(full)
        except OSError:
            return False
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)

    found = []
    for root, dirs, files in os.walk(path):
        found += [os.path.relpath(os.path.join(root, n), path) for n in [*dirs, *files]
                  if linked(os.path.join(root, n))]
        dirs[:] = [d for d in dirs if not linked(os.path.join(root, d))]
    return found


def _wsl_version(run: Runner) -> tuple[int, ...] | None:
    code, text = _out(run, ["wsl.exe", "--version"], timeout=60)
    # Its first line ("WSL version: 2.6.1.0", the label in Windows' language);
    # bounded parts, so the search stays linear on any text.
    found = re.search(r"(\d{1,6})\.(\d{1,6})\.(\d{1,6})", text[:400]) if code == 0 else None
    return tuple(int(x) for x in found.groups()) if found else None


def _postgres_part(rec: dict[str, Any], layout: df.Layout, machine: Machine) -> tuple[Part | None, list[str], str]:
    """The database's part of the move (None when it is in place or not
    installed), refusals, and what is already there."""
    from kazma_core import postgres_service as pgs
    from kazma_core.backup import wal_archive

    service = str(rec.get("service") or "")
    if not service:
        return None, [], ""
    config = pgs.service_config(service, run=machine.run)
    if not config["command"]:
        return None, [], ""        # not installed here: the next setup puts it in the folder
    major = int(rec.get("major") or pgs.PG_MAJOR)
    data_now = Path(config["data_dir"] or rec.get("data_dir") or "")
    if not str(data_now):
        return None, [f"the {service} service names no data folder (-D) on its command line"], ""
    wanted = [("data", data_now, layout.postgres_data(major))]
    if rec.get("wal_archive_dir"):
        wanted.append(("WAL archive", Path(rec["wal_archive_dir"]), layout.wal_archive(major)))
    base_now = wal_archive._base_root(rec)
    wanted.append(("base backups", base_now, layout.base_backups(major)))
    moving = [(what, src, dst) for what, src, dst in wanted if not df.same_folder(src, dst)]
    if not moving:
        return None, [], f"PostgreSQL {major}'s data is in {layout.postgres(major)}"
    refusals = []
    for what, src, dst in moving:
        if _holds_files(dst):
            refusals.append(f"{dst} holds files already: nothing is copied over them (the database's {what})")
        if what == "data" and not (src / "PG_VERSION").is_file():
            refusals.append(f"the service's data folder {src} holds no database (no PG_VERSION)")
        if what == "data":
            links = _links_inside(src) if src.is_dir() else []
            if links:
                refusals.append(f"the database's folder holds links ({', '.join(links[:3])}): a tablespace or a "
                                "WAL folder outside it is not moved by this command")
    size = sum(_size(src) for _what, src, _dst in moving if src.is_dir())
    return Part("postgres", moving, size, service=service, port=int(rec.get("port") or pgs.DEFAULT_PORT),
                major=major), refusals, ""


def _engine_part(layout: df.Layout, machine: Machine) -> tuple[Part | None, list[str], str]:
    from kazma_core.docker_cli import KAZMA_DISTRO

    found = machine.distro_location(KAZMA_DISTRO)
    if found is None:
        return None, [], ""
    base, vhd = found
    if df.same_folder(base, layout.engine):
        return None, [], f"Kazma's engine's disk is in {layout.engine}"
    refusals = []
    disk = base / vhd
    if not disk.is_file():
        refusals.append(f"the kazma-engine distro's disk is not at {disk}")
    if _holds_files(layout.engine):
        refusals.append(f"{layout.engine} holds files already: the engine's disk is not moved over them")
    version = _wsl_version(machine.run)
    if version is None or version[:2] < WSL_MOVE_MIN:
        refusals.append("moving the engine needs WSL 2.5 or later (wsl --manage --move): run wsl --update, "
                        "then this again")
    size = disk.stat().st_size if disk.is_file() else 0
    return Part("engine", [("engine disk", base, layout.engine)], size, vhd=vhd), refusals, ""


def plan_move(folder: str, *, machine: Machine, os_name: str = "windows", install_root: Path | None = None,
              data_dir: Path | None = None) -> MovePlan:
    """What moves into *folder* and what stops it. Reads only."""
    from kazma_core.postgres_service import read_record

    plan = MovePlan(Path(folder))
    problem = df.folder_problem(folder, disk=machine.disk, os_name=os_name, install_root=install_root,
                                data_dir=data_dir)
    if problem:
        plan.refusals.append(problem)
        return plan
    layout = df.Layout(Path(folder))
    rec = read_record(machine.record_path).get("postgres") or {}
    for found in (_postgres_part(rec, layout, machine), _engine_part(layout, machine)):
        part, refusals, already = found
        if part is not None:
            plan.parts.append(part)
        plan.refusals += refusals
        if already:
            plan.already.append(already)
    # Its room: what moves, with a margin; with nothing to move, what the
    # parts setup puts there when it installs them.
    need = sum(p.size for p in plan.parts) + df.MOVE_MARGIN_BYTES if plan.parts else df.FRESH_FREE_BYTES
    problem = df.folder_problem(folder, disk=machine.disk, os_name=os_name, install_root=install_root,
                                data_dir=data_dir, need_bytes=need)
    if problem:
        plan.refusals.append(problem)
    return plan


# -- folders: restricted, copied, compared ----------------------------------------------------------------


def secure_folder(path: Path, grants: list[str], run: Runner) -> tuple[bool, str]:
    """*path* made (if missing) and restricted to *grants* (icacls
    ``account:(OI)(CI)F``-style entries), inherited rights removed: on a
    second drive the root lets every signed-in user change files, and these
    hold the whole database."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"{path} could not be made: {exc}"
    argv = ["icacls", str(path), "/inheritance:r"]
    for grant in grants:
        argv += ["/grant", grant]
    code, text = _out(run, argv)
    return (True, "") if code == 0 else (False, f"icacls refused {path}: {text.strip()[-300:]}")


def _manifest(root: Path) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for current, _dirs, files in os.walk(root):
        for name in files:
            full = Path(current) / name
            rel = full.relative_to(root).as_posix()
            out[rel] = (full.stat().st_size, _hash_file(full))
    return out


def _copy_tree(src: Path, dst: Path) -> None:
    """Every folder and file of *src* into *dst* (which is empty): contents
    and times; the rights are *dst*'s, inherited."""
    for current, dirs, files in os.walk(src):
        rel = Path(current).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            target = dst / rel / name
            partial = target.with_name(name + ".kazma-copy")
            shutil.copyfile(Path(current) / name, partial)
            shutil.copystat(Path(current) / name, partial)
            os.replace(partial, target)
        dirs.sort()


def differences(src: Path, dst: Path) -> list[str]:
    """What differs between two folders, file by file (path, size, SHA-256);
    empty when the copy is whole."""
    a, b = _manifest(src), _manifest(dst)
    out = [f"missing in the copy: {rel}" for rel in sorted(set(a) - set(b))]
    out += [f"not in the original: {rel}" for rel in sorted(set(b) - set(a))]
    out += [f"differs: {rel}" for rel in sorted(set(a) & set(b)) if a[rel] != b[rel]]
    return out


# -- the database ------------------------------------------------------------------------------------------


def _sc(machine: Machine, *args: str) -> tuple[int, str]:
    return _out(machine.run, ["sc", *args])


def _set_start_mode(machine: Machine, service: str, mode: str) -> bool:
    """``auto`` or ``disabled`` (a disabled service cannot be started by
    anyone, the guard of any version included)."""
    return _sc(machine, "config", service, "start=", mode)[0] == 0


def set_service_command(machine: Machine, service: str, command: str) -> tuple[bool, str]:
    """The service's command line set whole, through ``Win32_Service.Change``
    (Windows PowerShell 5.1 mangles the quotes of ``sc config binPath=``)."""
    quoted_name = service.replace("'", "''")
    quoted = command.replace("'", "''")
    script = ("$ErrorActionPreference='Stop';"
              f"$svc=Get-CimInstance Win32_Service -Filter \"Name='{quoted_name}'\";"
              "if (-not $svc) { throw 'the service is not there' };"
              f"$r=Invoke-CimMethod -InputObject $svc -MethodName Change -Arguments @{{PathName='{quoted}'}};"
              "if ($r.ReturnValue -ne 0) { throw \"changing the service failed ($($r.ReturnValue))\" }")
    code, text = _out(machine.run, ["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    return (True, "") if code == 0 else (False, text.strip()[-300:])


def _secure_parents(folder: Path, major: int | None, machine: Machine) -> tuple[bool, str]:
    """The data folder (this account, SYSTEM, the administrators) and, for
    the database, PostgreSQL's folder in it (SYSTEM and the administrators):
    as ``data_folder.prepare_steps`` makes them for a new install."""
    user = machine.account()
    ok, why = secure_folder(folder, [*df.SYSTEM_AND_ADMINS, *([f"{user}:(OI)(CI)F"] if user else [])], machine.run)
    if ok and major:
        ok, why = secure_folder(df.Layout(folder).postgres(major), list(df.SYSTEM_AND_ADMINS), machine.run)
    return ok, why


def _postgres_grants(machine: Machine, account: str) -> dict[str, list[str]]:
    service = _account_grant(account) or "*S-1-5-20"
    user = machine.account()
    full = [f"{_SYSTEM}:(OI)(CI)F", f"{_ADMINS}:(OI)(CI)F"]
    return {
        "data": [*full, f"{service}:(OI)(CI)F"],
        "WAL archive": [*full, f"{service}:(OI)(CI)M", *([f"{user}:(OI)(CI)M"] if user else [])],
        "base backups": [*full, *([f"{user}:(OI)(CI)F"] if user else [])],
    }


def _inside_folder(path: str, folder: Path) -> bool:
    return df._inside(PureWindowsPath(str(path)), folder) and not df.same_folder(path, folder)


def _remove_created(entry: dict[str, Any], machine: Machine) -> None:
    """The folders this run made in the data folder, removed (only those,
    and only inside it)."""
    folder = Path(str(entry.get("folder") or ""))
    for made in entry.get("created") or []:
        if str(folder) and _inside_folder(made, folder):
            shutil.rmtree(made, ignore_errors=True)


def _database_up(part: Part, machine: Machine) -> bool:
    from kazma_core import postgres_service as pgs

    return pgs.wait_for_port(part.port, answers=machine.answers, sleep=machine.sleep, wait_s=DATABASE_WAIT_S)


def _move_postgres(part: Part, url: str, *, folder: Path, machine: Machine) -> bool:
    """The database moved (see the module docstring); False (said) when it
    was put back as it was."""
    from kazma_core import postgres_service as pgs
    from kazma_core.backup import wal_archive

    say, rp = machine.say, machine.record_path
    config = pgs.service_config(part.service, run=machine.run)
    entry: dict[str, Any] = {"part": "postgres", "folder": str(folder), "service": part.service,
                             "port": part.port, "major": part.major, "command_before": config["command"],
                             "folders": [[w, str(s), str(d)] for w, s, d in part.folders], "created": []}
    df.write_journal({**entry, "stage": "stopping"}, rp)
    say(f"-> Stopping {part.service} (disabled while its data is copied: nothing starts it on half a copy)")
    if not _set_start_mode(machine, part.service, "disabled"):
        say(f"   {part.service} could not be disabled: nothing was moved.")
        return _put_back({**entry, "stage": "stopping"}, machine)
    if not pgs.stop_service("windows", part.service, run=machine.run, sleep=machine.sleep):
        say(f"   {part.service} did not stop: nothing was moved.")
        return _put_back({**entry, "stage": "stopping"}, machine)
    grants = _postgres_grants(machine, config["account"])
    created: list[str] = []
    for made in (folder, df.Layout(folder).postgres(part.major)):
        if not made.exists():
            created.append(str(made))
    df.write_journal({**entry, "created": list(created), "stage": "copying"}, rp)
    ok, why = _secure_parents(folder, part.major, machine)
    if not ok:
        say(f"   {why}")
        return _put_back({**entry, "created": created, "stage": "copying"}, machine)
    for what, src, dst in part.folders:
        made = not dst.exists()
        df.write_journal({**entry, "created": created + ([str(dst)] if made else []), "stage": "copying"}, rp)
        if made:
            created.append(str(dst))
        ok, why = secure_folder(dst, grants.get(what, grants["data"]), machine.run)
        if not ok:
            say(f"   {why}")
            return _put_back({**entry, "created": created, "stage": "copying"}, machine)
        if src.is_dir():
            say(f"-> Copying the database's {what}: {src} -> {dst}")
            try:
                _copy_tree(src, dst)
                diff = differences(src, dst)
            except OSError as exc:
                diff = [f"{type(exc).__name__}: {exc}"]
            if diff:
                say(f"   The copy of the {what} is not whole ({len(diff)}): {'; '.join(diff[:3])}")
                return _put_back({**entry, "created": created, "stage": "copying"}, machine)
            say(f"   every file of the {what} is the same in the copy")
    entry["created"] = created
    to = {what: dst for what, _src, dst in part.folders}
    df.write_journal({**entry, "stage": "switching"}, rp)
    data_to = to.get("data")
    if data_to is not None and "WAL archive" in to:
        try:
            pgs.set_setting_on_disk(data_to, "archive_command", wal_archive._conf_string(
                wal_archive.archive_command(pgs.Platform("windows", "winget"), to["WAL archive"])))
        except OSError as exc:
            say(f"   The copy's archive_command could not be set: {exc}")
            return _put_back({**entry, "stage": "switching"}, machine)
    if data_to is not None:
        new_command = pgs.with_data_dir(config["command"], str(data_to))
        ok, why = set_service_command(machine, part.service, new_command)
        if not ok or not df.same_folder(pgs.service_config(part.service, run=machine.run)["data_dir"], data_to):
            say(f"   The service could not be pointed at {data_to}: {why or 'its command line did not change'}")
            return _put_back({**entry, "stage": "switching"}, machine)
    df.write_journal({**entry, "stage": "starting"}, rp)
    say(f"-> Starting {part.service}" + (f" on {data_to}" if data_to is not None else ""))
    _set_start_mode(machine, part.service, "auto")
    _sc(machine, "start", part.service)
    problem = ""
    if not _database_up(part, machine):
        problem = f"it did not answer on port {part.port}"
    elif data_to is not None and not (data_to / "postmaster.pid").is_file():
        problem = f"it does not run from {data_to} (no postmaster.pid there)"
    else:
        refusal = machine.refusal(url)
        problem = f"Kazma cannot sign in to it: {refusal}" if refusal else ""
    if problem:
        say(f"   {part.service} on the copy is not usable: {problem}.")
        return _put_back({**entry, "stage": "starting"}, machine)
    _record_postgres(part, to, machine)
    df.clear_journal(rp)
    say(f"-> PostgreSQL {part.major} runs from {data_to or 'its folder'}. The old folders are kept, unused: "
        + ", ".join(str(src) for _w, src, _d in part.folders) + " (remove them once you are sure).")
    return True


def _record_postgres(part: Part, to: dict[str, Path], machine: Machine) -> None:
    from kazma_core import postgres_upgrade as pgu
    from kazma_core.postgres_service import read_record

    rec = dict(read_record(machine.record_path).get("postgres") or {})
    if "data" in to:
        rec["data_dir"] = str(to["data"])
    if "WAL archive" in to:
        rec["wal_archive_dir"] = str(to["WAL archive"])
    if "base backups" in to:
        rec["base_backup_dir"] = str(to["base backups"])
    pgu.write_postgres_record(rec, machine.record_path)


def _put_postgres_back(entry: dict[str, Any], machine: Machine) -> bool:
    from kazma_core import postgres_service as pgs

    say, service = machine.say, str(entry.get("service") or "")
    before = str(entry.get("command_before") or "")
    port = int(entry.get("port") or pgs.DEFAULT_PORT)
    if not service:
        return True
    ok = True
    current = pgs.service_config(service, run=machine.run)
    if before and current["command"] and not df.same_folder(current["data_dir"], pgs.data_dir_in_command(before)):
        say(f"-> {service} pointed back at {pgs.data_dir_in_command(before)}")
        done, why = set_service_command(machine, service, before)
        if not done:
            say(f"   FAILED: {why}")
            ok = False
    if pgs.service_state("windows", service, port, run=machine.run, answers=lambda _p: False).get("running"):
        pgs.stop_service("windows", service, run=machine.run, sleep=machine.sleep)
    _set_start_mode(machine, service, "auto")
    _sc(machine, "start", service)
    up = pgs.wait_for_port(port, answers=machine.answers, sleep=machine.sleep, wait_s=DATABASE_WAIT_S)
    say(f"-> {service} runs from its old folder again" if up else f"   {service} did not answer after the put-back")
    _remove_created(entry, machine)
    return ok and up


# -- the engine ---------------------------------------------------------------------------------------------


def _wsl(machine: Machine, *args: str, timeout: float = 120) -> tuple[int, str]:
    return _out(machine.run, ["wsl.exe", *args], timeout=timeout)


def _engine_running(machine: Machine) -> bool:
    from kazma_core.docker_cli import KAZMA_DISTRO

    code, text = _wsl(machine, "-l", "-q", "--running", timeout=60)
    return code == 0 and KAZMA_DISTRO in [line.strip() for line in text.splitlines()]


def _stop_engine(machine: Machine) -> bool:
    """The engine ended, and nothing to start it again: its task disabled
    (its five-minute trigger would), Docker stopped cleanly in it, the distro
    ended. True when it is not running."""
    from kazma_core.container_engine_setup import TASK_NAME
    from kazma_core.docker_cli import KAZMA_DISTRO

    _out(machine.run, ["schtasks", "/Change", "/TN", TASK_NAME, "/DISABLE"])
    _out(machine.run, ["schtasks", "/End", "/TN", TASK_NAME])
    _wsl(machine, "-d", KAZMA_DISTRO, "-u", "root", "--", "/usr/local/sbin/kazma-engine-stop", timeout=240)
    _wsl(machine, "--terminate", KAZMA_DISTRO)
    for _ in range(30):
        if not _engine_running(machine):
            return True
        machine.sleep(1.0)
    return False


def _start_engine(machine: Machine) -> bool:
    """The task enabled and run again; True once Docker answers."""
    from kazma_core.container_engine_setup import TASK_NAME, _docker

    _out(machine.run, ["schtasks", "/Change", "/TN", TASK_NAME, "/ENABLE"])
    _out(machine.run, ["schtasks", "/Run", "/TN", TASK_NAME])
    waited = 0.0
    while waited <= ENGINE_WAIT_S:
        code, text = _docker(machine.run, "version", "--format", "{{.Server.Version}}")
        if code == 0 and text.strip():
            return True
        machine.sleep(3.0)
        waited += 3.0
    return False


def _move_engine(part: Part, *, folder: Path, machine: Machine) -> bool:
    """The engine's disk moved (see the module docstring); False (said) when
    it was put back."""
    from kazma_core.docker_cli import KAZMA_DISTRO

    say, rp = machine.say, machine.record_path
    _what, src, dst = part.folders[0]
    entry: dict[str, Any] = {"part": "engine", "folder": str(folder), "from": str(src), "to": str(dst),
                             "vhd": part.vhd, "created": [str(d) for d in (dst,) if not d.exists()]}
    df.write_journal({**entry, "stage": "stopping"}, rp)
    say("-> Stopping Kazma's container engine (its task disabled while its disk moves)")
    if not _stop_engine(machine):
        say("   The kazma-engine distro did not stop: nothing was moved.")
        return _put_back({**entry, "stage": "stopping"}, machine)
    user = machine.account()
    ok, why = _secure_parents(folder, None, machine)
    if ok:
        ok, why = secure_folder(dst, [f"{_SYSTEM}:(OI)(CI)F", f"{_ADMINS}:(OI)(CI)F",
                                      *([f"{user}:(OI)(CI)F"] if user else [])], machine.run)
    if not ok:
        say(f"   {why}")
        return _put_back({**entry, "stage": "stopping"}, machine)
    say(f"-> Checking the engine's disk ({part.size / 2**30:.1f} GB): {src / part.vhd}")
    entry["hash"] = machine.hasher(src / part.vhd)
    df.write_journal({**entry, "stage": "moving"}, rp)
    say(f"-> Moving it: wsl --manage {KAZMA_DISTRO} --move {dst}")
    code, text = _wsl(machine, "--manage", KAZMA_DISTRO, "--move", str(dst), timeout=4 * 3600)
    where = machine.distro_location(KAZMA_DISTRO)
    if code != 0 or where is None or not df.same_folder(where[0], dst):
        say(f"   WSL did not move it: {text.strip()[-300:] or f'exit {code}'}")
        return _put_back({**entry, "stage": "moving"}, machine)
    moved = dst / where[1]
    if not moved.is_file() or machine.hasher(moved) != entry["hash"]:
        say("   The moved disk is not the same as the one that was moved.")
        return _put_back({**entry, "stage": "moving"}, machine)
    df.write_journal({**entry, "stage": "starting"}, rp)
    say("-> Starting Kazma's container engine from its new place")
    if not _start_engine(machine):
        say(f"   Docker did not answer within {int(ENGINE_WAIT_S)} s from {dst}.")
        return _put_back({**entry, "stage": "starting"}, machine)
    df.clear_journal(rp)
    say(f"-> Kazma's engine runs from {dst}.")
    return True


def _put_engine_back(entry: dict[str, Any], machine: Machine) -> bool:
    from kazma_core.docker_cli import KAZMA_DISTRO

    say = machine.say
    src = Path(str(entry.get("from") or ""))
    where = machine.distro_location(KAZMA_DISTRO)
    ok = True
    if where is not None and not df.same_folder(where[0], src):
        say(f"-> Moving the engine's disk back to {src}")
        _stop_engine(machine)
        code, text = _wsl(machine, "--manage", KAZMA_DISTRO, "--move", str(src), timeout=4 * 3600)
        where = machine.distro_location(KAZMA_DISTRO)
        if code != 0 or where is None or not df.same_folder(where[0], src):
            say(f"   FAILED: {text.strip()[-300:] or f'exit {code}'}")
            ok = False
    up = _start_engine(machine)
    say("-> Kazma's engine runs from its old place again" if up and ok else "   Kazma's engine did not answer after "
        "the put-back: kazma setup engine --check")
    if ok:
        _remove_created(entry, machine)
    return ok and up


# -- the whole move ----------------------------------------------------------------------------------------------


def _put_back(entry: dict[str, Any], machine: Machine) -> bool:
    """The part *entry* names put back as it was before the move began, the
    journal cleared. Always False for the move it ends (it did not happen);
    the put-back's own failure is said."""
    put = {"postgres": _put_postgres_back, "engine": _put_engine_back}.get(str(entry.get("part") or ""))
    restored = put(entry, machine) if put is not None else True
    if restored:
        df.clear_journal(machine.record_path)
    else:
        machine.say("The part could not be put back by itself; the journal is kept, so the next run of "
                    "kazma setup data-folder tries again first.")
    return False


def recover(machine: Machine) -> str:
    """A move a run did not finish (the journal names its part and stage):
    put back. ``"none"`` (nothing to do), ``"_put_back"``, ``"failed"``."""
    entry = df.journal(machine.record_path)
    if not entry.get("part"):
        return "none"
    machine.say(f"An earlier move of {entry.get('part')} stopped at {entry.get('stage')}: putting it back first.")
    _put_back(entry, machine)
    return "failed" if df.journal(machine.record_path) else "_put_back"


def move(plan: MovePlan, url: str, *, machine: Machine, hold: Any) -> bool:
    """Every part of *plan* moved with Kazma held; True when all moved.
    A part that fails is put back and the others still move."""
    if not plan.parts:
        return True
    if not hold.stop():
        machine.say("Kazma was not stopped: nothing was moved.")
        return False
    moved = True
    for part in plan.parts:
        if part.name == "postgres":
            moved = _move_postgres(part, url, folder=plan.folder, machine=machine) and moved
        else:
            moved = _move_engine(part, folder=plan.folder, machine=machine) and moved
    return moved
