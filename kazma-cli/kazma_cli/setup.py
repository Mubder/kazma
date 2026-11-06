"""``kazma setup postgres`` -- Postgres 16 as an OS service (resilience plan A1).

One command for every OS: winget (Windows), Homebrew (macOS), apt
(Debian/Ubuntu, WSL) or dnf (Fedora/RHEL). It prints the plan and asks
before doing anything (``-y`` skips the question), then installs Postgres 16
as a service that starts at boot and restarts on failure, listening on this
machine only, creates Kazma's database and its ``kazma_app`` role (not a
superuser), turns on WAL archiving, writes ``KAZMA_DATABASE_URL`` into the
install's ``.env`` (never printed), signs in once as Kazma will, and records
what it did in ``<data dir>/setup_record.json`` for ``kazma doctor`` and the
guard.

``--check`` reports and changes nothing. ``--dry-run`` prints the commands.
A second run checks and repairs (start at boot, restart on failure, the
role) and never reinstalls. ``--move-from-container NAME`` copies the data of
the Docker container Kazma uses today into the new server before ``.env`` is
changed, and compares every Kazma table's row count.

The superuser's password is kept in ``<install>/.kazma/postgres_superuser.pass``
(this account only); the role's only in ``.env``. Neither is printed, and a
password is never on a command line except the Windows installer's throwaway
one, replaced by the next step.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path


def _say(text: str = "") -> None:
    print(text, flush=True)


def _scrub(text: str, secrets_: list[str]) -> str:
    for value in secrets_:
        if value:
            text = text.replace(value, "****")
    return text


def _env_path() -> Path:
    from kazma_core.paths import get_project_root

    return get_project_root() / ".env"


def _set_env_value(path: Path, key: str, value: str) -> None:
    """Set ``key=value`` in *path* (added, or replacing the line), restricted
    to this account."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    from kazma_core.atomic_files import atomic_write_text

    atomic_write_text(path, "\n".join(out) + "\n")
    from kazma_core.observability.outside_watch import _restrict_to_owner

    _restrict_to_owner(path)


def _env_file_value(path: Path, key: str) -> str | None:
    """*key*'s value in an env file (the last line that sets it), or None."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    found = None
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip() == key and not line.lstrip().startswith("#"):
            found = value.strip().strip('"').strip("'")
    return found


def _existing_app_password(port: int) -> str | None:
    """The kazma_app password in the install's current address, when it points here."""
    from urllib.parse import unquote, urlsplit

    from kazma_core.db.backend import get_database_url

    url = get_database_url() or _env_file_value(_env_path(), "KAZMA_DATABASE_URL") or ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.username == "kazma_app" and parts.hostname in ("127.0.0.1", "localhost") and parts.port == port:
        return unquote(parts.password or "") or None
    return None


def _superuser_pass_file() -> Path:
    from kazma_core.paths import user_home

    return user_home() / "postgres_superuser.pass"


def _role_sql() -> str:
    from kazma_core.paths import get_project_root

    return (get_project_root() / "deploy" / "postgres" / "kazma_app_role.sql").read_text(encoding="utf-8")


