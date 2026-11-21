"""A key per space, and a space's secrets sealed with it (Spaces S1-26).

Every space but the owner's has a key of its own, kept in the vault's file
(``space_keys``), wrapped by a key derived from the install's vault key. Once
Spaces is enforced a space's secrets are sealed with its key and each sealed
row names that key; a read follows the id the row names.

Each rule is shown failing on the code as it was or on a planted shape:

* the single vault key opens both spaces' secrets (the vault before S1-26);
* a space made with no key;
* a read by "the space's active key" instead of the row's;
* a wrapped key not bound to its space;
* a build that seals whether or not Spaces is enforced;
* a move that commits row by row;
* an erase that leaves the key;
* a restore drill that never looks at the keys.

An install with one account, never enforced, writes exactly the rows it
wrote before: no key, no sealed row, the same encryption.
"""

from __future__ import annotations

import ast
import re
import sqlite3
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from kazma_core.backup import restore_drill
from kazma_core.config_store import get_config_store
from kazma_core.security import vault as vault_mod
from kazma_core.security.vault import SecretVault, SpaceKeyMissing, sealing_space
from kazma_core.spaces import identity, people, scope, space_keys
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, bind_scope

A, B = "space-a", "space-b"
VAULT_KEY = "a" * 64
REPO = Path(__file__).resolve().parent.parent
VAULT_SOURCE = REPO / "kazma-core" / "kazma_core" / "security" / "vault.py"
PRODUCT = ("kazma-core", "kazma-ui", "kazma-cli", "kazma-gateway", "kazma-skills", "kazma-tui", "scripts")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", VAULT_KEY)
    v = SecretVault(db_path=str(tmp_path / "vault.db"))
    # The process's vault is this one: the identity store's hook makes keys
    # through ``get_vault()``.
    monkeypatch.setattr(vault_mod, "_vault", v)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    vault_mod.reset_posture_cache()
    yield v
    v.close()
    vault_mod.reset_posture_cache()


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def fresh_identity():
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    yield
    store.delete(identity.REGISTRY_KEY)


def as_space(space: str, who: str = "someone"):
    return bind_scope(space, OWNER_ACCOUNT_ID if space == OWNER_SPACE_ID else who)


def _rows(v: SecretVault) -> list[sqlite3.Row]:
    return v._conn.execute(
        "SELECT id, name, tenant_id, key_id, encrypted_value, nonce FROM secrets ORDER BY rowid").fetchall()


def _keys(v: SecretVault) -> list[sqlite3.Row]:
    return v._conn.execute("SELECT * FROM space_keys ORDER BY created_at, key_id").fetchall()


def _install_key(v: SecretVault) -> bytes:
    """The install's vault key, derived here from the file's own salt: the
    format every row had before S1-26, checked without the vault's code."""
    salt = bytes.fromhex(v._conn.execute(
        "SELECT value FROM vault_meta WHERE key = 'pbkdf2_salt'").fetchone()[0])
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000).derive(
        VAULT_KEY.encode())


def _opens_with_install_key(v: SecretVault, row: sqlite3.Row) -> str | None:
    try:
        return AESGCM(_install_key(v)).decrypt(row["nonce"], row["encrypted_value"], None).decode()
    except InvalidTag:
        return None


def _two_spaces_store(v: SecretVault) -> None:
    with as_space(A):
        v.store("space:space-a:email.gmail.refresh_token", "token-of-a")
        v.store("bank", "pin-of-a")
    with as_space(B):
        v.store("space:space-b:email.gmail.refresh_token", "token-of-b")
        v.store("cfg:tenant.space-b.connectors.x.api_key", "x-key-of-b")
        v.store("bank", "pin-of-b")


# ── a new space has a key ────────────────────────────────────────────────


def test_a_new_space_has_a_key(vault, fresh_identity) -> None:
    made = identity.create_space("Family", created_by=OWNER_ACCOUNT_ID)
    keys = vault.space_key_ids(made["id"])
    assert [k["state"] for k in keys] == ["active"]
    assert keys[0]["key_id"].startswith("k") and len(keys[0]["key_id"]) == 17
    # The owner's space has none (decision N22).
    assert vault.space_key_ids(OWNER_SPACE_ID) == []
    assert all(row["space_id"] != OWNER_SPACE_ID for row in _keys(vault))


