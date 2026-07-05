"""Tests for Swarm Workflows validation API endpoint.

Verifies:
1. Successfully validating and visualizing valid JSON and YAML DAG workflows.
2. Generating correct Mermaid diagram structures with correct styling classes.
3. Successfully detecting structural errors (cycles, referential integrity breaches) and returning clean validation messages.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_ui.swarm_panel import SwarmRouterBuilder

# Set up test app with the Swarm Router to isolate the endpoint
app = FastAPI()
swarm_router_builder = SwarmRouterBuilder(templates=None)  # Disable templates as we're testing JSON API
app.include_router(swarm_router_builder.build())


def test_validate_valid_workflow_json():
    """Verify that a valid workflow JSON is correctly validated and Mermaid is generated."""
    client = TestClient(app)
    
    workflow_data = {
        "id": "customer_support_pipeline",
        "name": "Customer Support Pipeline",
        "description": "Multi-agent pipeline for processing customer feedback",
        "nodes": {
            "triage_agent": {
                "id": "triage_agent",
                "type": "dispatch",
                "prompt_template": "Classify the feedback as urgent, billing, or general"
            },
            "billing_specialist": {
                "id": "billing_specialist",
                "type": "pipeline",
                "prompt_template": "Resolve billing disputes"
            },
            "general_support": {
                "id": "general_support",
                "type": "pipeline",
                "prompt_template": "Answer general customer inquiries"
            }
        },
        "edges": [
            {
                "from_node": "triage_agent",
                "to_node": "billing_specialist",
                "condition": "category == 'billing'"
            },
            {
                "from_node": "triage_agent",
                "to_node": "general_support",
                "condition": "category != 'billing'"
            }
        ],
        "global_constraints": {
            "max_total_retries": 3,
            "max_cost_limit": 5.0
        }
    }

    response = client.post(
        "/api/swarm/workflows/validate",
        json={"workflow_definition": json_dumps(workflow_data)}
    )
    
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["valid"] is True
    assert len(res_data["nodes"]) == 3
    assert len(res_data["edges"]) == 2
    
    # Verify Mermaid string contains correct nodes and style class associations
    mermaid = res_data["mermaid"]
    assert "graph TD" in mermaid
    assert "class triage_agent router;" in mermaid
    assert "class billing_specialist agent;" in mermaid
    assert "triage_agent -->|\"category == 'billing'\"| billing_specialist" in mermaid


def test_validate_valid_workflow_yaml():
    """Verify that a valid workflow YAML is correctly validated and Mermaid is generated."""
    client = TestClient(app)
    
    yaml_definition = """
id: simple_test_yaml
name: Simple Test YAML
description: A simple sequential pipeline
nodes:
  step_one:
    id: step_one
    type: dispatch
    prompt_template: Write a outline
  step_two:
    id: step_two
    type: pipeline
    prompt_template: Expand the outline into draft
edges:
  - from_node: step_one
    to_node: step_two
"""

    response = client.post(
        "/api/swarm/workflows/validate",
        json={"workflow_definition": yaml_definition}
    )
    
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["valid"] is True
    assert len(res_data["nodes"]) == 2
    assert len(res_data["edges"]) == 1
    assert "step_one --> step_two" in res_data["mermaid"]


def test_validate_cyclic_integrity_error():
    """Verify that a cycle in the workflow is rejected with a validation error."""
    client = TestClient(app)
    
    # Set up a cycle: A -> B -> A
    cyclic_workflow = {
        "id": "cyclic_fail",
        "name": "Cyclic Fail Workflow",
        "nodes": {
            "node_a": {"id": "node_a", "type": "dispatch", "prompt_template": "A"},
            "node_b": {"id": "node_b", "type": "dispatch", "prompt_template": "B"}
        },
        "edges": [
            {"from_node": "node_a", "to_node": "node_b"},
            {"from_node": "node_b", "to_node": "node_a"}
        ]
    }

    response = client.post(
        "/api/swarm/workflows/validate",
        json={"workflow_definition": json_dumps(cyclic_workflow)}
    )
    
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["valid"] is False
    assert "ValueError" in res_data["error"] or "cycle" in res_data["error"].lower()


def test_validate_referential_integrity_error():
    """Verify that edges pointing to non-existent nodes are rejected."""
    client = TestClient(app)
    
    # Edge references 'node_c' which is not defined in nodes
    invalid_workflow = {
        "id": "ref_fail",
        "name": "Ref Fail Workflow",
        "nodes": {
            "node_a": {"id": "node_a", "type": "dispatch", "prompt_template": "A"},
            "node_b": {"id": "node_b", "type": "dispatch", "prompt_template": "B"}
        },
        "edges": [
            {"from_node": "node_a", "to_node": "node_c"}
        ]
    }

    response = client.post(
        "/api/swarm/workflows/validate",
        json={"workflow_definition": json_dumps(invalid_workflow)}
    )
    
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["valid"] is False
    assert "references non-existent" in res_data["error"].lower() or "value error" in res_data["error"].lower() or "not found" in res_data["error"].lower()


# Helpers
def json_dumps(obj) -> str:
    import json
    return json.dumps(obj)
