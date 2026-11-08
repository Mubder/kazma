"""Long files are written in parts, and a step's output limit follows its
model (resilience plan D2).

29 replies hit the output limit and 8 file writes were refused because their
arguments were cut off: every step that offered tools was capped at 8,192
tokens whatever the model could write. The limit is now the model's own
(``models.max_output_tokens.<model>``), a cut-off call is answered with the
instruction to write in parts, and a large report goes through file_write
plus file_append parts.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from kazma_core import config_store
from kazma_core.model_registry import TOOL_STEP_OUTPUT_TOKENS, tool_step_output_limit


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = config_store.ConfigStore(str(tmp_path / "settings.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: settings)
    yield settings
    settings.close()


def test_a_model_without_its_own_limit_gets_the_default(store):
    assert tool_step_output_limit("deepseek-flash") == TOOL_STEP_OUTPUT_TOKENS == 8192
    assert tool_step_output_limit("") == 8192


def test_the_models_own_limit_is_used_and_bounded(store):
    store.set("models.max_output_tokens.deepseek-flash", 32768, category="models")
    assert tool_step_output_limit("deepseek-flash") == 32768
    store.set("models.max_output_tokens.tiny", 10, category="models")
    assert tool_step_output_limit("tiny") == 1024
    store.set("models.max_output_tokens.bad", "lots", category="models")
    assert tool_step_output_limit("bad") == 8192


def test_the_supervisor_asks_for_the_models_limit():
    """Read where the limit is set: the step's max_tokens is the model's."""
    import inspect

    from kazma_core.agent import graph_supervisor

    source = inspect.getsource(graph_supervisor)
    assert "_call_max_tokens = _tool_step_tokens if effective_tool_definitions else None" in source
    assert "_call_max_tokens = 8192" not in source, "negative control: the fixed cap is gone"
    assert "asyncio.to_thread(\n            tool_step_output_limit," in source, "a settings read, off the loop"


def test_a_cut_off_call_is_told_to_write_in_parts():
    from kazma_core.agent.graph_tool_worker import _severed_call_message

    text = _severed_call_message("file_write")
    assert "file_append" in text and "SMALLER pieces" in text


def test_a_60kb_report_goes_through_parts(tmp_path):
    from kazma_core.agent.tool_builtins.filesystem import register_filesystem_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.workspace.binding import resolve_active_root

    registry = LocalToolRegistry()
    register_filesystem_tools(registry)
    parts = [f"## Part {i}\n\n" + (f"Paragraph {i}. " * 500) + "\n\n" for i in range(10)]
    assert sum(len(p) for p in parts) > 60_000

    async def write_in_parts() -> str:
        # In a workspace of its own: the active one may be this checkout
        # when an earlier test left it active (2026-11-08: a report.md
        # turned up in the repository).
        async with workspace_path_scope(tmp_path):
            path = str(resolve_active_root() / f"d2-{uuid.uuid4().hex[:8]}" / "report.md")
            await registry.get_tool("file_write").func(path=path, content=parts[0])
            for part in parts[1:]:
                await registry.get_tool("file_append").func(path=path, content=part)
            return path

    path = asyncio.run(write_in_parts())
    from pathlib import Path

    assert Path(path).is_relative_to(tmp_path)
    assert Path(path).read_text(encoding="utf-8") == "".join(parts)
