"""Discover optional system binaries used by document engines."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

__all__ = ["find_soffice"]


def find_soffice() -> str | None:
    """Locate a LibreOffice ``soffice`` binary, including common install paths.

    Returns the absolute path when found, else ``None``. PATH is checked first;
    on Windows we also look under Program Files / LocalAppData LibreOffice
    folders so conversions work without a manual PATH edit.
    """

    for name in ("soffice", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found

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
            candidates.append(base / "LibreOffice" / "program" / "soffice.exe")
            try:
                for match in base.glob("LibreOffice*/program/soffice.exe"):
                    candidates.append(match)
            except OSError:
                continue
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
            resolved = str(candidate.resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.is_file():
            return resolved
    return None
