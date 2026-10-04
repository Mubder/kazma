"""Annotation cannot grant authority or silently reuse tuned conversations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from kazma_core.tenant_context import tenant_scope
from kazma_core.x_api.datasets import DatasetStore


def case(ident="c1", **over):
    row = {"id": ident, "language": "en", "categories": ["negation"], "held_out": False,
           "context": {"source_id": "123", "author_handle": "author", "text": "A real original opinion."},
           "summon": {"id": "456", "author": "owner", "conversation_id": "789"},
           "expected": {"target": "coffee", "auto": False, "evidence": True, "safety": True},
           "rationale": "Inspected original; an opinion with incomplete context must be held."}
    row.update(over)
    return row


@pytest.fixture
def store(tmp_path):
    return DatasetStore(tmp_path / "x_datasets.db")


def test_real_collection_persists_but_imported_human_attestation_is_reset(store):
    doc = store.create("Release", "release", actor="operator", document={"cases": [case(human_reviewed=True, labeler="invented")]})
    assert doc["cases"][0]["human_reviewed"] is False
    assert not doc["cases"][0]["context"]["complete"]
    saved = store.save_case(doc["id"], doc["cases"][0], revision=doc["revision"], actor="actual-reviewer", reviewed=True)
    assert saved["cases"][0]["labeler"] == "actual-reviewer"
    edited = store.save_case(doc["id"], saved["cases"][0], revision=saved["revision"], actor="editor")
    assert not edited["cases"][0]["human_reviewed"]
    assert DatasetStore(store.path).get(doc["id"])["revision"] == 3


def test_tenant_isolation_and_parallel_stale_edits(store):
    with tenant_scope("owner"):
        doc = store.create("Cases", "collection", actor="a")
    with tenant_scope("stranger"):
        assert not store.list()
        with pytest.raises(KeyError):
            store.get(doc["id"])

    def save():
        with tenant_scope("owner"):
            try:
                DatasetStore(store.path).save_case(doc["id"], case(), revision=1, actor="a")
                return "saved"
            except ValueError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: save(), range(2))) == ["conflict", "saved"]


def test_training_provenance_blocks_release_even_after_case_is_archived(store):
    tuned = store.create("Tuning", "tuning", actor="a", document={"cases": [case()]})
    store.save_case(tuned["id"], case(archived=True), revision=1, actor="a")
    with pytest.raises(ValueError, match="used for tuning"):
        store.create("Release", "release", actor="a", document={"cases": [case(held_out=True)]})
    assert len(store.list()) == 1


def observed():
    from kazma_core.x_api.verification import CHECK_NAMES

    return {"auto": False, "target": "coffee", "critical_violations": None,
            "checks": dict.fromkeys(CHECK_NAMES, "unknown"), "latency_ms": 1,
            "model_calls": 0, "output_tokens": 0}


def test_recorded_outcomes_are_immutable_and_context_changes_clear_them(store):
    original = case(actual=observed(), candidate="Recorded exact output.")
    doc = store.create("Observed", "release", actor="a", document={"cases": [original], "fingerprint": "a" * 64, "evaluated_at": 123})
    tampered = {**doc["cases"][0], "actual": {"auto": True}, "candidate": "Invented success"}
    updated = store.save_case(doc["id"], tampered, revision=1, actor="a")
    assert updated["cases"][0]["actual"]["auto"] is False
    assert updated["cases"][0]["candidate"] == "Recorded exact output."
    changed = {**updated["cases"][0], "context": {**updated["cases"][0]["context"], "text": "Different original."}}
    saved = store.save_case(doc["id"], changed, revision=2, actor="a")
    assert "actual" not in saved["cases"][0]


def test_later_tuning_invalidates_held_out_export(store):
    original = case(actual=observed(), candidate="Measured candidate", held_out=True)
    doc = store.create("Observed", "release", actor="a", document={"cases": [original], "fingerprint": "a" * 64, "evaluated_at": 123})
    reviewed = store.save_case(doc["id"], doc["cases"][0], revision=1, actor="reviewer", reviewed=True, critical_violations=0)
    assert store.export(reviewed["id"], report=True)["cases"]
    store.create("Later tuning", "tuning", actor="a", document={"cases": [case()]})
    with pytest.raises(ValueError, match="used for tuning"):
        store.export(doc["id"], report=True)


def test_incomplete_recordings_cannot_be_imported_as_measured_outcomes(store):
    with pytest.raises(ValueError, match="complete measured"):
        store.create("Forged", "release", actor="a", document={"cases": [case(actual={"auto": True})], "fingerprint": "a" * 64, "evaluated_at": 123})


def test_review_needs_labels_and_candidate_inspection(store):
    doc = store.create("Cases", "release", actor="a")
    with pytest.raises(ValueError, match="Complete"):
        store.save_case(doc["id"], case(expected={}), revision=1, actor="a", reviewed=True)
    with pytest.raises(ValueError, match="full shadow report"):
        store.export(doc["id"], report=True)


def test_collection_deduplicates_retained_cases_without_overwriting_labels(store, monkeypatch):
    monkeypatch.setattr("kazma_core.x_api.corpus.collect_cases", lambda **kw: [case(), case("c2")])
    doc = store.create("Cases", "collection", actor="a", document={"cases": [case(rationale="Existing human note.")]})
    saved = store.collect(doc["id"], revision=1, actor="a")
    assert len(saved["cases"]) == 2 and saved["cases"][0]["rationale"] == "Existing human note."


def test_api_csrf_and_revision_conflicts():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.x_dataset_api import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.post("/api/x/datasets", json={"name": "Cases"}).status_code == 403
        headers = {"X-Requested-With": "XMLHttpRequest"}
        doc = client.post("/api/x/datasets", json={"name": "Cases"}, headers=headers).json()["dataset"]
        body = {"case": case(), "expected_revision": 1}
        assert client.put(f"/api/x/datasets/{doc['id']}/case", json=body, headers=headers).status_code == 200
        assert client.put(f"/api/x/datasets/{doc['id']}/case", json=body, headers=headers).status_code == 409


@pytest.mark.parametrize("side", ["support", "against"])
def test_drafter_receives_actual_scope_exceptions_and_negative_examples(side):
    from kazma_core.x_api.reply import _build_prompt
    from kazma_core.x_api.stance import Subject

    subject = Subject(id="coffee", target="Coffee sourcing", match=("coffee",), side=side,
                      scope="Commercial sourcing only", exceptions=("Concede verified environmental harm",),
                      counterexamples=("Inventing a safety award",))
    system = _build_prompt(subject, "Original post", "author", mood="angry")[0]["content"]
    assert subject.scope in system and subject.exceptions[0] in system and subject.counterexamples[0] in system
    assert "FOR Coffee sourcing" in system if side == "support" else "AGAINST Coffee sourcing" in system
    assert "invent" in system and "HOW you speak" in system


@pytest.mark.parametrize("side,draft", [("support", "I oppose coffee sourcing."), ("against", "أؤيد توريد القهوة.")])
async def test_failed_stance_cannot_become_a_successful_preview(side, draft, monkeypatch):
    from dataclasses import replace

    from kazma_core.x_api.reply import preview_reply
    from kazma_core.x_api.stance import Subject, get_reply_config
    from kazma_core.x_api.verification import CHECK_NAMES, CheckResult

    async def generated(**kwargs):
        return draft

    async def checked(*args, **kwargs):
        return tuple(CheckResult(name, "fail" if name == "stance" else "pass", "Position reversed", draft) for name in CHECK_NAMES)

    monkeypatch.setattr("kazma_core.x_api.reply.draft_reply", generated)
    monkeypatch.setattr("kazma_core.x_api.verification.verify_candidate", checked)
    subject = Subject(id="coffee", match=("coffee",), side=side)
    result = await preview_reply(parent_text="coffee sourcing", subject_id="coffee", cfg=replace(get_reply_config(), subjects=(subject,), use_knowledge=False))
    assert not result.ok and result.action == "failed" and "stance" in result.reason
