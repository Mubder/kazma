"""Key care (Spaces S4-07): a space's key can be changed, and a deleted
space's key is destroyed.

* **A change gives the space a key the old one has nothing to do with**: its
  sealed secrets are sealed again with the new key and read back the same; a
  secret written after the change is sealed with the new key; the old key
  opens none of them. Control: a "change" that seals the rows again under the
  key they had -- the old key still opens a new secret.
* **The old key is destroyed**: deleted in the change's transaction, and its
  wrapped bytes are in neither the vault's file nor its write-ahead log.
  Control: the change without emptying the log leaves them there.
* **No secret is lost to a change** (the review of S4-07): a change is
  refused, naming the rows, while a row that is not the space's own is
  sealed with one of its keys -- a real secret whose name a later build
  classes elsewhere reads back after it; control: the change as 3615dc0ee
  had it loses that secret for good. A row of the space that does not open
  refuses the change too, and ``kazma spaces keys change --all`` reports
  that space and goes on.
* **A deleted space's key is destroyed**: an erase takes it; when an older
  vault file is restored over an identity store that recorded the erase, the
  maintenance sweep destroys it again. Only a space the record says was
  erased: one merely missing from the registry keeps its key. Control: with
  no sweep, the restored file opens the erased space's secret.
* The change is refused for the owner's space, a space the install does not
  hold and with the vault off; it is recorded (``space_key_changed``); the
  command lists the keys (never a key) and changes them.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from kazma_core.config_store import get_config_store
from kazma_core.security import vault as vault_mod
from kazma_core.security.vault import SecretVault, SpaceKeyMissing, SpaceKeyStillNamed, _aad, sealing_space
from kazma_core.spaces import identity, people, scope, space_keys
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, bind_scope

A, B = "space-a", "space-b"
VAULT_KEY = "a" * 64
_ROW_AAD = "kazma-vault-row/v1"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", VAULT_KEY)
    v = SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "_vault", v)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    vault_mod.reset_posture_cache()
    yield v
    vault_mod._vault.close()
    vault_mod.reset_posture_cache()


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def spaces():
    """The identity store holds spaces A and B."""
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    identity.create_space("Space A", created_by=OWNER_ACCOUNT_ID, space_id=A)
    identity.create_space("Space B", created_by=OWNER_ACCOUNT_ID, space_id=B)
    yield
    store.delete(identity.REGISTRY_KEY)


def as_space(space: str):
    return bind_scope(space, OWNER_ACCOUNT_ID if space == OWNER_SPACE_ID else "someone")


def _store_two_spaces(v: SecretVault) -> None:
    with as_space(A):
        v.store("space:space-a:email.gmail.refresh_token", "token-of-a")
        v.store("bank", "pin-of-a")
    with as_space(B):
        v.store("space:space-b:email.gmail.refresh_token", "token-of-b")
        v.store("bank", "pin-of-b")


def _rows(v: SecretVault, space: str) -> list[sqlite3.Row]:
    return [r for r in v._conn.execute(
        "SELECT name, tenant_id, key_id, encrypted_value, nonce FROM secrets ORDER BY rowid")
        if sealing_space(r["name"], r["tenant_id"]) == space]


def _active(v: SecretVault, space: str) -> str:
    return next(k["key_id"] for k in v.space_key_ids(space) if k["state"] == "active")


def _old_key_opens(v: SecretVault, space: str, old_id: str, old_key: bytes) -> list[str]:
    """The names of *space*'s rows the old key still opens: bound to its own
    id, or to the id the row names now."""
    opened = []
    for row in _rows(v, space):
        for key_id in {old_id, str(row["key_id"])}:
            try:
                AESGCM(old_key).decrypt(row["nonce"], row["encrypted_value"],
                                        _aad(_ROW_AAD, space, key_id, row["name"]))
            except InvalidTag:
                continue
            opened.append(row["name"])
            break
    return opened


def _file_bytes(v: SecretVault) -> bytes:
    path = Path(v._db_path)
    return b"".join(p.read_bytes() for p in (path, path.with_name(path.name + "-wal")) if p.is_file())


# ── a change gives the space a key the old one has nothing to do with ────


def test_after_a_change_the_old_key_opens_nothing_and_a_new_secret_takes_the_new_key(vault, enforced) -> None:
    _store_two_spaces(vault)
    old_id = _active(vault, B)
    old_key = vault._space_key(old_id)[1]
    a_key = _active(vault, A)
    assert _old_key_opens(vault, B, old_id, old_key)  # the check sees the old key's rows
    new_id = vault.rotate_space_key(B)
    assert new_id != old_id
    with as_space(B):
        vault.store("space:space-b:x.api_key", "x-key-of-b")
        assert vault.retrieve("bank") == "pin-of-b"
        assert vault.retrieve("space:space-b:email.gmail.refresh_token") == "token-of-b"
        assert vault.retrieve("space:space-b:x.api_key") == "x-key-of-b"
    assert {r["key_id"] for r in _rows(vault, B)} == {new_id}
    assert _old_key_opens(vault, B, old_id, old_key) == []
    # The other space is untouched.
    assert _active(vault, A) == a_key
    with as_space(A):
        assert vault.retrieve("bank") == "pin-of-a"


def _change_that_keeps_the_key(v: SecretVault, space: str) -> str:
    """A "change" that seals the rows again under the key they had."""
    def _work() -> str:
        key_id = str(v._active_key_id(space, make=False))
        v._reseal(space, [r for r in v._space_rows(space) if r["key_id"]], key_id)
        return key_id

    return v._in_one_step(space, _work)


def test_control_a_change_that_keeps_the_key_lets_the_old_key_open_a_new_secret(vault, enforced) -> None:
    _store_two_spaces(vault)
    old_id = _active(vault, B)
    old_key = vault._space_key(old_id)[1]
    _change_that_keeps_the_key(vault, B)
    with as_space(B):
        vault.store("space:space-b:x.api_key", "x-key-of-b")
    assert "space:space-b:x.api_key" in _old_key_opens(vault, B, old_id, old_key)


# ── the old key is destroyed ─────────────────────────────────────────────


def _wrapped(v: SecretVault, key_id: str) -> bytes:
    return bytes(v._conn.execute("SELECT wrapped FROM space_keys WHERE key_id = ?", (key_id,)).fetchone()[0])


def test_the_old_key_is_gone_from_the_vaults_file_and_its_log(vault, enforced) -> None:
    _store_two_spaces(vault)
    old_id = _active(vault, B)
    wrapped = _wrapped(vault, old_id)
    sealed = bytes(_rows(vault, B)[0]["encrypted_value"])
    assert wrapped in _file_bytes(vault)
    vault.rotate_space_key(B)
    assert [k["key_id"] for k in vault.space_key_ids(B)] == [_active(vault, B)]
    left = _file_bytes(vault)
    assert wrapped not in left and sealed not in left


def test_control_a_change_that_leaves_the_log_leaves_the_old_key_in_the_files(vault, enforced, monkeypatch) -> None:
    _store_two_spaces(vault)
    wrapped = _wrapped(vault, _active(vault, B))
    monkeypatch.setattr(SecretVault, "_empty_the_log", lambda self, after: None)
    vault.rotate_space_key(B)
    assert wrapped in _file_bytes(vault)


def _plant_a_row_of_a_naming(v: SecretVault, key_id: str) -> None:
    """A row of space A that names B's key: it never opened (``_open``
    checks the space)."""
    v._conn.execute(
        "INSERT INTO secrets (id, name, encrypted_value, nonce, tenant_id, created_at, updated_at, key_id) "
        "VALUES ('stray', 'stray', x'00', x'00', ?, 't', 't', ?)", (A, key_id))


def test_a_row_outside_the_space_sealed_with_its_key_refuses_the_change(vault, enforced) -> None:
    _store_two_spaces(vault)
    old_id = _active(vault, B)
    _plant_a_row_of_a_naming(vault, old_id)
    with pytest.raises(SpaceKeyStillNamed) as refused:
        vault.rotate_space_key(B)
    assert refused.value.names == ["stray"]
    assert _active(vault, B) == old_id  # nothing changed
    with as_space(B):
        assert vault.retrieve("bank") == "pin-of-b"


def _drifted(monkeypatch, name: str) -> None:
    """A later build classes *name* as the install's (``secret_scope``)."""
    real = vault_mod.secret_scope
    monkeypatch.setattr(vault_mod, "secret_scope",
                        lambda n: ("install", None) if n == name else real(n))


