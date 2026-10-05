"""Offline collection and human review; never call a model or certify accuracy."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from kazma_core.agent_evaluation import fingerprint, summarize, validate_dataset

DIMENSIONS = ("completion", "grounding", "language", "injection_resistance")


def collection_template() -> dict[str, Any]:
    """An empty intake, with no invented interactions or human labels."""
    return {
        "schema_version": 1, "cases": [], "collection_status": "draft",
        "instructions": [
            "Collect sanitized real interactions, including failed turns and corrections.",
            "Write the expected rubric before running the candidate model.",
            "Keep related conversations and translations in one group and one split.",
            "Holdout cases require source_kind=real, human_labeled=true and label_reviewer.",
            "Include ordinary requests, ambiguity, missing evidence, tool failures and injection attempts in en/ar.",
            "Have a person verify provenance, independence and removal of private data before freezing.",
        ],
        "case_fields": {
            "id": "unique ID", "prompt": "sanitized original request", "language": "en or ar",
            "split": "development or holdout", "group_id": "sanitized conversation/source family",
            "source": "auditable sanitized provenance", "source_kind": "real or synthetic",
            "human_labeled": False, "label_reviewer": "person who checked the rubric",
            "rubric": "expected outcome and evidence limits", "tools": [], "fixtures": {},
        },
    }


def freeze_collection(document: dict[str, Any]) -> dict[str, Any]:
    """Validate intake and freeze the declared labels; provenance needs human audit."""
    cases = validate_dataset(document)
    for case in cases:
        if case.get("split") == "holdout" and not (
            case.get("source_kind") == "real" and case.get("human_labeled") is True
            and isinstance(case.get("label_reviewer"), str) and case["label_reviewer"].strip()
        ):
            raise ValueError("Holdout needs real provenance and a named human rubric reviewer.")
    return {"schema_version": 1, "cases": deepcopy(cases), "collection_status": "frozen"}


def _candidate_rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in report.get("results", []):
        identity = row.get("id")
        if not isinstance(identity, str) or identity in rows:
            raise ValueError("Report results need unique IDs.")
        if row.get("answer_sha256") != fingerprint(row.get("answer", "")) or not row.get("case_sha256"):
            raise ValueError("Candidate answer identity is invalid.")
        rows[identity] = row
    if not rows:
        raise ValueError("There are no candidate answers to review.")
    return rows


def make_review_packet(report: dict[str, Any]) -> dict[str, Any]:
    """Copy evidence for a human, leaving every judgment explicitly unanswered."""
    rows = _candidate_rows(report)
    entries = []
    for row in rows.values():
        entries.append({
            "id": row["id"], "case_sha256": row["case_sha256"], "answer_sha256": row["answer_sha256"],
            "case_language": row.get("language"), "rubric": row.get("rubric"), "answer": row.get("answer"),
            "messages": deepcopy(row.get("messages", [])), "attempted_tools": deepcopy(row.get("attempted_tools", [])),
            "fixture_calls": deepcopy(row.get("fixture_calls", [])), "error": row.get("error"),
            "checks": deepcopy(row.get("checks", {})), "source": row.get("source"),
            "reviewer": "", "notes": "", **{dimension: None for dimension in DIMENSIONS},
        })
    return {"schema_version": 1, "report_sha256": fingerprint(report), "reviews": entries}


def apply_reviews(report: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Bind all judgments to the original report; reject stale or partial packets."""
    if packet.get("schema_version") != 1 or packet.get("report_sha256") != fingerprint(report):
        raise ValueError("Review packet belongs to a different or changed report.")
    rows = _candidate_rows(report)
    reviews: dict[str, dict[str, Any]] = {}
    evidence = {item["id"]: item for item in make_review_packet(report)["reviews"]}
    for review in packet.get("reviews", []):
        identity = review.get("id")
        if identity not in rows or identity in reviews:
            raise ValueError("Unknown or duplicate review ID.")
        row = rows[identity]
        if any(review.get(key) != row[key] for key in ("case_sha256", "answer_sha256")):
            raise ValueError("Review is stale: case or answer identity changed.")
        original = evidence[identity]
        for key in ("answer", "case_language", "rubric", "messages", "attempted_tools", "fixture_calls", "error", "checks", "source"):
            if review.get(key) != original[key]:
                raise ValueError("Review evidence changed; edit judgments and notes only.")
        if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
            raise ValueError("Every review needs a named human reviewer.")
        if any(type(review.get(dimension)) is not bool for dimension in DIMENSIONS):
            raise ValueError("A human must answer every review dimension true or false.")
        reviews[identity] = {key: review.get(key) for key in
                             ("reviewer", "notes", "case_sha256", "answer_sha256", *DIMENSIONS)}
    if set(reviews) != set(rows):
        raise ValueError("Review every result, including failed turns; do not drop failures.")
    result = deepcopy(report)
    for row in result["results"]:
        row["review"] = reviews[row["id"]]
    result["summary"] = summarize(result)
    return result


def review_readiness(report: dict[str, Any], dataset: dict[str, Any], *, minimum_per_language: int = 30) -> dict[str, Any]:
    """Check completeness and provenance for comparison, not production qualification."""
    if minimum_per_language < 1:
        raise ValueError("The minimum must be positive.")
    cases = validate_dataset(dataset)
    expected = {case["id"]: case for case in cases}
    rows = _candidate_rows(report)
    reasons = []
    if report.get("dataset_sha256") != fingerprint(dataset):
        reasons.append("dataset hash does not match the frozen collection")
    if set(rows) != set(expected) or report.get("planned_cases") != len(cases):
        reasons.append("the run is incomplete or contains extra results")
    counts = {"en": 0, "ar": 0}
    for identity, case in expected.items():
        row = rows.get(identity)
        if row is None or row.get("case_sha256") != fingerprint(case):
            reasons.append(f"{identity}: missing or changed case")
            continue
        if any(row.get(key) != case.get(key) for key in
               ("language", "split", "group_id", "human_labeled", "rubric", "source", "source_kind", "label_reviewer")):
            reasons.append(f"{identity}: result metadata disagrees with the frozen case")
            continue
        if case["split"] != "holdout":
            continue
        if not (case.get("source_kind") == "real" and case["human_labeled"]
                and isinstance(case.get("label_reviewer"), str) and case["label_reviewer"].strip()):
            reasons.append(f"{identity}: real provenance and a human rubric review are required")
            continue
        review = row.get("review") or {}
        if not (isinstance(review.get("reviewer"), str) and review["reviewer"].strip()
                and review.get("answer_sha256") == row["answer_sha256"]
                and review.get("case_sha256") == row["case_sha256"]
                and all(type(review.get(dimension)) is bool for dimension in DIMENSIONS)):
            reasons.append(f"{identity}: human candidate review is missing or stale")
            continue
        counts[case["language"]] += 1
    for language, count in counts.items():
        if count < minimum_per_language:
            reasons.append(f"{language}: {count}/{minimum_per_language} reviewed real holdout cases")
    return {
        "ready_for_human_comparison": not reasons, "pending_reasons": reasons,
        "reviewed_real_holdout": counts, "minimum_per_language": minimum_per_language,
        "accuracy_is_certified": False,
        "scope": "Fixture effects only. Review paired regressions and production integration separately.",
    }
