"""Every workflow says what its token may do, and only declared ones may write.

A workflow with no ``permissions:`` takes the repository's default token, and
on 2026-11-09 that default was read AND write, with leave to approve pull
requests: the CI workflow, which only reads the code and runs it, held a
token that could push. Three of six workflows declared nothing. Each one now
declares its own, so the repository's default can be read-only without
breaking a workflow, and a token that can write is on a short list with its
reason. Planted workflows are the negative controls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# (workflow file, "workflow" or the job's id, permission) -> why it writes.
WRITES: dict[tuple[str, str, str], str] = {
    ("release.yml", "workflow", "contents"):
        "creates the version tag and the release, and uploads its files",
    ("release.yml", "workflow", "id-token"):
        "Sigstore signing and SLSA provenance mint an OIDC token for the run",
    ("release.yml", "workflow", "attestations"):
        "build provenance is written to the attestations store",
    ("pypi-reserve.yml", "publish", "id-token"):
        "PyPI Trusted Publishing mints an OIDC token; no PyPI token exists",
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
