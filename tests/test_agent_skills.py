"""Tests for Agent Skills (agentskills.io / SKILL.md) support."""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.agent_skills.catalog import build_catalog_prompt, format_skill_activation
from kazma_core.agent_skills.discovery import discover_skills, get_skill
from kazma_core.agent_skills.installer import (
    install_from_source,
    parse_github_source,
    uninstall_skill,
)
from kazma_core.agent_skills.parser import parse_skill_md


SAMPLE_SKILL_MD = """\
---
name: improve
description: Survey any codebase and produce prioritized implementation plans. Use when auditing a codebase.
license: MIT
metadata:
  author: shadcn
  version: "1.0.0"
---

# Improve

You are a senior advisor, not an implementer.

See [references/playbook.md](references/playbook.md).
"""


def _write_skill(root: Path, name: str = "improve", body: str | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body or SAMPLE_SKILL_MD, encoding="utf-8")
    refs = skill_dir / "references"
    refs.mkdir(exist_ok=True)
    (refs / "playbook.md").write_text("# Playbook\n", encoding="utf-8")
    return skill_dir


class TestParseSkillMd:
    def test_parses_frontmatter_and_body(self):
        parsed = parse_skill_md(SAMPLE_SKILL_MD, directory_name="improve")
        assert parsed is not None
        assert parsed.name == "improve"
        assert "audit" in parsed.description.lower() or "codebase" in parsed.description.lower()
        assert "senior advisor" in parsed.body
        assert parsed.author == "shadcn"
        assert parsed.version == "1.0.0"
        assert parsed.license == "MIT"

    def test_missing_description_returns_none(self):
        bad = "---\nname: x\n---\n\nbody\n"
        assert parse_skill_md(bad) is None

    def test_lenient_colon_in_description(self):
        text = (
            "---\n"
            "name: pdf\n"
            "description: Use when: the user asks about PDFs\n"
            "---\n\n# PDF\n"
        )
        parsed = parse_skill_md(text, directory_name="pdf")
        assert parsed is not None
        assert "PDFs" in parsed.description


class TestParseGithubSource:
    def test_owner_repo(self):
        p = parse_github_source("shadcn/improve")
        assert p == {"owner": "shadcn", "repo": "improve", "ref": "", "subpath": ""}

    def test_https_url(self):
        p = parse_github_source("https://github.com/shadcn/improve")
        assert p is not None
        assert p["owner"] == "shadcn"
        assert p["repo"] == "improve"

    def test_url_with_tree_path(self):
        p = parse_github_source(
            "https://github.com/shadcn/improve/tree/main/skills/improve"
        )
        assert p is not None
        assert p["ref"] == "main"
        assert p["subpath"] == "skills/improve"

    def test_npx_phrase(self):
        p = parse_github_source("npx skills add shadcn/improve")
        assert p is not None
        assert p["owner"] == "shadcn"
        assert p["repo"] == "improve"

    def test_invalid(self):
        assert parse_github_source("not a source") is None


