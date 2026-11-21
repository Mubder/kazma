"""Spaces S1-33: what must hold before a second person is let in.

``kazma_core.spaces.preconditions`` names each precondition; a new account
(``people.create_person``) and the switch that turns Spaces on
(``preconditions.switch_on``) are refused while one is missing, and say
which.

* With everything in place a second account is made.
* With any ONE precondition missing, on its own, it is refused and the
  refusal names that one and no other; nothing is created.
* The switch: the same, plus the older multi-user mode's leftovers.
* The move's own steps (``plan`` / ``apply``): the two-row shape the live
  vault keeps (a global row and an older ``default`` one) ends with one
  value; a second run changes nothing; the vault is copied first.
* ``kazma spaces migrate --check`` and ``--dry-run`` read only.

Controls: the switch and the account with no check (the code of before) let
the second account in with every precondition missing; the old tenant
fallback answers the owner's space on an enforcing install; a planted
account writer and a planted switch writer fail the source gates.
"""

from __future__ import annotations

import ast
import http.server
import json
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from kazma_core import tenant_context
from kazma_core.config_store import get_config_store
from kazma_core.security import vault as vault_mod
from kazma_core.security.vault import SecretVault
from kazma_core.spaces import identity, people, preconditions, scope
from kazma_core.spaces.scope import NoSpaceBound, bind_scope

pytestmark = pytest.mark.real_preconditions

REPO = Path(__file__).resolve().parent.parent
VAULT_KEY = "b" * 64
GITHUB = "cfg:connectors.github.oauth_token"
PACKAGES = ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
            "kazma-cli/kazma_cli", "kazma-skills/kazma_skills", "kazma-tui/kazma_tui")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", VAULT_KEY)
    v = SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "_vault", v)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    monkeypatch.setattr("kazma_core.paths.vault_db_path", lambda: str(tmp_path / "vault.db"))
    vault_mod.reset_posture_cache()
    yield v
    v.close()
    vault_mod.reset_posture_cache()


@pytest.fixture
def install(vault, monkeypatch):
    """An install where every precondition holds, Spaces not yet on."""
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    store.delete(identity.ENFORCED_KEY)
    monkeypatch.setattr(preconditions, "TENANT_FALLBACKS_LEFT", 0)
    monkeypatch.setattr(preconditions, "_module_answer", lambda module: (True, "every person has one"))
    preconditions._make_owner_token()
    assert preconditions.missing(for_switch=True) == []
    try:
        yield vault
    finally:
        scope.set_enforced(False)
        preconditions.owner_token_path().unlink(missing_ok=True)
        for key in (identity.REGISTRY_KEY, identity.ENFORCED_KEY, "account.tokens",
                    "backups.offsite.rclone_remote", preconditions._JOURNAL_KEY):
            store.delete(key)


@pytest.fixture
def on(install):
    """The same install with Spaces switched on, the product's way."""
    preconditions.switch_on(by="test")
    assert scope.spaces_enforced()
    return install


def _old_fallback() -> str:
    """``safety.hitl.get_current_tenant_id`` as it was before S1-33."""
    return tenant_context.get_current_tenant_id() or "default"


def _plant_two_rows(vault: SecretVault, name: str = GITHUB) -> None:
    """The live shape: a global row and an older ``default`` one that
    disagree (written before Spaces is on, as live's were)."""
    vault.store(name, "the-july-token", tenant_id="default")
    vault.store(name, "the-september-token")
    vault._conn.execute(
        "UPDATE secrets SET updated_at = '2026-07-28T00:00:00+00:00' WHERE tenant_id = 'default'")
    vault._conn.execute(
        "UPDATE secrets SET updated_at = '2026-09-12T00:00:00+00:00' WHERE tenant_id IS NULL")
    vault._conn.commit()


def _values(vault: SecretVault, name: str) -> dict[str | None, str]:
    rows = vault._conn.execute(
        "SELECT name, tenant_id, key_id, encrypted_value, nonce FROM secrets WHERE name = ?", (name,)).fetchall()
    return {r["tenant_id"]: vault._open_row(r) for r in rows}


