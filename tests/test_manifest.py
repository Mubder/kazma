"""Tests for SkillManifest — Arabic context manifest loading."""

from __future__ import annotations

from pathlib import Path

import yaml
from kazma_skills.manifest import SkillManifest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(tmp_path: Path, tools: dict | None = None) -> SkillManifest:
    """Create a SkillManifest from a temp YAML file."""
    config = tmp_path / "manifest.yaml"
    data = {"tools": tools or {}}
    config.write_text(yaml.dump(data))
    return SkillManifest(manifest_path=config)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestManifestConstruction:
    def test_empty_manifest(self, tmp_path: Path) -> None:
        sm = _make_manifest(tmp_path)
        assert sm.list_tools() == []

    def test_loads_tools(self, tmp_path: Path) -> None:
        tools = {
            "web_search": {
                "arabic_name": "بحث الويب",
                "prompt_chain": ["ابحث في الإنترنت"],
                "cultural_context": {"direction": "rtl"},
                "description": "Search the web",
                "certified": True,
            }
        }
        sm = _make_manifest(tmp_path, tools)
        assert sm.list_tools() == ["web_search"]


# ---------------------------------------------------------------------------
# Arabic features
# ---------------------------------------------------------------------------


class TestArabicFeatures:
    def test_get_arabic_name(self, tmp_path: Path) -> None:
        tools = {"foo": {"arabic_name": "أداة"}}
        sm = _make_manifest(tmp_path, tools)
        assert sm.get_arabic_name("foo") == "أداة"

    def test_get_arabic_prompt(self, tmp_path: Path) -> None:
        tools = {
            "bar": {
                "prompt_chain": ["خطوة 1", "خطوة 2"],
            }
        }
        sm = _make_manifest(tmp_path, tools)
        prompt = sm.get_arabic_prompt("bar")
        assert "خطوة 1" in prompt
        assert "خطوة 2" in prompt

    def test_get_cultural_context(self, tmp_path: Path) -> None:
        tools = {
            "baz": {
                "cultural_context": {
                    "direction": "rtl",
                    "number_format": "arabic-indic",
                    "date_format": "hijri",
                },
            }
        }
        sm = _make_manifest(tmp_path, tools)
        ctx = sm.get_cultural_context("baz")
        assert ctx["direction"] == "rtl"
        assert ctx["number_format"] == "arabic-indic"
        assert ctx["date_format"] == "hijri"


# ---------------------------------------------------------------------------
# Certified tools
# ---------------------------------------------------------------------------


class TestCertification:
    def test_is_certified(self, tmp_path: Path) -> None:
        tools = {
            "good_tool": {"certified": True},
            "bad_tool": {"certified": False},
        }
        sm = _make_manifest(tmp_path, tools)
        assert sm.is_certified("good_tool")
        assert not sm.is_certified("bad_tool")

    def test_list_certified(self, tmp_path: Path) -> None:
        tools = {
            "a": {"certified": True},
            "b": {"certified": False},
            "c": {"certified": True},
        }
        sm = _make_manifest(tmp_path, tools)
        certified = sm.list_certified()
        assert "a" in certified
        assert "c" in certified
        assert "b" not in certified


# ---------------------------------------------------------------------------
# Missing / unknown tools
# ---------------------------------------------------------------------------


class TestMissingTools:
    def test_unknown_tool_returns_empty(self, tmp_path: Path) -> None:
        sm = _make_manifest(tmp_path)
        assert sm.get_arabic_prompt("nonexistent") == ""
        assert sm.get_arabic_name("nonexistent") == "nonexistent"
        assert sm.get_cultural_context("nonexistent") == {}
        assert sm.get_description("nonexistent") == ""
        assert not sm.is_certified("nonexistent")


# ---------------------------------------------------------------------------
# add_tool / save
# ---------------------------------------------------------------------------


class TestAddAndSave:
    def test_add_tool(self, tmp_path: Path) -> None:
        sm = _make_manifest(tmp_path)
        sm.add_tool(
            "new_tool",
            arabic_name="أداة جديدة",
            prompt_chain=["مرحبا"],
            description="A new tool",
            certified=True,
        )
        assert "new_tool" in sm.list_tools()
        assert sm.get_arabic_name("new_tool") == "أداة جديدة"
        assert sm.is_certified("new_tool")

    def test_save_persists(self, tmp_path: Path) -> None:
        config = tmp_path / "manifest.yaml"
        sm = SkillManifest(manifest_path=config)
        sm.add_tool("saved_tool", arabic_name="محفوظة")
        sm.save()

        # Reload
        sm2 = SkillManifest(manifest_path=config)
        assert "saved_tool" in sm2.list_tools()
        assert sm2.get_arabic_name("saved_tool") == "محفوظة"
