"""Light versioning: patch + commit id; minor needs CONFIRM."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "light_version_bump.py"


def _load():
    spec = importlib.util.spec_from_file_location("light_version_bump", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_strips_local_sha() -> None:
    mod = _load()
    assert mod.parse_base("0.12.1+g92c55af") == (0, 12, 1)
    assert mod.parse_base("0.12.0") == (0, 12, 0)


def test_format_full_embeds_g_sha() -> None:
    mod = _load()
    assert mod.format_full(0, 12, 1, "92c55af") == "0.12.1+g92c55af"
    assert mod.format_full(0, 12, 1, None) == "0.12.1"


def test_bump_triplet_levels() -> None:
    mod = _load()
    assert mod.bump_triplet(0, 12, 1, "patch") == (0, 12, 2)
    assert mod.bump_triplet(0, 12, 5, "minor") == (0, 13, 0)
    assert mod.bump_triplet(0, 12, 5, "major") == (1, 0, 0)


def test_minor_requires_confirm(capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load()
    rc = mod.main(["--level", "minor", "--dry-run"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "CONFIRM" in err


def test_patch_dry_run_ok() -> None:
    mod = _load()
    rc = mod.main(["--level", "patch", "--dry-run"])
    assert rc == 0
