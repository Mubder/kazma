"""A new restic passphrase file is restricted to this account before it holds
the passphrase.

It was written first and then ``chmod 0600``, which changes nothing on
Windows: the file kept every reader its folder had (CodeQL
py/clear-text-storage-sensitive-data, 2026-11-14). It now takes the order of
``env_files.persist_generated_secret``: an empty file, restricted, then
written.
"""

from __future__ import annotations

from kazma_core.backup import restic_repo
from kazma_core.security import file_access


def test_the_file_is_restricted_while_still_empty(monkeypatch, tmp_path):
    target = tmp_path / ".kazma" / "restic.pass"
    seen: list[str] = []

    def restrict(path):
        seen.append(path.read_text(encoding="utf-8"))
        return True

    monkeypatch.delenv("KAZMA_RESTIC_PASSWORD", raising=False)
    monkeypatch.setattr(restic_repo, "password_file", lambda: target)
    monkeypatch.setattr(file_access, "restrict_to_owner", restrict)
    secret, created = restic_repo.ensure_password(create=True)
    assert created and len(secret) >= 32
    assert seen == [""]
    assert target.read_text(encoding="utf-8") == secret
    # A second call reads it and changes nothing.
    assert restic_repo.ensure_password(create=True) == (secret, False)
    assert seen == [""]


def test_a_restriction_that_fails_still_keeps_the_passphrase(monkeypatch, tmp_path, caplog):
    target = tmp_path / "restic.pass"
    monkeypatch.delenv("KAZMA_RESTIC_PASSWORD", raising=False)
    monkeypatch.setattr(restic_repo, "password_file", lambda: target)
    monkeypatch.setattr(file_access, "restrict_to_owner", lambda path: False)
    secret, created = restic_repo.ensure_password(create=True)
    assert created and target.read_text(encoding="utf-8") == secret
    assert "could not restrict" in caplog.text
