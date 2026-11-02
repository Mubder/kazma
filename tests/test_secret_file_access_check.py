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
    from kazma_core import store_registry
    from kazma_core.backup import restic_repo
    from kazma_core.security.hardening import SecurityHardeningRunner

    monkeypatch.setattr(restic_repo, "password_file", lambda: secret)
    monkeypatch.setattr(store_registry, "install_secret_files", lambda: [])
    runner = SecurityHardeningRunner(secret.parent)
    assert asyncio.run(runner.check_secret_file_access()).passed is True
    _open_to_everyone(secret)
    result = asyncio.run(runner.check_secret_file_access())
    assert result.passed is False and "restic.pass" in result.message


# ── every secret file of the install, not only restic.pass (2026-11) ──────
#
# The live install folder granted read to a group another program had added
# (CodexSandboxUsers), and every file in it inherited that: .env (the vault
# key, the database password), restic.pass and vault.db. The check looked at
# restic.pass only.


@pytest.fixture
def install(tmp_path, monkeypatch):
    """Kazma's home and data dir with each kind of secret file in them."""
    from kazma_core import store_registry

    home, data = tmp_path / "home", tmp_path / "data"
    home.mkdir()
    data.mkdir()
    files = {
        "env": home / ".env",
        "env_local": home / ".env.local",
        "template": home / ".env.example",
        "restic": home / "restic.pass",
        "token": home / "document_broker.token",
        "vault": data / "vault.db",
        "named": tmp_path / "elsewhere" / "kazma.env",
        "ordinary": home / "preferences.json",
    }
    files["named"].parent.mkdir()
    for path in files.values():
        path.write_text("demo\n", encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_ENV_FILE", str(files["named"]))
    monkeypatch.delenv("KAZMA_DOCUMENT_BROKER_TOKEN_FILE", raising=False)
    monkeypatch.setattr(store_registry, "_install_env_dirs", lambda: [home.resolve()])
    return files


def test_every_secret_file_of_the_install_is_found(install):
    from kazma_core.store_registry import install_secret_files

    found = set(install_secret_files())
    expected = {install[k].resolve() for k in ("env", "env_local", "restic", "token", "vault", "named")}
    assert expected <= found
    assert install["template"].resolve() not in found, "a template is not a secret"
    assert install["ordinary"].resolve() not in found


def test_the_report_fails_on_an_open_env_file(install, monkeypatch):
    from kazma_core.backup import restic_repo
    from kazma_core.security.hardening import SecurityHardeningRunner

    monkeypatch.setattr(restic_repo, "password_file", lambda: install["restic"])
    runner = SecurityHardeningRunner(install["env"].parent)
    assert asyncio.run(runner.check_secret_file_access()).passed is True

    _open_to_everyone(install["env"])
    # Negative control: the check as it was, restic.pass alone, sees nothing.
    assert readable_by_others(install["restic"]) == []
    result = asyncio.run(runner.check_secret_file_access())
    assert result.passed is False
    assert ".env" in result.message and "restic.pass" not in result.message


def test_the_report_names_the_vault_and_the_token_too(install, monkeypatch):
    from kazma_core.backup import restic_repo
    from kazma_core.security.hardening import SecurityHardeningRunner

    monkeypatch.setattr(restic_repo, "password_file", lambda: install["restic"])
    for key in ("vault", "token"):
        _open_to_everyone(install[key])
    result = asyncio.run(SecurityHardeningRunner(install["env"].parent).check_secret_file_access())
    assert result.passed is False
    assert "vault.db" in result.message and "document_broker.token" in result.message


@pytest.mark.skipif(os.name != "nt", reason="Windows account names")
def test_an_account_is_named_not_just_numbered():
    from kazma_core.security.file_access import _account_name, _current_user_sid

    me = _current_user_sid()
    whoami = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
    assert (_account_name(me) or "").lower() == whoami.lower()
    assert _account_name("S-1-5-21-1-2-3-1003") is None, "an unknown SID keeps its number"
    # The report names it beside its number.
    sddl = f"D:(A;;FR;;;{me})(A;;FA;;;S-1-5-21-9-9-9-1001)"
    assert sddl_readers(sddl, "S-1-5-21-9-9-9-1001") == [f"{_account_name(me)} ({me})"]
