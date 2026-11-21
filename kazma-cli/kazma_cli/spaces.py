"""``kazma spaces`` -- the move to several people on one Kazma (Spaces S1-33).

``kazma spaces migrate --check``
    What must hold before a second person is let in, and whether each does.
    Reads only. Exit 0 when everything holds, 1 when something is missing.
``kazma spaces migrate --dry-run``
    The steps the move would make itself, with what each would change. Reads
    only.
``kazma spaces migrate``
    Makes those steps (one value per secret, a key per space, the owner's API
    token), after showing them and asking. The vault is copied first; a
    second run changes nothing. It does not turn Spaces on.
``kazma spaces migrate --switch-on``
    The same, then turns Spaces on -- only when every precondition holds;
    otherwise it says which do not and changes nothing more.

The checks are ``kazma_core.spaces.preconditions``.
"""

from __future__ import annotations

import argparse

__all__ = ["run"]


def _print_findings(findings: list) -> int:
    left = [f for f in findings if not f.ok]
    for f in findings:
        print(f"  [{'ok' if f.ok else 'MISSING'}] {f.title}")
        print(f"        {f.detail}")
        if not f.ok and f.fix:
            print(f"        What closes it: {f.fix}")
    print()
    if left:
        print(f"{len(left)} of {len(findings)} are missing: a second person cannot be added yet.")
    else:
        print("Everything holds: Spaces can be switched on (kazma spaces migrate --switch-on).")
    return len(left)


def _print_plan(steps: list[dict]) -> int:
    total = 0
    for step in steps:
        print(f"  {step['what']}:")
        if not step["changes"]:
            print("        nothing to do")
        for change in step["changes"]:
            total += 1
            print(f"        - {change}")
    return total


def _check() -> int:
    from kazma_core.diagnostic_scope import read_only_diagnostic
    from kazma_core.spaces import preconditions

    print("Before a second person is let in:")
    print()
    with read_only_diagnostic("kazma spaces migrate --check"):
        findings = preconditions.check(for_switch=True)
        notes = preconditions.notes()
    left = _print_findings(findings)
    for note in notes:
        print(f"Note: {note}")
    return 1 if left else 0


def _dry_run() -> int:
    from kazma_core.diagnostic_scope import read_only_diagnostic
    from kazma_core.spaces import preconditions

    print("What kazma spaces migrate would change (nothing is changed now):")
    print()
    with read_only_diagnostic("kazma spaces migrate --dry-run"):
        steps = preconditions.plan()
    if not _print_plan(steps):
        print()
        print("Nothing to change.")
    return 0


def _migrate(options: argparse.Namespace) -> int:
    from kazma_core.spaces import preconditions
    from kazma_core.spaces.scope import spaces_enforced

    steps = preconditions.plan()
    print("kazma spaces migrate will:")
    print()
    changes = _print_plan(steps)
    print()
    if changes and not options.yes:
        print("The vault is copied first. Type yes to make these changes: ", end="", flush=True)
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            print("Nothing was changed.")
            return 1
    if changes:
        done = preconditions.apply()
        if done["backup"]:
            print(f"The vault was copied to {done['backup']}")
        if done["vault_copies"]:
            print(f"One value each now: {', '.join(done['vault_copies'])}")
        if done["space_keys"]["keyed"]:
            print(f"Keys made for: {', '.join(done['space_keys']['keyed'])}")
        for space, count in done["space_keys"]["sealed"].items():
            print(f"Sealed {count} secret(s) of space {space} with its key")
        if done["owner_token"]:
            print(f"The owner's API token for the deploy check is in {preconditions.owner_token_path()}")
        print()
    else:
        print("Nothing to change.")
        print()
    findings = preconditions.check(for_switch=True)
    left = _print_findings(findings)
    if not options.switch_on:
        return 0
    if spaces_enforced():
        print("Spaces is already on.")
        return 0
    if left:
        print("Spaces was not switched on.")
        return 1
    try:
        preconditions.switch_on(by="kazma spaces migrate")
    except preconditions.PreconditionsMissing as exc:
        print(f"Spaces was not switched on. {exc}")
        return 1
    print("Spaces is on. Reload Kazma when it is idle (kazma_guard.py --reload --when-idle) so the")
    print("running server reads the switch; then add people in Settings -> Install -> People.")
    return 0


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kazma spaces", description="Several people on one Kazma.")
    sub = parser.add_subparsers(dest="command")
    migrate = sub.add_parser("migrate", help="Check and make the move to Spaces")
    migrate.add_argument("--check", action="store_true", help="Say what is missing; change nothing")
    migrate.add_argument("--dry-run", action="store_true", help="Say what would change; change nothing")
    migrate.add_argument("--yes", "-y", action="store_true", help="Make the changes without asking")
    migrate.add_argument("--switch-on", action="store_true",
                         help="Turn Spaces on once every precondition holds")
    options = parser.parse_args(argv)
    if options.command != "migrate":
        parser.print_help()
        return 2
    if options.check:
        return _check()
    if options.dry_run:
        return _dry_run()
    return _migrate(options)
