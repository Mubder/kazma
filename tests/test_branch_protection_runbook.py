"""The branch-protection runbook names every CI job, and nothing else.

``docs/docs/ops/branch-protection.md`` lists the status checks to require, by
the name GitHub reports for each job in ``.github/workflows/ci.yml``. A job
added to CI and left off the list is a check protection would not require; a
listed name that no job reports is a required check that never arrives, and
GitHub then blocks every push to ``main``.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CI = REPO / ".github" / "workflows" / "ci.yml"
RUNBOOK = REPO / "docs" / "docs" / "ops" / "branch-protection.md"


def ci_check_names(ci_text: str) -> set[str]:
    jobs = (yaml.safe_load(ci_text) or {}).get("jobs") or {}
    return {str(job.get("name") or key) for key, job in jobs.items()}


def runbook_check_names(runbook_text: str) -> set[str]:
    section = runbook_text.split("## 1. The checks to require", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `([^`]+)` \|", section, re.M))


def test_the_runbook_lists_exactly_the_ci_checks():
    ci = ci_check_names(CI.read_text(encoding="utf-8"))
    runbook = runbook_check_names(RUNBOOK.read_text(encoding="utf-8"))
    assert ci - runbook == set(), f"CI jobs the runbook would not require: {sorted(ci - runbook)}"
    assert runbook - ci == set(), f"required checks no CI job reports: {sorted(runbook - ci)}"


def test_the_comparison_sees_a_missing_job():
    """Negative control: a runbook one job short is caught."""
    ci_text = "jobs:\n  a:\n    name: Alpha\n  b:\n    name: Beta\n"
    runbook = "## 1. The checks to require\n\n| Check | What |\n|---|---|\n| `Alpha` | a |\n\n## 2. Next\n"
    assert ci_check_names(ci_text) - runbook_check_names(runbook) == {"Beta"}
