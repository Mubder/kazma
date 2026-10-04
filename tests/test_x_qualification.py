"""Synthetic fixtures test release arithmetic; they are never a live human corpus."""

from __future__ import annotations

import pytest
from kazma_core.x_api.qualification import REQUIRED_CATEGORIES, evaluate_report
from kazma_core.x_api.verification import CHECK_NAMES


def report():
    return {"fingerprint": "test-pipeline", "evaluated_at": 1000,
            "cases": [{"id": str(i), "language": "ar" if i % 2 else "en", "labeler": "synthetic test fixture",
                       "human_reviewed": True, "held_out": True, "categories": [REQUIRED_CATEGORIES[i % len(REQUIRED_CATEGORIES)]],
                       "expected": {"auto": True, "target": "coffee", "evidence": True, "safety": True},
                       "actual": {"auto": True, "target": "coffee", "critical_violations": 0, "latency_ms": 20, "model_calls": 4, "output_tokens": 100,
                                  "checks": dict.fromkeys(CHECK_NAMES, "pass")}} for i in range(200)]}


def evaluate(value):
    return evaluate_report(value, "test-pipeline", now=1001)


def test_metrics_are_computed_from_cases_and_reject_claimed_percentage():
    value = report()
    value["precision"] = 0
    metrics = evaluate(value)
    assert metrics["cases"] == 200 and metrics["precision"] == 1 and metrics["languages"] == {"ar": 100, "en": 100}


@pytest.mark.parametrize("key,value", [("held_out", False), ("human_reviewed", False), ("labeler", ""), ("language", "xx")])
def test_non_human_or_tuning_cases_cannot_qualify(key, value):
    data = report()
    data["cases"][0][key] = value
    with pytest.raises(ValueError):
        evaluate(data)


@pytest.mark.parametrize("change", [
    {"target": "wrong"}, {"critical_violations": 1}, {"checks": dict.fromkeys(CHECK_NAMES, "unknown")},
])
def test_one_unsafe_auto_case_blocks_release(change):
    data = report()
    data["cases"][0]["actual"].update(change)
    with pytest.raises(ValueError):
        evaluate(data)


@pytest.mark.parametrize("dimension", ["evidence", "safety"])
def test_unsupported_or_unsafe_claim_is_not_auto_eligible(dimension):
    data = report()
    data["cases"][0]["expected"][dimension] = False
    with pytest.raises(ValueError):
        evaluate(data)


def test_missing_fingerprint_stale_and_small_corpus_fail_closed():
    for key, value in (("fingerprint", "another-pipeline"), ("evaluated_at", 2000), ("cases", report()["cases"][:199])):
        data = report()
        data[key] = value
        with pytest.raises(ValueError):
            evaluate(data)
    with pytest.raises(ValueError):
        evaluate_report(report(), "test-pipeline", now=1000 + 15 * 86400)


def test_precision_threshold_has_real_denominator():
    data = report()
    for case in data["cases"][:4]:
        case["expected"]["auto"] = False
    assert evaluate(data)["precision"] == 0.98
    data["cases"][4]["expected"]["auto"] = False
    with pytest.raises(ValueError, match="98%"):
        evaluate(data)


def test_all_held_and_single_language_cannot_claim_perfect_precision():
    data = report()
    for case in data["cases"]:
        case["actual"]["auto"] = False
    with pytest.raises(ValueError):
        evaluate(data)
    data = report()
    for case in data["cases"]:
        case["language"] = "en"
    with pytest.raises(ValueError):
        evaluate(data)


def test_operator_activation_preserves_identical_evaluated_pipeline():
    from dataclasses import replace

    from kazma_core.x_api.qualification import pipeline_fingerprint
    from kazma_core.x_api.stance import get_reply_config

    cfg = get_reply_config()
    evaluated = pipeline_fingerprint(replace(cfg, enabled=True, mode="draft"))
    assert pipeline_fingerprint(replace(cfg, enabled=True, mode="auto")) == evaluated
    assert pipeline_fingerprint(replace(cfg, enabled=False, mode="off")) == evaluated
    assert pipeline_fingerprint(replace(cfg, stance_check=not cfg.stance_check)) != evaluated