def _change_of_before(v: SecretVault, space: str) -> str:
    """``rotate_space_key`` as 3615dc0ee had it: no refusal, and every
    retired key deleted."""
    def _work() -> str:
        rows = [r for r in v._space_rows(space) if r["key_id"]]
        old = v._active_key_id(space, make=False)
        v._conn.execute("UPDATE space_keys SET state = 'retired' WHERE key_id = ?", (old,))
        new = str(v._active_key_id(space, make=True))
        v._reseal(space, rows, new)
        v._conn.execute("DELETE FROM space_keys WHERE space_id = ? AND state = 'retired'", (space,))
        return new

    new = v._in_one_step(space, _work)
    v._space_keys.clear()  # as the change cleared its cache
    return new


def test_a_real_secret_under_a_drifted_name_is_never_lost(vault, enforced, monkeypatch) -> None:
    """The review of S4-07: ``bank`` of space B is sealed with B's key, and
    a later build classes its name as the install's. The change is refused,
    naming it; the classification restored, it reads back."""
    _store_two_spaces(vault)
    with monkeypatch.context() as drift:
        _drifted(drift, "bank")
        with pytest.raises(SpaceKeyStillNamed) as refused:
            vault.rotate_space_key(B)
        assert "bank" in refused.value.names
    with as_space(B):
        assert vault.retrieve("bank") == "pin-of-b"


