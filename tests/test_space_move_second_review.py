"""The second review of a space's move (Spaces S4-06, of 46f200bcb).

Each test fails on 46f200bcb:

* **a moved space read any file of the new machine**: the files X drafting
  reads travelled with the space, and were read whatever the space's path
  policy says. They never move -- a connection's settings move only when
  they are named (:data:`space_move._MOVED_SETTINGS`), and every key the code
  names is there or stays with its reason -- and ``load_references`` reads
  only where the bound space's file tools do, whoever set them;
* **another member's account travelled** after a refresh ran as the space's
  maker (``test_space_move_review.py``, the fixture and
  ``test_4_who_connected_an_account_is_recorded_once``; here, what binds
  ``connecting``);
* **the new owner's facts were forgotten as they arrived**, their membership
  being recorded last: the passes that take a space with no member for a
  left one leave a space in the import journal alone, and a document job of
  it waits;
* people's ids and names inside a chat's saved state; the space's key, which
  leaves with the bundle; a half-done import taken back at the next start,
  never a running one; a refused bundle's empty checkpoint file; a refusal
  of the record as a code; a space signed in with X, whose sign-in's expiry
  is a plain number, arrives.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from kazma_core import paths
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope, space_keys, space_move
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._space_world import A, _where_word, own_install
from tests.test_space_bundle_round_trip import PASSWORD, TARGET_KEY, entries_of, sealed_again
from tests.test_space_move_review import DEPARTED, _bring_in, _refused, _rows, moved, target  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = "connectors.x.drafting.reference_files"


# ── what X drafting reads ───────────────────────────────────────────────


@pytest.fixture
def shared(tmp_path, monkeypatch) -> Any:
    with own_install(monkeypatch, tmp_path / "install", vault_key=TARGET_KEY) as data:
        person = people.create_person(f"reader-{uuid.uuid4().hex[:4]}", "a-long-enough-password",
                                      by=OWNER_ACCOUNT_ID)["id"]
        space = people.create_shared_space("Posters", by=OWNER_ACCOUNT_ID)["id"]
        people.add_member(space, person, by=OWNER_ACCOUNT_ID)
        yield {"data": data, "space": space, "person": person}


def test_the_files_x_drafting_reads_never_move(shared, tmp_path) -> None:
    get_config_store().set(f"tenant.{shared['space']}.{REFERENCES}", ["notes.md"], category="connectors")
    get_config_store().set(f"tenant.{shared['space']}.connectors.x.handle", "@posters", category="connectors")
    bundle = tmp_path / "posters.kazmaspace"
    space_move.export_bundle(shared["space"], bundle, PASSWORD, by=OWNER_ACCOUNT_ID)
    settings = json.loads(entries_of(bundle)["settings.json"])
    assert set(settings) == {f"tenant.{shared['space']}.connectors.x.handle"}


def test_x_drafting_reads_only_where_the_spaces_file_tools_do(shared, tmp_path, monkeypatch) -> None:
    """Whoever set it. **Control:** with the policy saying yes, the old read
    opens the file of the machine."""
    from kazma_core.spaces import files as space_files
    from kazma_core.workspace import path_policy
    from kazma_core.x_api.references import ReferenceUnreadable, load_references

    outside = tmp_path / "the-admins-notes.md"
    outside.write_text("the admin's own words", encoding="utf-8")
    inside = space_files.space_dir(shared["space"]) / "files" / "about.md"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("what this space is about", encoding="utf-8")
    real = path_policy.check_path_access
    with scope.bind_scope(shared["space"], shared["person"]):
        with pytest.raises(ReferenceUnreadable, match="cannot be read in this space"):
            load_references((str(outside),))
        assert [d.text for d in load_references((str(inside),))] == ["what this space is about"]
        monkeypatch.setattr(path_policy, "check_path_access", lambda *_a, **_k: real(str(inside), "read"))
        assert [d.text for d in load_references((str(outside),))] == ["the admin's own words"]


def test_one_account_reads_its_references_as_before(tmp_path, monkeypatch) -> None:
    from kazma_core.workspace import path_policy
    from kazma_core.x_api.references import load_references

    outside = tmp_path / "brief.md"
    outside.write_text("my brief", encoding="utf-8")
    monkeypatch.setattr(scope, "_enforced", False)
    monkeypatch.setattr(path_policy, "check_path_access", lambda *_a, **_k: pytest.fail("the policy was asked"))
    assert [d.text for d in load_references((str(outside),))] == ["my brief"]


_KEY = re.compile(r"connectors\.(?:x|github)\.[a-z0-9_.]*[a-z0-9_]")


def _named_keys() -> dict[str, str]:
    """Every connector setting the product names: ``{key: file}``."""
    found: dict[str, str] = {}
    for package in ("kazma-core/kazma_core", "kazma-gateway/kazma_gateway", "kazma-ui/kazma_ui",
                    "kazma-skills/kazma_skills", "kazma-cli/kazma_cli", "kazma-tui/kazma_tui"):
        for path in sorted((ROOT / package).rglob("*.py")):
            if path.name == "space_move.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and _KEY.fullmatch(node.value):
                    found.setdefault(node.value, path.name)
    return found


#: Names that are not a space's setting: an adapter's field the same words
#: could name, a prefix the secret rules read.
_NOT_A_SETTING = {"connectors.x.credentials", "connectors.x.credentials.password", "connectors.x.bot_token"}


def _unsaid(keys: dict[str, str]) -> list[str]:
    def stays(key: str) -> bool:
        return any(key == held or (held[-1] in "._" and key.startswith(held))
                   for held in space_move._STAYING_SETTINGS)

    return sorted(f"{key} ({where})" for key, where in keys.items()
                  if key not in space_move._MOVED_SETTINGS and not stays(key) and key not in _NOT_A_SETTING)


def test_every_connector_setting_says_whether_it_moves() -> None:
    keys = _named_keys()
    assert {"connectors.x.handle", REFERENCES, "connectors.github.token"} <= set(keys)
    assert not _unsaid(keys), "a setting of a connection is neither moved nor staying"
    assert all(space_move._STAYING_SETTINGS.values())
    # Control: one named nowhere is found.
    assert _unsaid({"connectors.x.drafting.voice_file": "planted.py"}) == [
        "connectors.x.drafting.voice_file (planted.py)"]


def test_a_space_signed_in_with_x_arrives(shared, tmp_path, monkeypatch) -> None:
    """The sign-in's expiry and account are plain values among its
    credentials: 46f200bcb made the bundle and refused it (``bad_setting``)."""
    from kazma_core.x_api import auth_tokens

    space = shared["space"]
    with scope.bind_scope(space, OWNER_ACCOUNT_ID):
        auth_tokens.save_oauth2_tokens({"access_token": "acc", "refresh_token": "ref", "expires_in": 60,
                                        "scope": "tweet.read users.read"},
                                       account={"username": "posters", "id": "42", "name": "Posters"})
    bundle = tmp_path / "signed-in.kazmaspace"
    manifest = space_move.export_bundle(space, bundle, PASSWORD, by=OWNER_ACCOUNT_ID)
    assert manifest["settings"] == 4 and "settings" not in manifest["left_out"], manifest
    with own_install(monkeypatch, tmp_path / "there", vault_key="c" * 64):
        person = people.create_person("arrives", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
        space_move.import_bundle(bundle, PASSWORD, owner=person, by=OWNER_ACCOUNT_ID)
        with scope.bind_scope(space, person):
            assert auth_tokens.oauth2_account()["username"] == "posters"
            assert auth_tokens._get(auth_tokens.REFRESH_KEY) == "ref" and auth_tokens._expires_at() > 0


# ── who connected an account ────────────────────────────────────────────


def test_connecting_is_what_a_person_does_on_the_connect_routes(monkeypatch) -> None:
    """Through the real tenant middleware: a POST that connects an account
    of the space is one, its reads and every other route are not, and
    nothing is one while Spaces is not enforced."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.spaces.connected_accounts import is_connecting

    from kazma_ui import auth

    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())
    connects = ("/api/email/gmail/connect", "/api/email/protocol/connect", "/api/email/accounts",
                "/api/email/oauth/microsoft/device/poll", "/api/github/token", "/api/x/credentials",
                "/api/x/api-access")
    # A disconnect, a removal and a start keep nothing a person gives: what
    # they write is not theirs because they pressed it (the third review).
    others = ("/api/chat/anything", "/api/x/test", "/api/email/gmail/disconnect", "/api/x/disconnect",
              "/api/email/accounts/work/remove", "/api/email/oauth/gmail/start", "/api/calendar/anything")
    for path in connects + others:
        app.add_api_route(path, lambda: {"connecting": is_connecting()}, methods=["GET", "POST"])
    monkeypatch.setattr(auth, "get_request_principal", lambda _request: None)
    with TestClient(app) as client:
        monkeypatch.setattr(scope, "_enforced", True)
        for path in connects:
            assert client.post(path).json() == {"connecting": True}, path
            assert client.get(path).json() == {"connecting": False}, path
        for path in others:
            assert client.post(path).json() == {"connecting": False}, path
        assert is_connecting() is False
        monkeypatch.setattr(scope, "_enforced", False)
        assert client.post("/api/github/token").json() == {"connecting": False}


