"""Two unused locals that were hiding real defects (audit 2026-09-22 lint pass).

Ruff's F841 (assigned but never used) now gates CI. Most of the 36 hits were
leftovers; these two were not — the value was computed correctly and then the
code carried on with the wrong one.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


def test_a_json_skill_manifest_supplies_the_skill_id(tmp_path: Path):
    """The manifest was parsed and ignored, so the folder name won."""
    from kazma_core.security.certification import KazmaCertification

    skill = tmp_path / "some-folder-name"
    skill.mkdir()
    (skill / "skill.json").write_text(json.dumps({"name": "weather-report"}), encoding="utf-8")
    assert asyncio.run(KazmaCertification._extract_skill_id(skill)) == "weather-report"

    nameless = tmp_path / "nameless"
    nameless.mkdir()
    (nameless / "skill.json").write_text("{}", encoding="utf-8")
    assert asyncio.run(KazmaCertification._extract_skill_id(nameless)) == "nameless"


@pytest.fixture
def no_vault(monkeypatch: pytest.MonkeyPatch):
    import kazma_skills.native.email_manager.protocol_connect as pc

    monkeypatch.setattr(pc, "vault_store", lambda *a, **k: None)
    return pc


def test_the_validated_imap_port_is_what_gets_written(no_vault, monkeypatch):
    """The port was parsed with int() and then the raw input was written."""
    monkeypatch.delenv("EMAIL_IMAP_PORT", raising=False)
    result = no_vault.connect_protocol(
        provider="gmail", protocol="imap", address="a@example.com",
        password="app-password", imap_port=" 0993 ",  # type: ignore[arg-type]
    )
    assert result["ok"] is True
    import os

    assert os.environ["EMAIL_IMAP_PORT"] == "993"


def test_a_non_numeric_port_is_refused(no_vault):
    with pytest.raises(ValueError):
        no_vault.connect_protocol(
            provider="gmail", protocol="pop", address="a@example.com",
            password="app-password", pop_port="995; rm -rf /",  # type: ignore[arg-type]
        )