# ── Breaking one precondition at a time ─────────────────────────────────


def _break_probe(vault, monkeypatch) -> None:
    monkeypatch.setattr("kazma_core.safety.hitl.get_current_tenant_id", _old_fallback)


def _break_fallbacks(vault, monkeypatch) -> None:
    monkeypatch.setattr(preconditions, "TENANT_FALLBACKS_LEFT", 3)


def _break_copies(vault, monkeypatch) -> None:
    was = scope.spaces_enforced()
    scope.set_enforced(False)
    try:
        _plant_two_rows(vault)
    finally:
        scope.set_enforced(was)


def _break_limits(vault, monkeypatch) -> None:
    monkeypatch.setattr(preconditions, "_module_answer",
                        lambda module: None if module.endswith("daily_limits") else (True, ""))


def _break_grants(vault, monkeypatch) -> None:
    monkeypatch.setattr(preconditions, "_module_answer",
                        lambda module: None if module.endswith("features") else (True, ""))


def _break_copy(vault, monkeypatch) -> None:
    get_config_store().set("backups.offsite.rclone_remote", "gdrive:kazma")


def _break_token_file(vault, monkeypatch) -> None:
    preconditions.owner_token_path().unlink()


def _break_token_revoked(vault, monkeypatch) -> None:
    get_config_store().set("account.tokens", [], category="account")


def _break_token_expired(vault, monkeypatch) -> None:
    store = get_config_store()
    tokens = store.get("account.tokens")
    tokens[0]["created_at"] = "2020-01-01T00:00:00+00:00"
    store.set("account.tokens", tokens, category="account")


def _break_space_key(vault, monkeypatch) -> None:
    def _change(reg: dict[str, Any]) -> None:
        reg["spaces"]["workshop"] = {"id": "workshop", "name": "Workshop", "kind": "sandboxed",
                                     "created_by": "owner", "created_at": "2026-11-21T00:00:00+00:00"}
        reg["memberships"].append({"account": "owner", "space": "workshop", "role": "owner"})

    identity.update_registry(_change)
    vault._conn.execute("DELETE FROM space_keys WHERE space_id = 'workshop'")
    vault._conn.commit()
    vault._space_keys.clear()


BREAKS = {
    "unbound_work (a function answers the owner's space)": ("unbound_work", _break_probe),
    "unbound_work (fallbacks left in the code)": ("unbound_work", _break_fallbacks),
    "vault_copies": ("vault_copies", _break_copies),
    "daily_limits": ("daily_limits", _break_limits),
    "feature_grants": ("feature_grants", _break_grants),
    "encrypted_copy": ("encrypted_copy", _break_copy),
    "owner_token (no file)": ("owner_token", _break_token_file),
    "owner_token (revoked)": ("owner_token", _break_token_revoked),
    "owner_token (expired)": ("owner_token", _break_token_expired),
    "space_keys": ("space_keys", _break_space_key),
}


def test_every_precondition_for_an_account_has_a_break() -> None:
    asked = {pid for pid, (_t, _c, always) in preconditions.PRECONDITIONS.items() if always}
    assert asked == {pid for pid, _break in BREAKS.values()}


def _accounts() -> set[str]:
    return set(identity.read_registry()["accounts"])


# ── The account ─────────────────────────────────────────────────────────


def test_with_everything_in_place_a_second_account_is_made(on) -> None:
    person = people.create_person("Layla", "a-long-password", by="owner")
    assert person["id"] in _accounts()
    again = people.create_person("Noor", "a-long-password", by="owner")
    assert {person["id"], again["id"]} <= _accounts()
    assert preconditions.missing() == []  # each new space got its key


