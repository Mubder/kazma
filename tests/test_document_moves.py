"""Spaces S2-12, piece 5: moving one's own document (plan 4.11).

A person moves their own document from their own space into a shared space
they are in, and back out. The original goes through the target space's own
intake (as an upload would) and the source is retired, so after a move the
document is in exactly one space, by the list, the content route and
search. Only its maker moves it; taking it out of a shared space leaves the
space a line saying who took it out and when, which only Kazma's move can
write (a delete cannot name its reason).

Control: a move that does not retire the source leaves a copy readable
where it was.
"""

from __future__ import annotations

from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.documents.ingestion import DocumentIngestionError, set_ingestion_service
from kazma_core.spaces import identity, moves, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._documents_seed import seed_document, seeded_service


@pytest.fixture
def world(tmp_path, monkeypatch):
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    get_config_store().delete(identity.REGISTRY_KEY)
    reem = people.create_person("Reem", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    omar = people.create_person("Omar", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    family = people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)["id"]
    other = people.create_shared_space("Club", by=OWNER_ACCOUNT_ID)["id"]
    for space in (family, other):
        for person in (reem, omar):
            people.add_member(space, person["id"], by=OWNER_ACCOUNT_ID)
    service = seeded_service(tmp_path / "documents")
    set_ingestion_service(service)
    try:
        yield {"svc": service, "reem": reem["id"], "mine": reem["personal_space"], "omar": omar["id"],
               "omars": omar["personal_space"], "family": family, "club": other}
    finally:
        set_ingestion_service(None)
        service.close()
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


def _ids(svc, space: str, actor: str) -> list[str]:
    svc.grant_member_access(tenant_id=space, member=actor, force=True)
    return [d["title"] for d in svc.list_documents(tenant_id=space, actor_id=actor)]


def _move(w, doc: str, src: str, dst: str, by: str) -> dict[str, Any]:
    return moves.move_document(doc, from_space=src, to_space=dst, by=by, workspace_id="w", service=w["svc"])


def test_shared_then_taken_out_is_always_in_one_space(world) -> None:
    w, svc = world, world["svc"]
    doc = seed_document(svc, tenant_id=w["mine"], owner=w["reem"], marker="my private notes", title="Notes")
    shared = _move(w, doc.document_id, w["mine"], w["family"], w["reem"])
    assert shared["taken_out"] is False
    assert _ids(svc, w["mine"], w["reem"]) == []
    assert _ids(svc, w["family"], w["reem"]) == ["Notes"]
    assert _ids(svc, w["family"], w["omar"]) == ["Notes"], "the other member sees it now"
    assert svc.taken_out_documents(tenant_id=w["mine"]) == [], "sharing leaves no line"

    back = _move(w, shared["document_id"], w["family"], w["mine"], w["reem"])
    assert back["taken_out"] is True
    assert _ids(svc, w["family"], w["omar"]) == []
    assert _ids(svc, w["mine"], w["reem"]) == ["Notes"]
    lines = svc.taken_out_documents(tenant_id=w["family"])
    assert [(line["title"], line["by"]) for line in lines] == [("Notes", w["reem"])]
    # Its original came with it: the target holds the same bytes.
    record = svc.repository.get_document(tenant_id=w["mine"], document_id=back["document_id"], actor_id=w["reem"])
    version = svc.repository.get_version(tenant_id=w["mine"], version_id=record.current_version_id,
                                         actor_id=w["reem"])
    assert svc.repository.get_blob(tenant_id=w["mine"], blob_id=version.source_blob_id).sha256 == doc.sha256


def test_control_without_retiring_the_source_a_copy_stays(world, monkeypatch) -> None:
    w, svc = world, world["svc"]
    doc = seed_document(svc, tenant_id=w["mine"], owner=w["reem"], marker="my private notes", title="Notes")
    monkeypatch.setattr(svc, "_tombstone", lambda **_kw: {})
    _move(w, doc.document_id, w["mine"], w["family"], w["reem"])
    assert _ids(svc, w["mine"], w["reem"]) == ["Notes"] and _ids(svc, w["family"], w["reem"]) == ["Notes"]


def test_only_its_maker_takes_it_out(world) -> None:
    w, svc = world, world["svc"]
    doc = seed_document(svc, tenant_id=w["family"], owner=w["reem"], marker="the trip plan", title="Trip")
    with pytest.raises(DocumentIngestionError) as refused:
        _move(w, doc.document_id, w["family"], w["omars"], w["omar"])
    assert refused.value.code == "not_movable"
    assert _ids(svc, w["family"], w["omar"]) == ["Trip"]


def test_a_move_is_between_ones_own_space_and_a_shared_one(world) -> None:
    w, svc = world, world["svc"]
    doc = seed_document(svc, tenant_id=w["family"], owner=w["reem"], marker="x", title="X")
    for src, dst, code in ((w["family"], w["club"], "not_between_own_and_shared"),
                           (w["family"], w["omars"], "not_yours"),
                           (w["family"], w["family"], "not_yours")):
        with pytest.raises(moves.MoveRefused) as refused:
            _move(w, doc.document_id, src, dst, w["reem"])
        assert refused.value.code == code, (src, dst)
    assert _ids(svc, w["family"], w["reem"]) == ["X"]


def test_a_delete_cannot_write_the_taken_out_line(world) -> None:
    w, svc = world, world["svc"]
    doc = seed_document(svc, tenant_id=w["family"], owner=w["omar"], marker="y", title="Y")
    with pytest.raises(DocumentIngestionError) as refused:
        svc.delete_document(tenant_id=w["family"], actor_id=w["omar"], document_id=doc.document_id,
                            reason="taken_out")
    assert refused.value.code == "reserved_reason"
    assert svc.taken_out_documents(tenant_id=w["family"]) == []


def test_with_spaces_off_nothing_moves(world, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    refused, _out = moves.move_rule(from_space=world["mine"], to_space=world["family"], by=world["reem"])
    assert refused is not None and refused.code == "spaces_off"


def test_the_route_moves_it_and_the_list_shows_the_line(world, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth, documents_api

    w, svc = world, world["svc"]
    doc = seed_document(svc, tenant_id=w["family"], owner=w["reem"], marker="z", title="Zeta")
    principal = {"source": "session", "role": "operator", "user_id": w["reem"], "actor": "login",
                 "username": "Reem"}
    monkeypatch.setattr(documents_api, "_active_workspace_id", lambda: "w")
    monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())
    app.state.documents = svc
    app.include_router(documents_api.create_documents_router())
    with TestClient(app) as client:
        here = {"X-Kazma-Space": w["family"]}
        listed = client.get("/api/documents", headers=here).json()
        assert [d["mine"] for d in listed["documents"]] == [True]
        answer = client.post(f"/api/documents/{doc.document_id}/move", json={"to_space": w["mine"]}, headers=here)
        assert answer.status_code == 200 and answer.json()["taken_out"] is True, answer.text
        listed = client.get("/api/documents", headers=here).json()
        assert listed["documents"] == []
        assert [(line["title"], line["by_name"]) for line in listed["taken_out"]] == [("Zeta", "Reem")]
        refused = client.post(f"/api/documents/{doc.document_id}/move", json={"to_space": w["club"]},
                              headers=here)
        assert refused.status_code == 400 and refused.json()["code"] == "not_between_own_and_shared"
