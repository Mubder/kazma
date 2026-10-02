"""Every surface shows the integrity verdict activation gives a skill.

Live 2026-10-02 nine agent skills in the shared ``~/.agents/skills`` folder
were refused at activation: another key had signed them (the content still
matched its checksum). The model's catalog offered them as
``integrity="verified"`` (any recorded checksum counted), ``kazma
agent-skills list`` said "verified", and the Skills page showed them enabled
with an invented "100/100" -- a score no code computes, on every skill.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from kazma_core.agent_skills import catalog, discovery, integrity

_SECRET = "test-signing-secret-0123456789abcdef"


def _skill(root: Path, name: str, *, signed_with: str | None) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    text = f"---\nname: {name}\ndescription: The {name} skill.\n---\nDo the {name} thing.\n"
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    if signed_with is not None:
        integrity.write_install_meta(folder, {
            "source": "owner/repo", **integrity.compute_skill_signature(text, secret=signed_with),
        })
    return folder


@pytest.fixture
def skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Three agent skills: signed by this install, unsigned, signed by another key."""
    base = tmp_path / "agent-skills"
    _skill(base, "ours", signed_with=_SECRET)
    _skill(base, "plain", signed_with=None)
    _skill(base, "elsewhere", signed_with="another-install-secret-zz")
    monkeypatch.setattr(integrity, "_get_secret", lambda: _SECRET)
    monkeypatch.setattr(discovery, "skill_base_dirs", lambda **_: [("user", base)])
    return base


def test_the_verdict_is_activation_s(skills: Path) -> None:
    found = discovery.discover_skills(include_disabled=True)
    states = {name: catalog.skill_integrity(s) for name, s in found.items()}
    assert {n: st for n, (st, _why) in states.items()} == {
        "ours": "verified", "plain": "unsigned", "elsewhere": "refused",
    }
    assert "kazma agent-skills sign" in states["elsewhere"][1]
    # Activation refuses what the verdict calls refused, and loads the rest.
    assert "Refusing to load the skill body" in catalog.format_skill_activation(found["elsewhere"])
    assert "Refusing" not in catalog.format_skill_activation(found["ours"])


def test_the_model_is_not_offered_a_refused_skill(skills: Path) -> None:
    prompt = catalog.build_catalog_prompt()
    listed = prompt.split("</available_skills>", 1)[0]
    assert '<skill integrity="verified">\n    <name>ours</name>' in listed
    assert '<skill integrity="unsigned">\n    <name>plain</name>' in listed
    assert "<name>elsewhere</name>" not in listed
    assert "refused at activation, so not listed: elsewhere" in prompt

    # Negative control: the old rule called any recorded checksum "verified".
    refused = discovery.discover_skills()["elsewhere"]
    old_attribute = ' integrity="verified"' if refused.checksum else ' integrity="unsigned"'
    assert old_attribute == ' integrity="verified"'


def test_showing_a_verdict_logs_nothing_and_activation_still_warns(
    skills: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The catalog is built on every turn: each refused skill logged a WARNING
    every time (live 2026-10-02: 30 in two hours for one skill)."""
    with caplog.at_level(logging.DEBUG, logger="kazma_core.agent_skills.integrity"):
        catalog.build_catalog_prompt()
        catalog.build_catalog_prompt()
        catalog.list_skill_summaries()
        shown = [r for r in caplog.records if r.levelno >= logging.WARNING]
        caplog.clear()
        refused = discovery.discover_skills()["elsewhere"]
        catalog.format_skill_activation(refused)
        activated = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert shown == []
    assert len(activated) == 1 and "elsewhere" in activated[0].getMessage()


def test_the_list_tool_and_summaries_carry_the_verdict(skills: Path) -> None:
    from kazma_core.agent_skills.tools import list_agent_skills

    summaries = {s["name"]: s for s in catalog.list_skill_summaries()}
    assert (summaries["elsewhere"]["integrity"], summaries["ours"]["integrity"]) == ("refused", "verified")
    text = asyncio.run(list_agent_skills())
    assert "**elsewhere** [user, refused]" in text and "Refused at activation:" in text
    assert "**plain** [user, unsigned]" in text


def test_the_cli_lists_the_verdict_and_signs_several(skills: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from kazma_cli.main import _run_agent_skills

    _run_agent_skills(["list"])
    out = capsys.readouterr().out
    rows = {line.split()[0]: line.split()[2] for line in out.splitlines() if line.split()[:1] in (["ours"], ["plain"], ["elsewhere"])}
    assert rows == {"ours": "verified", "plain": "unsigned", "elsewhere": "refused"}
    assert "elsewhere: refused at activation:" in out

    # The owner vouches for both at once; activation then takes them. A
    # folder with no skill between them is named, and does not stop the rest.
    missing = skills / "no-such-skill"
    with pytest.raises(SystemExit) as exited:
        _run_agent_skills(["sign", str(skills / "elsewhere"), str(missing), str(skills / "plain")])
    assert exited.value.code == 1
    assert f"Not signed: {missing}" in capsys.readouterr().out
    found = discovery.discover_skills()
    assert catalog.skill_integrity(found["elsewhere"])[0] == "verified"
    assert catalog.skill_integrity(found["plain"])[0] == "verified"


def test_the_skills_page_shows_the_verdict_and_no_invented_score(
    skills: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_ui.skills_ui import create_skills_router

    class _NoHub:
        async def list_installed(self) -> list[Any]:
            return []

        async def close(self) -> None:
            return None

    monkeypatch.setattr("kazma_core.hub.registry.KazmaHub", _NoHub)
    app = FastAPI()
    app.include_router(create_skills_router(MagicMock(), Jinja2Templates(directory=str(tmp_path))))
    listed = TestClient(app).get("/api/skills").json()
    entries = listed if isinstance(listed, list) else listed.get("skills", listed)
    by_name = {e["name"]: e for e in entries}

    assert by_name["elsewhere"]["integrity"] == "refused"
    assert "kazma agent-skills sign" in by_name["elsewhere"]["integrity_reason"]
    assert by_name["ours"]["integrity"] == "verified"
    # No skill carries a score nobody computed: no built-in manifest declares one.
    assert all(e["security_score"] is None for e in entries), [
        (e["name"], e["security_score"]) for e in entries if e["security_score"] is not None
    ]
    assert any(e.get("builtin") for e in entries)  # the built-in skills are still listed


def test_the_page_renders_the_badge_only_where_there_is_a_verdict() -> None:
    template = (Path(__file__).resolve().parents[1] / "kazma-ui/kazma_ui/templates/skills.html").read_text(encoding="utf-8")
    assert "{% if skill.security_score is not none %}" in template
    assert "t('skills.integrity_' ~ skill.integrity)" in template
    # The reason is the server's words: shown for a refused skill, untranslated
    # (an English tooltip on Arabic pages failed the Arabic page tour).
    assert 'translate="no">{{ skill.integrity_reason }}</p>' in template
    assert 'title="{{ skill.integrity_reason }}"' not in template
    from kazma_ui.i18n.catalog.common import TRANSLATIONS

    for state in ("verified", "unsigned", "refused"):
        entry = TRANSLATIONS[f"skills.integrity_{state}"]
        assert entry["en"] and entry["ar"]
