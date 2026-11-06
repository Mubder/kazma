"""``kazma observer`` -- pair this install with the outside observer.

The observer (``deploy/observer``, resilience plan B1) watches the install
from outside the machine. ``pair`` registers the install with it and writes
``<install>/.kazma/observer.key``; the guard then sends it a signed heartbeat
every minute. ``status`` says what the guard and the observer see;
``unpair`` stops it (``--forget`` also deletes what the observer kept).

The pairing token is the observer's ``PAIRING_TOKEN`` secret. It is read
from ``KAZMA_OBSERVER_PAIRING_TOKEN`` or asked for without echo, never taken
on the command line (it would sit in the shell's history).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import time


def _token() -> str:
    token = (os.environ.get("KAZMA_OBSERVER_PAIRING_TOKEN") or "").strip()
    if token:
        return token
    return getpass.getpass("The observer's pairing token (PAIRING_TOKEN): ").strip()


def _print_status(data: dict) -> None:
    if not data.get("paired"):
        print("outside watch : not paired")
        print("  pair it     : kazma observer pair")
        return
    print(f"outside watch : paired as install {data.get('install')} with {data.get('url')}")
    guard = data.get("guard") or {}
    if guard:
        ack = guard.get("last_ack")
        seen = f", last answered {int(time.time() - ack)} s ago" if isinstance(ack, (int, float)) else ""
        print(f"  guard       : {guard.get('state')}{seen}")
    else:
        print("  guard       : no record (no guard has sent a heartbeat for this install yet)")
    observer = data.get("observer") or {}
    if observer.get("ok"):
        print(f"  observer    : {observer.get('condition')}"
              + (f" ({observer.get('detail')})" if observer.get("detail") else ""))
        pct = observer.get("up_7d_pct")
        if pct is not None:
            print(f"  this week   : up {pct} %")
        for inc in (observer.get("incidents_7d") or [])[:5]:
            print(f"  incident    : {inc.get('cause')}, "
                  f"{'ongoing' if not inc.get('ended_at') else 'ended'}")
    else:
        print(f"  observer    : did not answer ({observer.get('error')})")


def run(args: list[str]) -> int:
    from kazma_core.observability import outside_watch

    parser = argparse.ArgumentParser(
        prog="kazma observer",
        description="Pair this install with the outside observer (status.kazma.ai).")
    sub = parser.add_subparsers(dest="action", required=True)
    p_pair = sub.add_parser("pair", help="register this install and write the pairing key")
    p_pair.add_argument("--url", default=outside_watch.DEFAULT_OBSERVER_URL,
                        help="the observer's address (default: %(default)s)")
    p_un = sub.add_parser("unpair", help="stop the observer watching this install")
    p_un.add_argument("--forget", action="store_true", help="also delete what the observer kept")
    p_un.add_argument("--url", default=None, help="the observer's address (default: the paired one)")
    p_st = sub.add_parser("status", help="what the guard and the observer see")
    p_st.add_argument("--json", action="store_true")
    options = parser.parse_args(args)

    try:
        if options.action == "pair":
            done = outside_watch.pair_with_observer(options.url, _token())
            print(f"Paired as install {done['install']} with {done['url']}.")
            print(f"The key is in {done['path']} (readable by this account only).")
            print("The guard sends its first heartbeat within a minute; check with: kazma observer status")
            return 0
        if options.action == "unpair":
            done = outside_watch.unpair_from_observer(_token(), forget=options.forget, url=options.url)
            print(f"Unpaired install {done['install']} from {done['url']}"
                  + (" and deleted what it kept." if done["forgotten"] else "."))
            return 0
        data = outside_watch.outside_status()
        if options.json:
            print(json.dumps(data, indent=2))
        else:
            _print_status(data)
        return 0
    except outside_watch.PairingError as exc:
        print(f"Error: {exc}")
        return 1
