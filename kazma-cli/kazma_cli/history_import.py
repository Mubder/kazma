"""``kazma import <file>``: a ChatGPT or Claude export into Kazma's memory
(REMAINING 4.2, ``kazma_core.memory.history_import``).

It reads the export, says what it holds (conversations, turns, from when to
when), and imports on a yes (``-y`` says yes; ``--dry-run`` only says). It
runs in this process: the meaning-search model loads here, once.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from collections.abc import Callable

__all__ = ["main"]


def _day(epoch: float | None) -> str:
    return _dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d") if epoch else "?"


def main(argv: list[str] | None = None, *, ask: Callable[[str], str] = input,
         say: Callable[[str], None] = print) -> int:
    from kazma_core.english_count import count_noun
    from kazma_core.memory import history_import

    parser = argparse.ArgumentParser(prog="kazma import",
                                     description="Import a ChatGPT or Claude export into Kazma's memory.")
    parser.add_argument("file", help="the export: its .zip, or conversations.json")
    parser.add_argument("--dry-run", action="store_true", help="say what it holds; import nothing")
    parser.add_argument("-y", "--yes", action="store_true", help="import without asking")
    args = parser.parse_args(argv)
    try:
        history = history_import.read_export(args.file)
    except history_import.ImportRefused as exc:
        say(f"Not imported: {exc}.")
        return 1
    found = history.summary()
    name = "ChatGPT" if history.source == "chatgpt" else "Claude"
    say(f"A {name} export: {count_noun(found['chats'], 'conversation')}, {count_noun(found['turns'], 'turn')}, "
        f"from {_day(found['first'])} to {_day(found['last'])}.")
    if not found["turns"] or args.dry_run:
        return 0
    say("Each turn becomes a memory with its own date; the Memory page lists them and Forget forgets them.")
    say("Weekly summaries of the imported weeks are written over the next hours (model calls).")
    if not args.yes:
        try:
            answer = ask("Import it? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            say("Not imported.")
            return 2

    def progress(done: int, total: int) -> None:
        if done == total or done % 50 == 0:
            sys.stdout.write(f"\r  {done} of {total}")
            sys.stdout.flush()

    report = history_import.import_history(history, progress=progress)
    say("")
    say(f"Done: {count_noun(report.written, 'turn')} remembered, {report.already} already there, "
        f"{report.refused} kept out, {report.failed} failed.")
    return 0 if not report.failed else 1
