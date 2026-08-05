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
  member_tier   INTEGER DEFAULT 1,         -- 0=main(hub), 1=major, 2=sub, 3=leaf (see Tier model)
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
read it. Deleting a grouping never touches `beliefs`. `member_tier` carries
the hierarchy depth for coloring + tree layout (see Tier model).

## Tier model (the A/B/C/D hierarchy)

The operator models the graph as a **rooted tree of tiers**, not a flat
graph. Concretely:

| Tier | Name  | Example | Color (canvas) |
|------|-------|---------|----------------|
| 0    | main  | **Mubder** (the hub, id=`user`) | amber/orange (current hub color) |
| 1    | major | **kazma**, **shipx**, **kca** (the big projects) | distinct per-tier (e.g. cyan) |
| 2    | sub   | **kazma_app**, **shipx_pre_production_ops** (children of a major) | distinct per-tier (e.g. blue) |
| 3    | leaf  | the gmail-callback URL, a config note, a `noted` blob | distinct per-tier (e.g. slate/grey) |

Rules:
- **Tier 0 is always the hub** (`user` / "Mubder"). Auto-assigned, not
  groupable, not removable. Keeps the existing amber hub styling.
- **Tiers 1–3 are assigned via grouping.** When you "Group under →" a node,
  the member's tier defaults to `(parent's tier) + 1`. So grouping
  `kazma_app` under `kazma` (tier 1) makes `kazma_app` tier 2. Grouping the
  gmail callback under `kazma_app` makes it tier 3.
- **Tier overrides the entity-type color** when a node is part of the
  hierarchy. Ungrouped nodes keep the existing type-based color. So you get
  per-tier coloring for the things you've organized, and type-based color
  for the rest.
- **Colors are a per-tier palette**, defined once in CSS/JS, not per-node.
  Changing the tier-2 color re-colors all tier-2 nodes at once.

## API (proposed)

All under `/api/memory/v2/graph/groups`:

| Route | Effect |
|-------|--------|
| `GET /` | list groupings for the active tenant (for canvas render) — each row carries `group_root`, `member`, `member_tier`, `label` |
| `POST /` | `{group_root, member, label?, tier?}` — create a grouping. `tier` defaults to `(parent tier) + 1`. Rejects if it would create a cycle (member is its own ancestor). |
| `DELETE /{id}` | remove a grouping (view-only; no belief change). Children of the removed member become ungrouped (revert to type-based color + free layout). |
| `POST /member/{member_id}/move` | `{new_root, tier?}` — atomically move a member to a new group. Removes old grouping, adds new one, re-tiers the moved subtree (`member` and its descendants shift by the delta), in one transaction. |
| `POST /node/{node_id}/tier` | `{tier}` — manually override a node's tier (for when the auto `parent+1` is wrong). |

Crucially: **none of these touch `beliefs`**. The response explicitly
includes `memory_affected: false` so the UI can reassure the operator.

## Canvas behavior (proposed)

1. **Render groups as a visual cluster (tree layout).** When a node is a
   `group_root`, its members render in an **orbital ring around the root**,
   not in the free force-sim. The root sits at the cluster's center; tier-1
   members in an inner ring, tier-2 members in a ring around their tier-1
   parent, and so on — a **radial tree**, where each major node (B) holds
   its sub-nodes (C) around it, and C holds its leaves (D) around it.
   This is the "B close and holding its C nodes around, like a tree" ask.
   - Distances are **tier-relative**: tier-1 ring radius R1, tier-2 ring
     radius R2 (smaller, tighter), tier-3 tightest. Configurable constants.
   - **Not fixed/sticky** — the whole cluster can still be dragged, and the
     force sim nudges ungrouped nodes around it. But within a cluster, the
     parent-child distance is held by a stiff spring, so children don't
     collapse onto the parent or drift away. Each tier has its own spring
     length + strength.
   - **Cross-cluster belief edges** still render (a belief from a tier-2
     node to a different major's tier-1 node draws as a normal edge), but
     they don't pull the node out of its cluster — the cluster spring wins
     over the cross-edge spring.
2. **Tier colors.** Each node's fill follows the per-tier palette (see Tier
   model) when it's part of the hierarchy; ungrouped nodes use the existing
   entity-type color. Tier 0 (hub) keeps its amber/orange. So at a glance:
   amber hub → cyan majors → blue subs → slate leaves.
3. **Soft halo per cluster.** Each major node's cluster gets a translucent
   rounded halo behind it, tinted faintly by the major's tier color, so you
   see "this whole region belongs to kazma." Labeled with the group label
   if any.
4. **New action: "Group under" (or drag-onto).** On a node (including a
   blob/virtual-fact node), "Group under →" enters pick mode → click the
   parent node → `POST /group` creates the association and assigns the tier.
   Distinct from Link (which creates a belief) — labeled clearly so the
   operator knows which is which:
   - **Link** = creates a real memory edge (mutates `beliefs`).
   - **Group under** = visual grouping only (no memory change, tier + tree
     position assigned).
5. **Moving a member between groups** = `POST /member/{id}/move`. No memory
   change; the member (and its subtree) relocates to the new cluster and
   re-tiers.
6. **Filter interaction.** Grouping is independent of the predicate-type
   filter. A grouped blob stays in its cluster even when its belief edge
   is filtered out (it's shown dimmed per F2, but its group membership
   still anchors it visually to the root).
7. **Blob nodes specifically.** A grouped blob node renders as a compact
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
- **The `noted` extraction overuse** — separate, see MEMORY_REMAINING.md.

## Files this would touch (for the fresh-session implementer)

| File | Change | Risk |
|------|--------|------|
| `kazma-core/kazma_core/memory/schema_v2.py` | Add `graph_associations` table (idempotent CREATE) + `member_tier` column | Low |
| `kazma-ui/kazma_ui/memory_api.py` (or a new `graph_groups_api.py`) | The 5 group routes (list/create/delete/move/tier) | Medium — new routes, but all read/write the new table only |
| `kazma-ui/kazma_ui/routes_direct.py` | Include groupings (with tiers) in the `/graph` payload as a `groups` field so the canvas can render halos + tiers + tree | Low — additive |
| `kazma-ui/kazma_ui/static/js/memory_console.js` | "Group under" action + pick mode; **per-tier color palette** (overrides type-color for grouped nodes); **radial-tree layout** within clusters (tier-relative springs, halo per cluster); blob-chip compaction | High — canvas layout changes are the most delicate part; tier color + tree layout both touch `_v2gDrawCanvas` + the force step |
| `kazma-ui/kazma_ui/static/css/kazma.css` | Tier color variables (`--tier-0` … `--tier-3`) | Low |
| `kazma-ui/kazma_ui/templates/components/memory_console.html` | "Group under" + "Set tier" buttons in node inspector | Low |
| Tests | group create/list/move/delete + tier assignment; cycle rejection; confirm `beliefs` row count unchanged after any group op; subtree re-tiers on move | — |

## Implementation order (revised)

The tree-layout + tier-color work is the highest-risk part (it touches the
force sim + paint loop). Build bottom-up so each step is independently
shippable and testable:

1. **Schema + 5 routes + tests.** Pure backend, isolated. Ship alone.
2. **Tier colors only** (no layout change). The `/graph` payload carries
   tiers; the canvas picks fill from the tier palette instead of type-color
   for grouped nodes. Visual win, low risk — no force-sim change.
3. **"Group under" action + halo.** Creates associations; renders the soft
   cluster halo. Nodes still use the free force-sim (no tree yet).
4. **Radial-tree layout within clusters.** The hard part: tier-relative
   springs, parent-child distance holding, cross-cluster edges don't pull
   nodes out. Land last, behind a fallback to free-sim if anything regresses.
5. **Blob-chip compaction.** Cosmetic, after the layout is stable.
6. **Merge-integration** (groupings + tiers follow entity merges).

## Open questions for the implementer

1. Should groupings + tiers survive an entity merge? (If `shipx` merges into
   `kazma`, do groupings rooted at `shipx` repoint to `kazma`, and do
   `shipx`'s children keep their tiers under the new root?) Tentative: yes
   — `merge_entities` should rewrite `graph_associations.group_root` like it
   rewrites beliefs, and the children's tiers stay relative to the new root.
2. Multi-tenant scoping — groupings are tenant-scoped (the table has
   `tenant_id`), consistent with everything else.
3. Should the canvas offer "ungroup all" / "disband cluster" as a bulk
   action on a root? Probably yes for usability.
4. **Tree-layout vs pinned positions.** When an operator manually drags a
   node, does the tree spring re-assert on next reload, or does the pinned
   position win? Tentative: pinned wins (matches current `_v2gPosCache`
   behavior), with a "re-layout cluster" action to re-assert the tree.
5. **Tier cap.** Should tiers be capped (e.g. max tier 4) to avoid
   degenerate deep chains? Tentative: soft cap at 4, warn beyond.

Each step in the implementation order is independently shippable.