def test_a_sign_in_callback_is_connecting(shared) -> None:
    from kazma_core.spaces.connected_accounts import is_connecting

    from kazma_ui.oauth_sign_in import attempt_scope

    with attempt_scope({"space": shared["space"], "asker": shared["person"]}):
        assert is_connecting() and scope.bound_asker() == shared["person"]
    assert not is_connecting()


async def test_a_persons_own_secret_is_theirs_and_no_metadata_says_otherwise(shared) -> None:
    """The vault tool: the person gives the value now; a stamp they write in
    the metadata is not one."""
    from kazma_skills.native.secret_vault.tools import vault_store

    from tests.test_space_move_review import _stamp

    with scope.bind_scope(shared["space"], OWNER_ACCOUNT_ID):
        await vault_store("door-code", "1234")
    with scope.bind_scope(shared["space"], shared["person"]):
        await vault_store("door-code", "5678", metadata=json.dumps({"stored_by": OWNER_ACCOUNT_ID}))
    assert _stamp("door-code") == shared["person"]


# ── while an import is writing ──────────────────────────────────────────


def _erased_here_once(monkeypatch) -> None:
    """The record of an install that erased space A before it moved back."""
    real_read = identity.read_registry

    def _read() -> dict[str, Any]:
        reg = real_read()
        if A not in reg["spaces"]:
            reg["events"] = [*reg["events"], {"kind": "space_erased", "space": A}]
        return reg

    monkeypatch.setattr(identity, "read_registry", _read)