def test_a_new_persons_space_has_a_key(vault, fresh_identity, enforced) -> None:
    person = people.create_person("Sara", "a-long-password", by=OWNER_ACCOUNT_ID)
    space = next(s for s in identity.spaces_of(person["id"]))["id"]
    assert [k["state"] for k in vault.space_key_ids(space)] == ["active"]


def test_control_a_space_made_without_the_hook_has_no_key(vault, fresh_identity, monkeypatch) -> None:
    monkeypatch.setattr(identity, "_key_new_spaces", lambda new: None)
    made = identity.create_space("Family", created_by=OWNER_ACCOUNT_ID)
    assert vault.space_key_ids(made["id"]) == []


def test_a_space_with_no_key_gets_it_with_its_first_sealed_secret(vault, enforced) -> None:
    """The vault was off, or the key's write failed, when the space was made."""
    assert vault.space_key_ids(A) == []
    with as_space(A):
        vault.store("bank", "pin-of-a")
        assert vault.retrieve("bank") == "pin-of-a"
    assert len(vault.space_key_ids(A)) == 1


def test_a_failed_write_leaves_neither_the_row_nor_its_key(vault, enforced, monkeypatch) -> None:
    def refuse(*_a, **_k):
        raise SpaceKeyMissing("planted")

    monkeypatch.setattr(SecretVault, "_seal_with", refuse)
    with as_space(A), pytest.raises(SpaceKeyMissing):
        vault.store("bank", "pin-of-a")
    assert _rows(vault) == [] and _keys(vault) == []


def test_two_processes_make_one_key(vault, tmp_path) -> None:
    other = SecretVault(db_path=str(tmp_path / "vault.db"))
    try:
        assert vault.ensure_space_key(A) == other.ensure_space_key(A)
        assert len(_keys(vault)) == 1
    finally:
        other.close()


def test_the_owners_space_and_a_bad_id_get_no_key(vault) -> None:
    for space in (OWNER_SPACE_ID, "Not A Space", ""):
        with pytest.raises(ValueError):
            vault.ensure_space_key(space)
    assert space_keys.ensure_keys([OWNER_SPACE_ID, "Not A Space"]) == []
    assert _keys(vault) == []


def test_the_vault_off_makes_no_key_and_the_space_stands(fresh_identity, monkeypatch) -> None:
    monkeypatch.setattr(vault_mod, "_vault", None)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    made = identity.create_space("Family", created_by=OWNER_ACCOUNT_ID)
    assert made["id"] in identity.read_registry()["spaces"]


def test_an_old_registry_with_the_empty_field_reads_cleanly(vault, fresh_identity) -> None:
    """Before S1-26 the registry document held an empty ``space_keys`` field."""
    old = identity._empty_registry()
    old["space_keys"] = {}
    old["version"] = 3
    old["moved_platform_users"] = True
    get_config_store().set(identity.REGISTRY_KEY, old, category="auth")
    reg = identity.read_registry()
    assert "space_keys" not in reg and reg["version"] == 3
    made = identity.create_space("Family", created_by=OWNER_ACCOUNT_ID)
    stored = get_config_store().get(identity.REGISTRY_KEY)
    assert "space_keys" not in stored and made["id"] in stored["spaces"]
    assert len(vault.space_key_ids(made["id"])) == 1


# ── A's secret does not open with B's key ────────────────────────────────


def _each_space_under_its_own_key(v: SecretVault) -> None:
    """The plan's test: every row of A and B names its own space's key, opens
    with that key alone, and not with the install's vault key."""
    key_of = {space: v.space_key_ids(space)[0]["key_id"] if v.space_key_ids(space) else None
              for space in (A, B)}
    rows = _rows(v)
    assert len(rows) == 5
    for row in rows:
        space = sealing_space(row["name"], row["tenant_id"])
        other = B if space == A else A
        assert row["key_id"] is not None and row["key_id"] == key_of[space], row["name"]
        assert _opens_with_install_key(v, row) is None, f"the vault key alone opens {row['name']}"
        _space, mine = v._space_key(key_of[space])
        _other, theirs = v._space_key(key_of[other])
        aad = vault_mod._aad(vault_mod._ROW_AAD, space, row["key_id"], row["name"])
        assert AESGCM(mine).decrypt(row["nonce"], row["encrypted_value"], aad)
        for wrong_aad in (aad, vault_mod._aad(vault_mod._ROW_AAD, other, key_of[other], row["name"]), None):
            with pytest.raises(InvalidTag):
                AESGCM(theirs).decrypt(row["nonce"], row["encrypted_value"], wrong_aad)


