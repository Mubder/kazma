"""Spaces S1-31: the turn's environment note is the space's own.

A real turn runs through the supervisor graph (a recording model), which
adds the environment note at its first step, in a sandboxed space of a
person, on an install whose machine has a workspace, a repository, a bound
MCP root and a GitHub token in its environment. The note the model gets
names none of them -- not the install's data folder, not even the absolute
path of the space's own folder (it is under the install's) -- and offers
none of the host tools; it names the space's folder by relative paths only.

In the admin's own space the note is as it always was. Control: the note
of before (a sandboxed space read as a host one) names the install's
workspace, repository and MCP root.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from kazma_core import paths
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

REPO_SLUG = "install-owner/install-repo"
TOKEN = "ghp_" + "installownerstoken0123456789abcdefABCD"


@pytest.fixture
def install(monkeypatch, tmp_path):
    """Spaces enforced, a person with a sandboxed space, and the machine's
    workspace, repository, MCP root and GitHub token planted."""
    from kazma_core.ide import env_context
    from kazma_core.tools.file_write import _get_workspace
    from kazma_core.workspace import binding

    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setattr(scope, "_enforced", True)
    machine = Path(_get_workspace()).resolve()  # read with no space bound: the machine's
    mcp_root = tmp_path / "install-mcp-root"
    mcp_root.mkdir()
    monkeypatch.setattr(binding, "_bound_mcp_root", mcp_root.resolve())
    monkeypatch.setattr(env_context, "detect_repo_slug", lambda root: REPO_SLUG)
    monkeypatch.setattr(env_context, "detect_branch", lambda root: "install-branch")
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    person = people.create_person(f"note-{uuid.uuid4().hex[:6]}", "a-long-enough-password",
                                  by=OWNER_ACCOUNT_ID)
    try:
        yield {"machine": machine, "mcp_root": mcp_root.resolve(), "person": person}
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)


def _in(space: str, asker: str):
    return scope.bind_scope(space, asker)


async def _note_of_a_turn() -> str:
    """The environment note the model gets at the first step of a real turn."""
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.llm_provider import LLMResponse
    from kazma_core.tracing import KazmaTracer
    from kazma_core.turn_notes import note_kind

    seen: list[list[dict]] = []

    class RecordingLLM:
        async def chat(self, *, messages, tools=None, model=None, **kwargs):
            seen.append([dict(m) for m in messages])
            return LLMResponse(content="Done.", tool_calls=[], finish_reason="stop",
                               model="stub", usage={"total_tokens": 1}, cost_usd=0.0)

    graph = build_supervisor_graph(
        llm=RecordingLLM(), system_prompt="You are Kazma.", tool_definitions=[],
        tool_executor=LocalToolRegistry(include_builtins=False),
        cost_breaker=create_cost_breaker(), authority=create_authority(model="test", window=128000),
        tracer=KazmaTracer(backend="console"),
    )
    await graph.ainvoke({"messages": [{"role": "user", "content": "what can you do here?"}]},
                        {"configurable": {"thread_id": f"t-{uuid.uuid4().hex[:6]}"}})
    assert seen, "the model was not called"
    notes = [str(m.get("content")) for m in seen[0] if note_kind(m) == "environment"]
    assert len(notes) == 1, notes
    return notes[0]


def _install_names(install) -> list[str]:
    return [
        str(install["machine"]), install["machine"].name if len(install["machine"].name) > 8 else str(install["machine"]),
        str(paths.data_dir()), str(install["mcp_root"]), REPO_SLUG, "install-branch",
        "GitHub: authenticated", "MCP global root", "Workspace root", "shell_exec", "git_status",
        "Host shell policy",
    ]


@pytest.mark.asyncio
async def test_a_sandboxed_spaces_note_names_nothing_of_the_install(install) -> None:
    from kazma_core.spaces import files as space_files

    person = install["person"]
    with _in(person["personal_space"], person["id"]):
        note = await _note_of_a_turn()
        own_folder = str(space_files.sandboxed_files_root())
    leaked = [name for name in _install_names(install) if name in note]
    assert leaked == [], leaked
    assert own_folder not in note  # under the install's data folder: named relatively
    assert "This space's folder" in note and "relative paths" in note
    assert "file_read" in note or "Available tools in this space" not in note


@pytest.mark.asyncio
async def test_the_admins_own_space_keeps_the_note_it_always_had(install) -> None:
    with _in(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        note = await _note_of_a_turn()
    assert str(install["machine"]) in note
    assert REPO_SLUG in note and "MCP global root" in note
    assert "GitHub: authenticated" in note  # the owner's environment token is the owner's space's


@pytest.mark.asyncio
async def test_control_the_note_of_before_names_the_install(install, monkeypatch) -> None:
    from kazma_core.spaces import files as space_files

    person = install["person"]
    monkeypatch.setattr(space_files, "sandboxed_space", lambda: None)  # read as a host space
    with _in(person["personal_space"], person["id"]):
        note = await _note_of_a_turn()
    assert REPO_SLUG in note and str(install["mcp_root"]) in note and "Workspace root" in note


def test_a_space_whose_kind_cannot_be_read_is_told_nothing_of_the_machine(install, monkeypatch) -> None:
    from kazma_core.ide import env_context
    from kazma_core.spaces import files as space_files

    def _unknown():
        raise space_files.SpaceKindUnknown("unreadable")

    monkeypatch.setattr(space_files, "sandboxed_space", _unknown)
    person = install["person"]
    with _in(person["personal_space"], person["id"]):
        note = env_context._build_env_context_sync()
    assert REPO_SLUG not in note and str(install["machine"]) not in note


def test_a_sandboxed_task_that_names_a_workspace_is_told_its_own_folder(install) -> None:
    from kazma_core.ide import env_context
    from kazma_core.stores import get_workspace_store

    rows = get_workspace_store().list_workspaces()
    named = rows[0]["id"] if rows else "ws-any"
    person = install["person"]
    with _in(person["personal_space"], person["id"]):
        note = env_context._build_env_context_sync(workspace_id=named)
    assert str(install["machine"]) not in note and "This space's folder" in note


def test_without_spaces_the_note_is_as_before(install, monkeypatch) -> None:
    from kazma_core.ide import env_context

    monkeypatch.setattr(scope, "_enforced", False)
    note = env_context._build_env_context_sync()
    assert str(install["machine"]) in note and REPO_SLUG in note
