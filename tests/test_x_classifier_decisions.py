"""A classifier outage, invalid schema and exclusion never become voice permission."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from kazma_core.x_api.routing import AmbiguousSubjectError
from kazma_core.x_api.stance import ClassifierUnavailable, Subject, _llm_pick


@pytest.fixture
def classifier(monkeypatch):
    state = {"answer": {"state": "ready", "subject_id": "coffee", "evidence": "coffee", "reason": "Primary target."}}
    class Client:
        async def chat(self, messages, **kwargs):
            return SimpleNamespace(content=json.dumps(state["answer"]))
    class Registry:
        def get_client(self):
            return Client()
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: Registry())
    return state


async def test_exact_closed_decision_resolves_declared_target(classifier):
    subject = Subject(id="coffee", target="Coffee sourcing", match=("coffee",), side="against")
    assert await _llm_pick("coffee sourcing", (subject,)) == subject


@pytest.mark.parametrize("answer", [
    "coffee", {"state": "ready", "subject_id": "invented", "evidence": "coffee", "reason": "Primary target."},
    {"state": "ready", "subject_id": "coffee", "evidence": "not in the post", "reason": "Primary target."},
    {"state": "ready", "subject_id": "coffee", "evidence": "coffee", "reason": "", "approved": True},
    {"state": "no_match", "subject_id": "coffee", "evidence": "", "reason": ""},
])
async def test_unusable_classification_is_not_no_match(answer, classifier):
    classifier["answer"] = answer
    with pytest.raises(ClassifierUnavailable):
        await _llm_pick("coffee sourcing", (Subject(id="coffee", match=("coffee",), side="against"),))


async def test_model_cannot_expand_aliases_or_scopes(classifier):
    subject = Subject(id="coffee", match=("coffee",), side="against", scope="Commercial only")
    with pytest.raises(AmbiguousSubjectError):
        await _llm_pick("coffee sourcing", (subject,))


async def test_no_match_and_review_are_distinct(classifier):
    subject = Subject(id="coffee", match=("coffee",), side="against")
    classifier["answer"] = {"state": "no_match", "subject_id": "", "evidence": "", "reason": "Unrelated."}
    assert await _llm_pick("tea sourcing", (subject,)) is None
    classifier["answer"] = {"state": "needs_review", "subject_id": "", "evidence": "tea", "reason": "Uncertain."}
    with pytest.raises(AmbiguousSubjectError):
        await _llm_pick("tea sourcing", (subject,))