def test_a_secret_of_a_does_not_open_with_the_key_of_b(vault, enforced) -> None:
    _two_spaces_store(vault)
    _each_space_under_its_own_key(vault)
    with as_space(A):
        assert vault.retrieve("bank") == "pin-of-a"
        assert vault.retrieve("space:space-a:email.gmail.refresh_token") == "token-of-a"
        assert vault.retrieve("space:space-b:email.gmail.refresh_token") is None
    with as_space(B):
        assert vault.retrieve("bank") == "pin-of-b"
        assert vault.retrieve("cfg:tenant.space-b.connectors.x.api_key") == "x-key-of-b"


def test_control_the_single_vault_key_opens_both(vault, enforced, monkeypatch) -> None:
    """The vault as it was: every row under the install's one key."""
    monkeypatch.setattr(vault_mod, "sealing_space", lambda name, tenant_id: None)
    _two_spaces_store(vault)
    opened = [_opens_with_install_key(vault, row) for row in _rows(vault)]
    assert sorted(opened) == ["pin-of-a", "pin-of-b", "token-of-a", "token-of-b", "x-key-of-b"]
    monkeypatch.undo()
    monkeypatch.setattr(scope, "_enforced", True)
    with pytest.raises(AssertionError):
        _each_space_under_its_own_key(vault)


def test_the_installs_and_the_owners_secrets_keep_the_vault_key(vault, enforced) -> None:
    """N22: the owner's secrets are not sealed again until stage 4."""
    with as_space(OWNER_SPACE_ID):
        vault.store("cfg:providers.list.openai.api_key", "provider-key")
        vault.store("email.gmail.refresh_token", "owners-mail")
        vault.store("cfg:connectors.x.api_key", "owners-x")
        vault.store("bank", "owners-pin")
    for row in _rows(vault):
        assert row["key_id"] is None, row["name"]
        assert _opens_with_install_key(vault, row) is not None
    assert _keys(vault) == []


# ── a read follows the key the row names ─────────────────────────────────


def _second_active_key(v: SecretVault, space: str) -> str:
    v._conn.execute("UPDATE space_keys SET state = 'retired' WHERE space_id = ?", (space,))
    return str(v._active_key_id(space, make=True))


def test_a_row_opens_with_the_key_it_names_not_the_newest(vault, enforced) -> None:
    with as_space(A):
        vault.store("bank", "pin-of-a")
        first = vault.space_key_ids(A)[0]["key_id"]
        second = _second_active_key(vault, A)
        assert second != first
        assert vault.retrieve("bank") == "pin-of-a"  # still under the first
        vault.store("card", "card-of-a")
        assert {r["name"]: r["key_id"] for r in _rows(vault)} == {"bank": first, "card": second}
        assert vault.retrieve("card") == "card-of-a"


def test_control_a_read_by_the_active_key_loses_the_older_row(vault, enforced, monkeypatch) -> None:
    real = SecretVault._open

    def by_active(self, name, tenant_id, key_id, ciphertext, nonce):
        space = sealing_space(name, tenant_id)
        return real(self, name, tenant_id, self._active_key_id(space, make=False), ciphertext, nonce)

    with as_space(A):
        vault.store("bank", "pin-of-a")
        _second_active_key(vault, A)
        monkeypatch.setattr(SecretVault, "_open", by_active)
        with pytest.raises(SpaceKeyMissing):
            vault.retrieve("bank")


def test_a_row_whose_key_is_gone_is_refused_never_opened(vault, enforced) -> None:
    with as_space(A):
        vault.store("bank", "pin-of-a")
        vault._conn.execute("DELETE FROM space_keys")
        vault._space_keys.clear()
        with pytest.raises(SpaceKeyMissing):
            vault.retrieve("bank")
        assert vault_mod.retrieve_scoped("bank", vault) is None


