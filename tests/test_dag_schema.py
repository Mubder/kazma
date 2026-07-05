"""Tests for the DAG Schema and Validation module.

Verifies:
1. Valid workflow graph creation and parsing.
2. YAML and JSON serialization / deserialization.
3. Node reference integrity on edges.
4. Topological cycle detection (DFS-based) throwing ValueError on cycles.
5. Node-level tool restrictions and reliability policies.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kazma_core.swarm.dag_schema import (
    DAGWorkflow,
    WorkflowNode,
    WorkflowEdge,
    ToolRestrictions,
    ReliabilityPolicy,
    GlobalConstraints,
)


def test_valid_dag_workflow_yaml_json() -> None:
    """Test standard valid DAG construction and serialization (YAML/JSON)."""
    node_a = WorkflowNode(
        id="node_a",
        type="dispatch",
        workers=["developer_bot"],
        prompt_template="Solve this task: {{ task_desc }}",
        tool_restrictions=ToolRestrictions(
            allowed_tools=["file_write", "shell_exec"],
            blocked_tools=["delete_database"],
            require_approval=["shell_exec"],
        ),
        reliability_policy=ReliabilityPolicy(
            max_retries=2,
            timeout_seconds=60.0,
            circuit_breaker_enabled=True,
        ),
    )

    node_b = WorkflowNode(
        id="node_b",
        type="pipeline",
        workers=["reviewer_bot"],
        prompt_template="Review code for node_a",
    )

    edge = WorkflowEdge(
        from_node="node_a",
        to_node="node_b",
        condition="node_a.result == 'success'",
    )

    dag = DAGWorkflow(
        id="wf_001",
        name="Test Workflow",
        description="A sample test workflow with two nodes and one directed edge",
        nodes={"node_a": node_a, "node_b": node_b},
        edges=[edge],
        global_constraints=GlobalConstraints(
            max_total_retries=5,
            max_cost_limit=10.0,
        ),
    )

    # 1. Serialize to YAML and deserialization check
    yaml_str = dag.to_yaml()
    assert "node_a" in yaml_str
    assert "node_b" in yaml_str
    assert "wf_001" in yaml_str

    parsed_yaml = DAGWorkflow.from_yaml(yaml_str)
    assert parsed_yaml.id == dag.id
    assert len(parsed_yaml.nodes) == 2
    assert parsed_yaml.edges[0].from_node == "node_a"

    # 2. Serialize to JSON and deserialization check
    json_str = parsed_yaml.model_dump_json()
    parsed_json = DAGWorkflow.from_json(json_str)
    assert parsed_json.id == dag.id
    assert parsed_json.nodes["node_a"].tool_restrictions.blocked_tools == ["delete_database"]


def test_edge_referential_integrity_error() -> None:
    """Test referential integrity errors (non-existent node IDs in edges)."""
    node_a = WorkflowNode(id="node_a", prompt_template="Prompt A")
    node_b = WorkflowNode(id="node_b", prompt_template="Prompt B")

    # Edge references 'node_invalid' which does not exist
    edge = WorkflowEdge(from_node="node_a", to_node="node_invalid")

    with pytest.raises(ValueError) as exc:
        DAGWorkflow(
            id="wf_bad_edges",
            name="Bad Edges Workflow",
            nodes={"node_a": node_a, "node_b": node_b},
            edges=[edge],
        )

    assert "references non-existent 'to_node': 'node_invalid'" in str(exc.value)


def test_cycle_detection_simple() -> None:
    """Test cycle detection on a simple self-cycle (A -> A)."""
    node_a = WorkflowNode(id="node_a", prompt_template="Prompt A")
    edge = WorkflowEdge(from_node="node_a", to_node="node_a")

    with pytest.raises(ValueError) as exc:
        DAGWorkflow(
            id="wf_cycle",
            name="Cycle Workflow",
            nodes={"node_a": node_a},
            edges=[edge],
        )

    assert "Cycle detected" in str(exc.value)


def test_cycle_detection_multi_node() -> None:
    """Test cycle detection on a multi-node cycle (A -> B -> C -> A)."""
    node_a = WorkflowNode(id="node_a", prompt_template="Prompt A")
    node_b = WorkflowNode(id="node_b", prompt_template="Prompt B")
    node_c = WorkflowNode(id="node_c", prompt_template="Prompt C")

    edges = [
        WorkflowEdge(from_node="node_a", to_node="node_b"),
        WorkflowEdge(from_node="node_b", to_node="node_c"),
        WorkflowEdge(from_node="node_c", to_node="node_a"),  # Closes the cycle
    ]

    with pytest.raises(ValueError) as exc:
        DAGWorkflow(
            id="wf_cycle_abc",
            name="Cycle Workflow ABC",
            nodes={"node_a": node_a, "node_b": node_b, "node_c": node_c},
            edges=edges,
        )

    assert "Cycle detected" in str(exc.value)


def test_node_type_validation_error() -> None:
    """Test node type validation (must be one of the supported TaskTypes)."""
    with pytest.raises(ValidationError) as exc:
        WorkflowNode(
            id="node_bad_type",
            type="invalid_type_name",
            prompt_template="Bad type test",
        )

    assert "Task type 'invalid_type_name' is not supported" in str(exc.value)
