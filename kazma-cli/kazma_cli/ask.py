"""CLI wrapper for ``kazma ask`` / ``kazma acp``."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from kazma_core.cli.ask import (
    ask_help_text,
    parse_ask_argv,
    run_acp_stdio,
    run_ask,
)


def _hitl_decide_tty(payload: dict[str, Any], *, stdin_consumed: bool) -> dict[str, Any]:
    """TTY y/N (or a=always). Not a TTY / stdin already consumed → deny."""
    tool = payload.get("tool") or "danger tool"
    msg = str(payload.get("message") or f"Approve {tool}?").strip()
    print(f"\n[HITL] {msg}", file=sys.stderr)
    if stdin_consumed or not sys.stdin.isatty():
        print(
            "[HITL] denied (no TTY). Pass --yolo to allow danger tools.",
            file=sys.stderr,
        )
        return {"approved": False}
    try:
        print("Allow this once? [y/N/a=always] ", end="", file=sys.stderr, flush=True)
        ans = sys.stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return {"approved": False}
    if ans in ("y", "yes"):
        return {"approved": True}
    if ans in ("a", "always"):
        return {"approved": True, "always": True}
    return {"approved": False}


def _print_tool_line(ev: dict[str, Any]) -> None:
    tool = ev.get("tool") or "tool"
    if ev.get("event") == "tool_start":
        print(f"→ {tool}", file=sys.stderr, flush=True)
        return
    mark = "ok" if ev.get("ok") else "fail"
    print(f"{mark} {tool}", file=sys.stderr, flush=True)


def run(argv: list[str]) -> int:
    """Entry for ``kazma ask`` / ``kazma acp``. Returns process exit code."""
    try:
        opts = parse_ask_argv(argv)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(ask_help_text(), file=sys.stderr)
        return 2
    if opts.help:
        print(ask_help_text())
        return 0
    if opts.acp:
        return asyncio.run(run_acp_stdio(yolo=opts.yolo, workspace=opts.workspace))

    prompt = opts.prompt
    stdin_consumed = False
    if prompt == "-" or (not prompt and not sys.stdin.isatty()):
        prompt = sys.stdin.read()
        opts.prompt = prompt
        stdin_consumed = True
    if not (prompt or "").strip():
        print(ask_help_text(), file=sys.stderr)
        return 2

    printed_tokens = False

    def on_event(ev: dict[str, Any]) -> None:
        nonlocal printed_tokens
        if opts.json_out:
            print(json.dumps(ev, ensure_ascii=False), flush=True)
            if ev.get("event") == "token" and ev.get("text"):
                printed_tokens = True
            return
        kind = ev.get("event")
        if kind == "token" and ev.get("text"):
            sys.stdout.write(str(ev["text"]))
            sys.stdout.flush()
            printed_tokens = True
        elif kind in ("tool_start", "tool_end"):
            _print_tool_line(ev)
        elif kind == "error" and ev.get("text"):
            print(str(ev["text"]), file=sys.stderr)

    hitl_decide = None
    if not opts.yolo:
        def hitl_decide(payload: dict[str, Any]) -> dict[str, Any]:
            return _hitl_decide_tty(payload, stdin_consumed=stdin_consumed)

    try:
        result = asyncio.run(
            run_ask(
                prompt,
                opts,
                on_event=on_event if opts.stream else None,
                hitl_decide=hitl_decide,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    if opts.json_out and not opts.stream:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "text": result.text,
                    "thread_id": result.thread_id,
                    "error": result.error,
                    "plan": result.plan,
                    "yolo": result.yolo,
                },
                ensure_ascii=False,
            )
        )
    elif not opts.json_out:
        if printed_tokens:
            sys.stdout.write("\n")
            sys.stdout.flush()
        elif result.text:
            print(result.text)
        elif result.error:
            print(result.error, file=sys.stderr)
    return 0 if result.ok else 1