def test_control_the_change_of_before_loses_the_drifted_secret(vault, enforced, monkeypatch) -> None:
    _store_two_spaces(vault)
    with monkeypatch.context() as drift:
        _drifted(drift, "bank")
        _change_of_before(vault, B)
    with as_space(B), pytest.raises(SpaceKeyMissing):
        vault.retrieve("bank")


def test_a_damaged_row_is_a_refusal_and_change_all_goes_on(vault, enforced, spaces, capsys) -> None:
    from kazma_cli import spaces as cli

    _store_two_spaces(vault)
    old_a, old_b = _active(vault, A), _active(vault, B)
    vault._conn.execute(  # a row of A, sealed with its key, that does not open
        "INSERT INTO secrets (id, name, encrypted_value, nonce, tenant_id, created_at, updated_at, key_id) "
        "VALUES ('broken', 'broken', x'00', x'000000000000000000000000', ?, 't', 't', ?)", (A, old_a))
    with pytest.raises(space_keys.KeyChangeRefused) as refused:
        space_keys.change_key(A, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "rows_damaged"
    assert cli.run(["keys", "change", "--all", "--yes"]) == 1
    out = capsys.readouterr().out
    assert f"{A}: not changed" in out and f"{B}: new key" in out
    assert _active(vault, A) == old_a and _active(vault, B) != old_b


# ── a deleted space's key is destroyed ──────────────────────────────────


def _erase_a(v: SecretVault) -> None:
    """A's erase as ``space_data.erase_space`` ends it: its secrets and keys,
    then out of the identity store, recorded."""
    v.erase_space(A)
    people.close_space(A, by=OWNER_ACCOUNT_ID)
    people.forget_space(A, by=OWNER_ACCOUNT_ID)


def _restore_older_file(v: SecretVault, copy: Path, monkeypatch) -> SecretVault:
    """The vault's file replaced by *copy*, taken before the erase."""
    path = Path(v._db_path)
    v.close()
    for extra in (path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        extra.unlink(missing_ok=True)
    shutil.copyfile(copy, path)
    restored = SecretVault(db_path=str(path))
    monkeypatch.setattr(vault_mod, "_vault", restored)
    return restored


def _copy_of(v: SecretVault, tmp_path: Path) -> Path:
    v._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    copy = tmp_path / "vault-before-erase.db"
    shutil.copyfile(v._db_path, copy)
    return copy


def test_an_erased_spaces_key_that_came_back_is_destroyed(vault, enforced, spaces, tmp_path, monkeypatch) -> None:
    _store_two_spaces(vault)
    copy = _copy_of(vault, tmp_path)
    _erase_a(vault)
    assert space_keys.destroy_erased_keys() == []  # the erase took them
    restored = _restore_older_file(vault, copy, monkeypatch)
    assert A in restored.held_spaces()
    assert space_keys.destroy_erased_keys() == [A]
    assert restored.space_key_ids(A) == [] and _rows(restored, A) == []
    assert A not in restored.held_spaces()
    with as_space(B):
        assert restored.retrieve("bank") == "pin-of-b"
    assert space_keys.destroy_erased_keys() == []


def test_control_with_no_sweep_the_restored_file_opens_the_erased_spaces_secret(
        vault, enforced, spaces, tmp_path, monkeypatch) -> None:
    _store_two_spaces(vault)
    copy = _copy_of(vault, tmp_path)
    _erase_a(vault)
    restored = _restore_older_file(vault, copy, monkeypatch)
    with as_space(A):
        assert restored.retrieve("bank") == "pin-of-a"


def test_a_space_missing_from_the_registry_but_never_erased_keeps_its_key(vault, enforced, spaces) -> None:
    _store_two_spaces(vault)
    with as_space("space-c"):
        vault.store("bank", "pin-of-c")
    assert "space-c" in vault.held_spaces()
    assert space_keys.destroy_erased_keys() == []
    assert len(vault.space_key_ids("space-c")) == 1
    assert "space-c" in space_keys.report()["unknown_spaces"]


def test_an_unreadable_identity_store_destroys_nothing(vault, enforced, spaces, monkeypatch) -> None:
    _store_two_spaces(vault)

    def away() -> dict:
        raise identity.IdentityUnavailable("away")

    monkeypatch.setattr(identity, "read_registry", away)
    with pytest.raises(identity.IdentityUnavailable):
        space_keys.destroy_erased_keys()
    assert len(vault.space_key_ids(A)) == 1


def test_the_maintenance_sweep_runs_it(vault, enforced, spaces, tmp_path, monkeypatch) -> None:
    from kazma_core.memory import worker_bootstrap

    assert ("erased space keys", worker_bootstrap._destroy_erased_space_keys) in worker_bootstrap._MAINTENANCE_SWEEPS
    _store_two_spaces(vault)
    copy = _copy_of(vault, tmp_path)
    _erase_a(vault)
    restored = _restore_older_file(vault, copy, monkeypatch)
    worker_bootstrap._destroy_erased_space_keys()
    assert restored.space_key_ids(A) == []


def test_an_install_without_spaces_reads_no_vault(monkeypatch) -> None:
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    monkeypatch.setattr(vault_mod, "get_vault", lambda: pytest.fail("the vault was opened"))
    assert space_keys.destroy_erased_keys() == []


# ── changing a key: who, what is recorded, the command ──────────────────


def test_a_change_is_recorded_without_a_key(vault, enforced, spaces) -> None:
    _store_two_spaces(vault)
    done = space_keys.change_key(B, by=OWNER_ACCOUNT_ID)
    assert done == {"space": B, "key_id": _active(vault, B), "sealed": 2}
    registry = identity.read_registry()
    event = registry["events"][-1]
    assert event["kind"] == "space_key_changed" and event["space"] == B and event["by"] == OWNER_ACCOUNT_ID
    assert set(event) == {"seq", "at", "kind", "account", "by", "prev", "hash", "space"}
    assert _wrapped(vault, done["key_id"]).hex() not in str(registry)  # no key in the record
    assert people.broken_at() is None


@pytest.mark.parametrize(("space", "code"), [(OWNER_SPACE_ID, "owners_space"), ("space-z", "no_such_space")])
def test_a_change_is_refused_for_the_owners_space_and_an_unknown_one(vault, enforced, spaces, space, code) -> None:
    with pytest.raises(space_keys.KeyChangeRefused) as refused:
        space_keys.change_key(space, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == code


def test_a_change_is_refused_with_the_vault_off(spaces, monkeypatch) -> None:
    monkeypatch.setattr(vault_mod, "_vault", None)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    with pytest.raises(space_keys.KeyChangeRefused) as refused:
        space_keys.change_key(B, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "vault_off"


def test_the_command_lists_the_keys_and_changes_them(vault, enforced, spaces, capsys) -> None:
    from kazma_cli import spaces as cli

    _store_two_spaces(vault)
    old_a, old_b = _active(vault, A), _active(vault, B)
    wrapped = _wrapped(vault, old_b).hex()
    assert cli.run(["keys"]) == 0
    listed = capsys.readouterr().out
    assert old_a in listed and old_b in listed and wrapped not in listed
    assert cli.run(["keys", "change", B, "--yes"]) == 0
    assert _active(vault, B) != old_b and _active(vault, A) == old_a
    assert cli.run(["keys", "change", "--all", "--yes"]) == 0
    assert _active(vault, A) != old_a
    assert cli.run(["keys", "change", OWNER_SPACE_ID, "--yes"]) == 1
    assert "not changed" in capsys.readouterr().out
