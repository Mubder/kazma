#!/usr/bin/env python
"""Print a door report (Spaces S4-01, step 1: report mode).

A running Kazma counts every checkout of its own Postgres by the door it went
through -- a space's, the install's by its reason, or none -- and by caller,
in ``<data dir>/space_doors.json`` (``kazma_core.db.space_doors``). This
reads one, from this install or from a file named, and sets it beside the
lists in ``tests/_space_door_lists.py``:

* **findings**: checkouts through no door, by where they came from; each is
  ``known`` (``KNOWN_OPEN``: through a listed entry, from one of its listed
  first callers, with the step that gives it one) or **new**;
* **install**: the install's door, by reason and caller;
* **spaces**: a space's door, by caller (the bound scope, or a row's own);
* **not seen**: functions of ``CHECKOUTS`` no checkout came from (not proven
  by this report), and callers seen that ``CHECKOUTS`` lacks (a spelling the
  source gate misses).

Reads only. Usage (repo root, the dev checkout's Python)::

    python scripts/space_doors_report.py                      # this install's report
    python scripts/space_doors_report.py --file <path>        # another install's, read only
    python scripts/space_doors_report.py --strict             # exit 1 on a new finding
    python scripts/space_doors_report.py --since 2026-12-01T10:00  # only what was seen since (a deploy)

The report adds up across restarts and deploys and never forgets: once a step
gives a caller its door, its old finding stays in the file. ``--since`` (UTC)
keeps the entries seen since then; their counts are still their totals.

Exit code: 0; 1 with ``--strict`` when a finding is new, or the report cannot
be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

#: Pools whose callers the source gate lists. The checkpointer's are mostly
#: LangGraph's and whoever called the saver: its own Kazma callers (the
#: gateway's listings, the approvals') are counted when they are first.
_LISTED_POOLS = ("shared", "memory mirror", "pgvector")


def _lists() -> tuple[set[str], dict[str, tuple[str, str, tuple[str, ...]]]]:
    from tests._space_door_lists import CHECKOUTS, KNOWN_OPEN

    declared = {f"{rel}::{function}" for rel, functions in CHECKOUTS.items() for function in functions}
    return declared, dict(KNOWN_OPEN)


def _known(callers: list[str], known: dict[str, tuple[str, str, tuple[str, ...]]]) -> tuple[str, str] | None:
    """The (why, step) of the entry a finding passes through, when its first
    caller is one the entry lists; else None: new."""
    for caller in callers:
        if caller in known and callers and callers[0] in known[caller][2]:
            return known[caller][0], known[caller][1]
    return None


def _utc(when: str) -> str:
    """*when* (ISO, UTC when it names no zone) as the report writes times."""
    from datetime import UTC, datetime

    at = datetime.fromisoformat(when)
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(UTC).isoformat(timespec="seconds")


def summarise(report: dict[str, Any], *, since: str | None = None) -> dict[str, Any]:
    """The report set beside the lists: what :func:`main` prints. *since*:
    only the entries last seen then or later."""
    declared, known = _lists()
    entries = [e for e in report.get("entries", []) if isinstance(e, dict)]
    if since:
        cut = _utc(since)
        entries = [e for e in entries if str(e.get("last") or "") >= cut]
    findings = []
    for entry in entries:
        if entry.get("door") != "none":
            continue
        said = _known(entry.get("callers") or [], known)
        findings.append({**entry, "known": said is not None, "why": said[0] if said else "",
                         "step": said[1] if said else ""})
    seen = {(e.get("callers") or [""])[0] for e in entries if e.get("pool") in _LISTED_POOLS or (
        e.get("pool") == "checkpoints" and str((e.get("callers") or [""])[0]).startswith("kazma-"))}
    return {
        "since": _utc(since) if since else report.get("since"), "updated": report.get("updated"),
        "cut": bool(since),
        "checkouts": report.get("checkouts", 0), "dropped": report.get("dropped", 0),
        "failures": report.get("failures", 0),
        "findings": findings,
        "new": [f for f in findings if not f["known"]],
        "install": [e for e in entries if e.get("door") == "install"],
        "spaces": [e for e in entries if e.get("door") == "space"],
        "not_seen": sorted(declared - seen),
        "not_listed": sorted(c for c in seen - declared if c.startswith("kazma-")),
    }


def _line(entry: dict[str, Any]) -> str:
    return f"{entry.get('count', 0):>9}  {entry.get('pool', '?'):<13} {' <- '.join(entry.get('callers') or [])}"


def render(summary: dict[str, Any]) -> str:
    out = [f"Door report: {summary['checkouts']} checkouts from {summary['since']} to {summary['updated']}"
           f" (dropped {summary['dropped']}, failures {summary['failures']})"]
    if summary.get("cut"):
        out.append(f"Only what was seen since {summary['since']}; each count is its total since first seen.")
    out.append("")
    out.append(f"Findings, through no door: {len(summary['findings'])} ({len(summary['new'])} new)")
    for finding in summary["findings"]:
        mark = f"known, {finding['step']}" if finding["known"] else "NEW"
        out.append(f"{_line(finding)}  [{finding.get('how')}; {mark}]")
    out += ["", "The install's door, by reason:"]
    by_reason: dict[str, list[dict[str, Any]]] = {}
    for entry in summary["install"]:
        by_reason.setdefault(str(entry.get("reason")), []).append(entry)
    for reason, entries in sorted(by_reason.items(), key=lambda kv: -sum(e.get("count", 0) for e in kv[1])):
        out.append(f"  {reason} ({sum(e.get('count', 0) for e in entries)})")
        out += [f"  {_line(e)}" for e in entries]
    out += ["", "A space's door, by caller:"]
    out += [f"{_line(e)}  [{e.get('how')}]" for e in summary["spaces"]]
    out += ["", f"Declared checkouts no checkout came from: {len(summary['not_seen'])} (not proven by this report)"]
    out += [f"  {c}" for c in summary["not_seen"]]
    out += ["", f"Callers seen that CHECKOUTS lacks: {len(summary['not_listed'])}"]
    out += [f"  {c}" for c in summary["not_listed"]]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--file", help="a report to read (default: this install's)")
    parser.add_argument("--strict", action="store_true", help="exit 1 when a finding is new")
    parser.add_argument("--json", action="store_true", help="the summary as JSON (ASCII)")
    parser.add_argument("--since", help="only entries seen since this time (ISO, UTC when it names no zone)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from kazma_core.db import space_doors

    if args.file:
        path = Path(args.file)
    else:
        # This install's report: its data folder may be set in its .env, which
        # is read as the server reads it.
        from kazma_core.env_files import load_env_files

        load_env_files(include_cwd=False)
        path = space_doors.report_path()
    try:
        report = space_doors.read_report(path)
    except (OSError, ValueError) as exc:
        print(f"cannot read {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not report:
        print(f"no door report at {path} yet")
        return 0
    try:
        summary = summarise(report, since=args.since)
    except ValueError as exc:
        print(f"--since: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=1) if args.json else render(summary))
    return 1 if args.strict and summary["new"] else 0


if __name__ == "__main__":
    sys.exit(main())