def test_the_new_owners_facts_are_never_forgotten_as_they_arrive(moved, target, monkeypatch) -> None:  # noqa: F811
    """The forget convergence and the erased-key sweep run just before the
    space is recorded, when its rows and keys are all here (46f200bcb took
    both)."""
    from kazma_core.memory import forget
    from kazma_core.security.vault import get_vault

    real = people.record_moved_space
    seen: dict[str, Any] = {}

    def _sweeps_first(space_id: str, name: str, owner: str, *, by: str) -> None:
        seen["arriving"] = getattr(space_move, "importing_spaces", frozenset)()
        seen["forgotten"] = forget._forget_departed_entries()
        seen["destroyed"] = space_keys.destroy_erased_keys()
        real(space_id, name, owner, by=by)

    monkeypatch.setattr(people, "record_moved_space", _sweeps_first)
    _erased_here_once(monkeypatch)
    _bring_in(moved["bundle"], target["person"])
    assert seen["forgotten"] == {"forgotten": 0} and seen["destroyed"] == [] and seen["arriving"] == {A}
    mine = _rows("memory_state.db", "beliefs", "subject = ?", (f"member_{target['person']}",))
    assert "owner-fact" in [r["object"] for r in mine], mine
    assert get_vault().for_space(A).retrieve(f"space:{A}:own-secret") == "the-owners-secret"
    assert space_move.importing_spaces() == frozenset()