@pytest.mark.parametrize("label", list(BREAKS))
def test_one_missing_precondition_refuses_the_second_account_and_is_named(on, monkeypatch, label) -> None:
    pid, break_it = BREAKS[label]
    break_it(on, monkeypatch)
    before = _accounts()
    with pytest.raises(people.PeopleRefused) as refused:
        people.create_person("Layla", "a-long-password", by="owner")
    assert refused.value.code == "preconditions_missing" and refused.value.status == 409
    assert [m["id"] for m in refused.value.missing] == [pid]
    title = preconditions.PRECONDITIONS[pid][0]
    assert title in str(refused.value)
    assert refused.value.missing[0]["detail"] and refused.value.missing[0]["fix"]
    assert _accounts() == before


def test_the_refusal_names_every_missing_one(on, monkeypatch) -> None:
    _break_limits(on, monkeypatch)
    _break_copy(on, monkeypatch)
    _break_token_file(on, monkeypatch)
    with pytest.raises(people.PeopleRefused) as refused:
        people.create_person("Layla", "a-long-password", by="owner")
    assert [m["id"] for m in refused.value.missing] == ["daily_limits", "encrypted_copy", "owner_token"]


def test_the_page_is_told_which(on, monkeypatch) -> None:
    from kazma_ui import people_api

    _break_copy(on, monkeypatch)
    with pytest.raises(people.PeopleRefused) as refused:
        people.create_person("Layla", "a-long-password", by="owner")
    answer = people_api._refused(refused.value)
    body = json.loads(answer.body)
    assert answer.status_code == 409 and body["error"] == "preconditions_missing"
    assert [m["id"] for m in body["missing"]] == ["encrypted_copy"]


def test_a_check_that_cannot_be_made_is_missing(on, monkeypatch) -> None:
    def _fails() -> bool:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("kazma_core.backup.universal.plain_copy_configured", _fails)
    left = preconditions.missing()
    assert [f.id for f in left] == ["encrypted_copy"]
    assert "could not be checked" in left[0].detail and "locked" not in left[0].detail


def test_control_with_no_check_the_second_account_is_made_with_everything_missing(vault, monkeypatch) -> None:
    """The code of before S1-33: the switch stored by hand, an account made
    with no question asked."""
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    assert len(preconditions.missing(for_switch=True)) >= 4
    monkeypatch.setattr(people, "_require_preconditions", lambda: None)
    store.set(identity.ENFORCED_KEY, True, category="identity")  # the switch with no check
    identity.prime_enforcement()
    try:
        person = people.create_person("Layla", "a-long-password", by="owner")
        assert person["id"] in _accounts()
        assert len(preconditions.missing()) >= 4
    finally:
        scope.set_enforced(False)
        store.delete(identity.ENFORCED_KEY)
        store.delete(identity.REGISTRY_KEY)


# ── The switch ──────────────────────────────────────────────────────────


def test_the_switch_turns_spaces_on_when_everything_holds(install) -> None:
    assert not scope.spaces_enforced()
    preconditions.switch_on(by="test")
    assert get_config_store().get(identity.ENFORCED_KEY) is True
    assert scope.spaces_enforced()


@pytest.mark.parametrize("label", list(BREAKS))
def test_one_missing_precondition_refuses_the_switch_and_is_named(install, monkeypatch, label) -> None:
    pid, break_it = BREAKS[label]
    break_it(install, monkeypatch)
    with pytest.raises(preconditions.PreconditionsMissing) as refused:
        preconditions.switch_on(by="test")
    assert [f.id for f in refused.value.findings] == [pid]
    assert preconditions.PRECONDITIONS[pid][0] in str(refused.value)
    assert not get_config_store().get(identity.ENFORCED_KEY, False)
    assert not scope.spaces_enforced()


def test_the_older_modes_accounts_refuse_the_switch(install) -> None:
    def _change(reg: dict[str, Any]) -> None:
        reg["accounts"]["sara"] = {
            "id": "sara", "name": "sara", "role": "member", "enabled": True, "language": "",
            "sign_in": {"kind": "local", "password_hash": "x"}, "legacy_role": "operator",
            "legacy_user_id": "sara", "meta": {}, "created_at": "2026-01-01T00:00:00+00:00"}

    identity.update_registry(_change)
    with pytest.raises(preconditions.PreconditionsMissing) as refused:
        preconditions.switch_on(by="test")
    assert [f.id for f in refused.value.findings] == ["earlier_mode"]
    assert not scope.spaces_enforced()