def test_a_sealed_row_copied_into_another_space_does_not_open(vault, enforced) -> None:
    """Someone who can write the file copies A's row under B's id."""
    _two_spaces_store(vault)
    theirs = next(r for r in _rows(vault) if r["name"] == "bank" and r["tenant_id"] == A)
    vault._conn.execute(
        "UPDATE secrets SET encrypted_value = ?, nonce = ?, key_id = ? WHERE name = 'bank' AND tenant_id = ?",
        (theirs["encrypted_value"], theirs["nonce"], theirs["key_id"], B))
    with as_space(B), pytest.raises(SpaceKeyMissing):
        vault.retrieve("bank")


# ── a wrapped key is bound to its space and id ───────────────────────────


def _copy_a_key_onto_b(v: SecretVault) -> None:
    key_id = v.ensure_space_key(A)
    row = v._conn.execute("SELECT * FROM space_keys WHERE key_id = ?", (key_id,)).fetchone()
    v._conn.execute(
        "INSERT INTO space_keys (key_id, space_id, wrapped, nonce, wrap, state, created_at) "
        "VALUES ('kcopied', ?, ?, ?, ?, 'active', ?)",
        (B, row["wrapped"], row["nonce"], row["wrap"], row["created_at"]))
    v._space_keys.clear()


def test_a_wrapped_key_copied_onto_another_space_does_not_unwrap(vault) -> None:
    _copy_a_key_onto_b(vault)
    with pytest.raises(SpaceKeyMissing):
        vault._space_key("kcopied")


def test_control_a_wrap_bound_to_nothing_unwraps_anywhere(vault, monkeypatch) -> None:
    real = vault_mod._aad
    monkeypatch.setattr(
        vault_mod, "_aad", lambda *parts: b"" if parts[0] == vault_mod._KEY_AAD else real(*parts))
    _copy_a_key_onto_b(vault)
    assert vault._space_key("kcopied")[0] == B  # A's key, now B's


def test_a_space_key_does_not_unwrap_with_another_vault_key(vault, tmp_path, monkeypatch) -> None:
    key_id = vault.ensure_space_key(A)
    vault.close()
    monkeypatch.setenv("KAZMA_VAULT_KEY", "b" * 64)
    other = SecretVault(db_path=str(tmp_path / "vault.db"))
    try:
        with pytest.raises(SpaceKeyMissing):
            other._space_key(key_id)
    finally:
        other.close()


# ── one account, never enforced: exactly as before ───────────────────────

_EVERY_KIND = (
    ("cfg:providers.list.openai.api_key", "provider-key"),
    ("email.gmail.refresh_token", "owners-mail"),
    ("cfg:connectors.x.api_key", "owners-x"),
    ("cfg:connectors.github.oauth_token", "owners-github"),
    ("cfg:voice.tts.api_key", "a-setting"),
    ("space:space-b:email.gmail.refresh_token", "named-for-b"),
    ("bank", "an-agent-secret"),
)


def _stored_as_before(v: SecretVault) -> None:
    for name, value in _EVERY_KIND:
        v.store(name, value)
    v.for_space(B).store("bank", "under-a-tenant")
    rows = _rows(v)
    assert len(rows) == len(_EVERY_KIND) + 1
    for row in rows:
        assert row["key_id"] is None, row["name"]
    # The format on disk is the one before S1-26: the install's key, AES-GCM,
    # no associated data -- opened here without the vault's own code.
    assert sorted(_opens_with_install_key(v, row) for row in rows) == sorted(
        [value for _n, value in _EVERY_KIND] + ["under-a-tenant"])
    assert _keys(v) == []
    for name, value in _EVERY_KIND:
        assert v.retrieve(name) == value
    assert v.for_space(B).retrieve("bank") == "under-a-tenant"


def test_one_account_never_enforced_writes_what_it_always_wrote(vault) -> None:
    assert not scope.spaces_enforced()
    _stored_as_before(vault)
    assert vault.key_report()["keys"] == {} and vault.key_report()["broken"] == 0


def test_control_a_build_that_seals_without_enforcement_changes_the_rows(vault, monkeypatch) -> None:
    real = SecretVault._seal

    def always(self, name, value, tenant_id):
        with monkeypatch.context() as patch:
            patch.setattr(scope, "_enforced", True)
            return real(self, name, value, tenant_id)

    monkeypatch.setattr(SecretVault, "_seal", always)
    with pytest.raises(AssertionError):
        _stored_as_before(vault)


