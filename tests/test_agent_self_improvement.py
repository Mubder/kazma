"""Kazma-wide (chat supervisor) self-improvement store + analyze."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def evo_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "1")
    # Point data_dir at tmp
    monkeypatch.setattr(
        "kazma_core.paths.data_dir",
        lambda: tmp_path,
    )
    return tmp_path


def test_apply_and_get_agent_evolution(evo_dir: Path) -> None:
    from kazma_core.skills import self_improvement as si

    assert si.get_agent_evolution_block() == ""
    ok = si.apply_agent_mutation(
        "supervisor",
        "\n\n[SelfImprovement] Prefer citing tool results.",
    )
    assert ok is True
    block = si.get_agent_evolution_block("supervisor")
    assert "[SelfImprovement]" in block
    assert "Prefer citing" in block
    assert (evo_dir / "agent_evolution.json").exists()


def test_disabled_env(evo_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.skills import self_improvement as si

    si.apply_agent_mutation("supervisor", "[SelfImprovement] keep me")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    assert si.self_improvement_enabled() is False
    assert si.get_agent_evolution_block() == ""


@pytest.mark.asyncio
async def test_analyze_chat_turn_mutates(evo_dir: Path) -> None:
    from kazma_core.skills import self_improvement as si

    with (
        patch(
            "kazma_core.swarm.memory.adapter.get_adapter",
            return_value=None,
        ),
        patch(
            "kazma_core.model_registry.get_model_registry",
            side_effect=RuntimeError("no reg"),
        ),
    ):
        r = await si.analyze_and_apply_chat_turn(
            user_message="List my open PRs on the monorepo",
            success=True,
            output_snippet="Here are three PRs…",
        )
    assert r.get("action") == "mutate"
    assert r.get("applied") is True
    assert "[SelfImprovement]" in si.get_agent_evolution_block()


@pytest.mark.asyncio
async def test_analyze_skips_ack(evo_dir: Path) -> None:
    from kazma_core.skills import self_improvement as si

    r = await si.analyze_and_apply_chat_turn(
        user_message="ok",
        success=True,
        output_snippet="👍",
    )
    assert r.get("action") == "skip"