def test_only_the_switch_stores_the_switch() -> None:
    """No product code writes the Spaces switch but ``switch_on``."""
    offenders = _switch_writers(_product_sources())
    assert offenders == [], offenders


def test_control_a_planted_switch_writer_fails_the_gate() -> None:
    planted = {"kazma_core/elsewhere.py":
               "from kazma_core.spaces import identity\n\n"
               "def turn_on(store):\n    store.set(identity.ENFORCED_KEY, True)\n"}
    assert _switch_writers(planted) == ["kazma_core/elsewhere.py:turn_on"]
    literal = {"kazma_core/elsewhere.py": "def f(store):\n    store.batch_set({'identity.enforced': True})\n"}
    assert _switch_writers(literal) == ["kazma_core/elsewhere.py:f"]


# ── Work with no space ──────────────────────────────────────────────────


def test_every_probe_refuses_on_an_enforcing_install() -> None:
    for what, probe in preconditions.PROBES:
        assert preconditions._answers_with_nothing_bound(probe) is None, what


def test_the_probe_ignores_what_the_caller_has_bound() -> None:
    with bind_scope("default", "owner"):
        for what, probe in preconditions.PROBES:
            assert preconditions._answers_with_nothing_bound(probe) is None, what
        assert scope.require_space_id() == "default"


def test_the_tools_tenant_is_refused_with_nothing_bound(monkeypatch) -> None:
    from kazma_core.safety import hitl

    assert hitl.get_current_tenant_id() == "default"  # one operator: as before
    with bind_scope("space-a", "someone"):
        assert hitl.get_current_tenant_id() == "space-a"
    monkeypatch.setattr(scope, "_enforced", True)
    with pytest.raises(NoSpaceBound):
        hitl.get_current_tenant_id()
    with bind_scope("space-a", "someone"):
        assert hitl.get_current_tenant_id() == "space-a"


def test_control_the_old_fallback_answers_the_owners_space(monkeypatch) -> None:
    monkeypatch.setattr("kazma_core.safety.hitl.get_current_tenant_id", _old_fallback)
    found = preconditions._find("unbound_work")
    assert not found.ok and "safety.hitl.get_current_tenant_id" in found.detail and "'default'" in found.detail


def test_as_if_enforced_reaches_no_other_thread_and_never_loosens(monkeypatch) -> None:
    seen: list[bool] = []
    with scope.as_if_enforced():
        assert scope.spaces_enforced()
        worker = threading.Thread(target=lambda: seen.append(scope.spaces_enforced()))
        worker.start()
        worker.join()
    assert seen == [False] and not scope.spaces_enforced()
    monkeypatch.setattr(scope, "_enforced", True)
    with scope.as_if_enforced():
        pass
    assert scope.spaces_enforced()


def test_the_count_of_fallbacks_is_the_ratchets() -> None:
    from tests.test_no_space_no_data import FALLBACK_BASELINE

    assert preconditions.TENANT_FALLBACKS_LEFT == FALLBACK_BASELINE


# ── One value per secret ────────────────────────────────────────────────


def test_the_owners_two_rows_disagree_and_no_value_is_shown(vault) -> None:
    _plant_two_rows(vault)
    found = vault.copies_that_disagree()
    assert [(e["name"], e["whose"]) for e in found] == [(GITHUB, "default")]
    assert found[0]["wins"] == {"tenant_id": None, "updated_at": "2026-09-12T00:00:00+00:00"}
    assert "token" not in json.dumps(found).replace("oauth_token", "")


