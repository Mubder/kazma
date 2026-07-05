"""DAG schema and validation for multi-agent workflows in the Kazma framework.

Provides Pydantic models for constructing, serializing, and validating multi-agent
workflow graphs (Directed Acyclic Graphs), tool restrictions, and reliability policies.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator, field_validator

from kazma_core.swarm.task import TaskType

logger = logging.getLogger(__name__)


class ToolRestrictions(BaseModel):
    """Specifies tool execution boundaries for specific workflow steps."""

    allowed_tools: Optional[List[str]] = Field(
        default=None,
        description="Whitelist of tools that this node is permitted to call. If None, all tools are allowed.",
    )
    blocked_tools: List[str] = Field(
        default_factory=list,
        description="Blacklist of tools that this node is explicitly forbidden from calling.",
    )
    require_approval: List[str] = Field(
        default_factory=list,
        description="Tools that trigger human-in-the-loop (HITL) approval when called.",
    )


class ReliabilityPolicy(BaseModel):
    """Reliability constraints applied to worker executions inside a node."""

    max_retries: int = Field(
        default=3,
        ge=0,
        description="Maximum number of times to retry execution on transient errors.",
    )
    timeout_seconds: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="Execution timeout in seconds. None implies unlimited.",
    )
    circuit_breaker_enabled: bool = Field(
        default=False,
        description="Whether to enable automated circuit-breaker failure gating for this step.",
    )


class WorkflowNode(BaseModel):
    """A node inside the multi-agent DAG, representing a discrete task state."""

    id: str = Field(
        ...,
        description="Unique identifier for the node within the DAG.",
    )
    type: str = Field(
        default="dispatch",
        description="Task pattern execution type (e.g. dispatch, broadcast, pipeline, conditional).",
    )
    workers: List[str] = Field(
        default_factory=lambda: ["auto"],
        description="Names of workers assigned to this task node, or ['auto'] for polymorphic routing.",
    )
    prompt_template: str = Field(
        ...,
        description="Input prompt text. Supports dynamic template variables.",
    )
    tool_restrictions: Optional[ToolRestrictions] = Field(
        default=None,
        description="Optional tool usage policies for this step.",
    )
    reliability_policy: Optional[ReliabilityPolicy] = Field(
        default=None,
        description="Optional reliability rules and failure handling for this step.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="User-defined arbitrary metadata.",
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Ensure type aligns with supported TaskTypes."""
        valid_types = {t.value for t in TaskType}
        if v not in valid_types:
            raise ValueError(f"Task type '{v}' is not supported. Must be one of: {valid_types}")
        return v


class WorkflowEdge(BaseModel):
    """An edge in the DAG representing sequential flow between two nodes."""

    from_node: str = Field(..., description="The source node ID.")
    to_node: str = Field(..., description="The target node ID.")
    condition: Optional[str] = Field(
        default=None,
        description="Expression-based gating condition (e.g. 'result.status == \"completed\"').",
    )


class GlobalConstraints(BaseModel):
    """Global execution boundaries applied across the entire DAG lifecycle."""

    max_total_retries: Optional[int] = Field(default=None, ge=0)
    max_cost_limit: Optional[float] = Field(default=None, gt=0.0)


class DAGWorkflow(BaseModel):
    """Root multi-agent Directed Acyclic Graph (DAG) workflow model."""

    id: str = Field(..., description="Unique workflow model ID.")
    name: str = Field(..., description="Human-readable name of the workflow.")
    description: Optional[str] = Field(default=None)
    version: str = Field(default="1.0.0")
    nodes: Dict[str, WorkflowNode] = Field(
        ...,
        description="Map of node ID to WorkflowNode definitions.",
    )
    edges: List[WorkflowEdge] = Field(
        default_factory=list,
        description="Sequential list of Directed Acyclic Graph edges.",
    )
    global_constraints: Optional[GlobalConstraints] = Field(default=None)

    @model_validator(mode="after")
    def validate_dag_structure(self) -> DAGWorkflow:
        """Enforces edge referential integrity and checks for cycles."""
        node_ids = set(self.nodes.keys())

        # 1. Edge reference integrity
        for idx, edge in enumerate(self.edges):
            if edge.from_node not in node_ids:
                raise ValueError(
                    f"Edge at index {idx} references non-existent 'from_node': '{edge.from_node}'."
                )
            if edge.to_node not in node_ids:
                raise ValueError(
                    f"Edge at index {idx} references non-existent 'to_node': '{edge.to_node}'."
                )

        # 2. Cycle Detection (DFS)
        adj: Dict[str, List[str]] = {nid: [] for nid in node_ids}
        for edge in self.edges:
            adj[edge.from_node].append(edge.to_node)

        visited = {nid: 0 for nid in node_ids}  # 0: Unvisited, 1: Visiting, 2: Visited

        def has_cycle(u: str) -> bool:
            visited[u] = 1
            for v in adj[u]:
                if visited[v] == 1:
                    return True
                elif visited[v] == 0:
                    if has_cycle(v):
                        return True
            visited[u] = 2
            return False

        for nid in node_ids:
            if visited[nid] == 0:
                if has_cycle(nid):
                    raise ValueError(
                        f"Cycle detected in workflow path starting at node '{nid}'. "
                        "The workflow must be a Directed Acyclic Graph (DAG)."
                    )

        return self

    @classmethod
    def from_yaml(cls, yaml_str: str) -> DAGWorkflow:
        """Load and validate DAGWorkflow from a YAML string."""
        import yaml
        data = yaml.safe_load(yaml_str)
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, json_str: str) -> DAGWorkflow:
        """Load and validate DAGWorkflow from a JSON string."""
        import json
        data = json.loads(json_str)
        return cls.model_validate(data)

    def to_yaml(self) -> str:
        """Serialize DAGWorkflow to a YAML string."""
        import yaml
        # Using model_dump to convert into primitive dict
        return yaml.dump(self.model_dump(exclude_none=True), sort_keys=False)
