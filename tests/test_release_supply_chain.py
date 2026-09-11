"""The release workflow must keep producing verifiable artifacts.

`docs/SUPPLY_CHAIN.md` tells a stranger to run `gh attestation verify` and
`sigstore verify` against a Kazma release. If someone edits `release.yml` and
drops a step or a permission, those commands start failing and the document
becomes a false claim — the exact documentation-drift failure
`scripts/check_docs_sync.py` exists to prevent, applied to the supply chain.

These are structure assertions on the workflow file. They do not run a
release; the signing and attestation steps can only be exercised by an actual
dispatch on GitHub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_RELEASE_YML = _ROOT / ".github" / "workflows" / "release.yml"
_SUPPLY_DOC = _ROOT / "docs" / "SUPPLY_CHAIN.md"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(_RELEASE_YML.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return workflow["jobs"]["release"]["steps"]


def _uses(steps: list[dict[str, Any]]) -> list[str]:
    return [str(s.get("uses") or "") for s in steps if s.get("uses")]


@pytest.mark.parametrize(
    ("permission", "why"),
    [
        ("id-token", "Sigstore keyless signing mints an OIDC token"),
        ("attestations", "SLSA build provenance is written to the attestations store"),
        ("contents", "the release and its assets are uploaded"),
    ],
)
def test_release_has_required_permission(
    workflow: dict[str, Any], permission: str, why: str
) -> None:
    perms = workflow.get("permissions") or {}
    assert perms.get(permission) == "write", f"{permission}: write is required — {why}"


def test_release_builds_distributions(steps: list[dict[str, Any]]) -> None:
    """A tag-only release has nothing to sign."""
    assert any("uv build" in str(s.get("run") or "") for s in steps)


def test_release_generates_an_sbom_from_the_lockfile(
    steps: list[dict[str, Any]],
) -> None:
    runs = "\n".join(str(s.get("run") or "") for s in steps)
    assert "cyclonedx-py" in runs, "no SBOM step"
    # From uv.lock, not the runner's environment — the lock is what a user
    # actually installs, so it is what the SBOM has to describe.
    assert "uv export" in runs
    assert "--no-dev" in runs
    # Without --frozen, `uv export` re-resolves and can rewrite uv.lock during
    # the release, so the SBOM would describe a set nobody committed.
    assert "--frozen" in runs


def test_release_attests_build_provenance(steps: list[dict[str, Any]]) -> None:
    assert any(
        u.startswith("actions/attest-build-provenance@") for u in _uses(steps)
    ), "provenance attestation step missing — `gh attestation verify` would fail"


def test_release_signs_artifacts_with_sigstore(steps: list[dict[str, Any]]) -> None:
    assert any(
        u.startswith("sigstore/gh-action-sigstore-python@") for u in _uses(steps)
    ), "signing step missing — releases would ship unsigned"


def test_signing_covers_every_published_artifact(steps: list[dict[str, Any]]) -> None:
    """The wheel, the sdist, the SBOM and the checksum file all get signed.

    SHA256SUMS especially: an unsigned checksum file can simply be replaced
    alongside the artifacts it describes, which makes it worth nothing.
    """
    sign = next(
        s
        for s in steps
        if str(s.get("uses") or "").startswith("sigstore/gh-action-sigstore-python@")
    )
    inputs = str((sign.get("with") or {}).get("inputs") or "")
    for expected in (".whl", ".tar.gz", ".cdx.json", "SHA256SUMS"):
        assert expected in inputs, f"{expected} is published but never signed"


def test_third_party_actions_are_pinned_to_a_version(steps: list[dict[str, Any]]) -> None:
    """A bare `uses: org/action` floats on that action's default branch."""
    unpinned = [u for u in _uses(steps) if "@" not in u]
    assert not unpinned, f"unpinned actions: {unpinned}"


def test_supply_chain_doc_exists_and_names_the_real_commands(
    steps: list[dict[str, Any]],
) -> None:
    """The doc is the deliverable; the workflow is only how it becomes true."""
    assert _SUPPLY_DOC.is_file(), "docs/SUPPLY_CHAIN.md is referenced by release notes"
    text = _SUPPLY_DOC.read_text(encoding="utf-8")
    for command in ("gh attestation verify", "sigstore verify", "sha256sum -c"):
        assert command in text, f"verification doc never shows `{command}`"
    # Honesty clause: provenance is not reproducibility, and the doc says so.
    assert "not claimed" in text or "does not prove" in text.lower()