def test_the_same_name_in_two_spaces_is_two_secrets(vault, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)
    with bind_scope("space-a", "someone"):
        vault.store("bank", "pin-of-a")
    with bind_scope("space-b", "someone"):
        vault.store("bank", "pin-of-b")
    assert vault.copies_that_disagree() == []
    assert vault.settle_copies() == []


def test_copies_that_agree_are_left_alone(vault) -> None:
    vault.store(GITHUB, "one-token", tenant_id="default")
    vault.store(GITHUB, "one-token")
    assert vault.copies_that_disagree() == []


def test_once_enforced_the_owners_two_rows_are_still_found(vault, monkeypatch) -> None:
    """``find_divergent_duplicates`` stops looking at a space's names once
    Spaces is enforced; the precondition must not."""
    _plant_two_rows(vault)
    monkeypatch.setattr(scope, "_enforced", True)
    assert vault.find_divergent_duplicates() == []
    assert [e["name"] for e in vault.copies_that_disagree()] == [GITHUB]


def test_settling_gives_every_copy_the_newest_value_once(vault) -> None:
    _plant_two_rows(vault)
    assert vault.settle_copies() == [GITHUB]
    assert _values(vault, GITHUB) == {None: "the-september-token", "default": "the-september-token"}
    assert vault.copies_that_disagree() == [] and vault.settle_copies() == []


def test_a_winning_copy_that_does_not_open_changes_nothing(vault) -> None:
    _plant_two_rows(vault)
    vault._conn.execute("UPDATE secrets SET nonce = zeroblob(12) WHERE tenant_id IS NULL")
    vault._conn.commit()
    assert vault.settle_copies() == []
    row = vault._conn.execute("SELECT * FROM secrets WHERE tenant_id = 'default'").fetchone()
    assert vault._open_row(row) == "the-july-token"
    assert vault.copies_that_disagree()[0]["unreadable"] == 1