def test_control_without_the_journal_the_facts_and_the_key_go(moved, target, monkeypatch) -> None:  # noqa: F811
    from kazma_core.memory import forget

    real = people.record_moved_space

    def _sweeps_first(*a: Any, **kw: Any) -> None:
        monkeypatch.setattr(space_move, "importing_spaces", frozenset)
        assert forget._forget_departed_entries()["forgotten"] >= 1
        assert space_keys.destroy_erased_keys() == [A]
        real(*a, **kw)

    monkeypatch.setattr(people, "record_moved_space", _sweeps_first)
    _erased_here_once(monkeypatch)
    _bring_in(moved["bundle"], target["person"])
    mine = _rows("memory_state.db", "beliefs", "subject = ?", (f"member_{target['person']}",))
    assert "owner-fact" not in [r["object"] for r in mine]


def test_a_document_job_of_an_arriving_space_waits(shared, tmp_path) -> None:
    """Its people and its files are written after its row: the job is given
    back on the attempt it had, however long the import takes -- never failed
    as one whose person left, never dead-lettered (b55865291: each wait was a
    failed attempt, and the fourth claim found none left)."""
    from datetime import UTC, datetime, timedelta

    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import DocumentIngestionService
    from kazma_core.documents.worker import DocumentWorker

    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path / "documents", security_malware_scan="off", security_local_salvage=False,
        ocr_enabled=False))
    svc.jobs._jitter = lambda _base: 0.0
    now = [datetime.now(UTC)]
    svc.jobs._clock = lambda: now[0]
    try:
        with scope.bind_scope(shared["space"], shared["person"]):
            result = svc.ingest_stream(io.BytesIO(b"tides of the Gulf\n"), filename="tides.txt",
                                       tenant_id=shared["space"], actor_id=shared["person"], workspace_id="w")
        people.leave_space(shared["space"], by=shared["person"])  # as before its people are recorded
        space_move._journal(shared["space"], add=True, by=OWNER_ACCOUNT_ID)
        worker = DocumentWorker(svc.jobs, svc.stage_handlers())
        for waits in range(1, 9):  # eight waits: more than any job has attempts
            claimed = svc.jobs.claim_next(owner="worker", lease_seconds=60)
            assert claimed is not None and claimed.attempt == 1, (waits, claimed)
            asyncio.run(worker._process_claimed(claimed, "worker"))
            job = svc.jobs.get(tenant_id=shared["space"], job_id=result.job_id)
            assert job.state.value == "retry_wait" and job.attempt == 0 and job.error_code is None, job
            assert job.lease_owner is None
            assert svc.jobs.claim_next(owner="worker", lease_seconds=60) is None, "claimed again at once"
            now[0] += timedelta(seconds=31)
        events = [e.event_type for e in svc.jobs.events(tenant_id=shared["space"], job_id=result.job_id)]
        assert events.count("handed_back_space_arriving") == 8
        # One whose cancel was asked for meanwhile ends cancelled.
        svc.jobs.request_cancel(tenant_id=shared["space"], job_id=result.job_id)
        claimed = svc.jobs.claim_next(owner="worker", lease_seconds=60)
        if claimed is not None:
            asyncio.run(worker._process_claimed(claimed, "worker"))
        assert svc.jobs.get(tenant_id=shared["space"], job_id=result.job_id).state.value == "cancelled"
    finally:
        svc.close()


