"""psycopg reaches Postgres even when Windows refuses its bundled client library.

Live 2026-11-26, 11:30 UTC: Windows' Application Control (Smart App Control)
began refusing the client library psycopg's binary wheel bundles ("DLL load
failed while importing pq: An Application Control policy has blocked this
file"). The running server had loaded it before the policy came; the next
reload could not, psycopg found no other libpq, and Kazma started read-only
with the database answering the whole time.

psycopg tries its compiled ("binary") wrapper first and then its pure-Python
one, which loads ``libpq.dll`` through ctypes from a folder on PATH. The
PostgreSQL that ``kazma setup postgres`` installs ships its own signed
``libpq.dll`` in its ``bin`` folder. When Windows refuses the bundled
library, that folder is added to the end of this process's PATH, so the
fallback finds a library Windows allows. While Smart App Control checks
every file (``kazma_core.windows_policy``), that signed library is used first
and the bundled, unsigned one is never loaded (``PSYCOPG_IMPL=python``): a
file Windows would refuse is not tried at each start.

How it is said (the owner's rule, 2026-11-26): Windows checking a file is
Windows doing its job, never a fault. A fallback that worked is one INFO
line -- what Windows checked, what Kazma used instead -- with the operator's
detail; only "no signed alternative here" is a WARNING, and it names what
waits (reaching the database). :mod:`kazma_core.windows_trust` reads the same
answers for ``kazma doctor``, setup and the security report.

Standard library only, and nothing is imported from psycopg: this runs when
``kazma_core`` is imported, before anything in Kazma imports psycopg (every
import of it in Kazma is inside a function).
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["REFUSALS", "added_folder", "bundled_files", "load_error", "postgres_libpq_folders", "preferred",
           "reach_libpq"]

#: Windows' answers that refuse a library by policy: access denied, blocked
#: by group policy, and an Application Control policy (the one live met).
REFUSALS = frozenset({5, 1260, 4551})
_REFUSALS = REFUSALS

#: The folder this process added to PATH, once it did.
_added: Path | None = None


def _on_windows() -> bool:
    return os.name == "nt"


def _bundled() -> list[Path]:
    """psycopg_binary's compiled wrapper and the libpq it bundles (found, not imported)."""
    spec = importlib.util.find_spec("psycopg_binary")
    if spec is None or not spec.submodule_search_locations:
        return []
    root = Path(next(iter(spec.submodule_search_locations)))
    return sorted(root.glob("pq*.pyd")) + sorted((root.parent / "psycopg_binary.libs").glob("libpq*.dll"))


def bundled_files() -> list[Path]:
    """psycopg_binary's compiled wrapper and its bundled libpq (empty when not installed)."""
    return _bundled()


def load_error(path: Path) -> int | None:
    """Load ``path`` as a library: None when it loaded, else Windows' error
    code (-1 when the error carries none). Windows only."""
    import ctypes

    try:
        ctypes.WinDLL(str(path))
    except OSError as exc:
        code = getattr(exc, "winerror", None)
        return code if isinstance(code, int) else -1
    return None


def _refused(path: Path) -> bool:
    """True when Windows refuses to load ``path`` by policy."""
    return load_error(path) in REFUSALS


def _postgres_bins() -> list[Path]:
    """Installed PostgreSQL ``bin`` folders that hold ``libpq.dll``, the newest major first."""
    roots = {os.environ.get(v) for v in ("ProgramFiles", "ProgramW6432")} | {r"C:\Program Files"}
    found = {lib.parent for root in roots if root for lib in Path(root, "PostgreSQL").glob("*/bin/libpq.dll")}

    def major(folder: Path) -> int:
        head = folder.parent.name.split(".")[0]
        return int(head) if head.isdigit() else 0

    return sorted(found, key=major, reverse=True)


def postgres_libpq_folders() -> list[Path]:
    """Installed PostgreSQL ``bin`` folders holding ``libpq.dll``, the newest first."""
    return _postgres_bins()


