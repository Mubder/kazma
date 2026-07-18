"""Visual Pipeline Sandbox API router.

Exposes endpoints for scaffold layout retrieval and structural/worker-name validation of
drag-and-drop Visual Pipeline Graphs (DAGs).
"""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from kazma_core.agent.pipeline_schema import PipelineDAG, PipelineNode
from kazma_core.swarm.registry import get_worker_registry

logger = logging.getLogger(__name__)

__all__ = [
    "create_pipeline_router",
]


def create_pipeline_router() -> APIRouter:
    """Return an APIRouter providing the visual pipeline sandbox endpoints."""

    router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])

    @router.get("/scaffold", response_model=PipelineDAG)
    async def get_pipeline_scaffold() -> PipelineDAG:
        """Return a blueprint scaffold template of a multi-agent visual pipeline graph."""
        scaffold = PipelineDAG(
            nodes=[
                PipelineNode(
                    id="node-researcher",
                    worker_name="auto",
                    task_description="Query web sources and synthesize research notes on the requested topic.",
                    dependencies=[],
                ),
                PipelineNode(
                    id="node-writer",
                    worker_name="auto",
                    task_description="Transform the synthesized research notes into a formatted final report.",
                    dependencies=["node-researcher"],
                ),
            ]
        )
        return scaffold

    @router.post("/validate")
    async def validate_pipeline(dag_data: dict[str, Any]) -> JSONResponse:
        """Validate an ingested drag-and-drop DAG model.

        Ensures:
        1. Correct schema parsing.
        2. Referential integrity of node dependencies.
        3. Directed Acyclic Graph structure (no cyclic loops).
        4. Assigned worker names exist in the system's WorkerRegistry.
        """
        try:
            # 1. Structural Validation (Pydantic parsing, referential integrity, and cycle detection)
            dag = PipelineDAG.model_validate(dag_data)
        except ValidationError as exc:
            errors = []
            for err in exc.errors():
                loc = " -> ".join(str(l) for l in err["loc"])
                msg = err["msg"]
                errors.append(f"[{loc}]: {msg}")
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={
                    "valid": False,
                    "errors": errors,
                },
            )
        except ValueError as exc:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={
                    "valid": False,
                    "errors": [str(exc)],
                },
            )

        # 2. Worker Registry Name Matching
        registry = get_worker_registry()
        unregistered_workers = []

        for node in dag.nodes:
            w_name = node.worker_name
            # Allow "auto" as a valid polymorphic system router key
            if w_name != "auto" and w_name not in registry:
                unregistered_workers.append(
                    f"Node '{node.id}' is assigned to unregistered worker '{w_name}'."
                )

        if unregistered_workers:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "valid": False,
                    "errors": unregistered_workers,
                },
            )

        return JSONResponse(
            content={
                "valid": True,
                "errors": [],
            }
        )

    return router
