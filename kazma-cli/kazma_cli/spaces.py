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
``kazma spaces keys``
    Each space's key (S1-26): its id, and how many of its secrets it seals.
    Never a key. Reads only.
``kazma spaces keys change SPACE`` / ``--all``
    Gives a space a new key (S4-07): its secrets are sealed again with it and
    its old keys are destroyed, recorded. Asks first (``--yes`` does not).

The checks are ``kazma_core.spaces.preconditions``; the keys are
``kazma_core.spaces.space_keys``.
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
        for item in done.get("chat_links") or ():
            print(f"The owner's {item['platform']} account {item['user_id']} is the owner's link now")
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


def _list_keys() -> int:
    from kazma_core.diagnostic_scope import read_only_diagnostic
    from kazma_core.security.vault import get_vault
    from kazma_core.spaces import identity
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    with read_only_diagnostic("kazma spaces keys"):
        spaces = identity.read_registry()["spaces"]
        vault = get_vault()
        if vault is None:
            print("The vault is off (KAZMA_VAULT_KEY is not set): no space has a key.")
            return 1
        found = vault.key_report()
    print("The key of each space (the owner's space has none: its secrets are the install's vault key's):")
    print()
    for space_id in sorted(s for s in spaces if s != OWNER_SPACE_ID):
        name = str((spaces[space_id] or {}).get("name") or space_id)
        key = found["keys"].get(space_id) or "none yet (made with its first sealed secret)"
        sealed = found["sealed"].get(space_id, 0)
        under_vault = found["unsealed"].get(space_id, 0)
        print(f"  {name} ({space_id}): key {key}, {sealed} secret(s) sealed with it"
              + (f", {under_vault} still under the install's vault key" if under_vault else ""))
    if found["broken"]:
        print()
        print(f"{found['broken']} secret(s) name a key this vault does not hold: they cannot be opened.")
    return 0


def _change_keys(options: argparse.Namespace) -> int:
    from kazma_core.spaces import identity, space_keys
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    if options.all:
        chosen = sorted(s for s, space in identity.read_registry()["spaces"].items()
                        if s != OWNER_SPACE_ID and not (space or {}).get("erasing_at"))
    elif options.space:
        chosen = [options.space]
    else:
        print("Name a space (kazma spaces keys change SPACE), or --all.")
        return 2
    if not chosen:
        print("No space has a key of its own yet.")
        return 0
    if not options.yes:
        print(f"The key of {', '.join(chosen)} will be changed: each space's secrets are sealed again with")
        print("its new key, and its old keys are destroyed. Type yes to go on: ", end="", flush=True)
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            print("Nothing was changed.")
            return 1
    refused = 0
    for space_id in chosen:
        try:
            done = space_keys.change_key(space_id, by="kazma spaces keys change")
        except space_keys.KeyChangeRefused as exc:
            refused += 1
            print(f"  {space_id}: not changed. {exc}")
            continue
        print(f"  {space_id}: new key {done['key_id']}, {done['sealed']} secret(s) sealed with it")
    if refused < len(chosen):
        print()
        print("A running Kazma keeps the old key in its memory until it reloads: reload it when idle")
        print("(kazma_guard.py --reload --when-idle).")
    return 1 if refused else 0


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kazma spaces", description="Several people on one Kazma.")
    sub = parser.add_subparsers(dest="command")
    migrate = sub.add_parser("migrate", help="Check and make the move to Spaces")
    migrate.add_argument("--check", action="store_true", help="Say what is missing; change nothing")
    migrate.add_argument("--dry-run", action="store_true", help="Say what would change; change nothing")
    migrate.add_argument("--yes", "-y", action="store_true", help="Make the changes without asking")
    migrate.add_argument("--switch-on", action="store_true",
                         help="Turn Spaces on once every precondition holds")
    keys = sub.add_parser("keys", help="Each space's key: list them, or change one")
    keys.add_argument("action", nargs="?", choices=["list", "change"], default="list")
    keys.add_argument("space", nargs="?", help="The space whose key is changed")
    keys.add_argument("--all", action="store_true", help="Change the key of every space")
    keys.add_argument("--yes", "-y", action="store_true", help="Change it without asking")
    options = parser.parse_args(argv)
    if options.command == "keys":
        return _change_keys(options) if options.action == "change" else _list_keys()
    if options.command != "migrate":
        parser.print_help()
        return 2
    if options.check:
        return _check()
    if options.dry_run:
        return _dry_run()
    return _migrate(options)
