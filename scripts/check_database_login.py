#!/usr/bin/env python3
"""Sign in to Kazma's Postgres the way the server will, before you reload.

Run it with the install's own Python after changing the database login
(``docs/docs/ops/postgres-and-saas.md``, "A database role that is not a
superuser")::

    <install>/.venv/Scripts/python.exe <install>/scripts/check_database_login.py

It reads the install's ``.env`` as the server does, signs in with
``KAZMA_DATABASE_URL`` from this machine -- not from inside the database
container, where Postgres lets loopback sign-ins in with no password at all
-- and says who Kazma signs in as, or Postgres's reason for refusing. It
writes nothing. Exit 0 when the sign-in works, 1 when it does not, 2 when
the install has no Postgres address.

Live 2026-11-03: the new role's password had not taken, nothing checked the
sign-in before the reload, and the server waited 18 minutes on "password
authentication failed".
"""

from __future__ import annotations

import sys


def main() -> int:
    from kazma_core.env_files import load_env_files

    # The install's own .env, wherever this is run from: the checkout this
    # Python's Kazma is (``paths.installed_project_root``), as the server reads.
    load_env_files(include_cwd=False)

    from kazma_core.db.backend import get_database_url
    from kazma_core.db.postgres_pool import refusal_reason

    dsn = get_database_url() or ""
    if not dsn.startswith(("postgres://", "postgresql://")):
        print("No Postgres address (KAZMA_DATABASE_URL) in this install's .env: it uses SQLite.")
        return 2
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]
    reason = refusal_reason(dsn)
    if reason:
        print(f"NOT OK: {reason}")
        return 1
    import psycopg

    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
        user, superuser = conn.execute(
            "SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
        database = conn.execute("SELECT current_database()").fetchone()[0]
    print(f"OK: Kazma signs in to {database} as {user} "
          f"({'a superuser' if superuser else 'not a superuser'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
