"""The vault's space scope (Spaces S1-18).

Once Spaces is enforced a vault name is the install's or one space's
(``security.vault.secret_scope``). A space's secret is returned only to work
in that space: never to another space, never to work with no space bound.
The install's list stays: a provider key is read by everyone.

Each rule is shown failing on the vault as it was (the read with the
``default`` rung and the fall-back to a row with no tenant), and an install
that does not enforce Spaces behaves exactly as before.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from kazma_core.security import vault as vault_mod
from kazma_core.security.vault import SecretVault, VaultScopeError, retrieve_scoped, secret_scope
from kazma_core.spaces import scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, NoSpaceBound, bind_scope, install_work
from kazma_core.tenant_context import tenant_scope

A, B = "space-a", "space-b"
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "a" * 64)
    v = SecretVault(db_path=str(tmp_path / "vault.db"))
    vault_mod.reset_posture_cache()
    yield v
    v.close()
    vault_mod.reset_posture_cache()


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


def as_space(space: str, who: str = "someone"):
    return bind_scope(space, OWNER_ACCOUNT_ID if space == OWNER_SPACE_ID else who)


def old_retrieve_scoped(vault: SecretVault, name: str, monkeypatch) -> str | None:
    """The read as it was before S1-18: the vault as an install that does not
    enforce Spaces reads it, with the ``default`` rung open."""
    with monkeypatch.context() as patch:
        patch.setattr(scope, "_enforced", False)
        patch.setattr(vault_mod, "_operator_default_rung_allowed", lambda: True)
        return retrieve_scoped(name, vault)


# ── whose a name is ──────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "expected"), [
    ("cfg:providers.list.openai.api_key", ("install", None)),
    ("email.gmail.refresh_token", ("space", "default")),  # the owner's mailbox
    ("email.account.work.imap_password", ("space", "default")),
    ("calendar.google.refresh_token", ("space", "default")),
    ("email.gmail.client_secret", ("install", None)),  # the app registration
    ("cfg:voice.tts.api_key", ("install", None)),  # a setting: the install's until S1-23
    ("cfg-url:proxy.url", ("install", None)),
    ("backups.offsite.ftp.password", ("install", None)),
    ("space:space-b:email.gmail.refresh_token", ("space", "space-b")),
    ("cfg:tenant.space-b.connectors.x.api_key", ("space", "space-b")),
    ("cfg:connectors.x.api_key", ("space", None)),
    ("cfg:connectors.github.oauth_token", ("space", None)),
    ("x_api_key", ("space", None)),
    ("my_bank_password", ("space", None)),
    ("space:broken", ("space", "")),  # names no space: nobody's
])
def test_whose_a_name_is(name, expected) -> None:
    assert secret_scope(name) == expected


# ── a space's secret stays in its space ──────────────────────────────────


def test_a_spaces_secret_is_not_returned_to_another_space_or_to_no_space(vault, enforced) -> None:
    with as_space(A):
        vault.store("note", "a-secret")
    with as_space(B):
        vault.store("note", "b-secret")
    with as_space(A):
        assert vault.retrieve("note") == "a-secret"
        assert retrieve_scoped("note", vault) == "a-secret"
    with as_space(B):
        assert vault.retrieve("note") == "b-secret"
    # No space bound, and the install's own work: nothing.
    assert vault.retrieve("note") is None
    assert retrieve_scoped("note", vault) is None
    with install_work("a backup"):
        assert vault.retrieve("note") is None
        assert retrieve_scoped("note", vault) is None


def test_the_owners_secret_is_not_lent_to_work_with_no_space(vault, enforced, monkeypatch) -> None:
    """The plan's control: the ``default`` rung."""
    with as_space(OWNER_SPACE_ID):
        vault.store("cfg:connectors.x.api_key", "owner-x-key")
    assert retrieve_scoped("cfg:connectors.x.api_key", vault) is None
    with as_space(B):
        assert retrieve_scoped("cfg:connectors.x.api_key", vault) is None
    with as_space(OWNER_SPACE_ID):
        assert retrieve_scoped("cfg:connectors.x.api_key", vault) == "owner-x-key"
    # Control: the read as it was hands work with no space the owner's key.
    assert old_retrieve_scoped(vault, "cfg:connectors.x.api_key", monkeypatch) == "owner-x-key"


