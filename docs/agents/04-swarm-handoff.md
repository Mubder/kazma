<!-- AGENTS.md, piece 04-swarm-handoff. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 4. Swarm Handoff Cycle Detection (`kazma-core/kazma_core/swarm/engine.py`)
- **Handoff is reachable from shipped workers** (Swarm audit SW-E13, owner
  decision D6: a product feature). The LLM worker (`InProcessWorker`) offers
  its model `handoff_to_worker(worker, reason, task?, context?)`
  (`swarm/handoff.py`) whenever the engine dispatches it: `dispatch_worker`
  binds a `HandoffOffer` (the registered workers and the autoscaler's
  templates, never the worker itself, and the cycle guard at this point of the
  chain). The call raises `HandoffRequest` out of `dispatch()`
  (`except HandoffRequest: raise` before its catch-all) to `_handle_handoff`.
  The tool is the worker's, never the registry's (the main agent, the MCP
  server and cron never see it); it has a `TOOL_TIERS` tier (`write`) and a
  side-effect profile (`delegate`), and approves nothing: the target's tools
  ask the bus as any worker's (§7B). A refused call (unknown worker, no
  reason, beside other tool calls, a worker scope without `delegate`, the
  guard) is the tool's answer and the worker finishes the task itself.
- `_handle_handoff()` accepts `_visited: dict[str, int]` and `_depth: int`
- These thread through `_dispatch_worker_by_name_all` -> `_dispatch_worker` -> `_handle_handoff`
- Max depth is 5 (a hop at depth 5 is refused: four hops, five runs); removing the guard causes infinite recursion on A->B->A cycles
- Workers can be revisited up to `MAX_VISITS=2` times: each runs at most twice in a chain, so A->B->A and A->B->A->B run and B's hop back to A is refused
- Visit counts are tracked per worker IDENTITY (`_handoff_identity`): a pool
  instance counts as its template, since a hop to a template spawns a new
  instance each time (counting instance names, only the depth stopped a
  template ping-pong). A target name resolves like a pattern's
  (`_resolve_worker`: registered, else spawned from the template).
- Every ending of `_handle_handoff` releases the source's in-flight count
  (`_release_handoff_source`): the guard's refusal left it busy for good.
  The target keeps the dispatch's metadata, task id and workspace
  (`build_handoff_context`, with or without a blackboard); the hop is the
  task's `handoff` event (the Swarm page's chain) and `kazma_swarm_handoffs_total`.
  Gate: `tests/test_swarm_handoff_tool.py` (a scripted model end to end; the
  guard removed and instance counting as negative controls).

