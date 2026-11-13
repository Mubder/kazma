"""Find the uv Kazma's environments are made with (2026-11-13).

Kazma's environment is made by uv (``setup.cmd``/``setup.sh``, ``uv sync``),
and a uv environment has no pip. Setup puts uv in ``~/.local/bin`` and adds
that folder to the account's PATH, which a terminal opened before setup ran
does not have yet. Looking on PATH alone, ``kazma setup postgres`` fell back
to ``python -m pip`` and failed ("No module named pip") on the drill VM's
fresh install. :func:`find_uv` also looks where uv's own installer and Kazma's
setup put it.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

__all__ = ["find_uv", "this_python_has_pip"]


def find_uv(which: Callable[[str], str | None] | None = None, *, home: Path | None = None) -> str | None:
    """uv on PATH (*which*, ``shutil.which`` by default), else in
    ``UV_INSTALL_DIR``, ``~/.local/bin`` (setup's and uv's installer's) or
    ``~/.cargo/bin`` (uv's installer before 0.5); None when it is in none."""
    found = (which or shutil.which)("uv")
    if found:
        return found
    exe = "uv.exe" if os.name == "nt" else "uv"
    home = home or Path.home()
    folders = [os.environ.get("UV_INSTALL_DIR") or "", home / ".local" / "bin", home / ".cargo" / "bin"]
    for folder in folders:
        if folder and (Path(folder) / exe).is_file():
            return str(Path(folder) / exe)
    return None


def this_python_has_pip() -> bool:
    """Whether ``python -m pip`` can run in the Python running Kazma."""
    return importlib.util.find_spec("pip") is not None and bool(sys.executable)
