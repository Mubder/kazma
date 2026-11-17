"""Every workflow says what its token may do, and only declared ones may write.

A workflow with no ``permissions:`` takes the repository's default token, and
on 2026-11-09 that default was read AND write, with leave to approve pull
requests: the CI workflow, which only reads the code and runs it, held a
token that could push. Three of six workflows declared nothing. Each one now
declares its own, so the repository's default can be read-only without
breaking a workflow, and a token that can write is on a short list with its
reason. A write is granted to the job that needs it, never to the whole
workflow (2026-11-16, OpenSSF Scorecard's Token-Permissions: release.yml
granted its writes at the top, where every job added later inherits them).
Planted workflows are the negative controls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# (workflow file, "workflow" or the job's id, permission) -> why it writes.
WRITES: dict[tuple[str, str, str], str] = {
    ("release.yml", "release", "contents"):
        "creates the version tag and the release, and uploads its files",
    ("release.yml", "release", "id-token"):
        "Sigstore signing and SLSA provenance mint an OIDC token for the run",
    ("release.yml", "build", "contents"):
        "the release tag is pushed",
    ("release.yml", "windows-installer", "id-token"):
        "signing the Windows installer signs in to Azure with the run's OIDC token; no secret is stored",
    ("release.yml", "release", "attestations"):
        "build provenance is written to the attestations store",
    ("release.yml", "release", "artifact-metadata"):
        "actions/attest v4 records the attested files in the artifact storage record",
    ("pypi-reserve.yml", "publish", "id-token"):
        "PyPI Trusted Publishing mints an OIDC token; no PyPI token exists",
    ("codeql.yml", "analyze", "security-events"):
        "uploads CodeQL's results to GitHub's code scanning",
    ("scorecard.yml", "analysis", "security-events"):
        "uploads the Scorecard results to GitHub's code scanning",
    **{
        ("coverage.yml", job, "id-token"):
            "the Codecov upload proves the run with an OIDC token; no Codecov token is stored"
        for job in ("coverage", "postgres", "browser", "drills", "container", "durable")
    },
    ("scorecard.yml", "analysis", "id-token"):
        "publishing to the OpenSSF Scorecard API proves the run with an OIDC token",
}


def _grants(name: str, text: str) -> tuple[list[str], set[tuple[str, str, str]]]:
    """The problems in one workflow's permissions, and every write it grants."""
    doc: dict[Any, Any] = yaml.safe_load(text) or {}
    problems: list[str] = []
    writes: set[tuple[str, str, str]] = set()

    def read(scope: str, value: Any) -> None:
        if isinstance(value, str):
            # "write-all" grants everything; "read-all" more than any job here
            # needs. Either way the workflow has not said what it uses.
            problems.append(f"{name} ({scope}): permissions is {value!r}, name each permission")
            return
        for permission, level in (value or {}).items():
            if level == "write":
                writes.add((name, scope, str(permission)))
                if scope == "workflow":
                    problems.append(
                        f"{name}: '{permission}: write' is granted to the whole workflow; "
                        "grant it to the job that needs it"
                    )

    if "permissions" not in doc:
        problems.append(f"{name}: no top-level permissions, so it takes the repository's default token")
    else:
        read("workflow", doc["permissions"])
    for job_id, job in (doc.get("jobs") or {}).items():
        if isinstance(job, dict) and "permissions" in job:
            read(str(job_id), job["permissions"])
    return problems, writes


def _all() -> tuple[list[str], set[tuple[str, str, str]]]:
    problems: list[str] = []
    writes: set[tuple[str, str, str]] = set()
    files = sorted(WORKFLOWS.glob("*.y*ml"))
    assert files, "no workflow files found"
    for workflow in files:
        found, granted = _grants(workflow.name, workflow.read_text(encoding="utf-8"))
        problems += found
        writes |= granted
    return problems, writes


def test_every_workflow_declares_its_permissions():
    problems, _ = _all()
    assert not problems, problems


def test_only_declared_permissions_write():
    _, writes = _all()
    undeclared = sorted(writes - set(WRITES))
    assert not undeclared, (
        f"these grant write without a reason in WRITES: {undeclared}"
    )
    stale = sorted(set(WRITES) - writes)
    assert not stale, f"WRITES names a grant no workflow makes: {stale}"


def test_negative_control_a_workflow_without_permissions_is_found():
    planted = "name: X\non:\n  push:\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
    problems, writes = _grants("planted.yml", planted)
    assert problems and "no top-level permissions" in problems[0]
    assert not writes


def test_negative_control_write_all_and_an_undeclared_write_are_found():
    blanket = "name: X\npermissions: write-all\njobs: {}\n"
    problems, _ = _grants("planted.yml", blanket)
    assert problems and "write-all" in problems[0]

    job_write = (
        "name: X\npermissions:\n  contents: read\njobs:\n  a:\n"
        "    permissions:\n      contents: write\n    steps: []\n"
    )
    problems, writes = _grants("planted.yml", job_write)
    assert not problems
    assert writes == {("planted.yml", "a", "contents")}
    assert not writes <= set(WRITES)


def test_negative_control_a_write_granted_to_the_whole_workflow_is_found():
    """release.yml as it was until 2026-11-16."""
    top_level = (
        "name: X\npermissions:\n  contents: write\n  id-token: write\njobs:\n  a:\n"
        "    runs-on: ubuntu-latest\n    steps: []\n"
    )
    problems, _ = _grants("planted.yml", top_level)
    assert any("granted to the whole workflow" in p for p in problems)