def test_a_row_with_no_tenant_is_the_owners_and_nobody_elses(vault, enforced, monkeypatch) -> None:
    # Stored before Spaces, with nothing bound: a row with no tenant.
    monkeypatch.setattr(scope, "_enforced", False)
    vault.store("old_note", "from-before-spaces")
    monkeypatch.setattr(scope, "_enforced", True)
    with as_space(OWNER_SPACE_ID):
        assert vault.retrieve("old_note") == "from-before-spaces"
    with as_space(B):
        assert vault.retrieve("old_note") is None
        assert [row["name"] for row in vault.list_secrets()] == []
    assert vault.retrieve("old_note") is None
    # Control: the read as it was falls back from any space to that row.
    monkeypatch.setattr(scope, "_enforced", False)
    with tenant_scope(B):
        assert vault.retrieve("old_note") == "from-before-spaces"


def test_a_name_that_carries_its_space_is_that_spaces_only(vault, enforced, monkeypatch) -> None:
    name = f"space:{B}:email.gmail.refresh_token"
    with as_space(B):
        vault.store(name, "b-mail-token")
        assert vault.retrieve(name) == "b-mail-token"
        # The mail code pins the tenant to none while its scope stays bound.
        with tenant_scope(None):
            assert vault.retrieve(name) == "b-mail-token"
    with as_space(A):
        assert vault.retrieve(name) is None
        with pytest.raises(VaultScopeError):
            vault.store(name, "taken-over")
        assert vault.delete(name) is False
    assert vault.retrieve(name) is None
    with pytest.raises(NoSpaceBound):
        vault.store(name, "from-no-one")
    with as_space(B):
        assert vault.retrieve(name) == "b-mail-token"
        assert vault.delete(name) is True
        vault.store(name, "b-mail-token")
    # Control: as it was, anyone who names it reads it.
    monkeypatch.setattr(scope, "_enforced", False)
    with tenant_scope(A):
        assert vault.retrieve(name) == "b-mail-token"


def test_a_spaces_setting_is_read_only_in_that_space(vault, enforced) -> None:
    name = f"cfg:tenant.{B}.connectors.x.api_key"
    with as_space(B):
        vault.store(name, "b-x-key")
        assert retrieve_scoped(name, vault) == "b-x-key"
    with as_space(A):
        assert retrieve_scoped(name, vault) is None
    with as_space(OWNER_SPACE_ID):
        assert retrieve_scoped(name, vault) is None
    assert retrieve_scoped(name, vault) is None


def test_the_owners_mailbox_and_calendar_are_the_owners_spaces(vault, enforced, monkeypatch) -> None:
    """The review of S1-18: ``email.`` and ``calendar.`` are on the install
    list, so any space that named the owner's token read it."""
    names = ("email.gmail.refresh_token", "email.account.work.imap_password",
             "calendar.google.refresh_token")
    with as_space(OWNER_SPACE_ID):
        for name in names:
            vault.store(name, f"owners-{name}")
        vault.store("email.gmail.client_secret", "the-app-registration")
    with as_space(B):
        for name in names:
            assert vault.retrieve(name) is None
            assert retrieve_scoped(name, vault) is None
            with pytest.raises(VaultScopeError):
                vault.store(name, "taken-over")
            assert vault.delete(name) is False
        assert vault.retrieve("email.gmail.client_secret") == "the-app-registration"
        assert vault.list_secrets() == []
    for name in names:
        assert vault.retrieve(name) is None  # no space bound
    with as_space(OWNER_SPACE_ID):
        for name in names:
            assert vault.retrieve(name) == f"owners-{name}"
            # The mail code pins the tenant to none while its scope stays bound.
            with tenant_scope(None):
                assert vault.retrieve(name) == f"owners-{name}"
        vault.store(names[0], "rotated")  # still one row, with no tenant
        assert vault.describe_secret(names[0])[0]["tenant_id"] is None
        assert vault.delete(names[0]) is True
    # Control: the rule as it was first built (the install list alone) hands
    # B the owner's token.
    monkeypatch.setattr("kazma_core.spaces.connected_accounts.OWNER_ACCOUNT_PREFIXES", ())
    with as_space(B):
        assert vault.retrieve(names[1]) == f"owners-{names[1]}"


