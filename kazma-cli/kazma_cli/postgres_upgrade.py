"""``kazma setup postgres --upgrade``: Kazma's database onto a newer Postgres
major (2026-11-13). The steps and their safety rules are
``kazma_core.postgres_upgrade``'s docstring; this is the command around them:
the plan shown, one question, Kazma held through its guard while the data is
copied and the servers switch, a journal before every stage, and a run that
stopped anywhere picked up or undone by the next one.
"""

from __future__ import annotations

import secrets
import subprocess
import time
from pathlib import Path
from typing import Any

from kazma_cli import setup as _setup
from kazma_cli.setup import _MoveHold, _say

#: The guard pause this command holds Kazma under.
UPGRADE_PAUSE_REASON = "kazma setup postgres --upgrade: moving Kazma's database to a new PostgreSQL"


def _new_record(up, archive_dir: Path) -> dict[str, Any]:
    from kazma_core import postgres_service as pgs
    from kazma_core.backup import wal_archive

    previous = {k: v for k, v in up.old.items() if k != "previous"}
    previous["retired_at"] = time.time()
    rec = pgs.PostgresRecord(
        os=up.platform.os, manager=up.platform.manager or "", service=up.new_service, port=up.port,
        bin_dir=up.new_bin_dir, major=up.new_major, wal_archive_dir=str(archive_dir),
        installed_at=time.time(), base_backup_dir=str(wal_archive.default_base_dir(up.new_major)),
        offsite_tag=wal_archive.offsite_tag(up.new_major), previous=previous)
    from dataclasses import asdict

    return asdict(rec)


def _undo(up, journal_entry: dict[str, Any], hidden: list[str], *, run) -> bool:
    """A switch that did not finish, undone: the old server back on its port
    and on the boot list, the record naming it again."""
    from kazma_core import postgres_upgrade as pgu

    _say(f"-> Undoing the switch: PostgreSQL {up.old_major} back on port {up.port}")
    pgu.write_postgres_record(journal_entry["old_record"])
    ok = _setup._run_setup_steps(pgu.undo_steps(up), up.platform, up.old_service, up.port, hidden, run=run,
                          on_failure="")
    pgu.write_journal({**journal_entry, "stage": "installed"})
    return ok