def added_folder() -> Path | None:
    """The PostgreSQL ``bin`` this process put on PATH for psycopg, if it did."""
    return _added


def preferred() -> bool:
    """True when this process chose the signed PostgreSQL library before
    trying the bundled one (Smart App Control checks files: :func:`reach_libpq`)."""
    return _preferred


#: Set when the signed library was chosen first (Smart App Control on).
_preferred = False


def _checks_files() -> bool:
    """Smart App Control checks every file on this PC (``kazma_core.windows_policy``)."""
    from kazma_core.windows_policy import checks_files

    return checks_files()


def _prefer_signed(bundled: list[Path]) -> Path | None:
    """Smart App Control checks every file: use the installed PostgreSQL's
    signed ``libpq.dll`` (psycopg's pure-Python wrapper) and never load the
    bundled, unsigned one (measured 2026-11-26: psycopg_binary's five files
    carry no signature; PostgreSQL 18's libpq and the DLLs it needs are signed
    by EnterpriseDB). None when no installed one loads, or the operator chose
    psycopg's implementation (``PSYCOPG_IMPL``)."""
    global _added, _preferred
    if os.environ.get("PSYCOPG_IMPL"):
        return None
    folder = next((b for b in _postgres_bins() if load_error(b / "libpq.dll") is None), None)
    if folder is None:
        return None
    path = os.environ.get("PATH", "")
    _added, _preferred = folder, True
    os.environ["PATH"] = f"{path}{os.pathsep}{folder}" if path else str(folder)
    os.environ["PSYCOPG_IMPL"] = "python"
    logger.info("[postgres] Windows checks each file on this PC (Smart App Control), so Kazma uses the "
                "installed PostgreSQL's signed client library (%s\\libpq.dll) through psycopg's Python "
                "wrapper; psycopg's bundled library (%s) is not loaded", folder,
                ", ".join(p.name for p in bundled))
    return folder


def reach_libpq() -> Path | None:
    """On Windows, when the bundled libpq is refused, add an installed PostgreSQL's ``bin`` to PATH.

    While Smart App Control checks every file, the signed PostgreSQL library
    is used first, and the bundled one is never tried (:func:`_prefer_signed`).

    Returns the folder added, or None when nothing was needed or nothing
    could be done (said then: at WARNING only when no signed alternative is
    here, naming what waits)."""
    global _added
    if not _on_windows() or _added is not None:
        return _added
    try:
        bundled = _bundled()
        if bundled and _checks_files():
            chosen = _prefer_signed(bundled)
            if chosen is not None:
                return chosen
        refused = [p for p in bundled if _refused(p)]
        if not refused:
            return None
        path = os.environ.get("PATH", "")
        if any(Path(d, "libpq.dll").is_file() for d in path.split(os.pathsep) if d):
            logger.info("[postgres] Windows' file check did not allow psycopg's bundled client "
                        "library on this PC; psycopg uses the libpq.dll already on PATH (%s)",
                        ", ".join(p.name for p in refused))
            return None
        bins = _postgres_bins()
    except (OSError, ImportError, ValueError) as exc:
        logger.warning("[postgres] could not check psycopg's client library: %s", type(exc).__name__)
        return None
    if not bins:
        logger.warning("[postgres] Windows' file check did not allow psycopg's bundled client library "
                       "on this PC, and no signed PostgreSQL client library is installed here: reaching "
                       "the database waits until one is (kazma setup postgres installs it) or an update "
                       "brings a signed version. Nothing is wrong with the PC. Detail: %s refused by an "
                       "Application Control policy", ", ".join(p.name for p in refused))
        return None
    _added = bins[0]
    os.environ["PATH"] = f"{path}{os.pathsep}{_added}" if path else str(_added)
    logger.info("[postgres] Windows checked psycopg's bundled client library and did not allow it on "
                "this PC; Kazma uses the installed PostgreSQL's signed library instead (%s\\libpq.dll, "
                "added to this process's PATH). Detail: %s refused by an Application Control policy",
                _added, ", ".join(p.name for p in refused))
    return _added