def test_a_vault_file_from_before_opens_and_gains_only_the_column(tmp_path, monkeypatch) -> None:
    """A file written by the build before S1-26: its rows read as they were."""
    monkeypatch.setenv("KAZMA_VAULT_KEY", VAULT_KEY)
    path = tmp_path / "vault.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE secrets (id TEXT PRIMARY KEY, name TEXT NOT NULL, encrypted_value BLOB NOT NULL,
            nonce BLOB NOT NULL, category TEXT DEFAULT 'general', metadata TEXT DEFAULT '{}',
            tenant_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE vault_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    salt = b"\x01" * 32
    conn.execute("INSERT INTO vault_meta VALUES ('pbkdf2_salt', ?)", (salt.hex(),))
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000).derive(VAULT_KEY.encode())
    conn.execute(
        "INSERT INTO secrets VALUES ('1', 'email.gmail.refresh_token', ?, ?, 'email', '{}', NULL, 't', 't')",
        (AESGCM(key).encrypt(b"\x02" * 12, b"old-token", None), b"\x02" * 12))
    conn.commit()
    conn.close()
    v = SecretVault(db_path=str(path))
    try:
        assert v.retrieve("email.gmail.refresh_token") == "old-token"
        assert _rows(v)[0]["key_id"] is None
        assert v.sample_opens() == [("email.gmail.refresh_token", True)]
    finally:
        v.close()


# ── moving existing rows, both ways ──────────────────────────────────────


def _rows_from_before(v: SecretVault) -> dict[str, str]:
    """B's rows written while Spaces was not enforced: under the install key."""
    values = {"bank": "pin-of-b", "card": "card-of-b", "door": "door-of-b"}
    for name, value in values.items():
        v.for_space(B).store(name, value)
    v.store_install_scoped("space:space-b:email.gmail.refresh_token", "token-of-b")
    v.store("cfg:providers.list.openai.api_key", "provider-key")
    v.for_space(A).store("bank", "pin-of-a")
    assert all(row["key_id"] is None for row in _rows(v))
    return {**values, "space:space-b:email.gmail.refresh_token": "token-of-b"}


def test_seal_space_moves_its_rows_and_unseal_brings_them_back(vault, monkeypatch) -> None:
    values = _rows_from_before(vault)
    before = {r["id"]: (r["name"], r["tenant_id"]) for r in _rows(vault)}
    assert vault.key_report()["unsealed"] == {B: 4, A: 1}
    assert vault.seal_space(B) == 4
    key_id = vault.space_key_ids(B)[0]["key_id"]
    for row in _rows(vault):
        mine = sealing_space(row["name"], row["tenant_id"]) == B
        assert row["key_id"] == (key_id if mine else None), row["name"]
        assert (_opens_with_install_key(vault, row) is None) == mine
    assert vault.seal_space(B) == 0  # a second run moves nothing
    assert vault.key_report() == {"keys": {B: key_id}, "sealed": {B: 4}, "unsealed": {A: 1}, "broken": 0}
    monkeypatch.setattr(scope, "_enforced", True)
    with as_space(B):
        assert {name: vault.retrieve(name) for name in values} == values
    monkeypatch.setattr(scope, "_enforced", False)
    # ... and back: the same rows, under the install's key again.
    assert vault.unseal_space(B) == 4 and vault.unseal_space(B) == 0
    assert {r["id"]: (r["name"], r["tenant_id"]) for r in _rows(vault)} == before
    assert all(r["key_id"] is None and _opens_with_install_key(vault, r) for r in _rows(vault))
    assert {name: vault.for_space(B).retrieve(name) for name in ("bank", "card", "door")} == {
        name: values[name] for name in ("bank", "card", "door")}


def _fail_on_the_third(monkeypatch) -> None:
    real, calls = SecretVault._seal_with, []

    def third_fails(self, space, key_id, name, value):
        calls.append(name)
        if len(calls) == 3:
            raise SpaceKeyMissing("planted")
        return real(self, space, key_id, name, value)

    monkeypatch.setattr(SecretVault, "_seal_with", third_fails)


