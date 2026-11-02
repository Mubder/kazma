"""The security report says who else can read the restic passphrase (2026-11, FA-272).

``restic.pass`` unlocks every encrypted offsite snapshot. The guard keeps its
own token file to the account that runs it; nothing checked the passphrase
file. ``check_secret_file_access`` reads the file's permissions (on Windows
its ACL as SIDs, so the answer does not depend on the language) and fails
when anyone but Kazma's account, SYSTEM or the administrators can read it.
Measured on a real file; one opened to everyone is the negative control.
The live install's file was readable by another local account when this
was written (the owner restricts it).
"""

from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

from kazma_core.security.file_access import readable_by_others, sddl_readers

OWNER = "S-1-5-21-1-2-3-1001"


def test_an_acl_of_the_owner_system_and_administrators_is_private():
    assert sddl_readers(f"D:PAI(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{OWNER})", OWNER) == []


@pytest.mark.parametrize("sid, who", [("WD", "Everyone"), ("BU", "Users"), ("AU", "Authenticated Users"),
                                      ("S-1-5-21-1-2-3-1003", "another account (S-1-5-21-1-2-3-1003)")])
def test_anyone_else_is_named(sid, who):
    assert sddl_readers(f"D:(A;ID;FA;;;SY)(A;ID;FR;;;{sid})(A;;FA;;;{OWNER})", OWNER) == [who]


def test_a_deny_entry_is_not_a_reader():
    assert sddl_readers(f"D:(D;;FA;;;WD)(A;;FA;;;{OWNER})", OWNER) == []


@pytest.fixture
def secret(tmp_path):
    path = tmp_path / "own" / "restic.pass"
    path.parent.mkdir()
    path.write_text("demo passphrase\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    return path


def _open_to_everyone(path) -> None:
    if os.name == "nt":
        subprocess.run(["icacls", str(path), "/grant", "*S-1-1-0:R"], capture_output=True, check=True)
    else:
        path.chmod(0o644)


def test_a_private_file_passes_and_an_open_one_fails(secret):
    assert readable_by_others(secret) == []
    _open_to_everyone(secret)
    assert readable_by_others(secret)


def test_the_report_fails_on_an_open_passphrase_file(secret, monkeypatch):
    from kazma_core.backup import restic_repo
    from kazma_core.security.hardening import SecurityHardeningRunner

    monkeypatch.setattr(restic_repo, "password_file", lambda: secret)
    runner = SecurityHardeningRunner(secret.parent)
    assert asyncio.run(runner.check_secret_file_access()).passed is True
    _open_to_everyone(secret)
    result = asyncio.run(runner.check_secret_file_access())
    assert result.passed is False and "restic.pass" in result.message
