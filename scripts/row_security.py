#!/usr/bin/env python
"""Spaces' second lock on Kazma's Postgres (S4-01): its rules, by hand.

``kazma_core.db.row_security`` holds the rules: which of Kazma's tables is a
space's (a policy), which the install's, and what the role ``kazma_space`` may
do on each. Nothing arms the lock yet; this is how the owner (or a session
preparing the arming) looks at a database and puts the rules in place:

* ``check`` -- the catalog against the rules, both ways (role, membership,
  function, row security, policies, rights, the schema). Reads only.
* ``rows`` -- every row whose space the registry does not know, by table and
  space (review M6): such rows would be no space's once armed. Reads only, as
  the owner; ``--known`` names the spaces instead of reading the registry.
* ``prove`` -- ``check`` and ``rows``, then a connection as the space role:
  each space's table is under row security, raises with no space set, and
  shows nothing of a space that does not exist. Reads only (every statement
  on the space connection is rolled back).
* ``apply`` -- what is missing, as the tables' owner: the role when the login
  may make it, the function, row security, policies, rights. Asks first
  (``--yes`` does not). A table another session holds is left and said.

The address is ``--url`` or this install's (``KAZMA_DATABASE_URL``, read from
its ``.env`` as the server reads it). The address is never printed.

Usage (repo root, the dev checkout's Python)::

    python scripts/row_security.py check
    python scripts/row_security.py rows
    python scripts/row_security.py prove
    python scripts/row_security.py apply --url postgresql://...

Exit code: 0 when the answer is clean (or the rules were put in place whole),
1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _url(given: str | None) -> str:
    if given:
        return given
    from kazma_core.db.backend import get_database_url
    from kazma_core.env_files import load_env_files

    load_env_files(include_cwd=False)
    return get_database_url() or ""


def _known(given: str | None) -> set[str]:
    if given is not None:
        return {s.strip() for s in given.split(",") if s.strip()}
    from kazma_core.diagnostic_scope import read_only_diagnostic
    from kazma_core.spaces import identity

    with read_only_diagnostic("scripts/row_security.py"):
        return set(identity.read_registry()["spaces"])


def _print_findings(found: list) -> int:
    blocking = [f for f in found if f.blocking]
    for f in found:
        print(f"  [{'MISSING' if f.blocking else 'note'}] {f.subject}: {f.problem}")
        if f.fix:
            print(f"        fix: {f.fix}")
    print(f"{len(blocking)} difference(s) from the rules." if blocking else "Every table holds its rule.")
    return len(blocking)


def _print_strays(strays: list) -> int:
    for s in strays:
        print(f"  {s.table}: {s.rows} row(s) of {s.space!r} ({s.why})")
    print(f"{len(strays)} group(s) of rows whose space is unknown." if strays
          else "Every row's space is one the registry knows.")
    return len(strays)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("action", choices=("check", "rows", "prove", "apply"))
    parser.add_argument("--url", help="the database (default: this install's)")
    parser.add_argument("--known", help="the spaces there are, comma-separated (default: the registry's)")
    parser.add_argument("--yes", action="store_true", help="apply without asking")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    url = _url(args.url)
    if not url.startswith(("postgres://", "postgresql://")):
        print("Kazma's database is not Postgres here: there is no second lock to look at.", file=sys.stderr)
        return 1
    import psycopg
    from kazma_core.db import row_security

    try:
        if args.action == "check":
            return 1 if _print_findings(row_security.check(url)) else 0
        if args.action == "rows":
            return 1 if _print_strays(row_security.space_rows(url, _known(args.known))) else 0
        if args.action == "prove":
            proof = row_security.prove(url, _known(args.known))
            _print_findings(proof.findings)
            _print_strays(proof.strays)
            for failure in proof.failed:
                print(f"  [FAILED] {failure}")
            print("The lock holds." if proof.holds else "The lock does not hold: it must not be armed.")
            return 0 if proof.holds else 1
        if not args.yes:
            print("This puts the second lock's rules in place in that database (the owner sees every row as "
                  "before). Type yes to go on: ", end="", flush=True)
            try:
                answer = input().strip().lower()
            except EOFError:
                answer = ""
            if answer != "yes":
                print("Nothing was changed.")
                return 1
        done = row_security.apply(url)
    except (psycopg.Error, OSError) as exc:
        print(f"the database could not be read: {type(exc).__name__}", file=sys.stderr)
        return 1
    if done.refused:
        print(f"Nothing was changed: {done.refused}")
        return 1
    for change in done.changed:
        print(f"  {change}")
    for table in done.left:
        print(f"  [LEFT] {table}: its lock was not had in time; run this again when Kazma is idle")
    print("The rules are in place." if done.complete else "The rules are not all in place.")
    return 0 if done.complete else 1


if __name__ == "__main__":
    sys.exit(main())
