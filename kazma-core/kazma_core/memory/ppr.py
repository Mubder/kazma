"""Local Ego-Graph Personalized PageRank (HippoRAG 2 style).

Multi-hop associative recall under a strict sub-50ms budget by
restricting the computation to a 2-hop neighborhood (N ≤ 200) around
the query seed nodes, then running power iteration on that isolated
subgraph.

NumPy is required for the matrix power iteration (resolution #1: numpy
is pulled transitively by the ``[rag]`` extra, never a hard dependency).
When numpy is absent, :func:`compute_local_ppr` degrades to returning
the seed nodes with uniform weights — the caller (recall engine) then
relies on FTS5 + dense ranking alone, so retrieval still works, just
without the multi-hop boost.
"""

from __future__ import annotations

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

__all__ = ["compute_local_ppr", "build_ego_graph", "ppr_available"]


def ppr_available() -> bool:
    """True when numpy is importable (PPR can run the full power iteration)."""
    try:
        import numpy  # noqa: F401

        return True
    except ImportError:
        return False


def build_ego_graph(
    all_edges: list[tuple[str, str, float]],
    seed_node_ids: list[str],
    *,
    max_nodes: int = 200,
    hop_radius: int = 2,
) -> tuple[list[str], list[tuple[str, str, float]]]:
    """Extract a bounded ego-graph around the seed nodes via BFS.

    Args:
        all_edges: The full edge set as ``(source, target, weight)``.
        seed_node_ids: Starting nodes (query seeds).
        max_nodes: Hard cap on the subgraph size (truncated by edge weight).
        hop_radius: BFS depth (2 = 2-hop neighborhood).

    Returns:
        ``(nodes, edges)`` where ``edges`` is the subset of ``all_edges``
        with both endpoints in ``nodes``. If the seed set is empty,
        returns ``([], [])``.
    """
    if not seed_node_ids:
        return [], []

    # Build adjacency (undirected for neighborhood expansion)
    adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
    edge_set: set[tuple[str, str, float]] = set()
    for u, v, w in all_edges:
        adj[u].append((v, w))
        adj[v].append((u, w))
        edge_set.add((u, v, w))

    # BFS outward from seeds up to hop_radius
    visited: dict[str, int] = {}  # node → hop distance
    frontier = {s: 0 for s in seed_node_ids if s in adj or True}
    visited.update(frontier)
    current_hop = 0
    while current_hop < hop_radius and frontier:
        current_hop += 1
        next_frontier: dict[str, int] = {}
        for node in frontier:
            for neighbor, _w in adj.get(node, []):
                if neighbor not in visited:
                    visited[neighbor] = current_hop
                    next_frontier[neighbor] = current_hop
        frontier = next_frontier
        if len(visited) >= max_nodes:
            break

    nodes = list(visited.keys())[:max_nodes]
    node_set = set(nodes)
    edges = [(u, v, w) for (u, v, w) in edge_set if u in node_set and v in node_set]
    return nodes, edges


def compute_local_ppr(
    seed_node_ids: list[str],
    graph_edges: list[tuple[str, str, float]],
    *,
    alpha: float = 0.15,
    max_iter: int = 15,
    max_nodes: int = 200,
    tol: float = 1e-5,
) -> dict[str, float]:
    """Local Ego-Graph Personalized PageRank via power iteration.

    Args:
        seed_node_ids: The query seed nodes (top-K from FTS5+dense fusion).
        graph_edges: ``(source, target, weight)`` triples.
        alpha: Teleport/restart factor (0.15 = standard PPR).
        max_iter: Maximum power-iteration steps.
        max_nodes: Hard cap on subgraph size.
        tol: L1 convergence tolerance.

    Returns:
        ``{node_id: stationary_probability}`` over the ego-graph. Seeds
        get the highest weight by construction. When numpy is absent,
        returns the seed nodes with uniform weight (degraded mode).
    """
    if not seed_node_ids:
        return {}

    # Build the bounded ego-graph first
    nodes, edges = build_ego_graph(
        graph_edges, seed_node_ids, max_nodes=max_nodes, hop_radius=2
    )
    if not nodes:
        # Seeds aren't in the graph at all — return uniform on seeds
        return {s: 1.0 / len(seed_node_ids) for s in seed_node_ids}

    # ── Degraded path: no numpy → uniform on seeds ──
    try:
        import numpy as np
    except ImportError:
        logger.debug("[ppr] numpy absent — returning uniform seed weights")
        present_seeds = [s for s in seed_node_ids if s in set(nodes)]
        if not present_seeds:
            present_seeds = seed_node_ids
        return {s: 1.0 / len(present_seeds) for s in present_seeds}

    # ── Full power iteration ──
    n = len(nodes)
    node_to_idx = {node: i for i, node in enumerate(nodes)}
    m = np.zeros((n, n), dtype=np.float64)

    for u, v, weight in edges:
        if u in node_to_idx and v in node_to_idx:
            # Directed edge u→v; column-normalized later
            m[node_to_idx[v], node_to_idx[u]] += float(weight or 1.0)

    # Column-normalize (each column sums to 1)
    col_sums = m.sum(axis=0)
    col_sums[col_sums == 0] = 1.0
    m = m / col_sums

    # Personalization vector: uniform over seeds that are in the subgraph
    p0 = np.zeros(n, dtype=np.float64)
    seed_indices = [node_to_idx[s] for s in seed_node_ids if s in node_to_idx]
    if not seed_indices:
        # Seeds fell outside the truncated subgraph; fall back to uniform
        p0[:] = 1.0 / n
    else:
        p0[seed_indices] = 1.0 / len(seed_indices)

    p = p0.copy()
    for _ in range(max_iter):
        p_next = (1.0 - alpha) * (m @ p) + alpha * p0
        if np.linalg.norm(p_next - p, ord=1) < tol:
            p = p_next
            break
        p = p_next

    return {nodes[i]: float(p[i]) for i in range(n)}
