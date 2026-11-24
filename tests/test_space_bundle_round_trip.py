"""A space moves from one install to another (Spaces S4-06,
``kazma_core/spaces/space_move.py``).

Export, import into a fresh install, then the matrix:

* **the source** is S1-27's world: spaces A and B, each with a word planted in
  every table of every row store, its settings, a secret and a file. Space A
  is made into a bundle sealed with a password, and a second secret, whose
  value appears nowhere else, is stored first;
* **the target** is a fresh install of its own (its own data folder, settings
  store and vault, opened with another vault key) with one person, who will
  own the space there. The bundle is brought in by the admin;
* **then**: every table that moves holds the same rows of A (a table that
  never moves, :data:`space_move.MOVE_RULES` ``out``, holds none here), A's
  word is in the same tables and columns, B's word is nowhere; the settings,
  the secrets (opened with the target's own vault key: the space's key
  travelled inside the seal), the files and the chats' records are there,
  the chats now opened by the new owner. Every case of the space matrix is
  then run on an install whose space A was moved in
  (``test_the_matrix_holds_for_a_moved_space``, below).

The review's decisions and findings are held by
``tests/test_space_move_review.py``.

Refused before anything is written, each leaving the target as it was: a
bundle carrying another space's row (**the control**), a space already here,
an id already here (never merged into another space), an install that does
not enforce Spaces, a wrong password, a changed byte, a personal space, an
import by anyone but the admin. A half-done import is taken back, and one
whose take-back did not run (a stop, a locked database) is taken back by the
next (``settle_unfinished_imports``); the take-back removed is the control.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from kazma_core import paths
from kazma_core.config_store import get_config_store
from kazma_core.security.sealed_file import SealedReader, SealedWriter
from kazma_core.spaces import identity, people, scope, space_data, space_move, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._space_world import A, B, _where_word, own_install, plant_world

PASSWORD = "a-long-move-password-1"
SOURCE_KEY = "s" * 64
TARGET_KEY = "t" * 64


# ── the source, made once ───────────────────────────────────────────────


@pytest.fixture(scope="module")
def moved(tmp_path_factory) -> dict[str, Any]:
    """Space A of S1-27's world, as a bundle, and what the source said of it."""
    folder = tmp_path_factory.mktemp("move-source")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        world_gen = plant_world(patch, folder)
        world = next(world_gen)
        try:
            from kazma_core.security.vault import get_vault

            only_here = f"secretonly{uuid4().hex[:10]}"
            get_vault().for_space(A).store(f"space:{A}:only", only_here)
            counts = space_data.count_space(A)
            where = _where_word(world["words"][A])
            ctx, _ = space_data._walk(A)
            bundle = folder / "a.zip"
            # The key that travels; the old install has another one after.
            key = get_vault().export_space_sealed(A)[0][0]["key"]
            manifest = space_move.export_bundle(A, bundle, PASSWORD, by=OWNER_ACCOUNT_ID)
        finally:
            world_gen.close()
    return {"bundle": bundle, "manifest": manifest, "words": world["words"], "counts": counts,
            "where": where, "threads": list(ctx.ids["threads"]), "only_here": only_here, "key": key,
            "folder": folder}


# ── the target, fresh for each test ─────────────────────────────────────