def test_a_settle_that_fails_half_way_changes_nothing(vault, monkeypatch) -> None:
    _plant_two_rows(vault)
    _plant_two_rows(vault, "cfg:connectors.x.api_key")
    real, calls = SecretVault._reseal, []

    def second_fails(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(SecretVault, "_reseal", second_fails)
    with pytest.raises(sqlite3.OperationalError):
        vault.settle_copies()
    monkeypatch.setattr(SecretVault, "_reseal", real)
    assert len(vault.copies_that_disagree()) == 2


# ── The move's own steps ────────────────────────────────────────────────


def test_the_plan_says_what_would_change_and_changes_nothing(vault) -> None:
    from kazma_core.diagnostic_scope import read_only_diagnostic

    get_config_store().delete("account.tokens")
    _plant_two_rows(vault)
    with read_only_diagnostic("test"):
        steps = {s["step"]: s["changes"] for s in preconditions.plan()}
    assert len(steps["vault_copies"]) == 1 and GITHUB in steps["vault_copies"][0]
    assert "2026-09-12" in steps["vault_copies"][0] and "token" not in steps["vault_copies"][0].replace(GITHUB, "")
    assert steps["space_keys"] == []
    assert len(steps["owner_token"]) == 1
    assert _values(vault, GITHUB)["default"] == "the-july-token"
    assert not preconditions.owner_token_path().exists()


def test_the_move_settles_copies_makes_the_token_and_copies_the_vault_first(vault, tmp_path) -> None:
    store = get_config_store()
    store.delete("account.tokens")
    _plant_two_rows(vault)
    try:
        done = preconditions.apply()
        assert done["vault_copies"] == [GITHUB] and done["owner_token"] is True
        backup = Path(done["backup"])
        assert backup.is_file() and backup.parent == tmp_path and backup.name.startswith(".migrate-vault-backup-")
        before = SecretVault(db_path=str(backup))  # the copy is of before the change
        try:
            assert _values(before, GITHUB)["default"] == "the-july-token"
        finally:
            before.close()
        assert _values(vault, GITHUB)["default"] == "the-september-token"
        assert preconditions._owner_token_state() == "ok"
        journal = store.get(preconditions._JOURNAL_KEY)
        assert {k: v["state"] for k, v in journal.items()} == {"vault_copies": "done", "owner_token": "done"}
        again = preconditions.apply()
        assert again == {"backup": "", "vault_copies": [], "space_keys": {"keyed": [], "sealed": {}},
                         "owner_token": False}
        assert len(store.get("account.tokens")) == 1
    finally:
        preconditions.owner_token_path().unlink(missing_ok=True)
        store.delete("account.tokens")
        store.delete(preconditions._JOURNAL_KEY)


def test_the_move_keys_a_space_and_seals_what_it_wrote_before(on, monkeypatch) -> None:
    _break_space_key(on, monkeypatch)
    scope.set_enforced(False)
    with bind_scope("workshop", "owner"):
        on.store("space:workshop:email.gmail.refresh_token", "written-before-the-key")
    scope.set_enforced(True)
    found = preconditions._find("space_keys")
    assert not found.ok and "workshop" in found.detail
    done = preconditions.apply()
    assert done["space_keys"] == {"keyed": ["workshop"], "sealed": {"workshop": 1}}
    assert preconditions._find("space_keys").ok
    row = on._conn.execute("SELECT * FROM secrets WHERE name LIKE 'space:workshop:%'").fetchone()
    assert row["key_id"] and on._open_row(row) == "written-before-the-key"


def test_the_owner_token_file_is_one_of_kazmas_secret_files() -> None:
    from kazma_core.store_registry import is_secret_file_name

    assert is_secret_file_name(preconditions.OWNER_TOKEN_FILE)


# ── The command ─────────────────────────────────────────────────────────


def test_check_names_what_is_missing_and_writes_nothing(vault, capsys) -> None:
    from kazma_cli.spaces import run

    store = get_config_store()
    store.delete("account.tokens")
    _plant_two_rows(vault)
    assert run(["migrate", "--check"]) == 1
    said = capsys.readouterr().out
    for pid in ("unbound_work", "vault_copies", "daily_limits", "feature_grants", "owner_token"):
        title = preconditions.PRECONDITIONS[pid][0]
        assert f"[MISSING] {title}" in said, pid
    assert "[ok] Copies that leave the machine are encrypted" in said and GITHUB in said
    assert "the-july-token" not in said and "the-september-token" not in said
    assert run(["migrate", "--dry-run"]) == 0
    assert _values(vault, GITHUB)["default"] == "the-july-token"
    assert not preconditions.owner_token_path().exists() and not store.get("account.tokens")
    assert not store.get(identity.ENFORCED_KEY, False)


def test_check_passes_when_everything_holds(install, capsys) -> None:
    from kazma_cli.spaces import run

    assert run(["migrate", "--check"]) == 0
    assert "Everything holds" in capsys.readouterr().out


def test_the_switch_flag_is_refused_while_something_is_missing(install, monkeypatch, capsys) -> None:
    from kazma_cli.spaces import run

    _break_limits(install, monkeypatch)
    assert run(["migrate", "--yes", "--switch-on"]) == 1
    assert "Spaces was not switched on" in capsys.readouterr().out
    assert not scope.spaces_enforced() and not get_config_store().get(identity.ENFORCED_KEY, False)


def test_the_switch_flag_turns_spaces_on_when_everything_holds(install, capsys) -> None:
    from kazma_cli.spaces import run

    assert run(["migrate", "--yes"]) == 0
    assert not scope.spaces_enforced()  # the move alone never switches
    assert run(["migrate", "--yes", "--switch-on"]) == 0
    assert scope.spaces_enforced() and "Spaces is on" in capsys.readouterr().out


def test_the_move_asks_before_it_changes_anything(vault, monkeypatch, capsys) -> None:
    from kazma_cli.spaces import run

    get_config_store().delete("account.tokens")
    _plant_two_rows(vault)
    monkeypatch.setattr("builtins.input", lambda: "no")
    assert run(["migrate"]) == 1
    assert "Nothing was changed" in capsys.readouterr().out
    assert _values(vault, GITHUB)["default"] == "the-july-token"
    assert not preconditions.owner_token_path().exists()


# ── The deploy check's sign-in ──────────────────────────────────────────


class _Recorder(http.server.BaseHTTPRequestHandler):
    seen: list[dict[str, str]] = []
    refuse_tokens = False

    def do_GET(self) -> None:  # noqa: N802
        type(self).seen.append({"auth": self.headers.get("Authorization") or "",
                                "secret": self.headers.get("X-Kazma-Secret") or ""})
        refused = type(self).refuse_tokens and self.headers.get("Authorization")
        self.send_response(401 if refused else 200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture
def live_stub(tmp_path):
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import deploy_live
    finally:
        sys.path.remove(str(REPO / "scripts"))
    _Recorder.seen, _Recorder.refuse_tokens = [], False
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    live = tmp_path / "live"
    (live / ".kazma").mkdir(parents=True)
    (live / ".env").write_text("KAZMA_SECRET=the-shared-secret\n", encoding="utf-8")
    try:
        yield deploy_live, live, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_deploy_check_signs_in_with_the_owners_token(live_stub) -> None:
    deploy_live, live, base = live_stub
    (live / ".kazma" / "owner_api.token").write_text("kazma_demo-owner-token\n", encoding="utf-8")
    assert deploy_live.Server(live, base).json("GET", "/health/live")[0] == 200
    assert _Recorder.seen == [{"auth": "Bearer kazma_demo-owner-token", "secret": ""}]


def test_control_with_no_token_it_signs_in_with_the_shared_secret(live_stub) -> None:
    deploy_live, live, base = live_stub
    assert deploy_live.Server(live, base).json("GET", "/health/live")[0] == 200
    assert _Recorder.seen == [{"auth": "", "secret": "the-shared-secret"}]


def test_a_token_the_server_no_longer_takes_falls_back_to_the_secret(live_stub) -> None:
    deploy_live, live, base = live_stub
    (live / ".kazma" / "owner_api.token").write_text("kazma_demo-owner-token\n", encoding="utf-8")
    _Recorder.refuse_tokens = True
    server = deploy_live.Server(live, base)
    assert server.json("GET", "/health/live")[0] == 200
    assert server.json("GET", "/health/ready")[0] == 200
    assert [bool(s["secret"]) for s in _Recorder.seen] == [False, True, True]


# ── Source gates ────────────────────────────────────────────────────────


def _product_sources() -> dict[str, str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *PACKAGES],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return {name: (REPO / name).read_text(encoding="utf-8")
            for name in listed if name.endswith(".py") and "/tests/" not in name}


def _functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _names_the_switch(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "ENFORCED_KEY":
            return True
        if isinstance(sub, ast.Name) and sub.id == "ENFORCED_KEY":
            return True
        if isinstance(sub, ast.Constant) and sub.value == "identity.enforced":
            return True
    return False


_WRITES = {"set", "batch_set", "atomic_update"}


def _switch_writers(sources: dict[str, str]) -> list[str]:
    """Functions that write the Spaces switch to the settings store, other
    than ``preconditions.switch_on``."""
    found: list[str] = []
    for name, text in sorted(sources.items()):
        if "ENFORCED_KEY" not in text and "identity.enforced" not in text:
            continue
        for func in _functions(ast.parse(text)):
            for call in ast.walk(func):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr in _WRITES and _names_the_switch(call)):
                    where = f"{name}:{func.name}"
                    if not where.endswith("spaces/preconditions.py:switch_on") and where not in found:
                        found.append(where)
    return found


#: Functions that put an account into the identity store without asking the
#: preconditions, each with why that is right.
ACCOUNT_WRITERS = {
    "kazma-core/kazma_core/spaces/people.py:create_person":
        "asks them (_require_preconditions) before anything is made",
    "kazma-core/kazma_core/spaces/identity.py:_normalized":
        "the owner's own account, on every read: never a second one",
    "kazma-core/kazma_core/spaces/identity.py:read_registry":
        "shows the older multi-user mode's users as accounts on read; stores nothing",
    "kazma-core/kazma_core/spaces/identity.py:update_local_users":
        "the older multi-user mode's own user list, refused once Spaces is on "
        "(use_people_page); those accounts refuse the switch (earlier_mode)",
    "kazma-core/kazma_core/spaces/identity.py:move_platform_users":
        "moves the older mode's stored users into the store as they are; "
        "those accounts refuse the switch (earlier_mode)",
}


def _account_writers(sources: dict[str, str]) -> list[str]:
    """Functions that assign into ``[...]["accounts"][...]``."""
    found: list[str] = []
    for name, text in sorted(sources.items()):
        if '"accounts"' not in text and "'accounts'" not in text:
            continue
        for func in _functions(ast.parse(text)):
            for node in ast.walk(func):
                targets = node.targets if isinstance(node, ast.Assign) else []
                for target in targets:
                    inner = target.value if isinstance(target, ast.Subscript) else None
                    if (isinstance(inner, ast.Subscript) and isinstance(inner.slice, ast.Constant)
                            and inner.slice.value == "accounts"):
                        where = f"{name}:{func.name}"
                        if where not in found:
                            found.append(where)
    return found


def _calls(func: ast.AST, wanted: str) -> bool:
    return any(isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == wanted
               for n in ast.walk(func))


def test_every_writer_of_an_account_asks_the_preconditions_or_says_why_not() -> None:
    sources = _product_sources()
    writers = _account_writers(sources)
    nested = {w for w in writers if w.endswith(":_change") or w.endswith(":_updater")}
    named = [w for w in writers if w not in nested]
    assert set(named) <= set(ACCOUNT_WRITERS), sorted(set(named) - set(ACCOUNT_WRITERS))
    # A nested change function belongs to a declared outer function of its file.
    declared_files = {w.split(":")[0] for w in ACCOUNT_WRITERS}
    assert {w.split(":")[0] for w in nested} <= declared_files, nested
    stale = [w for w in ACCOUNT_WRITERS if w not in writers
             and not any(n.split(":")[0] == w.split(":")[0] for n in nested)]
    assert stale == [], stale
    people_tree = ast.parse(sources["kazma-core/kazma_core/spaces/people.py"])
    create = next(f for f in _functions(people_tree) if f.name == "create_person")
    assert _calls(create, "_require_preconditions")


def test_control_a_planted_account_writer_fails_the_gate() -> None:
    planted = {"kazma-core/kazma_core/elsewhere.py":
               "def invite(reg, account):\n    reg['accounts'][account['id']] = account\n"}
    assert _account_writers(planted) == ["kazma-core/kazma_core/elsewhere.py:invite"]
    assert not set(_account_writers(planted)) <= set(ACCOUNT_WRITERS)


# ── A per-person rule is met by its own check, never by a file ──────────


def _planted_module(name: str, **attrs):
    import importlib.machinery
    import types

    module = types.ModuleType(name)
    module.__spec__ = importlib.machinery.ModuleSpec(name, None)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


@pytest.mark.parametrize("attrs, holds", [
    ({}, False),                                                   # names no check
    ({"precondition": lambda: (False, "two people have none")}, False),
    ({"precondition": lambda: (True, "every person has one")}, True),
])
def test_a_built_rule_holds_only_by_its_own_check(attrs, holds) -> None:
    from tests._module_stubs import stub_modules

    name = "kazma_core.spaces.daily_limits"
    with stub_modules({name: _planted_module(name, **attrs)}):
        ok, detail, _fix = preconditions._built("daily_limits", "a daily spending limit per person")
    assert ok is holds, detail


def test_control_the_check_of_before_passed_on_a_module_alone() -> None:
    import importlib.util

    from tests._module_stubs import stub_modules

    name = "kazma_core.spaces.daily_limits"
    with stub_modules({name: _planted_module(name)}):
        assert importlib.util.find_spec(name) is not None  # what the check of before asked
