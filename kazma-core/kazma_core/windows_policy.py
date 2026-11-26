"""Windows' own word on whether it checks files: Smart App Control's setting.

``HKLM\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy``
``VerifiedAndReputablePolicyState``: 0 off, 1 on, 2 evaluation (Windows is
learning how the PC is used, and checks nothing yet).

One reader, standard library only: ``kazma_core.libpq_reach`` reads it when
``kazma_core`` is imported, and ``kazma_core.docker_cli`` reads it in the
guard, which loads both files by path and never imports Kazma (AGENTS §39).
While it is ``on``, Kazma uses the signed copy of a part where one is
installed (the PostgreSQL client library, Docker's CLI) instead of trying an
unsigned one first (measured 2026-11-26: psycopg's bundled libpq, libssl and
libcrypto and the engine's static-zip ``docker.exe`` carry no signature;
PostgreSQL 18's ``libpq.dll`` and its DLLs are signed by EnterpriseDB,
Docker Desktop's ``docker.exe`` by Docker Inc). See
:mod:`kazma_core.windows_trust` and AGENTS §71.
"""

from __future__ import annotations

import os

__all__ = ["checks_files", "smart_app_control"]

_POLICY_KEY = r"SYSTEM\CurrentControlSet\Control\CI\Policy"
_POLICY_VALUE = "VerifiedAndReputablePolicyState"
_STATES = {0: "off", 1: "on", 2: "evaluation"}


def _on_windows() -> bool:
    return os.name == "nt"


def smart_app_control() -> str | None:
    """``on``, ``off`` or ``evaluation``; None elsewhere than Windows or when
    the value is not there (a Windows without Smart App Control)."""
    if not _on_windows():
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _POLICY_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, _POLICY_VALUE)
    except (ImportError, OSError):
        return None
    return _STATES.get(value) if isinstance(value, int) else None


def checks_files() -> bool:
    """True while Smart App Control checks every program and library."""
    return smart_app_control() == "on"
