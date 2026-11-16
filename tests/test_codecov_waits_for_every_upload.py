"""Codecov reports a commit only once every coverage job has uploaded.

The coverage workflow uploads one report per suite, hours apart. Codecov
reported each commit as soon as the first small suite landed: 31% for a commit
whose total was 74% (2026-11-16). ``codecov.yml``'s ``after_n_builds`` must
equal the number of jobs that upload; a job added or removed without changing
it brings the partial reports back. Its statuses stay informational: coverage
measures and never gates.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _uploading_jobs(workflow: dict) -> list[str]:
    return [
        name for name, job in workflow["jobs"].items()
        if any("coverage-upload" in str(step.get("uses", "")) for step in job.get("steps", []))
    ]


def _expected_builds(settings: dict) -> int:
    return int(settings["codecov"]["notify"]["after_n_builds"])


def test_codecov_waits_for_every_coverage_job():
    workflow = yaml.safe_load((REPO / ".github/workflows/coverage.yml").read_text(encoding="utf-8"))
    settings = yaml.safe_load((REPO / "codecov.yml").read_text(encoding="utf-8"))
    jobs = _uploading_jobs(workflow)
    assert jobs, "no coverage job uploads"
    assert _expected_builds(settings) == len(jobs), jobs


def test_control_a_job_added_without_the_count_is_caught():
    workflow = yaml.safe_load((REPO / ".github/workflows/coverage.yml").read_text(encoding="utf-8"))
    settings = yaml.safe_load((REPO / "codecov.yml").read_text(encoding="utf-8"))
    workflow["jobs"]["planted"] = {"steps": [{"uses": "./.github/actions/coverage-upload"}]}
    assert _expected_builds(settings) != len(_uploading_jobs(workflow))


def test_codecov_statuses_never_fail_a_commit():
    settings = yaml.safe_load((REPO / "codecov.yml").read_text(encoding="utf-8"))
    for kind in ("project", "patch"):
        assert settings["coverage"]["status"][kind]["default"]["informational"] is True
