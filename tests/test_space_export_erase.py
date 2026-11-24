"""Spaces S1-27: count, export and erase one space, from the registry.

Every ``row`` store of ``store_registry.SPACE_RULES`` is made by its own code
in this run's data folder. A word is planted for space A, and another for
space B, into every content column the registry names, of every table, with
the space found the way the store finds it (a space column, a thread of the
space, a field of a JSON column, a row it hangs from); and into the space's
settings, secrets and files. Then:

* A's count says what A holds; A's export holds A's word and never B's;
* after A is erased, no column of any table of any database, and no file,
  holds A's word -- and every place B's word was is still there.

Controls: yesterday's erase -- one store left out -- leaves A's word behind;
a table with no rule refuses the count; the admin cannot export a member's
space, and erases a person's space only once the person is deleted.
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from kazma_core import paths
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope, space_data
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
from kazma_core.store_registry import SPACE_RULES

from tests._space_world import A, B, _where_word, plant_world


@pytest.fixture
def world(monkeypatch, tmp_path):
    yield from plant_world(monkeypatch, tmp_path)


def test_every_row_store_was_made_and_planted(world) -> None:
    content_stores = {name for name, rule in SPACE_RULES.items()
                      if rule.kind == "row" and rule.content and "*" not in name}
    made = set(world["made"]) | {"settings.db", "vault.db"}
    missing = sorted(content_stores - made - {"chat_sessions_spool.db", "chat_sessions_test.db",
                                               "documents-jobs-fallback.db", "x_posts.db", "x_audit.db",
                                               "x_datasets.db", "chat_inbox.db", "chat_outbox.db"})
    assert missing == [], missing
    planted_stores = {item.split(":")[0] for item in world["planted"][A]}
    assert len(planted_stores) >= 20, sorted(planted_stores)


def test_a_count_says_what_a_holds(world) -> None:
    counts = space_data.count_space(A)
    assert counts["rows"] >= len(world["planted"][A]), counts
    assert counts["settings"] == 2 and counts["secrets"] == 1 and counts["files"] == 1, counts


def test_a_table_a_feature_makes_on_first_use_is_counted(world, monkeypatch) -> None:
    """The approval bus makes ``hitl_watchers`` the first time a process
    watches for approvals, never with the gate schema: live's hitl_gates.db
    holds it, and S1-27's walk refused every count, export and erase there
    (``unknown_table``) until it had a rule. The world makes it by the bus's
    own code; the rules without its entry are the control."""
    with closing(sqlite3.connect(str(paths.data_dir() / "hitl_gates.db"))) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "hitl_watchers" in tables
    assert space_data.count_space(A)["rows"] >= len(world["planted"][A])
    without = {key: rule for key, rule in space_data.TABLE_RULES.items() if key != ("hitl_gates.db", "hitl_watchers")}
    monkeypatch.setattr(space_data, "TABLE_RULES", without)
    with pytest.raises(space_data.SpaceDataRefused) as refused:
        space_data.count_space(A)
    assert refused.value.code == "unknown_table" and "hitl_watchers" in str(refused.value)


def test_an_export_holds_its_own_space_and_never_the_other(world, tmp_path) -> None:
    target = tmp_path / "a.zip"
    space_data.export_space(A, target)
    with zipfile.ZipFile(target) as zipped:
        text = "\n".join(zipped.read(name).decode("utf-8", "replace") for name in zipped.namelist())
    assert world["words"][A] in text
    assert world["words"][B] not in text
    import fnmatch

    held = {item.split(":")[0] for item in world["planted"][A]}
    exported = {name.split("/")[1] for name in zipfile.ZipFile(target).namelist() if name.startswith("stores/")}
    # A store family is exported under its file's name.
    missing = {name for name in held - exported - {"thread_scopes.db"}
               if not any(fnmatch.fnmatch(found, name) for found in exported)}
    assert not missing, sorted(missing)


def test_after_erasing_a_no_store_holds_its_word_and_b_is_whole(world) -> None:
    before_a = _where_word(world["words"][A])
    before_b = _where_word(world["words"][B])
    assert len(before_a) >= 20, before_a
    erased = space_data.erase_space(A, by=OWNER_ACCOUNT_ID)
    assert erased["rows"] >= len(world["planted"][A]), erased
    assert _where_word(world["words"][A]) == {}
    assert _where_word(world["words"][B]) == before_b
    assert A not in identity.read_registry()["spaces"]
    assert identity.read_registry()["events"][-1]["kind"] == "space_erased"


def _documents_db() -> Path:
    from kazma_core.documents.config import get_document_storage_root

    return Path(get_document_storage_root()) / "documents.db"


def _guards() -> set[str]:
    with closing(sqlite3.connect(str(_documents_db()))) as conn:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'document_job_events'")}


def test_an_append_only_table_is_erased_and_keeps_its_guard(world) -> None:
    """document_job_events refuses every delete (its no-delete trigger), and
    an install whose document job queue is SQLite keeps it in documents.db:
    the erase failed there with "document job events are immutable" (CI,
    2026-11-20, when an earlier test had made the queue's tables). The erase
    lifts the guard inside its own transaction and puts it back."""
    assert any(item.startswith("documents.db:document_job_events") for item in world["planted"][A]), \
        sorted(item for item in world["planted"][A] if item.startswith("documents.db"))
    before = _guards()
    assert "document_job_events_no_delete" in before
    space_data.erase_space(A, by=OWNER_ACCOUNT_ID)
    assert _guards() == before
    with closing(sqlite3.connect(str(_documents_db()))) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM document_job_events")


def test_control_without_lifting_the_guard_the_erase_fails(world, monkeypatch) -> None:
    monkeypatch.setattr(space_data, "_lift_delete_guards", lambda conn, tables: [])
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        space_data.erase_space(A, by=OWNER_ACCOUNT_ID)
    # Nothing of that file went: one file's rows go together or not at all.
    assert any(key.startswith("documents.db") for key in _where_word(world["words"][A]))


def test_control_yesterdays_erase_misses_a_store(world, monkeypatch) -> None:
    real = space_data.store_paths
    monkeypatch.setattr(space_data, "store_paths",
                        lambda: [(name, path) for name, path in real() if name != "memory_state.db"])
    space_data.erase_space(A, by=OWNER_ACCOUNT_ID)
    left = _where_word(world["words"][A])
    assert left and all(key.startswith(Path(paths.primary_memory_db()).name) for key in left), left


def test_control_a_table_with_no_rule_refuses_the_count(world) -> None:
    with closing(sqlite3.connect(str(paths.data_dir() / "agent_artifacts.db"))) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS planted_unknown (note TEXT)")
        conn.commit()
    try:
        with pytest.raises(space_data.SpaceDataRefused) as refused:
            space_data.count_space(A)
        assert refused.value.code == "unknown_table"
    finally:
        with closing(sqlite3.connect(str(paths.data_dir() / "agent_artifacts.db"))) as conn:
            conn.execute("DROP TABLE planted_unknown")
            conn.commit()


def test_a_postgres_copy_of_memory_refuses_until_it_is_walked(world, monkeypatch) -> None:
    from kazma_core.memory import state_backend

    monkeypatch.setattr(state_backend, "mirrors_to_postgres", lambda cfg=None: True)
    for call in (lambda: space_data.count_space(A), lambda: space_data.erase_space(A, by=OWNER_ACCOUNT_ID)):
        with pytest.raises(space_data.SpaceDataRefused) as refused:
            call()
        assert refused.value.code == "postgres_not_yet"
    assert A in identity.read_registry()["spaces"]


def test_the_owners_space_is_never_erased(world) -> None:
    with pytest.raises(space_data.SpaceDataRefused) as refused:
        space_data.erase_space(scope.OWNER_SPACE_ID, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "owners_space"


# ── who may ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from kazma_ui.app import create_app

    with TestClient(create_app(), client=("127.0.0.1", 40000), base_url="http://127.0.0.1:9090") as client:
        yield client


def _as(monkeypatch, principal: dict[str, Any]) -> None:
    from kazma_ui import auth

    monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)
    monkeypatch.setattr(auth, "admin_decision",
                        lambda request: "ok" if principal.get("role") == "admin" else "forbidden")


def test_the_admin_counts_but_never_exports_a_members_space(client, monkeypatch) -> None:
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    try:
        person = people.create_person("erase-person", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        space = person["personal_space"]
        _as(monkeypatch, {"source": "secret", "role": "admin", "user_id": "", "actor": "secret"})
        counted = client.get(f"/api/spaces/{space}/counts")
        assert counted.status_code == 200, counted.text
        sealed_with = {"password": "a-long-password-1"}
        assert client.post(f"/api/spaces/{space}/export", json=sealed_with).status_code == 403
        assert client.delete(f"/api/spaces/{space}").status_code == 403
        _as(monkeypatch, {"source": "session", "role": "operator", "user_id": person["id"], "actor": "login"})
        # Sealed with a password the person chooses (N10): none, or a short one, is refused.
        short = client.post(f"/api/spaces/{space}/export", json={"password": "short"})
        assert short.status_code == 400 and short.json()["error"] == "password_too_short"
        assert client.post(f"/api/spaces/{space}/export", json={}).status_code == 400
        exported = client.post(f"/api/spaces/{space}/export", json=sealed_with)
        assert exported.status_code == 200 and exported.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(exported.content)) as zipped:
            assert "manifest.json" in zipped.namelist()
            assert {info.compress_type for info in zipped.infolist()} == {99}  # every entry sealed
        refused = client.delete(f"/api/spaces/{space}")
        assert refused.status_code == 403
        # Once the person is deleted, the admin erases their closed space.
        people.delete_person(person["id"], by=OWNER_ACCOUNT_ID)
        _as(monkeypatch, {"source": "secret", "role": "admin", "user_id": "", "actor": "secret"})
        assert client.delete(f"/api/spaces/{space}").status_code == 200
        assert space not in identity.read_registry()["spaces"]
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


def test_control_without_spaces_nothing_is_counted(client, monkeypatch) -> None:
    _as(monkeypatch, {"source": "secret", "role": "admin", "user_id": "", "actor": "secret"})
    answer = client.get(f"/api/spaces/{A}/counts")
    assert answer.status_code == 409 and answer.json()["error"] == "spaces_off"