class TestDiscoveryAndInstall:
    def test_discover_local(self, tmp_path: Path):
        skills_root = tmp_path / ".agents" / "skills"
        _write_skill(skills_root)
        found = discover_skills(project_root=tmp_path)
        assert "improve" in found
        assert found["improve"].scope == "project"
        assert found["improve"].location.name == "SKILL.md"

    def test_install_from_local_source(self, tmp_path: Path):
        src = _write_skill(tmp_path / "src", name="improve")
        dest = tmp_path / "dest"
        result = install_from_source(src, target_dir=dest)
        assert result.success
        assert len(result.installed) == 1
        assert result.installed[0]["name"] == "improve"
        assert (dest / "improve" / "SKILL.md").is_file()
        assert (dest / "improve" / "references" / "playbook.md").is_file()
        assert (dest / "improve" / ".kazma-install.json").is_file()

    def test_uninstall(self, tmp_path: Path):
        src = _write_skill(tmp_path / "src")
        dest = tmp_path / "dest"
        install_from_source(src, target_dir=dest)
        result = uninstall_skill("improve", target_dir=dest)
        assert result.success
        assert not (dest / "improve").exists()

    def test_catalog_prompt(self, tmp_path: Path):
        _write_skill(tmp_path / ".agents" / "skills")
        prompt = build_catalog_prompt(project_root=tmp_path)
        assert "<available_skills>" in prompt
        assert "improve" in prompt
        assert "activate_skill" in prompt

    def test_empty_catalog(self, tmp_path: Path):
        empty = tmp_path / "empty-proj"
        empty.mkdir()
        # Use a root with no skills dirs that exist as skill containers
        prompt = build_catalog_prompt(project_root=empty, workspace_root=empty)
        # May still pick up user-level skills on the machine — just ensure no crash
        assert isinstance(prompt, str)

    def test_activation_lists_resources(self, tmp_path: Path):
        _write_skill(tmp_path / ".agents" / "skills")
        skill = get_skill("improve", project_root=tmp_path)
        assert skill is not None
        text = format_skill_activation(skill)
        assert "<skill_content" in text
        assert "references/playbook.md" in text
        assert "senior advisor" in text


class TestTools:
    @pytest.mark.asyncio
    async def test_list_and_activate_tools(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        skills_root = tmp_path / ".agents" / "skills"
        _write_skill(skills_root)

        # Point discovery at tmp project
        from kazma_core.agent_skills import tools as tools_mod
        from kazma_core.agent_skills import discovery as disc

        monkeypatch.setattr(
            tools_mod,
            "_workspace_root",
            lambda: tmp_path,
        )
        # discover uses project_root from paths — inject via workspace
        monkeypatch.setattr(
            "kazma_core.agent_skills.discovery.skill_base_dirs",
            lambda **kw: [("project", skills_root)],
        )

        listed = await tools_mod.list_agent_skills()
        assert "improve" in listed

        activated = await tools_mod.activate_skill("improve")
        assert "senior advisor" in activated

        missing = await tools_mod.activate_skill("nope")
        assert "not found" in missing.lower()


class TestHitlWiring:
    def test_install_agent_skill_is_danger(self):
        from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS, get_tool_tier

        assert "install_agent_skill" in CANONICAL_DANGER_TOOLS
        assert "uninstall_agent_skill" in CANONICAL_DANGER_TOOLS
        assert get_tool_tier("install_agent_skill") == "danger"

    def test_yaml_parity(self):
        from pathlib import Path

        import yaml

        from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS

        root = Path(__file__).resolve().parents[1]
        data = yaml.safe_load((root / "kazma.yaml").read_text(encoding="utf-8"))
        listed = set(data["safety"]["hitl"]["require_approval_for"])
        assert listed == set(CANONICAL_DANGER_TOOLS)


class TestRegistryRegistration:
    def test_tools_registered(self):
        from kazma_core.agent.tool_registry import LocalToolRegistry

        reg = LocalToolRegistry(include_builtins=True)
        names = set(reg._tools.keys())
        for n in (
            "list_agent_skills",
            "activate_skill",
            "install_agent_skill",
            "uninstall_agent_skill",
        ):
            assert n in names, f"{n} not registered"


@pytest.mark.asyncio
async def test_live_install_shadcn_improve(tmp_path: Path):
    """Integration: download real shadcn/improve from GitHub (network)."""
    from kazma_core.agent_skills.installer import install_from_github

    result = await install_from_github("shadcn/improve", target_dir=tmp_path)
    if not result.success and any(
        "download" in e.lower() or "Could not" in e for e in result.errors
    ):
        pytest.skip(f"Network unavailable: {result.errors}")

    assert result.success, result.errors
    assert any(i["name"] == "improve" for i in result.installed)
    assert (tmp_path / "improve" / "SKILL.md").is_file()
    text = (tmp_path / "improve" / "SKILL.md").read_text(encoding="utf-8")
    assert "improve" in text.lower()
