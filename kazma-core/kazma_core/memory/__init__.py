"""Kazma Memory — V2 cognitive engine (bi-temporal beliefs, PPR recall).

The V1 stack (ChromaDB VectorMemory, FTS5, property graph, sqlite-vec) was
removed in the V1→V2 cutover. V2 is the single memory stack. The config
helpers remain re-exported here for convenience.
"""

from kazma_core.memory.config import (
    memory_auto_store_enabled,
    memory_enabled,
    memory_per_turn_enabled,
    read_memory_cfg,
)

__all__ = [
    "memory_auto_store_enabled",
    "memory_enabled",
    "memory_per_turn_enabled",
    "read_memory_cfg",
]
