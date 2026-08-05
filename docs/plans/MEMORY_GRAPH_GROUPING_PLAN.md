# Memory Graph Grouping — View-Only Node Associations

**Status:** Design proposal — not yet implemented. Created after the
2026-08-06 session that shipped F1–F4 + the virtual-node link partial fix.

## The problem this solves

The `/memory` graph paints a node for **every belief object**, including
`user →noted→ <huge text blob>` notes (the longest in the live store is
1000 chars). These "virtual fact" nodes are essay-length prose, not
entities. They clutter the graph and can't be meaningfully linked, merged,
or moved — every attempt to link them creates a *new* slug-divergent entity
(`shipx_deployment_modes_shipx_mode_...`) and a junk belief, leaving the
clicked node orphaned. This is the deeper form of the "node gets lost"
symptom.

**Operator's actual intent** (clarified in session):
> Linking/moving nodes on the graph should be a *visual/layout* concern — a
> way to declutter and group related nodes ("this note belongs near shipx")
> — **WITHOUT mutating the underlying memory** (no new beliefs, no slug
> entities, no orphaning).

The current `link` / `repoint` / `merge` actions all write to `beliefs`.
There is no "visual association" layer. That's the gap.

## The core design decision

**Graph associations are a separate store, NOT beliefs.**

A new `graph_associations` table (in `memory_state.db` — it's hot UI-read
data, not ops-queue work) records operator-defined groupings that exist
only for the canvas. The `beliefs` table is never touched by grouping
actions. This cleanly separates:

| Layer | Store | Mutated by | Example |
|-------|-------|------------|---------|
| **Memory (truth)** | `beliefs` | recall, extraction, link/repoint/merge | `user →has_project→ shipx` |
| **View (layout)** | `graph_associations` (NEW) | grouping actions only | "the ShipX-Deployment-Modes note node is grouped under shipx" |

This is the only design that satisfies "moving a sub-node to other main
nodes does not affect my memory at all."

## Schema (proposed)

```sql
CREATE TABLE IF NOT EXISTS graph_associations (
  id            TEXT PRIMARY KEY,          -- assoc_<uuid>
  tenant_id     TEXT NOT NULL DEFAULT 'default',
  group_root    TEXT NOT NULL,             -- the "main node" id (entity id or blob text)
  member        TEXT NOT NULL,             -- the node being grouped (entity id or blob text)
  label         TEXT,                      -- optional operator note, e.g. "deployment notes"
  created_at    REAL NOT NULL,
  created_by    TEXT DEFAULT 'operator',   -- operator | auto (future: auto-cluster)
  metadata_json TEXT DEFAULT '{}',
  UNIQUE(tenant_id, group_root, member)
);
CREATE INDEX IF NOT EXISTS idx_graph_assoc_root
  ON graph_associations(tenant_id, group_root);
```

Both `group_root` and `member` are stored as raw node ids (entity id OR
virtual-fact text). The table is purely advisory — recall/extraction never
read it. Deleting a grouping never touches `beliefs`.

## API (proposed)

All under `/api/memory/v2/graph/groups`:

| Route | Effect |
|-------|--------|
| `GET /` | list groupings for the active tenant (for canvas render) |
| `POST /` | `{group_root, member, label?}` — create a grouping |
| `DELETE /{id}` | remove a grouping (view-only; no belief change) |
| `POST /member/{member_id}/move` | `{new_root}` — atomically move a member from one group to another (the "move sub-node to other main node" action). Removes the old grouping, adds the new one, in one transaction. |

Crucially: **none of these touch `beliefs`**. The response explicitly
includes `memory_affected: false` so the UI can reassure the operator.

## Canvas behavior (proposed)

1. **Render groups as a visual cluster.** When a node is a `group_root`,
   its members render nearby with a soft bounding halo (a translucent
   rounded rect behind the cluster), labeled with the group's label if any.
   This is the "reduce clutter / see what's linked" win.
