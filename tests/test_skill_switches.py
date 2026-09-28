"""Switching a skill off on the Skills page switches its tools off.

Found on the live install 2026-09-28: the page wrote
``skills.enabled.<id>`` and nothing read it -- a built-in skill switched off
kept all its tools, the page listed every built-in skill as enabled whatever
was saved, and "Uninstall" on a built-in skill answered "not_found" while
the page said "Skill uninstalled".
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.config_store import get_config_store
from kazma_core.skills import switches


@pytest.fixture
def registry() -> LocalToolRegistry:
    reg = LocalToolRegistry(include_builtins=False)

    async def browse(selector: str = "") -> str:
        """Read text from the page."""
        return f"text of {selector or 'body'}"

    async def plain(x: int) -> str:
        """Kazma's own tool."""
        return str(x)

    reg.register_function(name="browser_extract_text", func=browse, description="Read text.", category="browser")
    reg.register_function(name="core_plain", func=plain, description="Plain.", category="general")
    reg._tools["browser_extract_text"].skill_id = "native:browser_automation"
    return reg


def _names(reg: LocalToolRegistry) -> set[str]:
    return {d["function"]["name"] for d in reg.get_tool_definitions()}


async def test_a_switched_off_skill_is_neither_offered_nor_run(registry):
    assert {"browser_extract_text", "core_plain"} <= _names(registry)
    switches.set_skill_enabled("native:browser_automation", False)
    try:
        assert "browser_extract_text" not in _names(registry)
        assert "core_plain" in _names(registry)  # Kazma's own tools are not skills
        out = await registry.execute("browser_extract_text", {"selector": "h1"})
        assert out["is_error"] and "switched off" in out["content"]
    finally:
        switches.set_skill_enabled("native:browser_automation", True)
    assert "browser_extract_text" in _names(registry)
    out = await registry.execute("browser_extract_text", {"selector": "h1"})
    assert not out["is_error"]


def test_the_switch_is_read_from_the_saved_setting(registry):
    """A new process (here: a fresh read) honours what the page saved."""
    get_config_store().set(switches._skill_key("native:browser_automation"), False, category="skills")
    try:
        assert "native:browser_automation" in switches.load_switches()
    finally:
        switches.set_skill_enabled("native:browser_automation", True)


def test_the_old_key_was_written_and_never_read(registry, monkeypatch):
    """Negative control: with the switch module out of the picture (the
    registry as it was), the saved "off" changes nothing."""
    monkeypatch.setattr(switches, "load_switches", lambda: frozenset())
    get_config_store().set(switches._skill_key("native:browser_automation"), False, category="skills")
    try:
        assert "browser_extract_text" in _names(registry)
    finally:
        switches.set_skill_enabled("native:browser_automation", True)


# ── the Skills page ─────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch) -> TestClient:
    from fastapi.templating import Jinja2Templates

    import kazma_ui.i18n  # noqa: F401 -- template globals
    from kazma_ui import skills_ui

    monkeypatch.setattr("kazma_ui.auth.admin_decision", lambda request: "ok")
    app = FastAPI()
    templates = Jinja2Templates(directory="kazma-ui/kazma_ui/templates")
    app.include_router(skills_ui.create_skills_router(agent=None, templates=templates))
    return TestClient(app)


def test_the_page_toggle_is_applied_and_listed(client):
    resp = client.post("/api/skills/toggle", json={"skill_id": "native:browser_automation", "enabled": False})
    assert resp.status_code == 200 and resp.json()["status"] == "ok"
    try:
        assert "native:browser_automation" in switches.load_switches()
        listed = {s["id"]: s for s in client.get("/api/skills").json()}
        entry = listed.get("native:browser_automation")
        assert entry is not None and entry["enabled"] is False and entry["builtin"] is True
    finally:
        client.post("/api/skills/toggle", json={"skill_id": "native:browser_automation", "enabled": True})


def test_a_builtin_skill_cannot_be_uninstalled(client):
    resp = client.post("/api/skills/uninstall", json={"skill_id": "native:browser_automation"})
    assert resp.status_code == 400
    assert "switch the skill off" in resp.json()["error"]
