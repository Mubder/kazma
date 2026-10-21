"""The tools the environment block names to the model are real tools.

``ide/env_context._ANNOUNCED_TOOLS`` is the short list of tools the brain is
told about by name. It named ``git_push_pull``, which no skill registers
(the git skill has ``git_push`` and ``git_pull``), and missed the read-only
``git_log`` and ``git_diff`` added on 2026-10-19 (docs audit, 2026-10-21).
Every name is checked against the live registry the tools catalog is built
from; a planted name is the negative control.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from kazma_core.ide import env_context

REPO = Path(__file__).resolve().parents[1]


def _registered_names() -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "_gen_catalog_announced", REPO / "scripts" / "generate_tools_catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module._live_registry()._tools)


def _unknown(names, registered: set[str]) -> list[str]:
    return [name for name in names if name not in registered]


def test_every_announced_tool_is_registered():
    registered = _registered_names()
    assert len(registered) > 50, "the registry did not load; the check proves nothing"
    assert _unknown(env_context._ANNOUNCED_TOOLS, registered) == []


def test_a_name_no_skill_registers_fails_the_check():
    registered = _registered_names()
    assert _unknown(("git_status", "git_push_pull"), registered) == ["git_push_pull"]
