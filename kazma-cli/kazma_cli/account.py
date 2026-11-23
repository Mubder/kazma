"""``kazma account`` -- what only the machine Kazma runs on may do to an
account (Spaces S4-02's re-check).

``kazma account second-factor off --account <id>``
    Takes the account's second factor off with no code: the way back for an
    owner who lost the phone and the recovery codes, or whose vault was
    reset (the factor marked on with nothing in the vault). It runs only on
    this machine, with no server: it holds the runtime lock while it works,
    and is refused while a Kazma server holds it (stop Kazma first). It
    reads the vault directly and records the change in the People record.
    The owner's account id is ``owner``.
"""

from __future__ import annotations

import argparse

__all__ = ["run"]

#: Who the People record says made the change.
_BY = "this machine (kazma account second-factor off)"


def _second_factor_off(account_id: str, yes: bool) -> int:
    from kazma_core.paths import data_dir
    from kazma_core.runtime_writer import RuntimeWriterBusy, RuntimeWriterLease
    from kazma_core.security import second_factor
    from kazma_core.spaces import identity
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

    lease = RuntimeWriterLease(data_dir())
    try:
        lease.acquire()
    except RuntimeWriterBusy:
        print("A Kazma server is running on this data folder. Stop it first")
        print("(kazma_guard.py --pause --stop), then run this again.")
        return 2
    try:
        if account_id != OWNER_ACCOUNT_ID and account_id not in identity.read_registry()["accounts"]:
            print(f"There is no account {account_id!r}. The owner's account is {OWNER_ACCOUNT_ID!r}.")
            return 1
        if not yes:
            print(f"This takes the second factor of account {account_id!r} off with no code.")
            print("Type yes to go on: ", end="", flush=True)
            try:
                answer = input().strip().lower()
            except EOFError:
                answer = ""
            if answer != "yes":
                print("Nothing was changed.")
                return 1
        done = second_factor.break_glass(account_id, by=_BY)
    finally:
        lease.release()
    print(f"The second factor of account {account_id!r} is off. Sign in with the password")
    print("(or the install's secret, for the owner), then turn it on again in My account.")
    if not done["vault_cleared"]:
        print("The vault could not be opened, so its copy was left there; it is never asked for.")
    return 0


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kazma account", description="What only this machine may do to an account.")
    sub = parser.add_subparsers(dest="command")
    factor = sub.add_parser("second-factor", help="An account's second factor")
    factor.add_argument("action", choices=["off"], help="off: take it off with no code")
    factor.add_argument("--account", required=True, help="The account id (the owner's is 'owner')")
    factor.add_argument("--yes", "-y", action="store_true", help="Do it without asking")
    options = parser.parse_args(argv)
    if options.command != "second-factor":
        parser.print_help()
        return 2
    return _second_factor_off(str(options.account).strip(), options.yes)
