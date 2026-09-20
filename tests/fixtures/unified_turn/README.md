# Shared unified-turn fixtures

`docs/plans/UNIFIED_TURN_BLOCK.md` §6:

> Use shared JSON fixtures to prove Python normalization/serialization and
> JavaScript projection agree. Do not rely on two implementations with
> independently written examples.

Each file in `messages/` is one stored assistant row plus the projection both
languages must produce from it. Two drivers read the same files:

- `tests/test_unified_turn_fixtures.py` — `kazma_ui.turn_document`
- `tests/js/test_unified_turn_fixtures.js` — `static/js/modules/turn_document.js`

The Python test also shells out to `node` and compares the two results
field by field, so a divergence fails once with a diff instead of twice with
two green suites.

`layout/four_sequential_gates.json` is the **agreed layout fixture** of Phase
0: the canonical scenario the acceptance matrix, the app-graph harness and
the convergence oracle all describe the same way.

## Canonical part key

Python `_part_key` returns a tuple, JavaScript `partKey` returns a string.
Fixtures record the **string** form; the Python driver joins the tuple with
`":"`. That is an exact match for every part type the projector models
(`text`, `reasoning`, `tool`, `status`, `hitl`). The two implementations
diverge only in the unknown-type fallback (`JSON.stringify` vs `repr`), which
no fixture exercises and which Phase 1 must either align or delete.
