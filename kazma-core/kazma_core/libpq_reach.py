"""psycopg reaches Postgres even when Windows refuses its bundled client library.

Live 2026-11-26, 11:30 UTC: Windows' Application Control began refusing the
client library psycopg's binary wheel bundles ("DLL load failed while
importing pq: An Application Control policy has blocked this file"). The
running server had loaded it before the policy came; the next reload could
not, psycopg found no other libpq, and Kazma started read-only with the
database answering the whole time.

psycopg tries its compiled ("binary") wrapper first and then its pure-Python
one, which loads ``libpq.dll`` through ctypes from a folder on PATH. The
PostgreSQL that ``kazma setup postgres`` installs ships its own signed
``libpq.dll`` in its ``bin`` folder. When Windows refuses the bundled
library, that folder is added to the end of this process's PATH, so the
fallback finds a library Windows allows. Said once at WARNING.

Standard library only, and nothing is imported from psycopg: this runs when
``kazma_core`` is imported, before anything in Kazma imports psycopg (every
import of it in Kazma is inside a function).
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: Windows' answers that refuse a library by policy: access denied, blocked
#: by group policy, and an Application Control policy (the one live met).
_REFUSALS = frozenset({5, 1260, 4551})

#: The folder this process added to PATH, once it did.
_added: Path | None = None


def _on_windows() -> bool:
    return os.name == "nt"


def _in_workflow_sandbox() -> bool:
    """True inside Temporal's workflow sandbox, which imports ``kazma_core`` again.

    The sandbox refuses file access at import ("Cannot access
    pathlib.Path.glob.__call__ from inside a workflow"), and a workflow
    never opens a database connection: the check is not for it. Read
    without importing Temporal (2026-11-26: every durable swarm worker on
    Windows failed to start because this module looked for files there)."""
    workflow = sys.modules.get("temporalio.workflow")
    unsafe = getattr(workflow, "unsafe", None)
    in_sandbox = getattr(unsafe, "in_sandbox", None)
    return bool(in_sandbox()) if callable(in_sandbox) else False


def _bundled() -> list[Path]:
    """psycopg_binary's compiled wrapper and the libpq it bundles (found, not imported)."""
    spec = importlib.util.find_spec("psycopg_binary")
    if spec is None or not spec.submodule_search_locations:
        return []
    root = Path(next(iter(spec.submodule_search_locations)))
    return sorted(root.glob("pq*.pyd")) + sorted((root.parent / "psycopg_binary.libs").glob("libpq*.dll"))


def _refused(path: Path) -> bool:
    """True when Windows refuses to load ``path`` by policy."""
    import ctypes

    try:
        ctypes.WinDLL(str(path))
    except OSError as exc:
        return getattr(exc, "winerror", None) in _REFUSALS
    return False


def _postgres_bins() -> list[Path]:
    """Installed PostgreSQL ``bin`` folders that hold ``libpq.dll``, the newest major first."""
    # Windows sets these; the literal is for a process started without them.
    roots = {os.environ.get(v) for v in ("ProgramFiles", "ProgramW6432")} - {None, ""} or {r"C:\Program Files"}
    found = {lib.parent for root in roots if root for lib in Path(root, "PostgreSQL").glob("*/bin/libpq.dll")}

    def major(folder: Path) -> int:
        head = folder.parent.name.split(".")[0]
        return int(head) if head.isdigit() else 0

    return sorted(found, key=major, reverse=True)


def reach_libpq() -> Path | None:
    """On Windows, when the bundled libpq is refused, add an installed PostgreSQL's ``bin`` to PATH.

    Returns the folder added, or None when nothing was needed or nothing
    could be done (said at WARNING then)."""
    global _added
    if not _on_windows() or _added is not None or _in_workflow_sandbox():
        return _added
    try:
        bundled = _bundled()
        if not bundled or not any(_refused(p) for p in bundled):
            return None
        path = os.environ.get("PATH", "")
        if any(Path(d, "libpq.dll").is_file() for d in path.split(os.pathsep) if d):
            return None
        bins = _postgres_bins()
    except (OSError, ImportError, ValueError) as exc:
        logger.warning("[postgres] could not check psycopg's client library: %s", type(exc).__name__)
        return None
    if not bins:
        logger.warning("[postgres] Windows refuses psycopg's bundled client library (an Application "
                       "Control policy), and no installed PostgreSQL holds libpq.dll: Kazma cannot "
                       "reach Postgres until the policy allows it")
        return None
    _added = bins[0]
    os.environ["PATH"] = f"{path}{os.pathsep}{_added}" if path else str(_added)
    logger.warning("[postgres] Windows refuses psycopg's bundled client library (an Application "
                   "Control policy); psycopg uses %s\\libpq.dll instead (added to this process's PATH)",
                   _added)
    return _added
