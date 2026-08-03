"""Reject leaked tool markup as final answers (2026-08-03 DSML leak)."""

from __future__ import annotations

from kazma_core.agent.graph_builder import is_unusable_assistant_content


def test_dsml_tool_leak_is_unusable():
    junk = (
        "The kazma CLI has no memory subcommand… Let me probe whether "
        "the proper memory tooling is now exposed:\n\n"
        "<|DSML|tool_calls>\n"
        '<|DSML|invoke name="memory_list_entities">\n'
        "</|DSML|invoke>\n"
        "</|DSML|tool_calls>"
    )
    assert is_unusable_assistant_content(junk) is True


def test_real_answer_is_usable():
    text = (
        "Status: partial.\n"
        "Found: one ShipX overview belief and a separate shipx concept entity.\n"
        "Not finished: deleting junk true/false entities — ask me to continue."
    )
    assert is_unusable_assistant_content(text) is False


def test_empty_unusable():
    assert is_unusable_assistant_content("") is True
    assert is_unusable_assistant_content(None) is True


def test_short_let_me_stub():
    assert is_unusable_assistant_content(
        "Let me probe whether the proper memory tooling is now exposed:"
    ) is True