# ── the rest ────────────────────────────────────────────────────────────


def test_a_chats_saved_state_names_no_one_who_did_not_arrive(moved, target) -> None:  # noqa: F811
    """The card it waits on named who asked, who answers and their names,
    and its state who spoke (46f200bcb: in the bundle and here)."""
    from kazma_core.checkpoint_serde import kazma_checkpoint_serde
    from langgraph.checkpoint.sqlite import SqliteSaver

    raw = b"".join(data for name, data in entries_of(moved["bundle"]).items() if name.startswith("stores/checkpoints/"))
    blobs = [json.loads(line) for line in raw.splitlines() if line.strip()]
    import base64

    plain = b"".join(base64.b64decode(v["base64"]) for row in blobs for v in row.values() if isinstance(v, dict))
    assert moved["maya"].encode() not in plain and b"Alnasser" not in plain
    _bring_in(moved["bundle"], target["person"])
    assert _where_word("Alnasser") == {}
    with closing(sqlite3.connect(str(paths.space_checkpoints_db(A)), check_same_thread=False)) as conn:
        saver = SqliteSaver(conn, serde=kazma_checkpoint_serde())
        found = saver.get_tuple({"configurable": {"thread_id": moved["waiting"], "checkpoint_ns": ""}})
    values = found.checkpoint["channel_values"]
    assert values["_post_turn_memory"] == {"said_by": DEPARTED, "turns": [1, 2]}
    assert values["opened"] == {"said_by": target["person"]}
    assert found.metadata["asker"] == DEPARTED and found.metadata["step"] == 1
    (_task, _channel, interrupts), = [w for w in found.pending_writes if w[1] == "__interrupt__"]
    card = interrupts[0].value
    assert card["tool"] == "x_post" and card["asker"] == DEPARTED
    assert card["answered_by"] == [DEPARTED, target["person"]] and not any(card["answered_by_names"])
    assert interrupts[0].id == moved["gate"]


