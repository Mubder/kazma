"""Discover optional system binaries used by document engines."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

__all__ = ["find_soffice", "run_soffice_cli", "windows_no_window_flags"]

logger = logging.getLogger(__name__)

# CREATE_NO_WINDOW — hide console on Windows (soffice.exe otherwise pops a
# "Press Enter to continue..." terminal during --version probes).
_CREATE_NO_WINDOW = 0x08000000


def windows_no_window_flags() -> int:
    """Return CREATE_NO_WINDOW on Windows, else 0."""
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW))


def _prefer_cli_binary(path: str | Path) -> str:
    """On Windows prefer ``soffice.com`` over ``soffice.exe`` for CLI use.

    ``soffice.exe`` is a GUI launcher that often opens an interactive console
    showing the version banner and waiting for Enter. ``soffice.com`` is the
    true console binary and works headlessly with redirected stdio.
    """
    resolved = Path(path)
    if sys.platform == "win32" and resolved.suffix.lower() == ".exe":
        com = resolved.with_suffix(".com")
        if com.is_file():
            return str(com)
    return str(resolved)


def find_soffice() -> str | None:
    """Locate a LibreOffice CLI binary, including common install paths.

    On Windows returns ``soffice.com`` when present (avoids GUI console popups).
    """

    names = (
        ("soffice.com", "soffice.exe", "soffice", "libreoffice")
        if sys.platform == "win32"
        else ("soffice", "libreoffice")
    )
    for name in names:
        found = shutil.which(name)
        if found:
            return _prefer_cli_binary(found)

    candidates: list[Path] = []
    if sys.platform == "win32":
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        for root in roots:
            if not root:
                continue
            base = Path(root)
            # Prefer .com first in each install tree.
            for pattern in (
                "LibreOffice*/program/soffice.com",
                "LibreOffice*/program/soffice.exe",
            ):
                try:
                    candidates.extend(base.glob(pattern))
                except OSError:
                    continue
            candidates.append(base / "LibreOffice" / "program" / "soffice.com")
            candidates.append(base / "LibreOffice" / "program" / "soffice.exe")
    else:
        candidates.extend(
            (
                Path("/usr/bin/soffice"),
                Path("/usr/bin/libreoffice"),
                Path("/usr/lib/libreoffice/program/soffice"),
                Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
            )
        )

    seen: set[str] = set()
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            resolved = str(candidate.resolve())
        except OSError:
            continue
        preferred = _prefer_cli_binary(resolved)
        if preferred in seen:
            continue
        seen.add(preferred)
        if Path(preferred).is_file():
            return preferred
    return None


def run_soffice_cli(
    args: Sequence[str],
    *,
    timeout: float = 30,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run LibreOffice headlessly without popping a Windows console.

    Prefers ``soffice.com`` on Windows, always redirects stdin from DEVNULL,
    and uses CREATE_NO_WINDOW so version probes never show
    "Press Enter to continue...".
    """
    executable = find_soffice()
    if not executable:
        raise FileNotFoundError("soffice not found")
    cmd = [executable, *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        check=False,
        timeout=timeout,
        text=True,
        stdin=subprocess.DEVNULL,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        creationflags=windows_no_window_flags(),
    )
