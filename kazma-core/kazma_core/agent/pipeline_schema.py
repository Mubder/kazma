"""Pydantic schemas and validation for the Visual Pipeline Sandbox drag-and-drop editor."""

from __future__ import annotations

import logging
from typing import Any, List
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class PipelineNode(BaseModel):
    """A single task node inside the drag-and-drop visual pipeline DAG."""

    id: str = Field(
        ...,
        description="Unique identifier for this node within the pipeline.",
    )
    worker_name: str = Field(
        ...,
        description="The name of the registered worker assigned to run this node.",
    )
    task_description: str = Field(
        ...,
        description="A high-level description of what task this worker executes.",
    )
    dependencies: List[str] = Field(
        default_factory=list,
        description="List of parent node IDs that must complete before this node can execute.",
    )


class PipelineDAG(BaseModel):
    """A complete Directed Acyclic Graph (DAG) for visual pipeline scaffolding and validation."""

    nodes: List[PipelineNode] = Field(
        ...,
        description="All nodes that define this visual pipeline.",
    )

    @model_validator(mode="after")
    def validate_pipeline_dag_structure(self) -> PipelineDAG:
        """Enforces referential integrity of dependencies and verifies there are no cycles."""
        node_ids = {node.id for node in self.nodes}

        # 1. Referential integrity: check that all dependencies exist as nodes
        for node in self.nodes:
            for dep in node.dependencies:
                if dep not in node_ids:
                    raise ValueError(
                        f"Node '{node.id}' references a non-existent dependency: '{dep}'."
                    )

        # 2. Cycle Detection (DFS)
        # Build adjacency list: parent_id -> list of child_ids
        adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
        for node in self.nodes:
            for dep in node.dependencies:
                adj[dep].append(node.id)

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
                        f"Cycle detected in visual pipeline starting at node '{nid}'. "
                        "The pipeline must be a Directed Acyclic Graph (DAG)."
                    )

        return self
