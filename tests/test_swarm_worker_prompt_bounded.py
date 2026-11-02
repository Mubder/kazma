"""A swarm worker's prompt carries the tools its role needs, not all of them
(2026-11, FA-239).

The shipped worker templates named no tools, and a worker with no tool list
is offered every tool the registry holds: 168 definitions, about 24,600
tokens, so a one-sentence swarm answer cost ~24,900 prompt tokens. Each
shipped template (``swarm_templates.json``) now names its role's tools --
the handoff tool among them, or a worker could no longer hand a task on.

Measured on the real registry: each template's tool definitions stay under
:data:`BOUND` tokens (4 characters a token), and every name it lists is a
tool that exists. The whole list (a template with none) is the negative
control.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = json.loads((ROOT / "swarm_templates.json").read_text(encoding="utf-8"))
#: Tokens a shipped worker's tool definitions may take.
BOUND = 6000


@pytest.fixture(scope="module")
def definitions():
    from kazma_core.agent.tool_registry import get_tool_registry

    return {d["function"]["name"]: d for d in get_tool_registry().get_tool_definitions()}


def _tokens(defs) -> int:
    return len(json.dumps(defs)) // 4


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t["name"])
def test_a_shipped_worker_names_its_tools_and_stays_small(template, definitions):
    from kazma_core.swarm.handoff import HANDOFF_TOOL_NAME

    tools = template["capabilities"].get("tools") or []
    assert tools, f"{template['name']} would be offered every tool"
    assert HANDOFF_TOOL_NAME in tools, "without it the worker cannot hand a task on"
    missing = [name for name in tools if name != HANDOFF_TOOL_NAME and name not in definitions]
    assert not missing, f"{template['name']} names tools that do not exist: {missing}"
    assert _tokens([definitions[n] for n in tools if n in definitions]) <= BOUND


def test_negative_control_every_tool_is_far_past_the_bound(definitions):
    assert _tokens(list(definitions.values())) > 3 * BOUND