@pytest.fixture
def target(tmp_path, monkeypatch) -> Any:
    """A fresh install: its own vault key, Spaces on, one person."""
    with own_install(monkeypatch, tmp_path / "target", vault_key=TARGET_KEY) as data:
        person = people.create_person(f"mover-{uuid4().hex[:4]}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        yield {"data": data, "person": person["id"]}


def _bring_in(bundle: Path, owner: str, *, password: str = PASSWORD, by: str = OWNER_ACCOUNT_ID) -> dict[str, Any]:
    return space_move.import_bundle(bundle, password, owner=owner, by=by)


def _normalized(where: dict[str, int]) -> dict[str, int]:
    """The vault file has a name of its own on each install."""
    out: dict[str, int] = {}
    for key, n in where.items():
        if key.startswith("vault-"):
            key = "vault.db" + key[key.index(":"):]
        out[key] = out.get(key, 0) + n
    return out


def _nothing_of(space: str) -> None:
    """The target holds nothing of *space*: no row, setting, secret, folder,
    identity entry or journal entry."""
    _ctx, found = space_data._walk(space)
    assert sum(item.rows for item in found) == 0, [(i.store, i.table) for i in found if i.rows]
    assert not any(key.startswith(f"tenant.{space}.")
                   for group in get_config_store().get_all().values() for key in group)
    assert not space_data._vault_rows(space) and not space_data._vault_keys(space)
    assert not any(p.exists() for p in space_move._space_folders(space).values())
    assert space not in identity.read_registry()["spaces"]
    assert not (get_config_store().get(space_move.JOURNAL_KEY) or {})


# ── the move ────────────────────────────────────────────────────────────


#: The columns live work's stop rewrites on arrival (space_move._STOPS).
_STOPPED_COLUMNS = {
    ("cron.db", "cron_jobs"): {"last_result", "pending_edit", "delivery_target", "delivery_error", "fire_due",
                               "fire_owner"},
    ("research_sessions.db", "research_sessions"): {"message"},
    ("x_publications.db", "x_operations"): {"reason"},
    ("x_publications.db", "x_threads"): {"token", "owner"},
}


def _moved_where(where: dict[str, int], moved_tables: dict[str, int]) -> dict[str, int]:
    """*where* (``file:table:column``) as a move leaves it: only the tables
    the bundle carried (a table that never moves, or whose rows were all
    another person's, left out), not the columns a stop rewrites, not the
    settings (checked one by one); the checkpoints under one name, the vault
    file under one."""
    out: dict[str, int] = {}
    for key, n in _normalized(where).items():
        if key.startswith("file:"):
            out[key] = out.get(key, 0) + n
            continue
        name, table, column = key.split(":", 2)
        if name == "settings.db":
            continue
        store = space_move.CHECKPOINTS if name.startswith("checkpoints") else name
        if name != "vault.db" and (f"{store}:{table}" not in moved_tables
                                   or column in _STOPPED_COLUMNS.get((store, table), ())):
            continue
        key = f"{store}:{table}:{column}"
        out[key] = out.get(key, 0) + n
    return out


def test_a_space_moves_whole(moved, target) -> None:
    taken = _bring_in(moved["bundle"], target["person"])
    assert taken["space"] == A and taken["secrets"] == 2 and taken["files"] == 1
    # Every table that moves holds the same rows of A; one that never moves
    # holds none.
    _ctx, found = space_data._walk(A)
    here: dict[str, int] = {}
    for item in found:
        store = space_move._logical_store(item.store, item.path)
        if item.rows and store is not None:
            here[f"{store}:{item.table}"] = here.get(f"{store}:{item.table}", 0) + item.rows
    # The keyword index is not carried: Knowledge's code writes it here, a
    # row for each moved chunk.
    assert here.pop("knowledge.db:knowledge_chunks_fts") == moved["manifest"]["tables"]["knowledge.db:knowledge_chunks"]
    assert here == moved["manifest"]["tables"], (here, moved["manifest"]["tables"])
    assert all(space_move.MOVE_RULES[tuple(key.split(":", 1))].how != "out" for key in here)
    # A's word is where it was, in what moves; B's is nowhere.
    carried = moved["manifest"]["tables"]
    assert _moved_where(_where_word(moved["words"][A]), carried) == _moved_where(moved["where"], carried)
    assert _where_word(moved["words"][B]) == {}
    # Counted the same, but for what never moves.
    counts = space_data.count_space(A)
    for key in ("secrets", "files", "libraries", "chats"):
        assert counts[key] == moved["counts"][key], (key, counts[key], moved["counts"][key])
    # The secrets open with this install's own vault key.
    from kazma_core.security.vault import get_vault

    assert get_vault().for_space(A).retrieve(f"space:{A}:only") == moved["only_here"]
    assert get_vault().for_space(A).retrieve(f"space:{A}:{moved['words'][A]}") == moved["words"][A]
    # The settings, the files.
    assert get_config_store().get(f"tenant.{A}.connectors.x.handle") == moved["words"][A]
    note = space_move._space_folders(A)["space"] / "files" / "note.txt"
    assert note.read_text(encoding="utf-8") == moved["words"][A]
    # The space is a shared space here, owned by the person chosen; its chats
    # are now opened by them (the exporter's account is theirs here).
    reg = identity.read_registry()
    assert reg["spaces"][A]["personal_of"] is None and reg["spaces"][A]["kind"] == "sandboxed"
    assert {"account": target["person"], "space": A, "role": "owner"} in reg["memberships"]
    assert any(e.get("kind") == "space_imported" and e.get("space") == A for e in reg["events"])
    for thread in moved["threads"]:
        record = threads.record_of(thread)
        assert record is not None and record.space_id == A and record.opener == target["person"]
    assert identity.is_member(target["person"], A) and not identity.is_member(OWNER_ACCOUNT_ID, A)
    assert not (get_config_store().get(space_move.JOURNAL_KEY) or {})


def entries_of(bundle: Path, password: str = PASSWORD) -> dict[str, bytes]:
    """Every entry of *bundle*, opened with *password* (in memory)."""
    with SealedReader(bundle, password, purpose=space_move.FORMAT,
                      max_bytes=space_move.MAX_BUNDLE_BYTES) as reader, zipfile.ZipFile(reader) as zipped:
        return {name: zipped.read(name) for name in zipped.namelist()}


def sealed_again(entries: dict[str, bytes], target: Path, *, password: str = PASSWORD,
                 relist: bool = True) -> Path:
    """*entries* sealed as a bundle at *target*. *relist*: the manifest's
    list of entries made to match them (else as it is)."""
    entries = dict(entries)
    if relist:
        manifest = json.loads(entries["bundle.json"])
        manifest["entries"] = {name: len(data) for name, data in entries.items() if name != "bundle.json"}
        entries["bundle.json"] = json.dumps(manifest).encode("utf-8")
    writer = SealedWriter(target, password, purpose=space_move.FORMAT)
    with zipfile.ZipFile(writer, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, data in entries.items():
            zipped.writestr(name, data)
    writer.finish()
    return target


def test_the_key_travels_inside_the_seal_never_a_vault_key(moved, tmp_path) -> None:
    raw = moved["bundle"].read_bytes()
    entries = entries_of(moved["bundle"])
    keys = json.loads(entries["keys.json"])
    # Neither the space's key nor a secret's value is in the bundle in the
    # clear, nor an entry's name: the whole file is sealed.
    for needle in (moved["key"], base64.b64encode(moved["key"]), moved["only_here"].encode(),
                   b"secrets.jsonl", b"keys.json", b"bundle.json", b"stores/"):
        assert needle not in raw
    # Inside the seal, the key the vault held as the bundle was made (wrapped
    # with the other install's own vault key on import).
    assert [base64.b64decode(k["key"]) for k in keys] == [moved["key"]]
    from kazma_core.security.sealed_file import SealedFileRefused

    with pytest.raises(SealedFileRefused) as wrong:
        entries_of(moved["bundle"], "another-long-password")
    assert wrong.value.code == "wrong_password"


# ── refused, the target as it was ───────────────────────────────────────


def _rebuilt(source: Path, target: Path, change: Any) -> Path:
    """*source*'s bundle opened, changed by *change(entries)* and sealed again
    with the same password (its list of entries made to match)."""
    entries = entries_of(source)
    change(entries)
    return sealed_again(entries, target)


def _carrying_b(moved: dict[str, Any], tmp_path: Path) -> tuple[Path, str]:
    """A's bundle with one of B's memories added to it."""
    planted = f"ep-planted-{uuid4().hex[:6]}"

    def plant_b(entries: dict[str, bytes]) -> None:
        name = "stores/memory_state.db/episodes.jsonl"
        lines = entries[name].decode("utf-8").splitlines()
        row = json.loads(lines[0])
        row.update(tenant_id=B, id=planted)
        entries[name] = ("\n".join([*lines, json.dumps(row)]) + "\n").encode("utf-8")

    return _rebuilt(moved["bundle"], tmp_path / "foreign.zip", plant_b), planted


def test_control_a_bundle_carrying_another_spaces_row_is_refused(moved, target, tmp_path) -> None:
    bundle, _planted = _carrying_b(moved, tmp_path)
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(bundle, target["person"])
    assert refused.value.code == "foreign_row"
    _nothing_of(A)
    _nothing_of(B)


def test_control_without_the_row_check_the_other_spaces_row_lands(moved, target, tmp_path, monkeypatch) -> None:
    """The refusal above is the row check's: taken out, B's memory is written
    into the install, under B, by a move of A."""
    bundle, planted = _carrying_b(moved, tmp_path)
    monkeypatch.setattr(space_move, "_belongs", lambda *_a, **_k: True)
    _bring_in(bundle, target["person"])
    with closing(sqlite3.connect(str(paths.primary_memory_db()))) as conn:
        assert conn.execute("SELECT tenant_id FROM episodes WHERE id = ?", (planted,)).fetchone() == (B,)


def test_a_setting_pointing_at_another_secret_is_refused(moved, target, tmp_path) -> None:
    def point_out(entries: dict[str, bytes]) -> None:
        settings = json.loads(entries["settings.json"])
        settings[f"tenant.{A}.connectors.x.api_key"] = {"category": "x", "value": "vault://cfg:providers.openai.api_key"}
        entries["settings.json"] = json.dumps(settings).encode("utf-8")

    bundle = _rebuilt(moved["bundle"], tmp_path / "pointer.zip", point_out)
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(bundle, target["person"])
    assert refused.value.code == "foreign_secret"
    _nothing_of(A)


def test_a_space_here_already_is_refused(moved, target) -> None:
    _bring_in(moved["bundle"], target["person"])
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(moved["bundle"], target["person"])
    assert refused.value.code == "space_exists"
    assert identity.is_member(target["person"], A)  # the first stays whole


def test_an_id_here_already_is_refused_never_merged(moved, target) -> None:
    from kazma_core.spaces import row_stores

    row_stores.make_row_stores()
    job = json.loads(_bundle_line(moved, "cron.db", "cron_jobs"))["job_id"]
    with closing(sqlite3.connect(str(paths.data_dir() / "cron.db"))) as conn:
        columns = {r[1]: r for r in conn.execute('PRAGMA table_info("cron_jobs")')}
        row = {name: ("x" if "TEXT" in (info[2] or "").upper() or not info[2] else 1)
               for name, info in columns.items() if info[3] and info[4] is None}
        row.update(job_id=job, tenant_id="another-space")
        conn.execute(f'INSERT INTO cron_jobs ({", ".join(row)}) VALUES ({", ".join("?" * len(row))})',
                     tuple(row.values()))
        conn.commit()
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(moved["bundle"], target["person"])
    assert refused.value.code == "id_collides"
    _nothing_of(A)
    with closing(sqlite3.connect(str(paths.data_dir() / "cron.db"))) as conn:
        assert conn.execute("SELECT tenant_id FROM cron_jobs WHERE job_id = ?", (job,)).fetchone()[0] \
            == "another-space"


def _bundle_line(moved: dict[str, Any], store: str, table: str, tmp: Path | None = None) -> str:
    return entries_of(moved["bundle"])[f"stores/{store}/{table}.jsonl"].decode("utf-8").splitlines()[0]


def test_an_install_without_spaces_refuses(moved, target, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(moved["bundle"], target["person"])
    assert refused.value.code == "spaces_off"
    monkeypatch.setattr(scope, "_enforced", True)
    _nothing_of(A)


def test_a_wrong_password_and_a_changed_byte_are_refused(moved, target, tmp_path) -> None:
    with pytest.raises(space_move.SpaceMoveRefused) as wrong:
        _bring_in(moved["bundle"], target["person"], password="not-the-password-at-all")
    assert wrong.value.code == "wrong_password"
    changed = tmp_path / "changed.zip"
    data = bytearray(moved["bundle"].read_bytes())
    data[400] ^= 1
    changed.write_bytes(bytes(data))
    with pytest.raises(space_move.SpaceMoveRefused) as altered:
        _bring_in(changed, target["person"])
    assert altered.value.code in ("changed", "not_a_bundle")
    _nothing_of(A)


def test_only_the_admin_brings_a_space_in(moved, target) -> None:
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        _bring_in(moved["bundle"], target["person"], by=target["person"])
    assert refused.value.code == "admin_only"
    _nothing_of(A)


def test_a_personal_space_stays(tmp_path, monkeypatch) -> None:
    with own_install(monkeypatch, tmp_path / "personal"):
        person = people.create_person(f"stays-{uuid4().hex[:4]}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        with pytest.raises(space_move.SpaceMoveRefused) as refused:
            space_move.export_bundle(person["personal_space"], tmp_path / "p.zip", PASSWORD, by=person["id"])
    assert refused.value.code == "personal_space_stays"
    assert not (tmp_path / "p.zip").exists()


# ── a half-done import ──────────────────────────────────────────────────


@contextmanager
def _files_fail(monkeypatch):
    def fail(*_a: Any, **_k: Any) -> int:
        raise OSError("the disk is full")

    monkeypatch.setattr(space_move, "_write_files", fail)
    yield


def test_a_half_done_import_leaves_the_install_as_it_was(moved, target, monkeypatch) -> None:
    with _files_fail(monkeypatch), pytest.raises(OSError):
        _bring_in(moved["bundle"], target["person"])
    _nothing_of(A)


def test_control_without_the_take_back_the_half_is_left(moved, target, monkeypatch) -> None:
    monkeypatch.setattr(space_move, "_take_back", lambda *_a, **_k: None)
    with _files_fail(monkeypatch), pytest.raises(OSError):
        _bring_in(moved["bundle"], target["person"])
    _ctx, found = space_data._walk(A)
    assert sum(item.rows for item in found) > 0  # the rows written before the failure stay


def test_an_import_not_taken_back_is_taken_back_by_the_next(moved, target, monkeypatch) -> None:
    """The take-back itself fails (a locked database), as it is left by a
    stop before it ran: the journal keeps the space, and the next import's
    settle takes it back."""
    real = space_move._take_back

    def locked(*_a: Any, **_k: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(space_move, "_take_back", locked)
    with _files_fail(monkeypatch), pytest.raises(OSError):
        _bring_in(moved["bundle"], target["person"])
    assert A in (get_config_store().get(space_move.JOURNAL_KEY) or {})  # the journal remembers it
    monkeypatch.setattr(space_move, "_take_back", real)
    assert space_move.settle_unfinished_imports() == [A]
    _nothing_of(A)


# ── the routes ──────────────────────────────────────────────────────────


@contextmanager
def _client(principal: dict[str, Any]):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.spaces_api import create_spaces_router

    app = FastAPI()
    app.include_router(create_spaces_router())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(auth, "get_request_principal", lambda request: principal)
        patch.setattr(auth, "admin_decision",
                      lambda request: "ok" if principal.get("role") == "admin" else "forbidden")
        with TestClient(app) as client:
            yield client


ADMIN = {"source": "secret", "role": "admin", "user_id": "", "actor": "secret"}


def test_the_import_route_is_the_admins(moved, target) -> None:
    person = {"source": "session", "role": "operator", "user_id": target["person"], "actor": "login"}
    form = {"password": PASSWORD, "owner": target["person"]}
    with _client(person) as client, open(moved["bundle"], "rb") as handle:
        refused = client.post("/api/spaces/import", data=form, files={"bundle": ("a.zip", handle, "application/zip")})
    assert refused.status_code == 403 and refused.json()["error"] == "admin_only"
    with _client(ADMIN) as client, open(moved["bundle"], "rb") as handle:
        wrong = client.post("/api/spaces/import", data={**form, "password": "not-the-password-at-all"},
                            files={"bundle": ("a.zip", handle, "application/zip")})
        assert wrong.status_code == 400 and wrong.json()["error"] == "wrong_password"
        handle.seek(0)
        taken = client.post("/api/spaces/import", data=form, files={"bundle": ("a.zip", handle, "application/zip")})
    assert taken.status_code == 201 and taken.json()["space"] == A, taken.text
    assert identity.is_member(target["person"], A)


def test_the_move_route_is_the_owners(tmp_path, monkeypatch) -> None:
    with own_install(monkeypatch, tmp_path / "move-route"):
        person = people.create_person(f"owner-{uuid4().hex[:4]}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        shared = people.create_shared_space("Moving", by=OWNER_ACCOUNT_ID)
        people.add_member(shared["id"], person["id"], by=OWNER_ACCOUNT_ID)
        member = {"source": "session", "role": "operator", "user_id": person["id"], "actor": "login"}
        with _client(member) as client:
            refused = client.post(f"/api/spaces/{shared['id']}/move", json={"password": PASSWORD})
        assert refused.status_code == 403  # a member, not its owner
        with _client(ADMIN) as client:
            short = client.post(f"/api/spaces/{shared['id']}/move", json={"password": "short"})
            made = client.post(f"/api/spaces/{shared['id']}/move", json={"password": PASSWORD})
        assert short.status_code == 400 and short.json()["error"] == "password_too_short"
        assert made.status_code == 200 and made.headers["content-type"] == "application/octet-stream", made.text
        assert made.content.startswith(b"KZSEALED")  # the whole file sealed


def _refusal_codes() -> set[str]:
    """Every code the move and the bring-in route answer, read from their
    source: the ``SpaceMoveRefused`` codes, the sealed file's and the vault's
    codes the move answers as its own, and the route's."""
    import re

    import kazma_ui.spaces_api as api

    source = Path(space_move.__file__).read_text(encoding="utf-8")
    codes = set(re.findall(r'SpaceMoveRefused\(\s*"([a-z_]+)"', source))
    codes |= {"wrong_password", "too_big", "changed"}  # SealedFileRefused, as the move answers it
    route = Path(api.__file__).read_text(encoding="utf-8")
    block = route[route.index('@router.post("/import")'):route.index('@router.post("/{space_id}/move")')]
    codes |= set(re.findall(r'_answer\("([a-z_]+)"', block))
    return codes


def test_every_refusal_is_worded_in_both_languages() -> None:
    from kazma_ui.i18n.catalog.settings import TRANSLATIONS as CATALOG

    codes = _refusal_codes()
    assert {"foreign_row", "id_collides", "space_exists", "spaces_off", "personal_space_stays"} <= codes
    missing = sorted(code for code in codes
                     if not all(CATALOG.get(f"settings.space_data_err.{code}", {}).get(lang) for lang in ("en", "ar")))
    assert not missing, missing


# ── then the matrix ─────────────────────────────────────────────────────
#
# Every case of the space matrix, run on an install whose space A was moved
# in: the matrix's space A, planted with a row in every table, made into a
# bundle on one install and brought into a fresh one for each case, its
# owner there the matrix's person A. Space B is made there as the matrix
# makes it. Whatever a case asks of A's space -- a membership, a record, a
# key, a thread -- is the moved space's.

from tests._space_matrix import Matrix  # noqa: E402
from tests.test_space_matrix import _REGISTERED, CASES, run_case  # noqa: E402


@pytest.fixture(scope="module")
def matrix_bundle(tmp_path_factory) -> Path:
    from tests._space_world import _make_stores, _plant, _word

    folder = tmp_path_factory.mktemp("matrix-source")
    bundle = folder / "matrix-a.zip"
    with pytest.MonkeyPatch.context() as patch, own_install(patch, folder):
        patch.delenv("KAZMA_PRODUCTION", raising=False)
        with Matrix() as made:
            _make_stores()
            _plant(Matrix.SPACE_A, _word(Matrix.SPACE_A))
            space_move.export_bundle(Matrix.SPACE_A, bundle, PASSWORD, by=made.account_a)
    return bundle


MOVED_VAULT = "vault-moved.db"


@pytest.fixture(scope="module")
def moved_install(matrix_bundle, tmp_path_factory) -> dict[str, Any]:
    """An install whose space A was brought in from the bundle, made once:
    the matrix's two people, A owning the moved space, B a space of their
    own made here. Each case runs on a copy of its data folder."""
    folder = tmp_path_factory.mktemp("matrix-target")
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("KAZMA_PRODUCTION", raising=False)
        with own_install(patch, folder, vault_key=TARGET_KEY, vault_name=MOVED_VAULT):
            made = Matrix()
            made.principal_a = made._person("matrix-person-a")
            made.principal_b = made._person("matrix-person-b")
            made.account_a = made._account(made.principal_a)
            made.account_b = made._account(made.principal_b)
            space_move.import_bundle(matrix_bundle, PASSWORD, owner=made.account_a, by=OWNER_ACCOUNT_ID)
            identity.create_space("Matrix B", created_by=made.account_b, space_id=Matrix.SPACE_B)
            registry = get_config_store().get(identity.REGISTRY_KEY)
    return {"data": folder / "kazma-data", "registry": registry,
            "people": (made.principal_a, made.principal_b, made.account_a, made.account_b)}


class _MovedMatrix(Matrix):
    """The matrix on a copy of the moved install: its people are there
    already, and its space A is the one brought in."""

    def __init__(self, people: tuple[dict[str, Any], dict[str, Any], str, str]) -> None:
        super().__init__()
        self.principal_a, self.principal_b, self.account_a, self.account_b = people

    def __enter__(self) -> Matrix:
        return self


@pytest.fixture
def moved_matrix(moved_install, tmp_path, monkeypatch) -> Matrix:
    import shutil

    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    shutil.copytree(moved_install["data"], tmp_path / "moved" / "kazma-data")
    with own_install(monkeypatch, tmp_path / "moved", vault_key=TARGET_KEY, vault_name=MOVED_VAULT):
        # own_install takes the registry back when it ends: the copy's is put back.
        get_config_store().set(identity.REGISTRY_KEY, moved_install["registry"])
        # Each case starts as the plain matrix starts one: Spaces off, the
        # case switching it on where it asks for it.
        monkeypatch.setattr(scope, "_enforced", False)
        with _MovedMatrix(moved_install["people"]) as made:
            assert identity.is_member(made.account_a, Matrix.SPACE_A)
            space = identity.read_registry()["spaces"][Matrix.SPACE_A]
            assert space["personal_of"] is None and space["created_by"] == made.account_a
            yield made


@pytest.mark.parametrize("case", _REGISTERED, ids=_REGISTERED)
def test_the_matrix_holds_for_a_moved_space(case, moved_matrix) -> None:
    run_case(CASES[case], moved_matrix)
