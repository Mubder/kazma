"""Resource limits for a child process, without ``preexec_fn``.

``subprocess``'s ``preexec_fn`` runs Python code in the child between
``fork()`` and ``exec()``. Python's own documentation says it is not safe in a
process with threads — the child can deadlock on a lock another thread held
at the fork — and the server is always multi-threaded (``to_thread`` workers,
the procedural recorder, document workers). The document sandbox and the
``python_exec`` tool both set their memory/CPU limits that way (audit
2026-09-22).

Instead, the command is wrapped in a tiny launcher: a fresh interpreter,
single-threaded, sets the limits on itself and then ``exec``s the real
command, which inherits them. POSIX only; Windows uses Job Objects and gets
the command back unchanged.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

__all__ = ["LIMIT_FAILURE_EXIT", "limited_command"]

#: Exit status of a strict launcher that could not apply a limit.
LIMIT_FAILURE_EXIT = 125

# Runs in the child. argv: <strict> <memory|-> <cpu|-> -- <command...>
# Every limit is read back before the exec: setrlimit can succeed without
# setting what was asked (a value it reinterprets), and a strict launcher
# promises the limit is in force, not that the call returned. Each limit is
# tried on its own, so a lenient launcher whose memory limit is refused still
# applies the CPU limit.
_LAUNCHER = """\
import os, resource, sys
strict, mem, cpu = sys.argv[1] == "1", sys.argv[2], sys.argv[3]
cmd = sys.argv[5:]
for kind, value in ((resource.RLIMIT_AS, mem), (resource.RLIMIT_CPU, cpu)):
    if value == "-":
        continue
    try:
        limit = int(value)
        resource.setrlimit(kind, (limit, limit))
        if resource.getrlimit(kind) != (limit, limit):
            raise ValueError("limit %d did not hold: %r" % (limit, resource.getrlimit(kind)))
    except Exception as exc:
        if strict:
            sys.stderr.write("resource limits could not be applied: %s\\n" % exc)
            sys.exit(125)
os.execvp(cmd[0], cmd)
"""


def limited_command(
    command: Sequence[str],
    *,
    memory_bytes: int | None = None,
    cpu_seconds: int | None = None,
    strict: bool = True,
    posix: bool | None = None,
) -> list[str]:
    """*command* wrapped so it runs under the given limits.

    ``strict`` launchers refuse to run the command (exit
    :data:`LIMIT_FAILURE_EXIT`) when a limit cannot be applied, so a caller
    that reports "limits enforced" is never wrong about it. ``posix``
    overrides platform detection (tests).

    Raises:
        ValueError: a limit below 1. ``setrlimit`` reads a negative value as
            unsigned — effectively unlimited — so a strict launcher used to
            run the command unlimited while its caller reported the limit
            enforced; zero kills the child before it starts.
    """
    for name, value in (("memory_bytes", memory_bytes), ("cpu_seconds", cpu_seconds)):
        if value is not None and int(value) < 1:
            raise ValueError(f"{name} must be a positive limit or None, got {value!r}")
    argv = [str(part) for part in command]
    if posix is None:
        posix = os.name != "nt"
    if not posix or (memory_bytes is None and cpu_seconds is None):
        return argv
    return [
        sys.executable,
        "-I",
        "-c",
        _LAUNCHER,
        "1" if strict else "0",
        "-" if memory_bytes is None else str(int(memory_bytes)),
        "-" if cpu_seconds is None else str(int(cpu_seconds)),
        "--",
        *argv,
    ]