def upgrade_postgres(options, *, run=subprocess.run, hold: _MoveHold | None = None, ask=input) -> int:
    """The whole move; the exit code (0 done, 1 failed with Kazma on the
    server it was on, 2 refused before anything changed)."""
    from kazma_core import postgres_service as pgs
    from kazma_core import postgres_upgrade as pgu
    from kazma_core.backup import wal_archive

    platform = pgs.detect_platform()
    record = pgs.read_record()
    rec = record.get("postgres") or {}
    entry = pgu.journal()
    if entry.get("stage") == "switched" and int(rec.get("major") or 0) == int(entry.get("to") or 0):
        return _finish(entry, hold or _MoveHold(UPGRADE_PAUSE_REASON), run=run)
    if entry.get("old_record"):
        rec = entry["old_record"]           # the server Kazma used before this upgrade began
    try:
        up = pgu.plan_upgrade(platform, rec, new_major=int(entry.get("to") or pgs.PG_MAJOR),
                              temp_port=int(entry.get("temp_port") or 0) or None)
    except ValueError as exc:
        _say(f"Nothing to upgrade: {exc}.")
        return 0 if "already runs" in str(exc) else 2

    app_password = _setup._existing_app_password(up.port)
    if not app_password:
        _say(f"Kazma's address does not name kazma_app on 127.0.0.1:{up.port}, the server the record names: "
             "nothing was changed. Check .env (kazma doctor).")
        return 2
    pass_file = _setup._superuser_pass_file()
    superuser_password = pass_file.read_text(encoding="utf-8").strip() if pass_file.exists() else ""
    if platform.os in ("windows", "macos") and not superuser_password:
        _say(f"This install does not know its Postgres superuser's password ({pass_file} is missing): nothing "
             "was changed.")
        return 2
    hidden = [superuser_password, app_password]
    hold = hold or _MoveHold(UPGRADE_PAUSE_REASON, what="its database")
    hold.adopt_leftover()
    refusal = hold.refusal()
    if refusal:
        _say(refusal)
        return 2

    new_state = pgs.service_state(platform.os, up.new_service, up.temp_port, run=run)
    new_installed = bool(new_state.get("installed")) and platform.os != "debian" or \
        (platform.os == "debian" and Path(f"/etc/postgresql/{up.new_major}/main").is_dir())
    archive_dir = wal_archive.default_archive_dir(platform, up.new_major)
    install = pgs.setup_plan(platform, port=up.temp_port, superuser_password=superuser_password or
                             secrets.token_urlsafe(24),
                             throwaway_password=superuser_password if new_installed else secrets.token_urlsafe(18),
                             app_password=app_password, role_sql=_setup._role_sql(),
                             wal_archive_command=wal_archive.archive_command(platform, archive_dir),
                             major=up.new_major)
    install = wal_archive.prepare_steps(platform, archive_dir) + install
    if new_installed:
        # An earlier run installed it: put it on its spare port and start it
        # (an undone switch left it stopped); the settings and the role again.
        install = [pgs.port_step(platform, up.new_major, up.temp_port),
                   pgs.Step(f"Start PostgreSQL {up.new_major} on port {up.temp_port}", action="start",
                            service=up.new_service, major=up.new_major, port=up.temp_port, admin=True),
                   *_setup._without_install_steps(install)]

    _say(f"PostgreSQL {up.old_major} -> {up.new_major} ({platform.os}), Kazma's database on port {up.port}:")
    for i, step in enumerate(install, 1):
        _say(f"  {i:2}. {step.title or '  (continued)'}\n      {step.printable()}")
    _say("  then: once no chat turn runs, stop Kazma through its guard and hold it")
    _say(f"  then: copy Kazma's database from PostgreSQL {up.old_major} into {up.new_major} (port "
         f"{up.temp_port}) and compare every table and sequence")
    for step in pgu.switch_steps(up):
        _say(f"  then: {step.title}")
    _say(f"  then: start Kazma and see it signed in to PostgreSQL {up.new_major}. .env does not change.")
    _say(f"  PostgreSQL {up.old_major} is kept, stopped, with its data: the way back.")
    if options.dry_run:
        return 0
    if platform.os == "windows" and not pgs.is_admin():
        _say("Run this from an elevated terminal (Run as administrator): installing a service needs it.")
        return 2
    if not options.yes and ask("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
        _say("Nothing was done.")
        return 1

    base = {"from": up.old_major, "to": up.new_major, "temp_port": up.temp_port, "port": up.port,
            "old_record": rec, "started_at": entry.get("started_at") or time.time()}
    if entry.get("stage") == "switching":
        if not _undo(up, {**base, **entry}, hidden, run=run):
            _say(f"The earlier switch could not be undone: PostgreSQL {up.old_major} may not be running. "
                 "Kazma stays stopped. kazma doctor shows the services; run this again.")
            return 1

    pgu.write_journal({**base, "stage": "installing"})
    if not _setup._run_setup_steps(install, platform, up.new_service, up.temp_port, hidden, run=run,
                            on_failure=f"Kazma still uses PostgreSQL {up.old_major}: nothing of its database changed. "
                                       "Fix it and run kazma setup postgres --upgrade again."):
        return 1
    pgu.write_journal({**base, "stage": "installed"})

    url = pgs.database_url(app_password, up.port)
    temp_url = pgu.with_port(url, up.temp_port)
    if not hold.stop():
        _say(f"Kazma was not stopped: nothing was copied, and it still uses PostgreSQL {up.old_major}. "
             "Run this again when it is idle.")
        return 1

    def _empty_target() -> bool:
        for step in pgs.empty_database_steps(platform, port=up.temp_port, superuser_password=superuser_password,
                                             role_sql=_setup._role_sql(), major=up.new_major):
            if not _setup._run_setup_steps([step], platform, up.new_service, up.temp_port, hidden, run=run,
                                    on_failure=""):
                return False
        return True

    if not pgu.copy_database(url, temp_url, bin_dir=up.new_bin_dir, say=_say, run=run, empty_target=_empty_target):
        hold.resume()
        _say(f"The copy is not whole: Kazma was started again on PostgreSQL {up.old_major}, unchanged.")
        return 1

    new_record = _new_record(up, archive_dir)
    pgu.write_journal({**base, "stage": "switching", "new_record": new_record, "switching_since": time.time()})
    # The record names the server about to be on the port before any server
    # stops: the guard's database watch never starts the other one.
    pgu.write_postgres_record(new_record)
    switched = _setup._run_setup_steps(pgu.switch_steps(up), platform, up.new_service, up.port, hidden, run=run,
                                on_failure="")
    reason = None
    if switched:
        from kazma_core.db.postgres_pool import refusal_reason

        major = pgu.server_major(url)
        reason = (refusal_reason(url) if major == up.new_major
                  else f"port {up.port} answers PostgreSQL {major}, not {up.new_major}")
    if not switched or reason:
        if reason:
            _say(f"PostgreSQL {up.new_major} on port {up.port} is not usable: {reason}")
        if _undo(up, {**base, "stage": "switching"}, hidden, run=run):
            hold.resume()
            _say(f"The switch was undone: Kazma was started again on PostgreSQL {up.old_major}, unchanged. "
                 f"PostgreSQL {up.new_major} stays installed on port {up.temp_port}, stopped.")
        else:
            _say("The switch could not be undone: Kazma stays stopped. kazma doctor; then run this again.")
        return 1

    pgu.write_journal({**base, "stage": "switched", "new_record": new_record})
    return _finish({**base, "stage": "switched", "new_record": new_record}, hold, run=run,
                   superuser_password=superuser_password)


def _finish(entry: dict[str, Any], hold: _MoveHold, *, run, superuser_password: str = "") -> int:
    """Kazma started on the new server and seen signed in there (when it was
    running: a Kazma that was not started is not waited for); its first base
    backup taken; the journal cleared. A finish a later run picks up reads
    the superuser's password itself."""
    from kazma_core import postgres_service as pgs
    from kazma_core import postgres_upgrade as pgu
    from kazma_core.backup import wal_archive

    rec = pgs.read_record().get("postgres") or {}
    port = int(rec.get("port") or 5432)
    url_password = _setup._existing_app_password(port)
    kazma_runs = bool(hold.running) or hold.adopt_leftover()
    if hold.held:
        _say(f"-> Starting Kazma on PostgreSQL {rec.get('major')}")
        result = hold.resume()
        if result != "up":
            _say(f"Kazma has not answered yet ({result}); its guard keeps at it: "
                 "python scripts/service/kazma_guard.py --status")
            return 1
    if kazma_runs and url_password and not _setup._kazma_signed_in(pgs.database_url(url_password, port)):
        _say(f"Kazma answers, but was not seen signed in to PostgreSQL {rec.get('major')}: check kazma doctor.")
        return 1
    if not superuser_password:
        pass_file = _setup._superuser_pass_file()
        superuser_password = pass_file.read_text(encoding="utf-8").strip() if pass_file.exists() else ""
    if superuser_password:
        try:
            base = wal_archive._base_backup(rec, superuser_password, root=wal_archive._base_root(rec))
            _say(f"-> The first base backup of PostgreSQL {rec.get('major')} is taken ({base.name}): point-in-time "
                 "restore starts here.")
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            _say(f"   The first base backup was not taken ({exc}); the WAL archive sweep takes one within the hour.")
    pgu.clear_journal()
    old = rec.get("previous") or {}
    _say(f"Done. Kazma runs on PostgreSQL {rec.get('major')}" + ("" if kazma_runs else " when it starts")
         + f". PostgreSQL {old.get('major')} is stopped and kept ({old.get('service')}, not started at boot), with "
         "its data and backups: remove it with its own uninstaller once you are sure.")
    return 0
