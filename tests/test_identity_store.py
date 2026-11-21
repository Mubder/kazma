"""The identity store: accounts, spaces and memberships (Spaces S1-01).

``platform.users`` was one list in one settings key, read, changed and saved
back whole. Two saves at once and one was lost: a user created while another
user's password was being set was gone. The identity store
(``kazma_core.spaces.identity``) keeps accounts, spaces and memberships in
one document changed only by ``update_registry`` -- one atomic step per
change -- and the old list moves into it once.

Through the real settings store. The read-modify-write of the old list is the
negative control.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.security import platform_rbac
from kazma_core.spaces import identity
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_identity():
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    store.delete("platform.users")
    yield
    store.delete(identity.REGISTRY_KEY)
    store.delete("platform.users")


# ── The owner is always there ────────────────────────────────────────────


def test_an_install_with_nothing_stored_is_the_owner_alone() -> None:
    reg = identity.read_registry()
    assert list(reg["accounts"]) == [OWNER_ACCOUNT_ID]
    assert reg["accounts"][OWNER_ACCOUNT_ID]["role"] == "admin"
    assert reg["spaces"][OWNER_SPACE_ID]["kind"] == "host"
    assert reg["spaces"][OWNER_SPACE_ID]["personal_of"] == OWNER_ACCOUNT_ID
    assert identity.is_member(OWNER_ACCOUNT_ID, OWNER_SPACE_ID)
    # A read writes nothing: an install with one account stores no registry.
    assert get_config_store().get(identity.REGISTRY_KEY, None) is None
    assert identity.move_platform_users() == 0
    assert get_config_store().get(identity.REGISTRY_KEY, None) is None


def test_the_owner_is_not_a_platform_user() -> None:
    """An install with only the owner is single-operator, as before Spaces."""
    assert platform_rbac.list_users() == []
    assert platform_rbac.multi_user_enabled() is False


# ── The old list moves in once and keeps every user ──────────────────────


def _old_list() -> list[dict]:
    return [
        {"user_id": "ab12cd34ef56ab78", "username": "Layla", "role": "admin", "enabled": True,
         "password_hash": platform_rbac._hash_password("layla-password-1"),
         "meta": {"tenant_id": "layla"}},
        {"user_id": "Not A Valid Id", "username": "omar", "role": "viewer", "enabled": False,
         "password_hash": platform_rbac._hash_password("omar-password-1"), "meta": {}},
        {"user_id": "owner", "username": "sara", "role": "operator", "enabled": True,
         "password_hash": platform_rbac._hash_password("sara-password-1"), "meta": {}},
    ]


def test_the_move_keeps_every_user() -> None:
    get_config_store().set("platform.users", _old_list(), category="auth")

    before = {u["username"]: u for u in identity.local_users()}
    assert set(before) == {"Layla", "omar", "sara"}, "read before the move shows the same users"

    assert identity.move_platform_users() == 3
    assert get_config_store().get("platform.users", None) in (None, []), "the old key is gone"
    after = {u["username"]: u for u in identity.local_users()}
    assert after == before, "the move changes no user"

    for name, role, enabled in (("Layla", "admin", True), ("omar", "viewer", False),
                                ("sara", "operator", True)):
        user = platform_rbac.get_user(name)
        assert user is not None and user.role == role and user.enabled is enabled
    assert platform_rbac.get_user("Layla").meta == {"tenant_id": "layla"}
    assert platform_rbac.authenticate_local_user("Layla", "layla-password-1") is not None
    assert platform_rbac.authenticate_local_user("sara", "sara-password-1") is not None
    assert platform_rbac.authenticate_local_user("omar", "omar-password-1") is None, "disabled"
    assert platform_rbac.multi_user_enabled() is True

    # A user whose old id was "owner" is not the owner.
    reg = identity.read_registry()
    assert reg["accounts"][OWNER_ACCOUNT_ID]["sign_in"]["kind"] == "owner"
    assert sum(1 for a in reg["accounts"].values() if a["role"] == "admin") == 1

    # Moving again changes nothing.
    version = reg["version"]
    assert identity.move_platform_users() == 3
    assert identity.read_registry()["version"] == version


def test_a_user_keeps_their_account_through_changes() -> None:
    platform_rbac.create_local_user("layla", "first-password-1", role="operator")
    first = {a["name"]: a["id"] for a in identity.list_accounts() if a["id"] != OWNER_ACCOUNT_ID}
    platform_rbac.create_local_user("omar", "omar-password-1", role="viewer")
    platform_rbac.set_local_password("layla", "second-password-2")
    platform_rbac.create_local_user("layla", "third-password-3", role="admin")
    now = {a["name"]: a["id"] for a in identity.list_accounts() if a["id"] != OWNER_ACCOUNT_ID}
    assert now["layla"] == first["layla"]
    assert platform_rbac.authenticate_local_user("layla", "third-password-3").role == "admin"
    assert platform_rbac.authenticate_local_user("layla", "second-password-2") is None


# ── Two writers never lose each other's change ───────────────────────────


def _two_writers(first, second) -> None:
    """Run *first*, and inside its read-to-write window run *second*."""
    barrier = threading.Barrier(2, timeout=10)
    errors: list[BaseException] = []

    def _run(fn) -> None:
        try:
            barrier.wait()
            fn()
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=_run, args=(fn,)) for fn in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors


def test_users_created_at_the_same_moment_are_both_kept() -> None:
    for round_ in range(6):
        names = [f"a{round_}", f"b{round_}"]
        _two_writers(
            lambda: platform_rbac.create_local_user(names[0], "password-number-1"),
            lambda: platform_rbac.create_local_user(names[1], "password-number-2"),
        )
        got = {u.username for u in platform_rbac.list_users()}
        assert set(names) <= got, f"round {round_}: {names} not all in {sorted(got)}"


def test_control_the_old_read_modify_write_loses_a_user() -> None:
    """The list as it was saved before: read, change, save the whole list.
    A user saved between the read and the save is gone."""
    platform_rbac.create_local_user("first", "password-number-1")

    stale = platform_rbac._load_users_from_store()  # writer A reads
    platform_rbac.create_local_user("second", "password-number-2")  # writer B saves
    for u in stale:  # writer A changes its copy and saves the whole list
        if u["username"] == "first":
            u["role"] = "viewer"
    platform_rbac._save_users_to_store(stale)

    assert {u.username for u in platform_rbac.list_users()} == {"first"}, (
        "the whole-list save is expected to lose 'second': it is the negative control"
    )

    # The same change as one atomic step keeps both.
    platform_rbac.create_local_user("second", "password-number-2")

    def _demote(users: list[dict]) -> None:
        for u in users:
            if u["username"] == "first":
                u["role"] = "operator"

    platform_rbac._update_users(_demote)
    assert {u.username for u in platform_rbac.list_users()} == {"first", "second"}


def test_no_product_code_reads_then_saves_the_whole_list() -> None:
    """Class gate: outside ``platform_rbac`` nothing calls
    ``_save_users_to_store`` (the whole-list save); a change goes through
    ``_update_users`` or the identity store."""
    offenders: list[str] = []
    for package in ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-tui",
                    "kazma-skills"):
        for path in (REPO / package).rglob("*.py"):
            if path.name == "platform_rbac.py" or "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                name = None
                if isinstance(node, ast.Call):
                    func = node.func
                    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == "_save_users_to_store":
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not offenders, f"whole-list saves of the user list: {offenders}"


def test_the_gate_sees_a_planted_whole_list_save(tmp_path: Path) -> None:
    planted = ast.parse("def f(users):\n    _save_users_to_store(users)\n")
    calls = [n for n in ast.walk(planted) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_save_users_to_store"]
    assert len(calls) == 1


# ── Spaces and memberships ───────────────────────────────────────────────


def test_a_shared_space_and_its_members() -> None:
    platform_rbac.create_local_user("layla", "password-number-1")
    layla = next(a["id"] for a in identity.list_accounts() if a["name"] == "layla")
    made = identity.create_space("Family", created_by=OWNER_ACCOUNT_ID)
    assert made["kind"] == "sandboxed" and made["personal_of"] is None
    assert identity.is_member(OWNER_ACCOUNT_ID, made["id"])
    assert not identity.is_member(layla, made["id"])

    identity.add_member(made["id"], layla)
    assert identity.is_member(layla, made["id"])
    assert [s["id"] for s in identity.spaces_of(layla)] == [made["id"]]
    assert identity.spaces_of(OWNER_ACCOUNT_ID)[0]["id"] == OWNER_SPACE_ID, "personal space first"

    with pytest.raises(identity.IdentityError) as twice:
        identity.add_member(made["id"], layla)
    assert twice.value.code == "already_member"

    identity.remove_member(made["id"], layla)
    assert not identity.is_member(layla, made["id"])
    with pytest.raises(identity.IdentityError) as gone:
        identity.remove_member(made["id"], layla)
    assert gone.value.code == "not_a_member"


def test_a_personal_space_has_one_member_forever() -> None:
    """Decision N5: nobody is added to a personal space, the admin included."""
    platform_rbac.create_local_user("layla", "password-number-1")
    layla = next(a["id"] for a in identity.list_accounts() if a["name"] == "layla")
    personal = identity.create_space("", created_by=OWNER_ACCOUNT_ID, personal_of=layla)
    assert identity.is_member(layla, personal["id"])
    assert not identity.is_member(OWNER_ACCOUNT_ID, personal["id"])

    for intruder in (OWNER_ACCOUNT_ID,):
        with pytest.raises(identity.IdentityError) as refused:
            identity.add_member(personal["id"], intruder)
        assert refused.value.code == "personal_space_is_private"
    with pytest.raises(identity.IdentityError):
        identity.add_member(OWNER_SPACE_ID, layla)
    assert not identity.is_member(layla, OWNER_SPACE_ID)


def test_a_refused_change_writes_nothing() -> None:
    made = identity.create_space("Family", created_by=OWNER_ACCOUNT_ID)
    version = identity.read_registry()["version"]
    for bad in (
        lambda: identity.add_member(made["id"], "nobody"),
        lambda: identity.add_member("no-such-space", OWNER_ACCOUNT_ID),
        lambda: identity.create_space("x", created_by=OWNER_ACCOUNT_ID, space_id=made["id"]),
        lambda: identity.create_space("x", created_by=OWNER_ACCOUNT_ID, space_id="Bad Id"),
        lambda: identity.create_space("x", created_by=OWNER_ACCOUNT_ID, kind="palace"),
    ):
        with pytest.raises(identity.IdentityError):
            bad()
    assert identity.read_registry()["version"] == version


def test_there_is_one_admin_and_it_is_the_owner() -> None:
    def _second_admin(reg: dict) -> None:
        reg["accounts"]["mallory"] = {
            "id": "mallory", "name": "mallory", "role": "admin", "enabled": True,
            "sign_in": {"kind": "local"},
        }

    with pytest.raises(identity.IdentityError) as refused:
        identity.update_registry(_second_admin)
    assert refused.value.code == "one_admin"

    def _demote_owner(reg: dict) -> None:
        reg["accounts"][OWNER_ACCOUNT_ID]["role"] = "member"

    with pytest.raises(identity.IdentityError) as refused:
        identity.update_registry(_demote_owner)
    assert refused.value.code == "owner_required"


# ── An unreadable store is never "no accounts" ───────────────────────────


def test_an_unreadable_store_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Down:
        def get(self, *_a, **_k):
            raise OSError("database is away")

    monkeypatch.setattr(identity, "_store", lambda: _Down())
    with pytest.raises(identity.IdentityUnavailable):
        identity.read_registry()
    with pytest.raises(identity.IdentityUnavailable):
        identity.is_member(OWNER_ACCOUNT_ID, OWNER_SPACE_ID)
    with pytest.raises(platform_rbac.PlatformUsersUnavailable):
        platform_rbac.multi_user_enabled()


def test_a_registry_of_the_wrong_type_is_unreadable() -> None:
    get_config_store().set(identity.REGISTRY_KEY, ["not", "a", "mapping"], category="auth")
    with pytest.raises(identity.IdentityUnavailable):
        identity.read_registry()


# ── Spaces is not enforced, and one checked switch turns it on ───────────


def test_spaces_is_not_enforced_and_no_product_code_writes_the_switch() -> None:
    """Spaces is off, and one function writes the switch:
    ``preconditions.switch_on`` (plan S1-33), which checks its preconditions
    first (``tests/test_second_account_preconditions.py``)."""
    assert identity.enforcement_enabled() is False
    writers: list[str] = []
    for package in ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-tui",
                    "kazma-skills"):
        for path in (REPO / package).rglob("*.py"):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "ENFORCED_KEY" not in text and "identity.enforced" not in text:
                continue
            for node in ast.walk(ast.parse(text)):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("set", "batch_set", "atomic_update",
                                               "set_if_absent")
                        and "ENFORCED_KEY" in ast.unparse(node)):
                    writers.append(path.relative_to(REPO).as_posix())
    assert writers == ["kazma-core/kazma_core/spaces/preconditions.py"], (
        f"the Spaces switch is written by: {writers}")