2. **New action: "Group under" (or drag-onto).** On a node (including a
   blob/virtual-fact node), "Group under →" enters pick mode → click the
   main node → `POST /group` creates the association. Distinct from Link
   (which creates a belief) — labeled clearly so the operator knows which
   is which:
   - **Link** = creates a real memory edge (mutates `beliefs`).
   - **Group under** = visual grouping only (no memory change).
3. **Moving a member between groups** = `POST /member/{id}/move`. No memory
   change; the member just visually relocates to the new cluster.
4. **Filter interaction.** Grouping is independent of the predicate-type
   filter. A grouped blob stays in its cluster even when its belief edge
   is filtered out (it's shown dimmed per F2, but its group membership
   still anchors it visually to the root).
5. **Blob nodes specifically.** A grouped blob node renders as a compact
   chip (`ShipX — Deployment Modes` + a `…` expandable) inside the cluster,
   not as a giant node. The full text is available on click/expand. This is
   the clean answer to "1000-char essays shouldn't be huge nodes."

## What this does NOT change

- **Recall, extraction, schedulers, queue** — untouched. `graph_associations`
  is never read by the memory engine.
- **The existing link/repoint/merge actions** — kept as-is. They remain the
  way to create *real* memory edges when you actually want one. The
  grouping layer is additive.
- **The `noted` overuse problem** — not solved by this (that's an
  extraction-side fix, see MEMORY_REMAINING). Grouping just makes the
  existing `noted` blobs manageable in the view.

## Relationship to the shipped virtual-node link fix

The `subject_id` opt-in fix (commit `1fb06ac9`) is still valid and shipped:
it makes `link` attach to the clicked node verbatim for nodes ≤200 chars.
For the blob case (>200 chars), the grouping layer is the *correct* answer
rather than forcing a blob into `entities.id`. The two coexist:
- Normal nodes you want as real memory edges → Link (now works verbatim).
- Blob/view-only associations → Group under (new).

## Out of scope (deferred)

- **Auto-clustering** (suggested groupings from belief density) — future
  enhancement once manual grouping is proven.
- **Persisting node positions per-group** — the existing `_v2gPosCache`
  already handles pinned positions; grouping adds the halo, not new
  position logic.
- **The `noted` extraction overuse** — separate, see MEMORY_REMAINING.md.

## Files this would touch (for the fresh-session implementer)

| File | Change | Risk |
|------|--------|------|
| `kazma-core/kazma_core/memory/schema_v2.py` | Add `graph_associations` table to `PRIMARY_DDL` (idempotent CREATE) | Low |
| `kazma-ui/kazma_ui/memory_api.py` (or a new `graph_groups_api.py`) | The 4 group routes | Medium — new routes, but all read/write the new table only |
| `kazma-ui/kazma_ui/routes_direct.py` | Include groupings in the `/graph` payload (a `groups` field) so the canvas can render halos | Low — additive |
| `kazma-ui/kazma_ui/static/js/memory_console.js` | "Group under" action + pick mode + halo rendering + blob-chip compaction | Medium — canvas changes, additive |
| `kazma-ui/kazma_ui/templates/components/memory_console.html` | "Group under" button in node inspector | Low |
| Tests | group create/list/move/delete; confirm `beliefs` row count unchanged after any group op | — |

## Open questions for the implementer

1. Should groupings survive an entity merge? (If `shipx` merges into
   `kazma`, do groupings rooted at `shipx` repoint to `kazma`?) Tentative:
   yes — `merge_entities` should rewrite `graph_associations.group_root`
   like it rewrites beliefs.
2. Multi-tenant scoping — groupings are tenant-scoped (the table has
   `tenant_id`), consistent with everything else.
3. Should the canvas offer "ungroup all" / "disband cluster" as a bulk
   action on a root? Probably yes for usability.

## Sequencing suggestion

1. Schema + 4 routes + tests (the backend is small and isolated).
2. Canvas "Group under" action + halo rendering.
3. Blob-chip compaction (the visual win for 1000-char nodes).
4. Merge-integration (groupings follow entity merges).

Each step is independently shippable.