def test_a_move_that_fails_half_way_changes_nothing(vault, monkeypatch) -> None:
    _rows_from_before(vault)
    _fail_on_the_third(monkeypatch)
    with pytest.raises(SpaceKeyMissing):
        vault.seal_space(B)
    assert all(row["key_id"] is None for row in _rows(vault))
    assert _keys(vault) == []


def test_control_a_move_that_commits_row_by_row_leaves_two_moved(vault, monkeypatch) -> None:
    _rows_from_before(vault)
    _fail_on_the_third(monkeypatch)
    monkeypatch.setattr(SecretVault, "_in_one_step", lambda self, space, work: work())
    with pytest.raises(SpaceKeyMissing):
        vault.seal_space(B)
    assert sum(1 for row in _rows(vault) if row["key_id"]) == 2


def test_the_owners_space_is_never_sealed(vault) -> None:
    vault.store("bank", "owners-pin", tenant_id=None)
    for move in (vault.seal_space, vault.unseal_space, vault.rotate_space_key):
        with pytest.raises(ValueError):
            move(OWNER_SPACE_ID)
    assert _rows(vault)[0]["key_id"] is None


def test_a_spaces_key_can_be_changed(vault, enforced) -> None:
    _two_spaces_store(vault)
    old = vault.space_key_ids(B)[0]["key_id"]
    a_key = vault.space_key_ids(A)[0]["key_id"]
    new = vault.rotate_space_key(B)
    assert new != old
    assert vault.space_key_ids(B) == [{"key_id": new, "state": "active",
                                       "created_at": vault.space_key_ids(B)[0]["created_at"]}]
    for row in _rows(vault):
        space = sealing_space(row["name"], row["tenant_id"])
        assert row["key_id"] == (new if space == B else a_key)
    _each_space_under_its_own_key(vault)
    with as_space(B):
        assert vault.retrieve("bank") == "pin-of-b"
        assert vault.retrieve("cfg:tenant.space-b.connectors.x.api_key") == "x-key-of-b"
    with as_space(A):
        assert vault.retrieve("bank") == "pin-of-a"


# ── erase ────────────────────────────────────────────────────────────────


def _file_bytes(path: Path) -> bytes:
    return b"".join(
        p.read_bytes() for p in (path, path.with_name(path.name + "-wal")) if p.is_file())


def test_erasing_a_space_takes_its_keys_and_leaves_the_others(vault, enforced, tmp_path) -> None:
    _two_spaces_store(vault)
    kept = next(r for r in _rows(vault) if r["name"] == "bank" and r["tenant_id"] == A)
    key_row = vault._conn.execute("SELECT * FROM space_keys WHERE space_id = ?", (A,)).fetchone()
    wrapped, sealed = bytes(key_row["wrapped"]), bytes(kept["encrypted_value"])
    assert wrapped in _file_bytes(tmp_path / "vault.db")
    assert vault.erase_space(A) == 2
    assert vault.space_key_ids(A) == [] and len(vault.space_key_ids(B)) == 1
    with as_space(B):
        assert vault.retrieve("bank") == "pin-of-b"
    # Neither the key nor the secret it sealed is left in the file.
    left = _file_bytes(tmp_path / "vault.db")
    assert wrapped not in left and sealed not in left
    # A copy of the sealed row taken before the erase opens with nothing now.
    vault._conn.execute(
        "INSERT INTO secrets (id, name, encrypted_value, nonce, tenant_id, created_at, updated_at, key_id) "
        "VALUES ('copy', 'bank', ?, ?, ?, 't', 't', ?)",
        (kept["encrypted_value"], kept["nonce"], A, kept["key_id"]))
    with as_space(A), pytest.raises(SpaceKeyMissing):
        vault.retrieve("bank")
    assert _opens_with_install_key(vault, kept) is None


def test_control_the_erase_of_before_leaves_the_key(vault, enforced) -> None:
    _two_spaces_store(vault)
    first, second = f"space:{A}:", f"cfg:tenant.{A}."
    vault._conn.execute(  # the statement erase_space ran before S1-26
        "DELETE FROM secrets WHERE tenant_id = ? OR substr(name, 1, ?) = ? OR substr(name, 1, ?) = ?",
        (A, len(first), first, len(second), second))
    assert len(vault.space_key_ids(A)) == 1


