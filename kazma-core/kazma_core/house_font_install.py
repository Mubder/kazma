"""Kazma's document font installed on the machine Kazma runs on (2026-11-12).

Every document Kazma makes is set in IBM Plex Sans Arabic (``documents.fonts``),
and Word and PowerPoint files carry it embedded. Office's Protected View loads
no embedded font, and on Windows it cannot use a font installed for one user
only: measured on the owner's machine, PowerPoint drew Arabic set in a
per-user IBM Plex as wrong glyphs of other scripts and Word showed Arial,
while fonts in the Windows Fonts folder (Arial, Calibri, Segoe UI, Dubai)
displayed correctly. So setup installs the bundled faces:

- **Windows**: for all users -- the files in the Windows Fonts folder, the
  machine's font list (HKLM) naming them -- and retires a per-user copy of the
  same faces, which shadows them for that user. Administrator rights.
- **macOS**: the user's font folder (``~/Library/Fonts``), where a font book
  install puts it.
- **Linux**: the user's font folder (``~/.local/share/fonts/kazma``), then
  ``fc-cache``.

:class:`HouseFont` is what ``kazma setup system`` and ``kazma setup install``
check and run (``system_deps``, ``kazma_cli.setup.run_system_step``); tests give
it a fake :class:`WindowsFonts`, home folder and runner.
"""

from __future__ import annotations

import hashlib
import ntpath
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["FACES", "HouseFont", "WindowsFonts"]

FAMILY = "IBM Plex Sans Arabic"
#: The bundled faces: file name -> the name Windows lists it under.
FACES: dict[str, str] = {
    "IBMPlexSansArabic-Regular.ttf": "IBM Plex Sans Arabic (TrueType)",
    "IBMPlexSansArabic-Bold.ttf": "IBM Plex Sans Arabic Bold (TrueType)",
}
#: What a check puts in ``missing``: the faces for all users, a per-user copy to retire.
NOT_INSTALLED = "ibm plex"
PER_USER_COPY = "per-user copy"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundled() -> Path | None:
    from kazma_core.documents.fonts import bundled_font_dir

    folder = bundled_font_dir()
    if folder is None or not all((folder / name).is_file() for name in FACES):
        return None
    return folder