def test_a_token_from_before_spaces_is_read_by_the_owners_space(vault, enforced, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    vault.store("email.gmail.refresh_token", "kept-since-august")  # as live keeps it
    monkeypatch.setattr(scope, "_enforced", True)
    with as_space(OWNER_SPACE_ID):
        assert vault.retrieve("email.gmail.refresh_token") == "kept-since-august"
    with as_space(B):
        assert vault.retrieve("email.gmail.refresh_token") is None


def test_the_offsite_backup_reads_the_owners_drive_grant_with_no_space_bound(vault, enforced, monkeypatch) -> None:
    from kazma_core.backup import cloud_sync

    monkeypatch.setattr(vault_mod, "get_vault", lambda: vault)
    with as_space(OWNER_SPACE_ID):
        vault.store("email.gmail.refresh_token", "owners-drive-grant")
    assert cloud_sync._read_vault("email.gmail.refresh_token") == "owners-drive-grant"
    cloud_sync._write_vault("email.gmail.access_token", "refreshed", category="email")
    with as_space(OWNER_SPACE_ID):
        assert vault.retrieve("email.gmail.access_token") == "refreshed"
    with as_space(B):
        assert vault.retrieve("email.gmail.access_token") is None
    # Control: read with nothing bound, as the backup did, it finds nothing.
    assert retrieve_scoped("email.gmail.refresh_token", vault) is None


# ── the install's list stays ─────────────────────────────────────────────


def test_the_installs_secrets_are_read_by_every_space_and_by_no_space(vault, enforced) -> None:
    with as_space(OWNER_SPACE_ID):
        vault.store("cfg:providers.list.openai.api_key", "provider-key")
        vault.store("cfg:voice.tts.api_key", "voice-key")  # not on the list: a setting
    for space in (A, B, OWNER_SPACE_ID):
        with as_space(space):
            assert retrieve_scoped("cfg:providers.list.openai.api_key", vault) == "provider-key"
            assert retrieve_scoped("cfg:voice.tts.api_key", vault) == "voice-key"
    assert retrieve_scoped("cfg:providers.list.openai.api_key", vault) == "provider-key"
    with install_work("the backup sync"):
        assert retrieve_scoped("cfg:voice.tts.api_key", vault) == "voice-key"


def test_a_setting_saved_before_spaces_under_the_owners_tenant_is_still_read(vault, enforced, monkeypatch) -> None:
    """Live keeps half its rows under tenant ``default``: a setting's secret
    saved there is the install's once enforced, for every space."""
    monkeypatch.setattr(scope, "_enforced", False)
    with tenant_scope("default"):
        vault.store("cfg:voice.tts.api_key", "saved-in-settings")
    vault.store("backups.offsite.ftp.password", "nas-password")
    monkeypatch.setattr(scope, "_enforced", True)
    with as_space(B):
        assert retrieve_scoped("cfg:voice.tts.api_key", vault) == "saved-in-settings"
    assert retrieve_scoped("backups.offsite.ftp.password", vault) == "nas-password"
    # A new save from any space replaces every copy.
    with as_space(B):
        vault.store("cfg:voice.tts.api_key", "saved-again")
    with as_space(OWNER_SPACE_ID):
        assert retrieve_scoped("cfg:voice.tts.api_key", vault) == "saved-again"
    assert vault.delete("cfg:voice.tts.api_key") is True
    assert retrieve_scoped("cfg:voice.tts.api_key", vault) is None


# ── writing, listing, deleting ───────────────────────────────────────────


def test_a_secret_is_never_written_for_no_one(vault, enforced) -> None:
    with pytest.raises(NoSpaceBound):
        vault.store("note", "whose?")
    with install_work("a sweep"), pytest.raises(NoSpaceBound):
        vault.store("note", "whose?")
    assert vault.count == 0


def test_a_list_holds_the_callers_space_only(vault, enforced) -> None:
    with as_space(A):
        vault.store("a_note", "1")
    with as_space(B):
        vault.store("b_note", "2")
        vault.store(f"space:{B}:email.gmail.refresh_token", "3")
    with as_space(OWNER_SPACE_ID):
        vault.store("cfg:providers.list.openai.api_key", "4")
        vault.store("owner_note", "5")
    names = lambda: sorted(row["name"] for row in vault.list_secrets())  # noqa: E731
    with as_space(A):
        assert names() == ["a_note"]
    with as_space(B):
        assert names() == ["b_note", f"space:{B}:email.gmail.refresh_token"]
    with as_space(OWNER_SPACE_ID):
        assert names() == ["cfg:providers.list.openai.api_key", "owner_note"]
    assert names() == ["cfg:providers.list.openai.api_key"]  # no space: the install's
    assert sorted(r["name"] for r in vault.for_space(A).list_secrets()) == ["a_note"]


def test_a_delete_removes_the_callers_own_row_only(vault, enforced, monkeypatch) -> None:
    for space in (A, B):
        with as_space(space):
            vault.store("note", space)
    with as_space(A):
        assert vault.delete("note", every_scope=True) is True
        assert vault.retrieve("note") is None
    with as_space(B):
        assert vault.retrieve("note") == B
    assert vault.delete("note") is False  # no space: nothing
    with as_space(B):
        assert vault.retrieve("note") == B
    # The owner's delete takes its row from before Spaces too (live keeps a
    # sign-in under both).
    monkeypatch.setattr(scope, "_enforced", False)
    vault.store("cfg:connectors.github.oauth_token", "old-global")
    with tenant_scope("default"):
        vault.store("cfg:connectors.github.oauth_token", "older-default")
    monkeypatch.setattr(scope, "_enforced", True)
    with as_space(OWNER_SPACE_ID):
        assert vault.delete("cfg:connectors.github.oauth_token") is True
        assert vault.retrieve("cfg:connectors.github.oauth_token") is None
    with as_space(B):
        assert vault.retrieve("note") == B


def test_two_spaces_holding_one_name_are_not_a_divergent_duplicate(vault, enforced, monkeypatch) -> None:
    for space in (A, B):
        with as_space(space):
            vault.store("note", f"{space}-value")
    assert vault.find_divergent_duplicates() == []
    # The install's names still are: a copy under the owner's tenant that
    # disagrees with the one with no tenant.
    monkeypatch.setattr(scope, "_enforced", False)
    vault.store("cfg:voice.tts.api_key", "one")
    with tenant_scope("default"):
        vault.store("cfg:voice.tts.api_key", "two")
    monkeypatch.setattr(scope, "_enforced", True)
    assert [d["name"] for d in vault.find_divergent_duplicates()] == ["cfg:voice.tts.api_key"]


def test_the_restore_drills_sample_judges_rows_with_no_space_bound(vault, enforced) -> None:
    with as_space(A):
        vault.store("a_note", "1")
    with as_space(OWNER_SPACE_ID):
        vault.store("cfg:providers.list.openai.api_key", "2")
    assert sorted(vault.sample_opens(5)) == [("a_note", True), ("cfg:providers.list.openai.api_key", True)]


def test_a_handle_for_a_space_names_it_on_every_call(vault, enforced) -> None:
    a = vault.for_space(A)
    a.store("note", "a-secret")
    assert a.retrieve("note") == "a-secret"
    assert vault.for_space(B).retrieve("note") is None
    assert a.delete("note") is True
    with pytest.raises(ValueError):
        vault.for_space("Not A Space")


def test_a_settings_secret_for_no_one_is_refused_not_kept_as_plaintext(tmp_path, monkeypatch, enforced) -> None:
    """The settings store kept a secret as plaintext when the vault refused
    it: a space's secret saved with no space bound would sit in the settings
    table, readable by every space."""
    from kazma_core import config_store as cs

    monkeypatch.setenv("KAZMA_VAULT_KEY", "a" * 64)
    v = SecretVault(db_path=str(tmp_path / "v.db"))
    monkeypatch.setattr(cs, "_try_get_vault", lambda: v)
    store = cs.ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        with pytest.raises(NoSpaceBound):
            store.set("connectors.x.api_key", "x-key-of-no-one", category="connectors")
        assert store._stored_raw("connectors.x.api_key") is None
        with pytest.raises(NoSpaceBound):
            store.set("connectors.x.credentials", {"api_key": "nested-of-no-one"}, category="connectors")
        with as_space(OWNER_SPACE_ID):
            store.set("connectors.x.api_key", "owners-x-key", category="connectors")
            assert store.get("connectors.x.api_key") == "owners-x-key"
        assert str(store._stored_raw("connectors.x.api_key")).startswith("vault://")
        # An install's setting is saved by anyone, bound or not.
        store.set("voice.tts.api_key", "voice-key", category="voice")
        assert store.get("voice.tts.api_key") == "voice-key"
    finally:
        store.close()
        v.close()


def test_the_matrix_cases_fail_on_the_vault_as_it_was(monkeypatch) -> None:
    """Control for ``tests/_vault_space_cases.py``: with the vault's rules
    as they were, A is shown the owner's secret from before Spaces."""
    from tests import _vault_space_cases as cases
    from tests._space_matrix import Matrix

    with Matrix() as matrix:
        cases.vault_retrieve(matrix)
        monkeypatch.setattr(vault_mod, "_enforced", lambda: False)
        with pytest.raises(AssertionError):
            cases.vault_retrieve(matrix)
        with pytest.raises(AssertionError):
            cases.vault_list(matrix)


# ── an install that does not enforce Spaces is unchanged ─────────────────


def test_without_spaces_the_vault_reads_and_writes_as_before(vault) -> None:
    assert not scope.spaces_enforced()
    vault.store("note", "global")  # nothing bound: a row with no tenant
    with tenant_scope("default"):
        assert vault.retrieve("note") == "global"  # tenant, then no tenant
        vault.store("note", "default-copy")
        assert vault.retrieve("note") == "default-copy"
        assert sorted(r["tenant"] for r in vault.list_secrets()) == ["default", "global"]
    assert vault.retrieve("note") == "global"
    with tenant_scope("other"):
        assert vault.retrieve("note") == "global"
    # A setting off the install list keeps its caller's tenant.
    with tenant_scope("default"):
        vault.store("cfg:voice.tts.api_key", "voice")
    assert vault.retrieve("cfg:voice.tts.api_key") is None
    with tenant_scope("default"):
        assert vault.delete("note") is True
    assert vault.retrieve("note") == "global"
    assert vault.delete("note", every_scope=True) is True


# ── the gate: nothing names a tenant to the vault ────────────────────────

#: Product code that passes a tenant to a vault call, with why. Everything
#: else lets the vault read the space the work is bound to.
NAMES_A_TENANT: dict[str, str] = {
    "kazma-core/kazma_core/security/vault.py": "the vault itself, and its handle for one space",
}

_VAULT_CALLS = {"store", "retrieve", "delete", "list_secrets"}


def tenant_naming_calls(source: str) -> list[int]:
    """Lines of *source* that call a vault method with a tenant: a keyword
    ``tenant_id=``, or the positional place it has in ``retrieve`` / ``delete``
    / ``list_secrets``, on a receiver that is a vault by its name."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _VAULT_CALLS:
            continue
        receiver = ast.unparse(node.func.value).lower()
        if "vault" not in receiver and receiver not in {"v", "_v"}:
            continue
        positional = {"retrieve": 1, "delete": 1, "list_secrets": 0, "store": 4}[node.func.attr]
        if any(k.arg == "tenant_id" for k in node.keywords) or len(node.args) > positional:
            found.append(node.lineno)
    return found


def test_no_product_code_names_a_tenant_to_the_vault() -> None:
    problems: list[str] = []
    for package in ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-skills", "kazma-tui", "scripts"):
        for path in sorted((REPO / package).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel in NAMES_A_TENANT:
                continue
            for line in tenant_naming_calls(path.read_text(encoding="utf-8")):
                problems.append(f"{rel}:{line}")
    assert not problems, (
        "These calls name a tenant to the vault. The vault reads the space the work "
        "is bound to (bind_scope), or use vault.for_space(space) and declare the file "
        "in NAMES_A_TENANT with why:\n  " + "\n  ".join(problems)
    )


def test_the_gate_finds_a_planted_call() -> None:
    planted = (
        "def f(vault, v):\n"
        "    vault.retrieve('x', 'space-b')\n"
        "    v.store('x', 'y', tenant_id='space-b')\n"
        "    get_vault().list_secrets('space-b')\n"
        "    vault.retrieve('x')\n"
        "    other.retrieve('x', 'y')\n"
    )
    assert tenant_naming_calls(planted) == [2, 3, 4]
