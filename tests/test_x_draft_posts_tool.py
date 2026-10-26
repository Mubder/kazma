"""Chat drafts X posts the way X Studio's Generate does.

Asked in chat for X posts, the model wrote them itself and saved them with
save_proposal: no reference documents and no checks, so a post about Kazma
said whatever the model remembered. ``x_draft_posts`` runs X Studio's own
pipeline (the X model, the reference documents set in Settings -> X, the four
checks) and saves the drafts under the running turn, so the rule that a draft
is never published in the turn that wrote it still holds.
"""

from __future__ import annotations

import pytest

from tests.test_x_drafting_references import MARKER, drafting  # noqa: F401 -- the fixture


@pytest.fixture
def chat_turn(monkeypatch, tmp_path):
    from kazma_core.agent.artifacts import ArtifactStore
    from kazma_core.safety.commitment import proposals

    store = ArtifactStore(tmp_path / "artifacts.db")
    monkeypatch.setattr("kazma_core.agent.artifacts.get_artifact_store", lambda: store)
    token = proposals._current_turn.set("chat-turn-1")
    yield store
    proposals._current_turn.reset(token)


async def test_chat_drafting_reads_the_references_and_saves_under_the_turn(drafting, chat_turn):  # noqa: F811
    from kazma_core.safety.commitment.proposals import drafted_this_turn
    from kazma_skills.native.x_publisher.tools import x_draft_posts

    answer = await x_draft_posts("A post about why Kazma asks first.", 1)
    assert MARKER in drafting["messages"][1]["content"], "the X drafter was not given the reference"
    assert "what-is-kazma.md" in answer and "X length" in answer and "checks:" in answer
    item_id = answer.split("- ", 1)[1].split(" ", 1)[0]
    info = chat_turn.resolve_proposal(item_id)
    assert info["texts"] == ["Kazma asks before it acts."]
    assert info["saved_turn"] == "chat-turn-1" and drafted_this_turn(info)


async def test_negative_control_without_references_the_answer_says_so(drafting, chat_turn, monkeypatch):  # noqa: F811
    from kazma_skills.native.x_publisher.tools import x_draft_posts

    monkeypatch.setattr("kazma_core.x_api.post_drafting.load_references", lambda paths: ())
    answer = await x_draft_posts("A post.", 1)
    assert MARKER not in drafting["messages"][1]["content"]
    assert "no reference documents are set" in answer


async def test_a_bad_count_or_an_unreadable_reference_is_refused_before_any_model_call(drafting, chat_turn, tmp_path):  # noqa: F811
    from kazma_core.agent.tool_refusal import KnownEffectError, RefusedBeforeEffect
    from kazma_skills.native.x_publisher.tools import x_draft_posts

    with pytest.raises(RefusedBeforeEffect, match="count"):
        await x_draft_posts("A post.", "many")
    drafting["configure"](tmp_path / "missing.md")
    with pytest.raises(RefusedBeforeEffect) as refused:
        await x_draft_posts("A post.", 1)
    assert not isinstance(refused.value, KnownEffectError) and "missing.md" in str(refused.value)
    assert drafting["calls"] == 0


async def test_a_rejected_draft_is_a_known_effect_and_nothing_is_saved(drafting, chat_turn):  # noqa: F811
    from kazma_core.agent.tool_refusal import KnownEffectError
    from kazma_skills.native.x_publisher.tools import x_draft_posts

    drafting["output"] = {"drafts": ["x" * 400]}
    with pytest.raises(KnownEffectError, match="Nothing was saved"):
        await x_draft_posts("A post.", 1)
    assert chat_turn.list_proposals(tenant_id="default") == []


def test_the_tool_is_registered_everywhere_a_tool_must_be():
    from pathlib import Path

    import yaml
    from kazma_core.safety.hitl import TOOL_TIERS
    from kazma_core.safety.side_effects import EffectKind, get_effect_profile
    from kazma_core.store_registry import TOOL_WRITES

    assert TOOL_TIERS["x_draft_posts"] == "write"
    assert get_effect_profile("x_draft_posts").effect is EffectKind.NONE
    assert TOOL_WRITES["x_draft_posts"].targets == ("agent_artifacts.db",)
    import kazma_skills.native.x_publisher as skill

    manifest = yaml.safe_load((Path(skill.__file__).parent / "skill_manifest.yaml").read_text(encoding="utf-8"))
    assert "x_draft_posts" in manifest["tools"]


def test_the_model_is_steered_to_the_tool_for_x_posts():
    import inspect

    from kazma_core import product_knowledge
    from kazma_core.agent.tool_builtins import external

    assert "x_draft_posts(brief, count)" in inspect.getsource(product_knowledge)
    assert "x_draft_posts instead" in inspect.getsource(external)
