"""The S2-12 review, findings 3 and 5: a move of a document between spaces
is safe at once and across a stop.

* Two moves of one document at the same time leave it in exactly one
  space: a lock per document is held from reading it to retiring it, and a
  move takes back only a copy it made itself, and only while the source is
  still there. Control: piece 5's move, run the same way, leaves it in
  neither space.
* Each attempt has a key of its own: a move after an earlier move of the
  same document back and forth makes a new copy, never reuses an old one.
* A move is recorded before it starts and settled at the next start: a copy
  a stop cut off while the source is still there is taken back; one whose
  source was retired is left. Control: with the journal settled by nothing,
  the copy stays and the document is in two spaces.
* A document with more than one version is refused with a clear message:
  a move never leaves its earlier versions behind.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.documents import ingestion
from kazma_core.documents.ingestion import DocumentIngestionError, set_ingestion_service
from kazma_core.spaces import identity, moves, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._documents_seed import seed_document, seeded_service


@pytest.fixture
def world(tmp_path, monkeypatch):
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    store.delete(ingestion.MOVES_KEY)
    reem = people.create_person("Reem", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    family = people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)["id"]
    people.add_member(family, reem["id"], by=OWNER_ACCOUNT_ID)
    service = seeded_service(tmp_path / "documents")
    set_ingestion_service(service)
    try:
        yield {"svc": service, "reem": reem["id"], "mine": reem["personal_space"], "family": family}
    finally:
        set_ingestion_service(None)
        service.close()
        store.delete(identity.REGISTRY_KEY)
        store.delete(ingestion.MOVES_KEY)
        reset_posture_cache()


def _live(svc, space: str, actor: str) -> list[str]:
    svc.grant_member_access(tenant_id=space, member=actor, force=True)
    return [d["title"] for d in svc.list_documents(tenant_id=space, actor_id=actor)]


def _everywhere(w) -> list[str]:
    return _live(w["svc"], w["mine"], w["reem"]) + _live(w["svc"], w["family"], w["reem"])


def _two_moves_at_once(w, doc_id: str) -> list[Any]:
    """Both moves read the document before either copies it."""
    both_read = threading.Barrier(2, timeout=2)
    real_ingest = w["svc"].ingest_stream
    answers: list[Any] = []

    def ingest(*args: Any, **kwargs: Any) -> Any:
        try:
            both_read.wait()
        except threading.BrokenBarrierError:
            pass  # the other move was held by the lock: it reads after this one ends
        return real_ingest(*args, **kwargs)


    def one() -> None:
        try:
            answers.append(moves.move_document(doc_id, from_space=w["mine"], to_space=w["family"],
                                               by=w["reem"], workspace_id="w", service=w["svc"]))
        except (DocumentIngestionError, moves.MoveRefused) as exc:
            answers.append(exc)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(w["svc"], "ingest_stream", ingest)
        threads = [threading.Thread(target=one) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
    return answers


def test_two_moves_at_once_leave_it_in_exactly_one_space(world) -> None:
    w = world
    doc = seed_document(w["svc"], tenant_id=w["mine"], owner=w["reem"], marker="trip notes", title="Trip")
    answers = _two_moves_at_once(w, doc.document_id)
    assert _everywhere(w) == ["Trip"], answers
    assert sum(isinstance(a, dict) for a in answers) == 1, answers
    assert get_config_store().get(ingestion.MOVES_KEY) in (None, {}), "every move settled"


def _move_as_before(svc, *, document_id, from_tenant, to_tenant, actor_id, workspace_id, taken_out):
    """Piece 5's move, as it was before the review (no lock, one key per
    document, the copy taken back whenever the source was not retired),
    stopping between its steps so two can be interleaved."""
    from kazma_core.documents.models import DocumentId

    doc_id = DocumentId(document_id)
    record = svc.repository.get_document(tenant_id=from_tenant, document_id=doc_id, actor_id=actor_id)
    version = svc.repository.get_version(tenant_id=from_tenant, version_id=record.current_version_id,
                                         actor_id=actor_id)
    blob = svc.repository.get_blob(tenant_id=from_tenant, blob_id=version.source_blob_id)
    yield "read"
    original = svc.storage.for_space(from_tenant).locate_blob(kind=blob.storage_kind, sha256=blob.sha256)
    with original.open("rb") as stream:
        moved = svc.ingest_stream(
            stream, filename=version.original_filename, tenant_id=to_tenant, workspace_id=workspace_id,
            actor_id=actor_id, title=record.title, idempotency_key=f"move:{from_tenant}:{doc_id}",
            content_length=blob.byte_size,
        )
    yield "copied"
    retired = False
    try:
        svc._tombstone(tenant_id=from_tenant, actor_id=actor_id, document_id=doc_id,
                       reason="taken_out" if taken_out else "shared_to_space")
        retired = True
    finally:
        if not retired:
            svc._tombstone(tenant_id=to_tenant, actor_id=actor_id, document_id=moved.document_id,
                           reason="user_requested")
    yield "done"


def test_control_the_move_of_before_loses_the_document(world) -> None:
    w = world
    doc = seed_document(w["svc"], tenant_id=w["mine"], owner=w["reem"], marker="trip notes", title="Trip")
    kw = {"document_id": doc.document_id, "from_tenant": w["mine"], "to_tenant": w["family"],
          "actor_id": w["reem"], "workspace_id": "w", "taken_out": False}
    a, b = _move_as_before(w["svc"], **kw), _move_as_before(w["svc"], **kw)
    assert next(a) == next(b) == "read"  # both read the document
    assert next(a) == "copied" and next(b) == "copied"  # B reuses A's copy
    assert next(a) == "done"  # A retires the source
    with pytest.raises(DocumentIngestionError):
        next(b)  # B cannot retire it, and takes back the one copy
    assert _everywhere(w) == [], "the race of before: in neither space"


def test_each_attempt_has_a_key_of_its_own(world) -> None:
    w = world
    doc = seed_document(w["svc"], tenant_id=w["mine"], owner=w["reem"], marker="trip notes", title="Trip")
    there = moves.move_document(doc.document_id, from_space=w["mine"], to_space=w["family"], by=w["reem"],
                                workspace_id="w", service=w["svc"])
    back = moves.move_document(there["document_id"], from_space=w["family"], to_space=w["mine"],
                               by=w["reem"], workspace_id="w", service=w["svc"])
    again = moves.move_document(back["document_id"], from_space=w["mine"], to_space=w["family"],
                                by=w["reem"], workspace_id="w", service=w["svc"])
    assert len({there["document_id"], back["document_id"], again["document_id"]}) == 3
    assert _live(w["svc"], w["family"], w["reem"]) == ["Trip"] and _live(w["svc"], w["mine"], w["reem"]) == []


def _cut_off_after_the_copy(w, doc_id: str) -> None:
    """A move stopped after its copy and before the source was retired."""
    def stopped(**_kw: Any) -> dict:
        raise KeyboardInterrupt("the server stopped")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(w["svc"], "_tombstone", stopped)
        patch.setattr(ingestion, "_settle_move", lambda attempt: None)  # the process ended
        with pytest.raises(KeyboardInterrupt):
            moves.move_document(doc_id, from_space=w["mine"], to_space=w["family"], by=w["reem"],
                                workspace_id="w", service=w["svc"])


def test_a_move_cut_off_is_settled_at_the_next_start(world) -> None:
    w = world
    doc = seed_document(w["svc"], tenant_id=w["mine"], owner=w["reem"], marker="trip notes", title="Trip")
    _cut_off_after_the_copy(w, doc.document_id)
    assert len(get_config_store().get(ingestion.MOVES_KEY) or {}) == 1, "recorded before it started"
    assert len(_everywhere(w)) == 2, "the stop left a copy"
    settled = w["svc"].settle_moves()
    assert [s["outcome"] for s in settled] == ["copy_taken_back"]
    assert _live(w["svc"], w["mine"], w["reem"]) == ["Trip"] and _live(w["svc"], w["family"], w["reem"]) == []
    assert not get_config_store().get(ingestion.MOVES_KEY)
    # Control: nothing settles it, and the document stays in two spaces.


def test_control_unsettled_the_copy_stays(world) -> None:
    w = world
    doc = seed_document(w["svc"], tenant_id=w["mine"], owner=w["reem"], marker="trip notes", title="Trip")
    _cut_off_after_the_copy(w, doc.document_id)
    assert len(_everywhere(w)) == 2


def test_a_move_recorded_refuses_another_of_the_same_document(world) -> None:
    w = world
    doc = seed_document(w["svc"], tenant_id=w["mine"], owner=w["reem"], marker="trip notes", title="Trip")
    _cut_off_after_the_copy(w, doc.document_id)
    with pytest.raises(DocumentIngestionError) as refused:
        moves.move_document(doc.document_id, from_space=w["mine"], to_space=w["family"], by=w["reem"],
                            workspace_id="w", service=w["svc"])
    assert refused.value.code == "move_in_progress"


def test_a_document_with_versions_is_refused(world, monkeypatch) -> None:
    w = world
    doc = seed_document(w["svc"], tenant_id=w["mine"], owner=w["reem"], marker="trip notes", title="Trip")
    real = w["svc"].repository.list_versions
    monkeypatch.setattr(w["svc"].repository, "list_versions",
                        lambda **kw: real(**kw) * 2)  # a second version
    with pytest.raises(DocumentIngestionError) as refused:
        moves.move_document(doc.document_id, from_space=w["mine"], to_space=w["family"], by=w["reem"],
                            workspace_id="w", service=w["svc"])
    assert refused.value.code == "more_than_one_version" and "earlier versions" in str(refused.value)
    assert _live(w["svc"], w["mine"], w["reem"]) == ["Trip"] and _live(w["svc"], w["family"], w["reem"]) == []
