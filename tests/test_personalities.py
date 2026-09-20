"""Tests for agent personality templates and /personality slash command.

Covers:
  - Loading default personality
  - Loading from kazma.yaml config
  - Loading from KAZMA_PERSONALITY env var
  - Personality injected as system message at position 0
  - /personality list shows all templates
  - /personality <name> switches and persists
  - Priority chain: config > env > default
"""

from __future__ import annotations

import pytest
from kazma_core.personalities import (
    PERSONALITIES,
    list_personalities,
    load_personality,
    reset_runtime_personality,
    set_runtime_personality,
)
from kazma_core.tools.personality_cmd import (
    handle_personality_command,
    is_personality_command,
)


@pytest.fixture(autouse=True)
def _clean_runtime_override():
    """Ensure no runtime personality leaks between tests."""
    reset_runtime_personality()
    yield
    reset_runtime_personality()


class TestPersonalityLoading:
    """Tests for the personality loading priority chain."""

    def test_load_default_personality(self):
        """load_personality with no config or env returns the 'default' profile."""
        profile = load_personality(config=None, env={})
        assert profile.name == "default"
        assert "professional" in profile.system_prompt.lower()
        assert profile.emoji == "🤖"

    def test_load_personality_from_config(self):
        """kazma.yaml agent.personality override takes effect."""
        config = {"agent": {"personality": "concise"}}
        profile = load_personality(config=config, env={})
        assert profile.name == "concise"
        assert "bullet" in profile.system_prompt.lower()

    def test_load_personality_from_env(self):
        """KAZMA_PERSONALITY env var is respected when no config override."""
        profile = load_personality(config=None, env={"KAZMA_PERSONALITY": "sysadmin"})
        assert profile.name == "sysadmin"
        assert "shell" in profile.system_prompt.lower()

    def test_priority_config_over_env(self):
        """Config > env > default priority chain."""
        config = {"agent": {"personality": "teacher"}}
        env = {"KAZMA_PERSONALITY": "sysadmin"}
        profile = load_personality(config=config, env=env)
        assert profile.name == "teacher", "Config should win over env"

    def test_priority_runtime_over_config(self):
        """Runtime override (from /personality command) wins over everything."""
        set_runtime_personality("creative_partner")
        config = {"agent": {"personality": "concise"}}
        profile = load_personality(config=config, env={"KAZMA_PERSONALITY": "sysadmin"})
        assert profile.name == "creative_partner", "Runtime override should win"

    def test_invalid_config_personality_falls_back(self):
        """Unknown personality in config falls back gracefully."""
        config = {"agent": {"personality": "nonexistent"}}
        profile = load_personality(config=config, env={})
        assert profile.name == "default"

    def test_all_eight_templates_present(self):
        """All 8 specified templates are registered."""
        expected = {
            "default", "friendly_expert", "concise", "gulf_engineer",
            "creative_partner", "sysadmin", "teacher", "code_reviewer",
        }
        assert expected == set(PERSONALITIES.keys()), \
            f"Missing: {expected - set(PERSONALITIES.keys())}"

    def test_list_personalities_returns_sorted(self):
        """list_personalities returns all entries sorted by name."""
        all_p = list_personalities()
        names = [p.name for p in all_p]
        assert names == sorted(names)
        assert len(all_p) >= 8


class TestPersonalityTemplateStructure:
    """Verify each template has the required dict keys."""

    @pytest.mark.parametrize("name", [
        "default", "friendly_expert", "concise", "gulf_engineer",
        "creative_partner", "sysadmin", "teacher", "code_reviewer",
    ])
    def test_template_has_required_keys(self, name):
        p = PERSONALITIES[name]
        assert "name" in p
        assert "system_prompt" in p
        assert "description" in p
        assert "emoji" in p
        assert len(p["system_prompt"]) > 20, "system_prompt should be substantive"

    def test_gulf_engineer_has_arabic(self):
        """gulf_engineer should reference Gulf Arabic phrases."""
        p = PERSONALITIES["gulf_engineer"]
        assert "عربية" in p.system_prompt or "يالله" in p.system_prompt or "خلاص" in p.system_prompt


