"""Kazma swarm memory utilities.

The V1 4-layer memory stack (adapter / graph / fts5 / sqlite_vec) was removed
in the V1→V2 cutover — V2 (``kazma_core.memory``) is the single memory stack.
This package now retains only the shared, non-chat-memory utilities that other
subsystems depend on:

    - ``pipeline_logger`` — swarm pipeline telemetry (used by swarm/patterns.py)

The relocated shared symbols (embedder, VectorStore) live in
``kazma_core.memory`` (embedder.py, vector_store_global.py).
"""

from kazma_core.swarm.memory.pipeline_logger import get_pipeline_logger

__all__ = ["get_pipeline_logger"]
