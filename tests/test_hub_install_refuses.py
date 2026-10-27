"""Installing a hub skill is refused, never claimed (audit 2026-10-26 AUD-074).

``KazmaHub.install`` was a stub: it created an empty folder under the skills
directory, marked the skill installed and returned the folder, so
``kazma hub install`` printed "Installed X to <folder>", the Skills page
answered ``{"status": "ok"}`` and the wizard printed "Installation Complete!"
over a skill with no files and no tools (AGENTS.md §22G: hub skills register
no tools -- not built). Its id was checked only for ``/``, so a name of
``..`` made the folder outside the skills directory.

Every surface now refuses with "not implemented", and an id whose author or
name is more than one path segment is invalid. The negative controls run the
stub and the old id pattern.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from kazma_core.hub.manifest_schema import SkillManifest

SKILL = {
    "name": "weather-report",
    "author": "alice",
    "version": "1.0.0",
    "description": "Reports the weather",
    "license": "MIT",
}
SKILL_ID = "kazma-hub://alice/weather-report@1.0.0"


def _registered(db: Path) -> None:
    from kazma_core.hub.registry import KazmaHub

    async def _go() -> None:
        hub = KazmaHub(registry_path=str(db))
        try:
            await hub.register(SkillManifest.from_dict(dict(SKILL)))
        finally:
            await hub.close()

    asyncio.run(_go())


def _installed(db: Path) -> list:
    from kazma_core.hub.registry import KazmaHub

    async def _go() -> list:
        hub = KazmaHub(registry_path=str(db))
        try:
            return await hub.list_installed()
        finally:
            await hub.close()

    return asyncio.run(_go())


def _skills_folder_entries() -> set[str]:
    from kazma_core.paths import installed_skills_dir

    return {p.name for p in installed_skills_dir().iterdir()}


# ── the CLI ──────────────────────────────────────────────────────────────


def test_hub_install_says_not_implemented_and_installs_nothing(tmp_path: Path) -> None:
    from kazma_core.hub.cli import hub

    db = tmp_path / "registry.db"
    _registered(db)
    before = _skills_folder_entries()

    result = CliRunner().invoke(hub, ["--registry-path", str(db), "install", SKILL_ID])

    assert result.exit_code == 2, result.output
    assert "not implemented" in result.output
    assert "Installed" not in result.output
    assert _installed(db) == []
    assert _skills_folder_entries() == before


def test_the_old_stub_claimed_an_install(tmp_path: Path) -> None:
    """Negative control: the stub's body as it was marks an empty folder
    installed, which the checks above refuse."""
    from kazma_core.hub import registry

    async def _old_install(hub, skill_id: str) -> Path:
        author, name, version = registry._SKILL_ID_RE.match(skill_id).groups()
        install_path = tmp_path / "skills" / name
        install_path.mkdir(parents=True, exist_ok=True)
        conn = await hub._get_conn()
        await conn.execute(
            "UPDATE skills SET installed_path=? WHERE name=? AND author=? AND version=?",
            (str(install_path), name, author, version),
        )
        return install_path

    async def _go(db: Path) -> Path:
        hub = registry.KazmaHub(registry_path=str(db))
        try:
            return await _old_install(hub, SKILL_ID)
        finally:
            await hub.close()

    db = tmp_path / "registry.db"
    _registered(db)
    folder = asyncio.run(_go(db))
    assert folder.is_dir() and not any(folder.iterdir()), "an empty folder"
    assert [m.data["name"] for m in _installed(db)] == ["weather-report"]
    # And ``..`` as the name put that folder outside the skills directory.
    _author, name, _version = registry._SKILL_ID_RE.match("kazma-hub://alice/..@1.0.0").groups()
    assert (tmp_path / "skills" / name).resolve() == tmp_path.resolve()


# ── one path segment ─────────────────────────────────────────────────────

_ESCAPING_IDS = (
    "kazma-hub://alice/..@1.0.0",
    "kazma-hub://../weather-report@1.0.0",
    "kazma-hub://alice/..\\..\\outside@1.0.0",
    "kazma-hub://alice/C:evil@1.0.0",
    "kazma-hub://alice/.@1.0.0",
)


@pytest.mark.parametrize("skill_id", _ESCAPING_IDS)
def test_an_id_naming_more_than_one_path_segment_is_invalid(skill_id: str) -> None:
    from kazma_core.hub.registry import _parse_skill_id

    with pytest.raises(ValueError):
        _parse_skill_id(skill_id)


def test_an_ordinary_id_still_parses() -> None:
    from kazma_core.hub.registry import _parse_skill_id

    assert _parse_skill_id(SKILL_ID) == ("alice", "weather-report", "1.0.0")


def test_the_old_pattern_let_dot_dot_through() -> None:
    """Negative control: the id pattern alone, as the stub used it."""
    old = re.compile(r"^kazma-hub://([^/]+)/([^@]+)@(.+)$")
    assert all(old.match(skill_id) for skill_id in _ESCAPING_IDS)


@pytest.mark.parametrize("skill_id", _ESCAPING_IDS)
def test_the_cli_refuses_an_escaping_id(tmp_path: Path, skill_id: str) -> None:
    from kazma_core.hub.cli import hub

    result = CliRunner().invoke(
        hub, ["--registry-path", str(tmp_path / "registry.db"), "install", skill_id]
    )
    assert result.exit_code == 1
    assert "Invalid skill ID" in result.output


# ── the Skills page and the wizard ───────────────────────────────────────


def test_the_skills_page_answers_not_implemented(tmp_path: Path, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_core.hub import registry
    from kazma_ui.skills_ui import create_skills_router

    db = tmp_path / "registry.db"
    _registered(db)
    real = registry.KazmaHub
    monkeypatch.setattr(registry, "KazmaHub", lambda registry_path=None: real(registry_path=str(db)))

    app = FastAPI()
    app.include_router(create_skills_router(MagicMock(), MagicMock()))
    with patch("kazma_ui.auth.get_kazma_secret", return_value="s"), \
         patch("kazma_ui.auth.is_authenticated", return_value=True), \
         patch("kazma_ui.auth.get_request_principal", return_value={"role": "admin"}):
        resp = TestClient(app).post("/api/skills/install", json={"skill_id": SKILL_ID})

    assert resp.status_code == 501, resp.text
    body = resp.json()
    assert body["status"] == "error" and "not implemented" in body["error"]
    assert _installed(db) == []


def test_the_wizard_stops_instead_of_reporting_success(tmp_path: Path, capsys) -> None:
    from kazma_core.cli.wizard import SkillInstallationWizard

    db = tmp_path / "registry.db"
    _registered(db)
    wizard = SkillInstallationWizard(registry_path=str(db))
    wizard.context.selected_skill = dict(SKILL)

    assert asyncio.run(wizard._step_install()) is False
    out = capsys.readouterr().out
    assert "Not installed" in out and "not implemented" in out