class TestPersonalitySystemMessageInjection:
    """Test personality injection into the message list at position 0."""

    def test_personality_injected_as_system_message(self):
        """Personality system_prompt is injected as a system message at position 0..1."""
        from kazma_core.agent.graph_builder import _ensure_personality

        base_prompt = "You are Kazma."
        personality_prompt = "Be concise and direct."
        messages = [{"role": "user", "content": "Hello"}]

        result = _ensure_personality(messages, base_prompt, personality_prompt)

        # Base system prompt at position 0
        assert result[0]["role"] == "system"
        assert base_prompt in result[0]["content"]

        # Personality at position 1
        assert result[1]["role"] == "system"
        assert personality_prompt in result[1]["content"]

        # Original message preserved after
        assert result[2]["role"] == "user"

    def test_personality_replaces_stale_on_switch(self):
        """Switching personality replaces the old personality message, not appends."""
        from kazma_core.agent.graph_builder import _ensure_personality

        base_prompt = "You are Kazma."
        old_personality = "Be concise."
        new_personality = "Be verbose and detailed."

        # First injection
        messages = [{"role": "user", "content": "Hi"}]
        messages = _ensure_personality(messages, base_prompt, old_personality)
        assert len(messages) == 3  # base + personality + user

        # Switch personality — should replace, not append
        messages = _ensure_personality(messages, base_prompt, new_personality)
        assert len(messages) == 3, "Should still be 3 — old personality replaced"

        # New personality should be present, old should be gone
        all_content = " ".join(m["content"] for m in messages)
        assert new_personality in all_content
        assert old_personality not in all_content


class TestPersonalitySlashCommand:
    """Tests for the /personality slash command."""

    def test_is_personality_command(self):
        assert is_personality_command("/personality") is True
        assert is_personality_command("/personality list") is True
        assert is_personality_command("/personality concise") is True
        assert is_personality_command("/persona list") is True  # alias
        assert is_personality_command("hello") is False
        assert is_personality_command("/help") is False

    def test_slash_personality_list(self):
        """/personality list shows all available templates with descriptions."""
        result = handle_personality_command("/personality list")

        assert "personalities" in result.lower() or "Available" in result
        # Every personality name should appear in the listing
        for name in PERSONALITIES:
            assert name in result, f"'{name}' missing from /personality list"

    def test_slash_personality_switch(self):
        """/personality <name> switches and persists via runtime override."""
        result = handle_personality_command("/personality gulf_engineer")

        assert "gulf_engineer" in result
        assert "Switched" in result or "✅" in result

        # Verify it actually persisted (runtime override is set)
        from kazma_core.personalities import get_runtime_personality
        assert get_runtime_personality() == "gulf_engineer"

    def test_slash_personality_current(self):
        """/personality (no args) or /personality current shows current personality."""
        set_runtime_personality("teacher")

        result = handle_personality_command("/personality")
        assert "teacher" in result
        assert "📚" in result  # teacher emoji

        result2 = handle_personality_command("/personality current")
        assert "teacher" in result2

    def test_slash_personality_unknown(self):
        """/personality with unknown name gives helpful error."""
        result = handle_personality_command("/personality nonexistent")

        assert "❌" in result or "Unknown" in result
        # Should list available options
        assert "default" in result

    def test_slash_personality_case_insensitive(self):
        """/personality CONCISE works same as lowercase."""
        result = handle_personality_command("/personality CONCISE")
        assert "concise" in result.lower() or "Switched" in result or "✅" in result


class TestPersonalityUserExtensible:
    """Verify the personality system is extensible by users."""

    def test_register_custom_personality(self):
        """Users can add custom personalities at runtime."""
        from kazma_core.personalities import PERSONALITIES, _register

        custom = _register({
            "name": "pirate",
            "system_prompt": "Arr! Speak like a pirate, matey!",
            "description": "Pirate captain. Uses nautical slang.",
            "emoji": "🏴‍☠️",
        })

        assert "pirate" in PERSONALITIES
        assert PERSONALITIES["pirate"]["emoji"] == "🏴‍☠️"

        # Clean up
        del PERSONALITIES["pirate"]
