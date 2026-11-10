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
    # Without --locked (or --frozen), `uv export` re-resolves and can rewrite
    # uv.lock during the release, so the SBOM would describe a set nobody
    # committed; --frozen also accepted a lock that no longer matched
    # pyproject.toml. Read the command, not the comments around it.
    commands = [
        line.strip() for line in runs.splitlines()
        if line.strip().startswith("uv export") and not line.strip().startswith("#")
    ]
    assert commands and all("--locked" in c and "--frozen" not in c for c in commands), commands


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


def _publish_problems(run: str) -> list[str]:
    """What is wrong with a publish step's commands (comments are not read)."""
    lines = [
        line.strip() for line in run.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    commands = [line for line in lines if line.startswith("gh release ")]
    problems: list[str] = []
    creates = [c for c in commands if c.startswith("gh release create")]
    if not creates:
        problems.append("no release is created")
    elif not all("--draft" in c and "dist/" in c for c in creates):
        problems.append("the release is created published, or without its files")
    if not commands or "--draft=false" not in commands[-1]:
        problems.append("publishing is not the last release command: files change after it")
    if any("|| true" in line for line in lines):
        problems.append("a failed command is swallowed (|| true)")
    if "isDraft" not in run or "exit 1" not in lines:
        problems.append("a release that is already published is not refused")
    return problems


_OLD_PUBLISH = """
gh release create "${TAG}" --title "${TAG}" --notes "${NOTES}" \\
  || gh release edit "${TAG}" --notes "${NOTES}" \\
  || true
# --clobber so re-running the workflow replaces assets instead of failing.
gh release upload "${TAG}" dist/* --clobber
"""


def test_release_is_a_draft_until_every_file_is_attached(
    steps: list[dict[str, Any]],
) -> None:
    """Draft, files, then publish; a published release is never rewritten.

    With GitHub's immutable releases on, a published release takes no more
    files: the old step published first and uploaded after, so it would have
    published an empty release and failed. And the updater checks a wheel
    against the release's SHA256SUMS: files replaced under a published tag
    are what it must never meet. Until 2026-11-09 a re-run replaced them.
    """
    publish = [s for s in steps if "gh release create" in str(s.get("run") or "")]
    assert len(publish) == 1, "one step creates the release"
    assert _publish_problems(str(publish[0]["run"])) == []
    # Nothing after it touches the release.
    later = steps[steps.index(publish[0]) + 1:]
    assert not any("gh release" in str(s.get("run") or "") for s in later)


def test_negative_control_the_old_publish_step_fails() -> None:
    problems = _publish_problems(_OLD_PUBLISH)
    assert len(problems) == 4, problems


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


def _order_problems(steps: list[dict[str, Any]]) -> list[str]:
    """Checksums are taken before any other tool runs and checked before signing.

    The SBOM generator is fetched unpinned at run time, after the build and
    before the signature; with no checksum taken at build time it could rewrite
    the wheel that then gets attested and signed.
    """
    runs = [str(s.get("run") or "") for s in steps]
    uses = [str(s.get("uses") or "") for s in steps]
    problems: list[str] = []

    def first(pred) -> int | None:
        return next((i for i in range(len(steps)) if pred(runs[i], uses[i])), None)

    build = first(lambda r, u: "uv build" in r)
    sbom = first(lambda r, u: "cyclonedx-py" in r)
    attest = first(lambda r, u: u.startswith("actions/attest-build-provenance@"))
    sign = first(lambda r, u: u.startswith("sigstore/gh-action-sigstore-python@"))
    publish = first(lambda r, u: "gh release create" in r)
    if None in (build, sbom, attest, sign, publish):
        return ["a supply-chain step is missing"]
    if "sha256sum dist/" not in runs[build]:
        problems.append("the built files are not checksummed in the build step")
    if "sha256sum -c" not in runs[sbom]:
        problems.append("the files are not checked after the SBOM tool ran")
    if "sha256sum -c" not in "\n".join(runs[sbom + 1 : attest]):
        problems.append("the files are not checked before attestation")
    if "sha256sum -c" not in runs[publish]:
        problems.append("the files are not checked before the release is created")
    # An interpolated expression inside a script is an injection sink: use env.
    for i, run in enumerate(runs):
        if "${{" in run:
            problems.append(f"step {steps[i].get('name')!r} interpolates an expression into its script")
    return problems


def test_checksums_are_taken_at_build_and_checked_before_signing_and_publishing(
    steps: list[dict[str, Any]],
) -> None:
    assert _order_problems(steps) == []
    run_texts = [str(s.get("run") or "") for s in steps]
    build = next(i for i, r in enumerate(run_texts) if "uv build" in r)
    sbom = next(i for i, r in enumerate(run_texts) if "cyclonedx-py" in r)
    assert build < sbom, "the checksums must exist before the SBOM tool runs"
    # The SBOM's own checksum joins the list after it is generated.
    assert ">> build-artifacts.sha256" in run_texts[sbom]


def test_negative_control_the_old_order_fails() -> None:
    old = [
        {"name": "Build", "run": "uv build --out-dir dist\nls -la dist"},
        {"name": "SBOM", "run": 'uvx --from cyclonedx-bom cyclonedx-py requirements r.txt -o "dist/kazma-${{ steps.ver.outputs.base }}.cdx.json"'},
        {"name": "Checksums", "run": "cd dist\nsha256sum kazma-* > SHA256SUMS"},
        {"name": "Attest", "uses": "actions/attest-build-provenance@abc # v2"},
        {"name": "Sign", "uses": "sigstore/gh-action-sigstore-python@abc # v3"},
        {"name": "Publish", "run": 'gh release create "$TAG" --draft dist/*'},
    ]
    problems = _order_problems(old)
    assert len(problems) == 5, problems
