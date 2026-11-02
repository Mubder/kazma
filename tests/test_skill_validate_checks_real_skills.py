"""The Skills page's Validate checks a skill the way Kazma loads it (FA-249).

The 2026-11 functional audit, on live: ``POST /api/skills/validate`` ran the
retired hub validator (``skill_manifest.yaml``) on a path resolved against
the server's working directory. Every skill the install had failed ("Missing
skill_manifest.yaml") or was refused, a folder that did not exist scored
70/100, and the refusals came back 200 with an ``error`` field the tab did
not show.

Through the route: every skill discovery finds on a seeded install passes (by
name and by folder), a built-in skill is checked by its loader, a missing
folder is a 404, a folder outside the skill folders a 400. The negative
control is the hub validator on the same skill.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

SKILL = """---
name: fa249-demo
description: A skill the Validate tab must recognise.
---
Say hello.
"""


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    """A user skill in the install's skill folder (``skills_home``)."""
    home = tmp_path / "home"
    folder = home / ".agents" / "skills" / "fa249-demo"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL, encoding="utf-8")
    monkeypatch.setenv("KAZMA_SKILLS_HOME", str(home))
    return folder


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_ui.skills_ui import create_skills_router

    app = FastAPI()
    from types import SimpleNamespace

    app.include_router(create_skills_router(SimpleNamespace(), Jinja2Templates(directory=".")))
    return TestClient(app)


def _validate(client, ref: str):
    return client.post("/api/skills/validate", json={"path": ref})


def test_every_discovered_skill_passes(client, seeded):
    from kazma_core.agent_skills.discovery import discover_skills

    skills = discover_skills(include_disabled=True)
    assert "fa249-demo" in skills
    for name, skill in skills.items():
        for ref in (name, str(skill.location.parent)):
            resp = _validate(client, ref)
            assert resp.status_code == 200, (ref, resp.text)
            body = resp.json()
            assert body["name"] == name and body["state"] in ("verified", "unsigned"), body
            assert body["passed"] is True, body
            assert "score" not in body


def test_a_relative_folder_is_read_in_the_skill_folders(client, seeded):
    body = _validate(client, "fa249-demo").json()
    assert body["location"] == str(seeded.resolve())


def test_a_built_in_skill_is_checked_by_its_loader(client):
    from kazma_skills.native_loader import NativeSkillLoader

    folder = NativeSkillLoader(registry=None).skill_dirs()[0]
    body = _validate(client, f"native:{folder.name}").json()
    assert body["kind"] == "native" and body["state"] == "built_in" and body["passed"] is True, body


def test_a_missing_folder_and_a_folder_outside_are_refused(client, seeded, tmp_path):
    missing = _validate(client, "fa249-no-such-skill")
    assert missing.status_code == 404 and missing.json()["error"] == "not_found"
    outside = _validate(client, str(tmp_path))
    assert outside.status_code == 400 and outside.json()["error"] == "outside_skill_folders"
    empty = _validate(client, "  ")
    assert empty.status_code == 400 and empty.json()["error"] == "no_skill"


def test_a_tampered_skill_is_refused(client, seeded):
    """An installed skill whose checksum no longer matches is what activation
    refuses; so does the check."""
    from kazma_core.agent_skills.integrity import compute_skill_signature, write_install_meta

    write_install_meta(seeded, compute_skill_signature(SKILL, secret="fa249-test-secret"))
    (seeded / "SKILL.md").write_text(SKILL + "\nAnd do something else.\n", encoding="utf-8")
    body = _validate(client, "fa249-demo").json()
    assert body["state"] == "refused" and body["passed"] is False, body


def test_the_hub_validator_failed_every_real_skill(seeded):
    """Negative control: the validator the route ran, on the same skill."""
    from kazma_core.hub.validator import SkillValidator

    result = asyncio.run(SkillValidator().validate(seeded))
    assert result.passed is False and any("skill_manifest" in e for e in result.errors)
    missing = asyncio.run(SkillValidator().validate(Path(seeded.parent / "fa249-missing")))
    assert missing.score > 0  # a folder that is not there scored
