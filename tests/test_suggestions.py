"""Tests for proactive suggestions and automatic tool intent detection.

Imports directly via importlib to isolate the suggestions module for
unit testing without pulling in the full gateway package init chain.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Direct module import — bypass broken __init__.py chain
# ---------------------------------------------------------------------------
_SUGGESTIONS_PATH = (
    Path(__file__).resolve().parent.parent
    / "kazma-gateway"
    / "kazma_gateway"
    / "suggestions.py"
)
_MODULE_NAME = "_suggestions_test_mod"
_spec = importlib.util.spec_from_file_location(
    _MODULE_NAME, str(_SUGGESTIONS_PATH)
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules[_MODULE_NAME] = _mod  # register so dataclasses resolves __module__
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

PostTaskSuggester = _mod.PostTaskSuggester
detect_tool_intent = _mod.detect_tool_intent
suggestions_from_config = _mod.suggestions_from_config


# =========================================================================
# PostTaskSuggester
# =========================================================================


class TestPostTaskSuggester:
    """Tests for PostTaskSuggester — post-task next-step hints."""

    def test_file_write_suggests_tests(self) -> None:
        """After file_write, suggest running tests."""
        s = PostTaskSuggester()
        hints = s.suggest(["file_write"])
        assert len(hints) >= 1
        assert any("pytest" in h for h in hints)

    def test_git_commit_suggests_push(self) -> None:
        """After git_commit, suggest pushing."""
        s = PostTaskSuggester()
        hints = s.suggest(["git_commit"])
        assert any("push" in h.lower() for h in hints)

    def test_git_add_suggests_commit(self) -> None:
        """After git_add, suggest committing."""
        s = PostTaskSuggester()
        hints = s.suggest(["git_add"])
        assert any("commit" in h.lower() for h in hints)

    def test_search_suggests_url_extract(self) -> None:
        """After search, suggest URL extraction."""
        s = PostTaskSuggester()
        hints = s.suggest(["search"])
        assert any("extract" in h.lower() or "url" in h.lower() for h in hints)

    def test_code_change_suggests_review(self) -> None:
        """After code change actions, suggest review."""
        s = PostTaskSuggester()
        hints = s.suggest(["code_exec"])
        assert any("review" in h.lower() for h in hints)

    def test_max_two_suggestions(self) -> None:
        """Never more than 2 suggestions (default)."""
        s = PostTaskSuggester()
        hints = s.suggest(["file_write", "git_commit", "search", "code_exec"])
        assert len(hints) <= 2

    def test_custom_max_suggestions(self) -> None:
        """Respects custom max_suggestions."""
        s = PostTaskSuggester(max_suggestions=1)
        hints = s.suggest(["file_write", "git_commit"])
        assert len(hints) <= 1

    def test_disabled_returns_empty(self) -> None:
        """When disabled, always returns empty list."""
        s = PostTaskSuggester(enabled=False)
        hints = s.suggest(["file_write", "git_commit"])
        assert hints == []

    def test_empty_actions_returns_empty(self) -> None:
        """No actions → no suggestions."""
        s = PostTaskSuggester()
        assert s.suggest([]) == []

    def test_no_matching_actions(self) -> None:
        """Unrecognized actions produce no suggestions."""
        s = PostTaskSuggester()
        hints = s.suggest(["unknown_tool", "another_thing"])
        assert hints == []

    def test_tool_prefix_normalization(self) -> None:
        """Actions with 'tool_' prefix are normalized."""
        s = PostTaskSuggester()
        hints = s.suggest(["tool_file_write"])
        assert len(hints) >= 1
        assert any("pytest" in h for h in hints)

    def test_mcp_prefix_normalization(self) -> None:
        """Actions with 'mcp_' prefix are normalized."""
        s = PostTaskSuggester()
        hints = s.suggest(["mcp_git_commit"])
        assert any("push" in h.lower() for h in hints)

    def test_enabled_property_toggle(self) -> None:
        """Enabled property can be toggled."""
        s = PostTaskSuggester(enabled=True)
        assert s.enabled is True
        s.enabled = False
        assert s.suggest(["file_write"]) == []
        s.enabled = True
        assert len(s.suggest(["file_write"])) >= 1

    def test_no_duplicate_suggestions(self) -> None:
        """Same suggestion pattern shouldn't appear twice."""
        s = PostTaskSuggester(max_suggestions=10)
        hints = s.suggest(["file_write", "code_exec", "shell_exec"])
        # All three are code_change actions — "review" should appear once
        review_count = sum(1 for h in hints if "review" in h.lower())
        assert review_count <= 1

    def test_priority_ordering(self) -> None:
        """file_write (test suggestion) should rank above search."""
        s = PostTaskSuggester(max_suggestions=2)
        hints = s.suggest(["search", "file_write"])
        # First hint should be the higher-priority one (file_write → pytest)
        assert "pytest" in hints[0]

    def test_git_push_suggests_pr(self) -> None:
        """After git_push, suggest PR or release."""
        s = PostTaskSuggester()
        hints = s.suggest(["git_push"])
        assert any("pr" in h.lower() or "release" in h.lower() for h in hints)


# =========================================================================
# detect_tool_intent
# =========================================================================


