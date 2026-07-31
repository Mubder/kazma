"""Legacy V1 4-layer memory package — being retired (V1→V2 cutover).

The shared symbols that lived here (embedder, VectorStore) were relocated to
``kazma_core.memory`` so V2 and the non-memory subsystems (KB index, semantic
cache/router) survive the V1 deletion. The remaining V1 submodules
(adapter, graph, fts5, sqlite_vec, pipeline_logger) are slated for deletion
in Phase 4 of the V1 removal. This package is intentionally empty — import
the relocated symbols from ``kazma_core.memory`` directly.
"""
