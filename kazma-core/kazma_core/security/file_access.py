"""Who else can read a secret file (2026-11, FA-272).

The restic passphrase (``restic.pass``) unlocks every encrypted offsite
snapshot. The guard restricts its own token file to the account that runs it
(``kazma_guard._restrict_to_owner``: no inherited entries, only that account);
nothing checked the passphrase file. :func:`readable_by_others` answers for
any file: on POSIX from its mode, on Windows from its ACL as SIDs
(``icacls /save``), so the answer does not depend on the system's language.

The account itself, SYSTEM and the Administrators group may read it -- they
can read anything on the machine anyway. Anyone else is a problem: Everyone,
Users, Authenticated Users, Guests, or another account.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

__all__ = ["readable_by_others", "restrict_to_owner"]

#: SIDs that may read a secret file: SYSTEM, Administrators, the owner rights
#: and the creator owner (the account itself is added at runtime).
_MAY_READ = frozenset({"SY", "BA", "OW", "CO", "S-1-5-18", "S-1-5-32-544", "S-1-3-4", "S-1-3-0"})
#: Short names of groups that hold every user of the machine.
_EVERYONE = {"WD": "Everyone", "AU": "Authenticated Users", "BU": "Users", "BG": "Guests",
             "IU": "Interactive users", "S-1-1-0": "Everyone", "S-1-5-11": "Authenticated Users",
             "S-1-5-32-545": "Users", "S-1-5-32-546": "Guests", "S-1-5-4": "Interactive users"}
_ACE = re.compile(r"\(([^;()]*);([^;()]*);([^;()]*);[^;()]*;[^;()]*;([^;()]+)\)")


def _current_user_sid() -> str:
    try:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    sid = out.rsplit(",", 1)[-1].strip().strip('"')
    return sid if sid.startswith("S-") else ""


def restrict_to_owner(path: Path) -> bool:
    """Only this account may read *path*: no inherited entries, the account
    alone (POSIX: mode 0600). True when the restriction is in place.

    The one way Kazma restricts a secret file it writes: the observer's
    pairing key, the Postgres superuser's password, the env file a first boot
    writes its generated keys into. Blocking (``icacls`` on Windows).
    """
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
            return True
        except OSError:
            return False
    sid = _current_user_sid()
    if not sid:
        return False
    try:
        result = subprocess.run(  # noqa: S603 -- fixed program, no shell
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _windows_sddl(path: Path) -> str | None:
    with tempfile.TemporaryDirectory() as folder:
        saved = Path(folder) / "acl.txt"
        try:
            done = subprocess.run(["icacls", str(path), "/save", str(saved)], capture_output=True,
                                  timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0 or not saved.is_file():
            return None
        raw = saved.read_bytes()
    text = raw.decode("utf-16-le", errors="replace") if raw[:2] != b"\xef\xbb" else raw.decode("utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else None


def _account_name(sid: str) -> str | None:
    """The account or group a SID names on this machine (``DOMAIN\\name``),
    or None when Windows does not know it (or this is not Windows).

    The live install's ``.env`` was readable by ``CodexSandboxUsers``, a
    group another program had added; the report said "another account
    (S-1-5-21-...-1003)", which nobody can act on (2026-11).
    """
    if os.name != "nt" or not sid.startswith("S-"):
        return None
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.LookupAccountSidW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    psid = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(sid, ctypes.byref(psid)):
        return None
    try:
        name, domain = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        name_len, domain_len, use = wintypes.DWORD(256), wintypes.DWORD(256), wintypes.DWORD()
        if not advapi32.LookupAccountSidW(None, psid, name, ctypes.byref(name_len), domain,
                                          ctypes.byref(domain_len), ctypes.byref(use)):
            return None
        return f"{domain.value}\\{name.value}" if domain.value else name.value or None
    finally:
        kernel32.LocalFree(psid)


def sddl_readers(sddl: str, owner_sid: str) -> list[str]:
    """Who besides *owner_sid*, SYSTEM and the administrators an ACL lets in."""
    others: list[str] = []
    for kind, _flags, _rights, sid in _ACE.findall(sddl.split("S:", 1)[0]):
        if kind != "A" or sid in _MAY_READ or sid == owner_sid:
            continue
        if sid in _EVERYONE:
            others.append(_EVERYONE[sid])
            continue
        name = _account_name(sid)
        others.append(f"{name} ({sid})" if name else f"another account ({sid})")
    return others


def readable_by_others(path: Path) -> list[str] | None:
    """Who else can read *path* (empty: only its owner); None when it could
    not be read or does not exist."""
    if not path.is_file():
        return None
    if os.name != "nt":
        mode = path.stat().st_mode & 0o077
        return [f"group or others (mode {path.stat().st_mode & 0o777:o})"] if mode else []
    sddl = _windows_sddl(path)
    owner = _current_user_sid()
    if sddl is None or not owner:
        return None
    return sddl_readers(sddl, owner)