def test_erasing_a_space_through_space_data_takes_a_key_with_no_secret(vault, monkeypatch, tmp_path) -> None:
    from kazma_core import paths
    from kazma_core.spaces import space_data

    monkeypatch.setattr(paths, "vault_db_path", lambda: str(tmp_path / "vault.db"))
    vault.ensure_space_key(A)
    assert space_data._vault_rows(A) == [] and space_data._vault_keys(A) == 1
    assert space_data._erase_secrets(A) == 0
    assert vault.space_key_ids(A) == []


# ── the restore drill ────────────────────────────────────────────────────


def _backup_with_sealed_rows(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "backup"
    (d / "dbs").mkdir(parents=True)
    (d / "manifest.json").write_text('{"version": 1, "databases": {"ok": 1, "failed": 0}}', encoding="utf-8")
    (d / ".env").write_text(f"KAZMA_VAULT_KEY={VAULT_KEY}\n", encoding="utf-8")
    monkeypatch.setenv("KAZMA_VAULT_KEY", VAULT_KEY)
    v = SecretVault(db_path=str(d / "dbs" / "vault.db"))
    try:
        for n in range(6):  # the drill's sample of five reads these first
            v.store(f"cfg:providers.list.p{n}.api_key", f"provider-{n}")
        with monkeypatch.context() as patch:
            patch.setattr(scope, "_enforced", True)
            _two_spaces_store(v)
    finally:
        v.close()
    return d


def _drill_check(res, name):
    return next((c for c in res.checks if c["check"] == name), None)


def test_the_restore_drill_opens_the_sealed_rows(tmp_path, monkeypatch) -> None:
    res = restore_drill.verify_backup(_backup_with_sealed_rows(tmp_path, monkeypatch))
    assert _drill_check(res, "vault:decrypt")["ok"]
    keys = _drill_check(res, "vault:space_keys")
    assert keys["ok"] and "5 secret(s)" in keys["detail"], keys


def test_the_restore_drill_fails_a_backup_that_lost_its_space_keys(tmp_path, monkeypatch) -> None:
    d = _backup_with_sealed_rows(tmp_path, monkeypatch)
    conn = sqlite3.connect(d / "dbs" / "vault.db")
    conn.execute("DELETE FROM space_keys")
    conn.commit()
    conn.close()
    res = restore_drill.verify_backup(d)
    keys = _drill_check(res, "vault:space_keys")
    assert keys is not None and keys["ok"] is False and "5 secret(s)" in keys["detail"], keys
    # Control: the drill's check of before, alone, calls this backup good --
    # its sample reads the install's rows only.
    assert _drill_check(res, "vault:decrypt")["ok"]


def test_a_vault_with_no_sealed_row_has_no_space_key_check(tmp_path, monkeypatch) -> None:
    """One account: the drill says what it always said."""
    d = _backup_with_sealed_rows(tmp_path, monkeypatch)
    conn = sqlite3.connect(d / "dbs" / "vault.db")
    conn.execute("DELETE FROM secrets WHERE key_id IS NOT NULL")
    conn.execute("DELETE FROM space_keys")
    conn.commit()
    conn.close()
    res = restore_drill.verify_backup(d)
    assert _drill_check(res, "vault:decrypt")["ok"] and _drill_check(res, "vault:space_keys") is None


# ── what the switch-on check reads ───────────────────────────────────────


def test_the_report_names_what_is_missing(vault, fresh_identity, monkeypatch) -> None:
    monkeypatch.setattr(identity, "_key_new_spaces", lambda new: None)  # made before S1-26
    made = identity.create_space("Family", created_by=OWNER_ACCOUNT_ID)["id"]
    vault.for_space(made).store("bank", "pin")
    vault.ensure_space_key("gone-space")
    assert space_keys.report() == {
        "vault": True, "no_key": [made], "unsealed": {made: 1}, "broken": 0,
        "unknown_spaces": ["gone-space"]}
    assert vault.seal_space(made) == 1
    found = space_keys.report()
    assert found["no_key"] == [] and found["unsealed"] == {}


# ── class gates ──────────────────────────────────────────────────────────

#: The only functions of the vault that encrypt or decrypt.
_CRYPTO_HOMES = {"_encrypt", "_decrypt", "_wrap_space_key", "_space_key", "_seal_with", "_open"}
#: Who may call the install-key primitives: a row is opened by ``_open`` and
#: sealed by ``_seal`` (or moved by ``_reseal``), nowhere else.
_PRIMITIVE_CALLERS = {"_decrypt": {"_open"}, "_encrypt": {"_seal", "_reseal"}}


def _vault_problems(source: str) -> list[str]:
    tree = ast.parse(source)
    problems: list[str] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                target = node.func.value
                on_self = isinstance(target, ast.Name) and target.id == "self"
                if on_self and attr in _PRIMITIVE_CALLERS and func.name not in _PRIMITIVE_CALLERS[attr]:
                    problems.append(f"{func.name} calls self.{attr}: a row is opened by _open and sealed by _seal")
                if not on_self and attr in ("encrypt", "decrypt") and func.name not in _CRYPTO_HOMES:
                    problems.append(f"{func.name} encrypts or decrypts by itself")
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        sql = " ".join(node.value.split())
        if "INSERT INTO secrets" in sql and "key_id" not in sql:
            problems.append("an INSERT INTO secrets that names no key_id")
        if sql.startswith("SELECT") and "encrypted_value" in sql and "key_id" not in sql:
            problems.append("a SELECT of a sealed value that does not read its key_id")
    return problems


def test_the_vault_opens_and_seals_rows_in_one_place() -> None:
    assert _vault_problems(VAULT_SOURCE.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("planted", [
    "class V:\n    def retrieve(self, row):\n        return self._decrypt(row['encrypted_value'], row['nonce'])\n",
    "class V:\n    def store(self, value):\n        return self._encrypt(value)\n",
    "class V:\n    def peek(self, key, row):\n        return AESGCM(key).decrypt(row['nonce'], row['v'], None)\n",
    "SQL = 'INSERT INTO secrets (id, name, encrypted_value, nonce) VALUES (?, ?, ?, ?)'\n",
    "SQL = 'SELECT name, encrypted_value, nonce FROM secrets'\n",
])
def test_control_planted_shapes_fail_the_vault_gate(planted) -> None:
    assert _vault_problems(planted)


_SEALED_VALUE_SQL = re.compile(
    r"\b(select\b[^;]*\bencrypted_value\b[^;]*\bfrom secrets\b|insert into secrets\b|update secrets\b)")
_WRAPPED_KEY_SQL = re.compile(
    r"\b(select (\*|[^;]*\bwrapped\b)[^;]*\bfrom space_keys\b|insert (or \w+ )?into space_keys\b|update space_keys\b)")


def _outside_problems(files: dict[str, str]) -> list[str]:
    """Product code outside the vault that reads a sealed value or a wrapped
    key: only the vault opens either."""
    problems: list[str] = []
    for name, source in files.items():
        if name.endswith("security/vault.py"):
            continue
        for node in ast.walk(ast.parse(source)):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            sql = " ".join(node.value.split()).lower()
            if _SEALED_VALUE_SQL.search(sql):
                problems.append(f"{name}: reads or writes a secret's sealed value")
            if _WRAPPED_KEY_SQL.search(sql):
                problems.append(f"{name}: reads or writes a wrapped space key")
    return problems


def _product_sources() -> dict[str, str]:
    import subprocess

    listed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "--", *PRODUCT],
        cwd=REPO, capture_output=True, text=True, check=True).stdout.split("\n")
    out: dict[str, str] = {}
    for rel in listed:
        if rel.endswith(".py") and "/tests/" not in rel and (REPO / rel).is_file():
            out[rel] = (REPO / rel).read_text(encoding="utf-8")
    return out


def test_only_the_vault_reads_a_sealed_value_or_a_wrapped_key() -> None:
    files = _product_sources()
    assert "kazma-core/kazma_core/security/vault.py" in files
    assert _outside_problems(files) == []


@pytest.mark.parametrize("planted", [
    "Q = 'SELECT name, encrypted_value, nonce FROM secrets WHERE name = ?'\n",
    "Q = 'SELECT wrapped, nonce FROM space_keys WHERE space_id = ?'\n",
    "Q = 'INSERT INTO space_keys (key_id, space_id) VALUES (?, ?)'\n",
])
def test_control_planted_readers_fail_the_outside_gate(planted) -> None:
    assert _outside_problems({"kazma-core/kazma_core/somewhere.py": planted})
