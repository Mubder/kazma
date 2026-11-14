"""``kazma report <what happened>``: a problem report from the terminal (2026-11-14).

Shows the whole report (``kazma_core.support.problem_report``) and sends it
only on a yes (``-y`` says yes; ``--dry-run`` only shows it). A report that
cannot be sent is saved, and the command says where.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

__all__ = ["main"]


def main(argv: list[str] | None = None, *, ask: Callable[[str], str] = input,
         say: Callable[[str], None] = print) -> int:
    from kazma_core.support import problem_report

    parser = argparse.ArgumentParser(prog="kazma report",
                                     description="Report a problem to Kazma's makers (you see it first).")
    parser.add_argument("note", nargs="*", help="what happened, in your words")
    parser.add_argument("--dry-run", action="store_true", help="show the report; send nothing")
    parser.add_argument("-y", "--yes", action="store_true", help="send without asking")
    args = parser.parse_args(argv)
    note = " ".join(args.note).strip()
    if not note and not args.yes:
        try:
            note = ask("What happened? ").strip()
        except EOFError:
            note = ""
    report = problem_report.collect(note, where="cli")
    say("This is the whole report. Nothing private is in it: no chats, memories, documents, settings or keys,")
    say("and home folders, emails and IP addresses are taken out.\n")
    say(problem_report.preview(report))
    if args.dry_run:
        return 0
    if not args.yes:
        try:
            answer = ask("\nSend it? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            say("Not sent.")
            return 2
    result = problem_report.send(report)
    say(problem_report.describe(result))
    return 0 if result.sent else 1