def _run_step(step, secrets_: list[str], *, run=subprocess.run) -> tuple[bool, str]:
    env = {**os.environ, **step.env}
    try:
        done = run(list(step.argv), input=step.stdin, capture_output=True, text=True,
                   env=env, timeout=1800, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run: {type(exc).__name__}"
    out = _scrub(((done.stdout or "") + (done.stderr or "")).strip(), secrets_)
    return done.returncode in step.ok_codes, f"exit {done.returncode}" + (f": {out[-600:]}" if out else "")


def _print_check(platform, record: dict, state: dict | None) -> int:
    from kazma_core import postgres_service as pgs

    _say(f"platform    : {platform.os}" + (" (WSL)" if platform.wsl else "")
         + f", package manager: {platform.manager or 'none known'}")
    rec = record.get("postgres") or {}
    if not rec:
        _say("setup       : not set up by kazma setup postgres")
    else:
        _say(f"setup       : PostgreSQL {rec.get('major')} as {rec.get('service')} on port {rec.get('port')}")
    if state is None:
        return 1
    problems = 0
    for key, label in (("installed", "installed"), ("running", "running"), ("at_boot", "starts at boot"),
                       ("restarts", "restarts on failure"), ("answers", f"it answers on port {rec.get('port', pgs.DEFAULT_PORT)}")):
        value = state.get(key)
        mark = "ok" if value else ("??" if value is None else "NO")
        problems += 0 if value else 1
        _say(f"  {mark:4} {label}")
    if problems:
        _say("Repair it with: kazma setup postgres")
    return 0 if not problems else 1


def run(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kazma setup", description="Set up what Kazma runs on.")
    sub = parser.add_subparsers(dest="what", required=True)
    pg = sub.add_parser("postgres", help="PostgreSQL 16 as an OS service")
    pg.add_argument("-y", "--yes", action="store_true", help="do not ask before acting")
    pg.add_argument("--check", action="store_true", help="report only; change nothing")
    pg.add_argument("--dry-run", action="store_true", help="print the commands; run nothing")
    pg.add_argument("--port", type=int, default=5432)
    pg.add_argument("--move-from-container", metavar="NAME",
                    help="copy the data of this Docker container (Kazma's database today) into the new server")
    pg.add_argument("--no-env", action="store_true", help="do not write KAZMA_DATABASE_URL into .env")
    options = parser.parse_args(args)
    return _setup_postgres(options)


def _setup_postgres(options, *, run=subprocess.run) -> int:
    from kazma_core import postgres_service as pgs

    platform = pgs.detect_platform()
    record = pgs.read_record()
    service = pgs.SERVICE.get(platform.os)
    state = pgs.service_state(platform.os, service, options.port, run=run) if service else None
    if options.check:
        return _print_check(platform, record, state)
    if platform.manager is None:
        _say(f"No package manager Kazma knows on this {platform.os} system (winget, Homebrew, apt, dnf).")
        _say("Install PostgreSQL 16 by hand: docs/docs/ops/postgres-and-saas.md, \"Postgres as an OS service\".")
        return 2

    installed = bool(state and state.get("installed"))
    pass_file = _superuser_pass_file()
    superuser_password = (pass_file.read_text(encoding="utf-8").strip() if pass_file.exists() else "") \
        or secrets.token_urlsafe(24)
    app_password = _existing_app_password(options.port) or secrets.token_urlsafe(24)
    throwaway = secrets.token_urlsafe(18)
    hidden = [superuser_password, app_password, throwaway]

    from kazma_core.backup import wal_archive

    archive_dir = wal_archive.default_archive_dir(platform)
    steps = pgs.setup_plan(platform, port=options.port, superuser_password=superuser_password,
                           throwaway_password=throwaway, app_password=app_password, role_sql=_role_sql(),
                           wal_archive_command=wal_archive.archive_command(platform, archive_dir))
    steps = wal_archive.prepare_steps(platform, archive_dir) + steps
    if installed:
        # A second run checks and repairs: it never reinstalls. The superuser's
        # password and the role are set again; the install steps are skipped.
        steps = [s for s in steps if not s.title.startswith(("Install", "Add the", "Create its data"))
                 and s.argv[:1] != ("",)]
        if not pass_file.exists() and platform.os == "windows":
            _say("Postgres is installed, but this install does not know its superuser's password")
            _say(f"({pass_file} is missing). Put it there, then run this again.")
            return 2

    _say(f"PostgreSQL {pgs.PG_MAJOR} as a {platform.os} service ({service}) on 127.0.0.1:{options.port}"
         + (" -- installed: checking and repairing" if installed else ""))
    for i, step in enumerate(steps, 1):
        title = step.title or "  (continued)"
        _say(f"  {i:2}. {title}\n      {step.printable()}")
    if options.move_from_container:
        _say(f"  then: copy the data of container {options.move_from_container} into it and compare row counts")
    if not options.no_env:
        _say("  then: write KAZMA_DATABASE_URL into .env (not shown), and sign in once as Kazma will")
    if options.dry_run:
        return 0
    if platform.os == "windows" and not pgs.is_admin():
        _say("Run this from an elevated terminal (Run as administrator): installing a service needs it.")
        return 2
    if not options.yes and input("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
        _say("Nothing was done.")
        return 1

    from kazma_core.atomic_files import atomic_write_text
    from kazma_core.observability.outside_watch import _restrict_to_owner

    atomic_write_text(pass_file, superuser_password + "\n")
    _restrict_to_owner(pass_file)

    for step in steps:
        _say(f"-> {step.title or step.printable()}")
        if step.action == "wait":
            ok = pgs.wait_for_port(options.port)
            detail = "" if ok else f"nothing answered on port {options.port} within a minute"
        elif step.action == "restart":
            ok = pgs.restart_service(platform.os, service, options.port, run=run)
            detail = "" if ok else "it did not answer after the restart"
        else:
            ok, detail = _run_step(step, hidden, run=run)
        if not ok:
            _say(f"   FAILED: {detail}")
            _say("Nothing past this step was done. Fix it and run kazma setup postgres again.")
            return 1

    url = pgs.database_url(app_password, options.port)
    if options.move_from_container:
        from kazma_core.backup import pg_move

        moved = pg_move.move_from_container(options.move_from_container, url, port=options.port, say=_say)
        if not moved:
            _say("The data was NOT moved; .env was not changed. Kazma still uses the container.")
            return 1

    if not options.no_env:
        _set_env_value(_env_path(), "KAZMA_DATABASE_URL", url)
        _say(f"-> KAZMA_DATABASE_URL written to {_env_path()} (this account only)")

    from kazma_core.db.postgres_pool import refusal_reason

    reason = refusal_reason(url)
    if reason:
        _say(f"Signing in as kazma_app failed: {reason}")
        return 1
    _say("-> Kazma signs in as kazma_app (not a superuser).")
    pgs.write_record(pgs.PostgresRecord(
        os=platform.os, manager=platform.manager, service=service, port=options.port,
        bin_dir=pgs.bin_dir_for(platform), wal_archive_dir=str(archive_dir), installed_at=__import__("time").time()))
    _say("Done. Reload Kazma when it is idle to use the new database:")
    _say("  python scripts/service/kazma_guard.py --reload --when-idle")
    return 0


def run_restore(args: list[str]) -> int:
    """``kazma restore postgres --to TIME --into DIR``: lay the newest base
    backup older than TIME into a new folder and set it to replay the WAL up to
    TIME. The live database is never touched; the steps left are printed."""
    parser = argparse.ArgumentParser(prog="kazma restore", description="Restore the database to a time.")
    sub = parser.add_subparsers(dest="what", required=True)
    pg = sub.add_parser("postgres", help="point-in-time restore of Kazma's database")
    pg.add_argument("--to", required=True, help='the time to stop at, e.g. "2026-11-20 14:05"')
    pg.add_argument("--into", required=True, help="a new, empty folder for the restored data")
    options = parser.parse_args(args)

    from kazma_core import postgres_service as pgs
    from kazma_core.backup import wal_archive

    rec = pgs.read_record().get("postgres") or {}
    if not rec.get("wal_archive_dir"):
        _say("No WAL archive is set up on this install (kazma setup postgres turns it on).")
        return 2
    _say(f"Restoring to {options.to} into {options.into} (the live database is not touched).")
    try:
        steps = wal_archive.restore_to(options.to, Path(options.into), Path(rec["wal_archive_dir"]),
                                       os_name=rec.get("os", ""))
    except (ValueError, OSError) as exc:
        _say(f"Nothing was restored: {exc}")
        return 1
    _say("Laid out. What is left:")
    for i, step in enumerate(steps, 1):
        _say(f"  {i}. {step}")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
