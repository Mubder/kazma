"""What the Memory graph keeps when it cannot show every node.

The graph route used to sort nodes by belief count and cut the list at its
limit, then drop every link that lost an endpoint. A fact node (one belief)
is the first to go, so its subject was painted alone; a subject that lost
its link to ``user`` floated away from the hub. Operators read those as
orphaned memories and deleted good concepts.

:func:`keep_connected` cuts by connected groups instead: the group holding
the hub first, grown outward from the hub by belief count, then the other
groups in order of weight, each grown from its heaviest node. Every kept
node is joined to the rest of its group through kept nodes, so a node
painted alone is alone in the data.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping
from typing import Any

__all__ = ["keep_connected", "isolated_ids", "value_node_id"]


def value_node_id(belief_id: str) -> str:
    """Node id for one belief's literal value (``true``, ``4``, ...).

    A value such as ``true`` must not become one node shared by every fact
    that holds it (that was the "true" pseudo-hub); one node per belief
    keeps the fact drawn on its subject without joining unrelated subjects.
    """
    return f"value:{belief_id}"


def _adjacency(node_ids: Iterable[str], links: Iterable[Mapping[str, Any]]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {nid: set() for nid in node_ids}
    for link in links:
        src, tgt = link.get("source"), link.get("target")
        if src in adj and tgt in adj and src != tgt:
            adj[src].add(tgt)
            adj[tgt].add(src)
    return adj


def isolated_ids(
    nodes: Iterable[Mapping[str, Any]], links: Iterable[Mapping[str, Any]]
) -> list[str]:
    """Ids of the nodes no link touches (self-links do not count)."""
    ids = [str(n["id"]) for n in nodes]
    adj = _adjacency(ids, links)
    return [nid for nid in ids if not adj[nid]]


def keep_connected(
    nodes: list[Mapping[str, Any]],
    links: Iterable[Mapping[str, Any]],
    limit: int,
    *,
    hub_id: str = "user",
) -> set[str]:
    """Ids of at most *limit* nodes, chosen so no kept node loses its group.

    Weight is ``beliefCount``; ties break on id, so the cut is stable
    between polls.
    """
    if limit <= 0:
        return set()
    weight = {str(n["id"]): int(n.get("beliefCount") or 0) for n in nodes}
    if len(weight) <= limit:
        return set(weight)
    adj = _adjacency(weight, links)

    # Connected groups.
    group_of: dict[str, int] = {}
    groups: list[list[str]] = []
    for start in sorted(weight):
        if start in group_of:
            continue
        idx = len(groups)
        members = [start]
        group_of[start] = idx
        stack = [start]
        while stack:
            cur = stack.pop()
            for nxt in adj[cur]:
                if nxt not in group_of:
                    group_of[nxt] = idx
                    members.append(nxt)
                    stack.append(nxt)
        groups.append(members)

    def _root(members: list[str]) -> str:
        if hub_id in members:
            return hub_id
        return min(members, key=lambda m: (-weight[m], m))

    order = sorted(
        range(len(groups)),
        key=lambda i: (
            hub_id not in groups[i],  # the hub's group first
            -sum(weight[m] for m in groups[i]),
            _root(groups[i]),
        ),
    )

    kept: set[str] = set()
    for i in order:
        if len(kept) >= limit:
            break
        root = _root(groups[i])
        frontier: list[tuple[int, str]] = [(-weight[root], root)]
        seen = {root}
        while frontier and len(kept) < limit:
            _, cur = heapq.heappop(frontier)
            kept.add(cur)
            for nxt in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    heapq.heappush(frontier, (-weight[nxt], nxt))
    return kept