def test_a_chats_saved_state_naming_a_person_here_is_refused(moved, target, tmp_path) -> None:  # noqa: F811
    """And one that cannot be read is not taken."""
    import base64

    from kazma_core.checkpoint_serde import kazma_checkpoint_serde

    here = people.create_person(f"here-{uuid.uuid4().hex[:4]}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
    entries = entries_of(moved["bundle"])
    name = "stores/checkpoints/writes.jsonl"
    row = json.loads(entries[name].decode("utf-8").splitlines()[0])
    kind, blob = kazma_checkpoint_serde().dumps_typed({"asker": here})
    for label, change, code in (
            ("person", {"type": kind, "value": {"base64": base64.b64encode(blob).decode()}}, "foreign_person"),
            ("unreadable", {"type": "msgpack", "value": {"base64": base64.b64encode(b"\xc1\xc1").decode()}},
             "not_a_bundle")):
        changed = {**entries, name: entries[name] + (json.dumps({**row, "idx": 7, **change}) + "\n").encode()}
        _refused(sealed_again(changed, tmp_path / f"{label}.kazmaspace"), target["person"], code)


def test_the_old_install_has_a_new_key_once_the_bundle_is_made(moved) -> None:  # noqa: F811
    keys = {k["key_id"] for k in json.loads(entries_of(moved["bundle"])["keys.json"])}
    left = moved["left"]
    assert keys == left["before"], "the bundle carries the key its secrets are sealed with"
    assert left["key_ids"] and not (left["key_ids"] | set(left["held"])) & keys
    assert left["opens"] == "the-owners-secret" and len(left["recorded"]) == 1


def test_a_secret_that_does_not_open_stops_the_bundle(shared, tmp_path) -> None:
    from kazma_core.security.vault import get_vault

    with scope.bind_scope(shared["space"], OWNER_ACCOUNT_ID):
        get_vault().for_space(shared["space"]).store(f"space:{shared['space']}:token", "v")
    with closing(sqlite3.connect(str(paths.vault_db_path()))) as conn:
        conn.execute("UPDATE secrets SET encrypted_value = ? WHERE name = ?",
                     (b"damaged-bytes-damaged-bytes", f"space:{shared['space']}:token"))
        conn.commit()
    get_vault()._space_keys.clear()
    with pytest.raises(space_move.SpaceMoveRefused) as refused:
        space_move.export_bundle(shared["space"], tmp_path / "x.kazmaspace", PASSWORD, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "secrets_unreadable" and not list(tmp_path.glob("x.kazmaspace*"))


def _cut_off(bundle: Path, owner: str, monkeypatch) -> None:
    """An import a stop cuts off after everything was written."""

    class Stopped(BaseException):
        pass

    def _stop(*_a: Any, **_kw: Any) -> None:
        raise Stopped

    with monkeypatch.context() as patch:
        patch.setattr(people, "record_moved_space", _stop)
        patch.setattr(space_move, "_take_back", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("stopped")))
        with pytest.raises(Stopped):
            _bring_in(bundle, owner)


def test_a_half_done_import_is_taken_back_at_the_next_start(moved, target, monkeypatch) -> None:  # noqa: F811
    from kazma_core.spaces import space_data

    _cut_off(moved["bundle"], target["person"], monkeypatch)
    assert space_move.importing_spaces() == {A} and paths.space_checkpoints_db(A).exists()
    space_move.settle_at_start()
    assert sum(item.rows for item in space_data._walk(A)[1]) == 0
    assert space_move.importing_spaces() == frozenset() and not paths.space_checkpoints_db(A).exists()
    _bring_in(moved["bundle"], target["person"])  # and it comes in whole afterwards
    # Kazma's start asks for it.
    app = (ROOT / "kazma-ui/kazma_ui/app.py").read_text(encoding="utf-8")
    assert "spawn_background(_aio.to_thread(settle_at_start)" in app


def test_a_running_import_is_never_taken_back(moved, target, monkeypatch) -> None:  # noqa: F811
    real = people.record_moved_space
    started: list[threading.Thread] = []

    def _start_meanwhile(*a: Any, **kw: Any) -> None:
        settle = threading.Thread(target=space_move.settle_at_start, daemon=True)
        settle.start()
        settle.join(0.5)
        assert settle.is_alive(), "Kazma's start took a running import back"
        started.append(settle)
        real(*a, **kw)

    monkeypatch.setattr(people, "record_moved_space", _start_meanwhile)
    _bring_in(moved["bundle"], target["person"])
    started[0].join(10)
    assert A in identity.read_registry()["spaces"]
    assert _rows("memory_state.db", "beliefs", "tenant_id = ?", (A,))


@pytest.mark.parametrize("code, answered", [("bad_space_kind", "not_recorded"), ("space_exists", "space_exists"),
                                            ("no_such_account", "no_such_account")])
def test_a_record_that_refuses_the_space_is_a_code(moved, target, monkeypatch, code, answered) -> None:  # noqa: F811
    def _refuses(*_a: Any, **_kw: Any) -> None:
        raise identity.IdentityError(code, "no")

    monkeypatch.setattr(people, "record_moved_space", _refuses)
    _refused(moved["bundle"], target["person"], answered)


def test_a_space_whose_name_this_install_does_not_take_is_refused_unwritten(moved, target, tmp_path) -> None:  # noqa: F811
    entries = entries_of(moved["bundle"])
    manifest = json.loads(entries["bundle.json"])
    manifest["name"] = "x" * 500
    entries["bundle.json"] = json.dumps(manifest).encode()
    _refused(sealed_again(entries, tmp_path / "named.kazmaspace"), target["person"], "not_a_bundle")
