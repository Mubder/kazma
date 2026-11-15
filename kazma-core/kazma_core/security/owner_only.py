"""Only this account may open a file: its whole access list, set in one step.

Standard library only: the guard (``scripts/service/kazma_guard.py``) loads
this file by path, as it loads ``process_priority.py`` -- it never imports
the app -- so the guard and Kazma restrict a secret file the same way
(``security.file_access.restrict_to_owner`` is Kazma's door to it).

Windows: the file's access list is REPLACED by one entry, this account with
full control, protected from inheritance (``SetNamedSecurityInfoW``), then
read back: the restriction holds only when every entry that allows anything
names this account. Until 2026-11-15 both copies ran ``icacls /inheritance:r
/grant:r <account>:(F)``, which removes the inherited entries and keeps every
explicit one: a file with an explicit ``Users:R`` entry stayed readable by
Users and the call still answered True. POSIX: mode 0600, read back.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

__all__ = ["current_user_sid", "restrict"]

# SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION
_SE_FILE_OBJECT = 1
_DACL_INFO = 0x00000004
_PROTECTED_DACL = 0x80000000
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1

#: One access-control entry of an SDDL string: (type;flags;rights;object;inherit object;trustee).
_ACE = re.compile(r"\(([^;()]*);[^;()]*;[^;()]*;[^;()]*;[^;()]*;([^;()]+)\)")


def _win():
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    vp = ctypes.c_void_p
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(vp), ctypes.POINTER(wintypes.ULONG)]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        vp, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.ULONG)]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        vp, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(vp), ctypes.POINTER(wintypes.BOOL)]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, vp, vp, vp, vp]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(vp), ctypes.POINTER(vp),
        ctypes.POINTER(vp), ctypes.POINTER(vp), ctypes.POINTER(vp)]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, vp, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [vp, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.argtypes = [vp]
    kernel32.LocalFree.restype = vp
    return ctypes, wintypes, advapi32, kernel32


def current_user_sid() -> str:
    """The SID of the account this process runs as (Windows), or ""."""
    if os.name != "nt":
        return ""
    try:
        ctypes, wintypes, advapi32, kernel32 = _win()
    except (OSError, AttributeError):
        return ""
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        return ""
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        if not size.value:
            return ""
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, size, ctypes.byref(size)):
            return ""
        psid = ctypes.c_void_p.from_buffer(buffer).value  # TOKEN_USER.User.Sid
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(text)):
            return ""
        try:
            sid = text.value or ""
        finally:
            kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel32.CloseHandle(token)
    return sid if sid.startswith("S-") else ""


def _descriptor_text(sd, ctypes, wintypes, advapi32, kernel32) -> str | None:
    text = wintypes.LPWSTR()
    if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            sd, _SDDL_REVISION_1, _DACL_INFO, ctypes.byref(text), None):
        return None
    try:
        return text.value
    finally:
        kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def _dacl_sddl(path: Path) -> str | None:
    """The access list of *path* as SDDL (``D:...``), or None (not Windows,
    or it could not be read)."""
    if os.name != "nt":
        return None
    try:
        ctypes, wintypes, advapi32, kernel32 = _win()
    except (OSError, AttributeError):
        return None
    dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    rc = advapi32.GetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _DACL_INFO, None, None,
                                        ctypes.byref(dacl), None, ctypes.byref(sd))
    if rc != 0:
        return None
    try:
        return _descriptor_text(sd, ctypes, wintypes, advapi32, kernel32)
    finally:
        kernel32.LocalFree(sd)


def _trustee_form(sid: str) -> str:
    """How SDDL writes *sid* (an alias such as ``SY`` for SYSTEM, or the SID)."""
    try:
        ctypes, wintypes, advapi32, kernel32 = _win()
    except (OSError, AttributeError):
        return sid
    sd = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:(A;;FA;;;{sid})", _SDDL_REVISION_1, ctypes.byref(sd), None):
        return sid
    try:
        text = _descriptor_text(sd, ctypes, wintypes, advapi32, kernel32) or ""
    finally:
        kernel32.LocalFree(sd)
    found = _ACE.findall(text)
    return found[0][1] if found else sid


def _only_owner_allowed(sddl: str, owner: str) -> bool:
    """True when *sddl*'s access list is protected from inheritance and every
    entry that allows anything names *owner* (as SDDL writes it)."""
    dacl = sddl.split("S:", 1)[0]
    if not dacl.startswith("D:"):
        return False
    flags = dacl[2:].split("(", 1)[0]
    if "P" not in flags:
        return False
    allows = [trustee for kind, trustee in _ACE.findall(dacl) if kind.startswith("A")]
    return bool(allows) and all(trustee == owner for trustee in allows)


def restrict(path: Path) -> bool:
    """Only this account may open *path*: True when that is what the file's
    permissions now say (read back after the change)."""
    path = Path(path)
    if os.name != "nt":
        try:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
            return stat.S_IMODE(os.stat(path).st_mode) & 0o077 == 0
        except OSError:
            return False
    sid = current_user_sid()
    if not sid:
        return False
    try:
        ctypes, wintypes, advapi32, kernel32 = _win()
    except (OSError, AttributeError):
        return False
    sd = ctypes.c_void_p()
    # A folder's entry is inherited by the files and folders made in it.
    inherit = "OICI" if path.is_dir() else ""
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;{inherit};FA;;;{sid})", _SDDL_REVISION_1, ctypes.byref(sd), None):
        return False
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(sd, ctypes.byref(present), ctypes.byref(dacl),
                                                  ctypes.byref(defaulted)) or not present.value:
            return False
        rc = advapi32.SetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _DACL_INFO | _PROTECTED_DACL,
                                            None, None, dacl, None)
    finally:
        kernel32.LocalFree(sd)
    if rc != 0:
        return False
    after = _dacl_sddl(path)
    return after is not None and _only_owner_allowed(after, _trustee_form(sid))