class TestDetectToolIntent:
    """Tests for detect_tool_intent — pre-response tool hints."""

    def test_search_intent(self) -> None:
        """'search for X' suggests web_search."""
        hints = detect_tool_intent("search for best python frameworks")
        assert len(hints) == 1
        assert "web_search" in hints[0]

    def test_look_up_intent(self) -> None:
        """'look up X' suggests web_search."""
        hints = detect_tool_intent("look up the weather in Kuwait")
        assert any("web_search" in h for h in hints)

    def test_google_intent(self) -> None:
        """'google X' suggests web_search."""
        hints = detect_tool_intent("google how to deploy kubernetes")
        assert any("web_search" in h for h in hints)

    def test_find_intent(self) -> None:
        """'find X' suggests web_search."""
        hints = detect_tool_intent("find the latest Rust release")
        assert any("web_search" in h for h in hints)

    def test_url_paste_suggests_read(self) -> None:
        """Pasting a URL suggests read_url."""
        hints = detect_tool_intent(
            "Can you check this? https://example.com/article"
        )
        assert any("read_url" in h for h in hints)

    def test_run_code_intent(self) -> None:
        """'run this code' suggests python_exec."""
        hints = detect_tool_intent("run this code for me")
        assert any("python_exec" in h for h in hints)

    def test_execute_script_intent(self) -> None:
        """'execute the script' suggests python_exec."""
        hints = detect_tool_intent("execute the script please")
        assert any("python_exec" in h for h in hints)

    def test_install_intent(self) -> None:
        """'install X' suggests shell_exec."""
        hints = detect_tool_intent("install requests")
        assert any("shell_exec" in h for h in hints)

    def test_pip_install_intent(self) -> None:
        """'pip install X' suggests shell_exec."""
        hints = detect_tool_intent("pip install numpy")
        assert any("shell_exec" in h for h in hints)

    def test_summarize_intent(self) -> None:
        """'summarize this document' suggests read_url."""
        hints = detect_tool_intent("summarize this document for me")
        assert len(hints) == 1
        assert "url" in hints[0].lower() or "file" in hints[0].lower()

    def test_already_used_tool_suppressed(self) -> None:
        """If tool was already used, its hint is skipped."""
        hints = detect_tool_intent(
            "search for python tutorials",
            used_tools=["web_search"],
        )
        assert hints == []

    def test_url_already_read_suppressed(self) -> None:
        """If read_url already used, URL hint is skipped."""
        hints = detect_tool_intent(
            "check https://example.com",
            used_tools=["read_url"],
        )
        assert hints == []

    def test_empty_message(self) -> None:
        """Empty message → no hints."""
        assert detect_tool_intent("") == []
        assert detect_tool_intent("   ") == []

    def test_no_match(self) -> None:
        """Plain message with no tool intent."""
        hints = detect_tool_intent("hello, how are you?")
        assert hints == []

    def test_multiple_intents(self) -> None:
        """Message with URL gives URL hint."""
        hints = detect_tool_intent(
            "check https://example.com for best practices"
        )
        assert len(hints) >= 1

    def test_used_tools_partial_suppression(self) -> None:
        """Only the used tool's hint is suppressed, others remain."""
        hints = detect_tool_intent(
            "search for https://example.com",
            used_tools=["web_search"],
        )
        # web_search suppressed, but read_url for the URL should remain
        assert len(hints) == 1
        assert "read_url" in hints[0]

    def test_none_used_tools(self) -> None:
        """Passing None for used_tools works like empty list."""
        hints = detect_tool_intent("search for cats", used_tools=None)
        assert len(hints) >= 1

    def test_search_suppressed_code_exec_not(self) -> None:
        """Used web_search suppresses only search hint, not others."""
        hints = detect_tool_intent(
            "run this code and also search for examples",
            used_tools=["web_search"],
        )
        assert any("python_exec" in h for h in hints)
        assert not any("web_search" in h for h in hints)

    def test_execute_vs_run_both_match(self) -> None:
        """Both 'run' and 'execute' variants trigger python_exec."""
        for phrase in [
            "run this code",
            "execute this script",
            "run the program",
            "exec the code",
        ]:
            hints = detect_tool_intent(phrase)
            assert any("python_exec" in h for h in hints), f"Failed for: {phrase}"


# =========================================================================
# suggestions_from_config
# =========================================================================


class TestSuggestionsFromConfig:
    """Tests for the config loader helper."""

    def test_enabled_from_config(self) -> None:
        """Enabled when config says so."""
        config = {"gateway": {"suggestions": {"enabled": True}}}
        s = suggestions_from_config(config)
        assert s.enabled is True

    def test_disabled_from_config(self) -> None:
        """Disabled when config says so."""
        config = {"gateway": {"suggestions": {"enabled": False}}}
        s = suggestions_from_config(config)
        assert s.enabled is False

    def test_missing_config_defaults_enabled(self) -> None:
        """Missing config section defaults to enabled."""
        s = suggestions_from_config({})
        assert s.enabled is True

    def test_partial_config_defaults_enabled(self) -> None:
        """Partial gateway config defaults to enabled."""
        s = suggestions_from_config({"gateway": {}})
        assert s.enabled is True
