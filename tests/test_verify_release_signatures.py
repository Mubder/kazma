"""scripts/verify_release_signatures.py: the release workflow's last check
before it publishes is the updater's own rule (2026-11-11).

Recorded, real material (``tests/fixtures/release_signature/``, no network):
Kazma v0.11.0's ``SHA256SUMS`` and the bundle the release workflow made for
it, another project's valid bundle, and Sigstore's root of trust.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sigstore")

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "release_signature"
SCRIPT = ROOT / "scripts" / "verify_release_signatures.py"


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("_verify_release_signatures", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _recorded_verifier() -> Any:
    from sigstore.models import TrustedRoot
    from sigstore.verify import Verifier

    return Verifier(trusted_root=TrustedRoot.from_file(str(FIXTURES / "trusted_root.json")))


def _release(folder: Path, *, bundle: str = "kazma-0.11.0-SHA256SUMS.sigstore.json") -> Path:
    sums = folder / "SHA256SUMS"
    shutil.copyfile(FIXTURES / "kazma-0.11.0-SHA256SUMS", sums)
    shutil.copyfile(FIXTURES / bundle, folder / "SHA256SUMS.sigstore.json")
    return sums


def test_a_file_the_release_workflow_signed_passes(tmp_path, capsys):
    sums = _release(tmp_path)
    assert _script().check_files([sums], verifier=_recorded_verifier()) == []
    assert "signed by https://github.com/Mubder/kazma/.github/workflows/release.yml@" in capsys.readouterr().out


def test_a_changed_file_is_refused(tmp_path):
    sums = _release(tmp_path)
    sums.write_bytes(sums.read_bytes() + b"\n")
    problems = _script().check_files([sums], verifier=_recorded_verifier())
    assert len(problems) == 1 and problems[0].startswith("SHA256SUMS: refused"), problems


def test_another_signers_bundle_and_a_missing_bundle_are_refused(tmp_path):
    sums = _release(tmp_path, bundle="rfc8785-0.1.4-py3-none-any.whl.sigstore.json")
    problems = _script().check_files([sums], verifier=_recorded_verifier())
    assert len(problems) == 1 and "refused" in problems[0], problems
    (tmp_path / "SHA256SUMS.sigstore.json").unlink()
    assert _script().check_files([sums], verifier=_recorded_verifier()) == [
        "SHA256SUMS: no SHA256SUMS.sigstore.json"
    ]


def test_a_folder_without_a_wheel_or_sums_fails(tmp_path, capsys):
    assert _script().main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "no kazma wheel" in out and "no SHA256SUMS" in out


def test_it_checks_every_file_a_release_publishes(tmp_path):
    for name in ("kazma-1.0.0-py3-none-any.whl", "kazma-1.0.0.tar.gz", "kazma-1.0.0.cdx.json",
                 "SHA256SUMS", "kazma-1.0.0-py3-none-any.whl.sigstore.json", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    assert sorted(p.name for p in _script().published_files(tmp_path)) == [
        "SHA256SUMS", "kazma-1.0.0-py3-none-any.whl", "kazma-1.0.0.cdx.json", "kazma-1.0.0.tar.gz",
    ]
