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

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from kazma_core.security import owner_only

logger = logging.getLogger(__name__)

__all__ = ["copy_private", "create_private", "readable_by_others", "restrict_secret_files",
           "restrict_to_owner", "write_private"]

#: SIDs that may read a secret file: SYSTEM, Administrators, the owner rights
#: and the creator owner (the account itself is added at runtime).
_MAY_READ = frozenset({"SY", "BA", "OW", "CO", "S-1-5-18", "S-1-5-32-544", "S-1-3-4", "S-1-3-0"})
#: Short names of groups that hold every user of the machine.
_EVERYONE = {"WD": "Everyone", "AU": "Authenticated Users", "BU": "Users", "BG": "Guests",
             "IU": "Interactive users", "S-1-1-0": "Everyone", "S-1-5-11": "Authenticated Users",
             "S-1-5-32-545": "Users", "S-1-5-32-546": "Guests", "S-1-5-4": "Interactive users"}
_ACE = re.compile(r"\(([^;()]*);([^;()]*);([^;()]*);[^;()]*;[^;()]*;([^;()]+)\)")


def _current_user_sid() -> str:
    sid = owner_only.current_user_sid()
    if sid:
        return sid
    try:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    sid = out.rsplit(",", 1)[-1].strip().strip('"')
    return sid if sid.startswith("S-") else ""


def restrict_to_owner(path: Path) -> bool:
    """Only this account may read *path* (POSIX: mode 0600). True only when
    :func:`readable_by_others` then finds no one else.

    The one way Kazma restricts a secret file: the observer's pairing key, the
    Postgres superuser's password, every env file (:func:`write_private`, the
    one writer), the copies of secret files in a backup. Windows: the file's
    whole access list is replaced (``owner_only.restrict``): an explicit
    entry for another account is removed too. It ran ``icacls
    /inheritance:r /grant:r``, which keeps explicit entries -- a file with an
    explicit ``Users:R`` stayed readable by Users while this answered True
    (2026-11-15). A folder is restricted the same way, its entry inherited by
    what is made in it. Blocking.
    """
    path = Path(path)
    try:
        if not owner_only.restrict(path):
            return False
    except OSError:
        return False
    if path.is_dir():
        return True  # read back by owner_only; readable_by_others reads files
    return readable_by_others(path) == []


def _private_sibling(path: Path) -> Path:
    """An empty new file beside *path*, made for this account only (0600)."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".private", dir=str(path.parent))
    os.close(fd)
    return Path(name)


def create_private(path: Path) -> bool:
    """Make *path* a new EMPTY file only this account may read (it must not
    exist). True when restricted; the file is left either way, for the caller
    to fill or remove. For a writer that cannot hand over its bytes at once
    (a zip being written)."""
    path = Path(path)
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    return restrict_to_owner(path)


def write_private(path: Path, data: bytes | str, *, mtime_ns: int | None = None) -> bool:
    """Replace *path* with *data*, readable by this account only: the one way
    Kazma writes a secret file (an env file, a copy of one in a backup).

    An empty sibling is made and restricted BEFORE the secret is in it, then
    filled, flushed and renamed over *path* (a rename keeps the file's own
    access list); *path* is restricted again after. Returns True when the
    file ends restricted. A restriction that fails is a WARNING and the data
    is still written: a vault key that is not kept leaves every stored secret
    unreadable. Raises ``OSError`` when it cannot be written. *mtime_ns*
    gives the file a modification time (a copy keeps its source's).
    """
    path = Path(path)
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _private_sibling(path)
    renamed = False
    try:
        restricted = restrict_to_owner(tmp)
        with open(tmp, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        if mtime_ns is not None:
            os.utime(tmp, ns=(mtime_ns, mtime_ns))
        os.replace(tmp, path)
        renamed = True
    finally:
        if not renamed:
            tmp.unlink(missing_ok=True)  # never a half-written secret left beside it
    restricted = restrict_to_owner(path) and restricted
    if not restricted:
        logger.warning("[file-access] could not restrict %s to this account; it was written anyway", path)
    return restricted


def restrict_secret_files(root: Path) -> list[str]:
    """Restrict every file under *root* whose name holds a secret
    (``store_registry.holds_a_secret``: an env file, ``restic.pass``, the
    vault...) to this account; the paths (relative to *root*) that could not
    be. For a backup, a restore or an export staging folder: a copy takes
    what its new folder grants, and the backups folder let ``Users`` read a
    backup's ``.env`` and ``vault.db`` (2026-11-15)."""
    from kazma_core.store_registry import holds_a_secret

    root = Path(root)
    left: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and holds_a_secret(path.name) and not restrict_to_owner(path):
            left.append(path.relative_to(root).as_posix())
    if left:
        logger.warning("[file-access] %d secret file(s) under %s could not be restricted to this "
                       "account: %s", len(left), root, ", ".join(left[:10]))
    return left


def copy_private(src: Path, dst: Path) -> bool:
    """Copy *src* to *dst* readable by this account only (:func:`write_private`,
    its modification time kept). ``shutil.copy2`` gave the copy what its new
    folder grants: a backup's ``.env`` -- the vault key -- was readable by
    every account the backups folder lets in."""
    src = Path(src)
    return write_private(dst, src.read_bytes(), mtime_ns=src.stat().st_mtime_ns)


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