@dataclass
class WindowsFonts:
    """The Windows font lists and folders (``winreg``, GDI). Tests replace it."""

    key: str = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"

    def system_dir(self) -> Path:
        return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

    def user_dir(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Microsoft" / "Windows" / "Fonts"

    def _root(self, scope: str):  # noqa: ANN202 -- winreg.HKEYType
        import winreg

        return winreg.HKEY_LOCAL_MACHINE if scope == "system" else winreg.HKEY_CURRENT_USER

    def entries(self, scope: str) -> dict[str, str]:
        """The registered fonts of *scope* ("system" or "user"): name -> file."""
        import winreg

        found: dict[str, str] = {}
        try:
            with winreg.OpenKey(self._root(scope), self.key) as handle:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(handle, index)
                    except OSError:
                        break
                    found[str(name)] = str(value)
                    index += 1
        except OSError:
            pass
        return found

    def set_system(self, name: str, file: str) -> None:
        import winreg

        with winreg.OpenKey(self._root("system"), self.key, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, file)

    def delete_user(self, name: str) -> None:
        import winreg

        with winreg.OpenKey(self._root("user"), self.key, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.DeleteValue(handle, name)

    def load(self, path: Path) -> None:
        import ctypes

        ctypes.windll.gdi32.AddFontResourceW(str(path))

    def unload(self, path: Path) -> None:
        import ctypes

        ctypes.windll.gdi32.RemoveFontResourceW(str(path))

    def announce(self) -> None:
        """Tell running programs the font list changed (WM_FONTCHANGE)."""
        import ctypes
        from ctypes import wintypes

        result = wintypes.DWORD()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001D, 0, 0, 0x0002, 1000, ctypes.byref(result))


@dataclass
class HouseFont:
    """IBM Plex Sans Arabic on this machine: :meth:`state` and :meth:`install`."""

    os_name: str
    windows: WindowsFonts = field(default_factory=WindowsFonts)
    home: Path = field(default_factory=Path.home)
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    which: Callable[[str], str | None] = shutil.which

    @classmethod
    def for_this_machine(cls) -> HouseFont:
        os_name = "windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux"
        return cls(os_name)

    # ── where ────────────────────────────────────────────────────────
    def _user_folder(self) -> Path:
        if self.os_name == "macos":
            return self.home / "Library" / "Fonts"
        return self.home / ".local" / "share" / "fonts" / "kazma"

    def _windows_file(self, data: str, folder: Path) -> Path:
        """A font list entry's file: a full path (a per-user entry) or a name
        in the Fonts folder (an all-users entry)."""
        return Path(data) if (ntpath.isabs(data) or os.path.isabs(data)) else folder / data

    def _windows_copies(self) -> tuple[set[str], list[tuple[str, Path]]]:
        """The bundled faces registered for all users (file names), and every
        per-user registration of one of them (name, file)."""
        system_dir = self.windows.system_dir()
        system = {
            ntpath.basename(data).lower()
            for data in self.windows.entries("system").values()
            if self._windows_file(data, system_dir).is_file()
        }
        wanted = {name.lower() for name in FACES}
        user_dir = self.windows.user_dir()
        per_user = [
            (name, self._windows_file(data, user_dir))
            for name, data in self.windows.entries("user").items()
            if ntpath.basename(data).lower() in wanted
        ]
        return system & wanted, per_user

    # ── check ────────────────────────────────────────────────────────
    def state(self):  # noqa: ANN201 -- system_deps.DepState (no import cycle)
        from kazma_core.system_deps import DepState

        if self.os_name == "windows":
            system, per_user = self._windows_copies()
            installed = len(system) == len(FACES)
            if installed and not per_user:
                return DepState("fonts", True, f"{FAMILY}, installed for all users")
            missing = ([] if installed else [NOT_INSTALLED]) + ([PER_USER_COPY] if per_user else [])
            if per_user and not installed:
                detail = (f"{FAMILY} is installed for this Windows user only: Office's Protected View "
                          "draws its Arabic as wrong letters")
            elif per_user:
                detail = f"{FAMILY} is installed for all users, and a per-user copy shadows it"
            else:
                detail = f"{FAMILY} is not installed for all users (Office's Protected View shows another face)"
            return DepState("fonts", False, detail, missing)
        if self._installed_elsewhere():
            return DepState("fonts", True, f"{FAMILY} installed")
        return DepState("fonts", False, f"{FAMILY} is not installed", [NOT_INSTALLED])

    def _installed_elsewhere(self) -> bool:
        folders = [self._user_folder()]
        if self.os_name == "macos":
            folders.append(Path("/Library/Fonts"))
        if any(all((folder / name).is_file() for name in FACES) for folder in folders):
            return True
        if self.os_name == "linux" and self.which("fc-list"):
            try:
                done = self.run(["fc-list", ":", "family"], stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=30, check=False, encoding="utf-8", errors="replace")
            except (OSError, subprocess.SubprocessError):
                return False
            return FAMILY in (done.stdout or "")
        return False

    # ── install ──────────────────────────────────────────────────────
    def install(self) -> tuple[bool, str]:
        """Install the bundled faces as the module docstring says. Returns
        (done, what happened); nothing is half-registered on a failure."""
        source = _bundled()
        if source is None:
            return False, "Kazma's bundled IBM Plex Sans Arabic files are missing"
        try:
            if self.os_name == "windows":
                return self._install_windows(source)
            folder = self._user_folder()
            folder.mkdir(parents=True, exist_ok=True)
            for name in FACES:
                shutil.copyfile(source / name, folder / name)
            if self.os_name == "linux" and self.which("fc-cache"):
                self.run(["fc-cache", "-f", str(folder)], stdin=subprocess.DEVNULL, capture_output=True,
                         timeout=120, check=False)
            return True, f"installed in {folder}"
        except PermissionError:
            return False, "needs an elevated terminal (Run as administrator)"
        except OSError as exc:
            return False, str(exc)

    def _install_windows(self, source: Path) -> tuple[bool, str]:
        system_dir = self.windows.system_dir()
        system, per_user = self._windows_copies()
        said: list[str] = []
        for name, listed_as in FACES.items():
            if name.lower() in system:
                continue
            target = system_dir / name
            if not target.is_file() or _digest(target) != _digest(source / name):
                shutil.copyfile(source / name, target)
            self.windows.set_system(listed_as, name)
            self.windows.load(target)
            said.append(name)
        kept: list[str] = []
        for listed_as, path in per_user:
            self.windows.unload(path)
            self.windows.delete_user(listed_as)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                kept.append(path.name)  # in use: unregistered, removed at the next sign-in by hand
        self.windows.announce()
        parts = []
        if said:
            parts.append(f"installed for all users: {', '.join(said)}")
        if per_user:
            parts.append(f"per-user copy retired: {', '.join(p.name for _, p in per_user)}")
        if kept:
            parts.append(f"(file still in use, left on disk: {', '.join(kept)})")
        return True, "; ".join(parts) or "already installed for all users"
