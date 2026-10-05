"""Review binding, honest collection and failure-preserving denominators."""
from __future__ import annotations

from copy import deepcopy

import pytest

from kazma_core.agent_evaluation import fingerprint
from kazma_core.evaluation_review import (
    DIMENSIONS, apply_reviews, collection_template, freeze_collection, make_review_packet, review_readiness,
)


@pytest.fixture
def evidence():
    cases = [{"id": lang, "prompt": f"real sanitized {lang} request", "language": lang,
              "split": "holdout", "group_id": lang, "source": "sanitized collection",
              "source_kind": "real", "human_labeled": True, "label_reviewer": "rubric-reviewer",
              "rubric": "answer only from evidence", "tools": [], "fixtures": {}} for lang in ("en", "ar")]
    dataset = freeze_collection({"schema_version": 1, "cases": cases})
    report = {"schema_version": 1, "planned_cases": 2, "dataset_sha256": fingerprint(dataset),
              "results": [{**deepcopy(case), "answer": "candidate", "case_sha256": fingerprint(case),
                           "answer_sha256": fingerprint("candidate"), "checks": {"turn_succeeded": True},
                           "review": None} for case in cases]}
    packet = make_review_packet(report)
    for review in packet["reviews"]:
        review.update(reviewer="human-reviewer", **dict.fromkeys(DIMENSIONS, True))
    return dataset, report, packet


def test_intake_does_not_invent_labels():
    draft = collection_template()
    assert draft["cases"] == []
    assert draft["case_fields"]["human_labeled"] is False
    with pytest.raises(ValueError, match="nonempty"):
        freeze_collection(draft)


@pytest.mark.parametrize("field,value", [("source_kind", "synthetic"), ("label_reviewer", ""), ("human_labeled", False)])
def test_freeze_rejects_unqualified_holdout(evidence, field, value):
    dataset, _, _ = evidence
    dataset["cases"][0][field] = value
    with pytest.raises(ValueError, match="real provenance"):
        freeze_collection(dataset)


def test_packet_leaves_every_judgment_unanswered(evidence):
    _, report, _ = evidence
    packet = make_review_packet(report)
    assert all(review[d] is None for review in packet["reviews"] for d in DIMENSIONS)
    with pytest.raises(ValueError, match="reviewer"):
        apply_reviews(report, packet)


@pytest.mark.parametrize("change", ["report", "hash", "answer", "missing", "duplicate"])
def test_changed_evidence_or_dropped_results_cannot_be_reviewed(evidence, change):
    _, report, packet = evidence
    if change == "report":
        report["results"][0]["answer"] = "altered"
    elif change == "hash":
        packet["reviews"][0]["case_sha256"] = "altered"
    elif change == "answer":
        packet["reviews"][0]["answer"] = "altered evidence shown to reviewer"
    elif change == "missing":
        packet["reviews"].pop()
    else:
        packet["reviews"].append(packet["reviews"][0])
    with pytest.raises(ValueError):
        apply_reviews(report, packet)
    assert all(row["review"] is None for row in report["results"])


def test_failed_turns_remain_in_reviewed_denominator(evidence):
    dataset, report, _ = evidence
    report["results"][0]["checks"]["turn_succeeded"] = False
    packet = make_review_packet(report)
    for review in packet["reviews"]:
        review.update(reviewer="human", **dict.fromkeys(DIMENSIONS, True))
    reviewed = apply_reviews(report, packet)
    assert reviewed["summary"]["languages"]["en"]["accuracy"] == 0
    assert review_readiness(reviewed, dataset, minimum_per_language=1)["ready_for_human_comparison"]
    assert reviewed["summary"]["accuracy_is_certified"] is False
    assert report["results"][0]["review"] is None


def test_incomplete_or_unreviewed_run_is_pending(evidence):
    dataset, report, packet = evidence
    assert not review_readiness(report, dataset, minimum_per_language=1)["ready_for_human_comparison"]
    reviewed = apply_reviews(report, packet)
    reviewed["results"].pop()
    pending = review_readiness(reviewed, dataset, minimum_per_language=1)
    assert not pending["ready_for_human_comparison"]
    assert any("incomplete" in reason for reason in pending["pending_reasons"])


def test_candidate_metadata_cannot_promote_a_case(evidence):
    dataset, report, packet = evidence
    reviewed = apply_reviews(report, packet)
    reviewed["results"][0]["language"] = "ar"
    assert not review_readiness(reviewed, dataset, minimum_per_language=1)["ready_for_human_comparison"]
