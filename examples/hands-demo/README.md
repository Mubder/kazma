# Hands demo (Kazma 0.11)

Eight-minute tape. Switch the workspace to this folder first.

1. Open `/` (chat). If first-run, enter a key + model.
2. Teach: “Remember: the board meeting is Tuesday 16 September 2026 at 10:00.”
3. “This test is red. Fix `add` so `test_add` passes. Use a patch, not a full rewrite.”
4. Approve the HITL card (`file_apply_patch_set`).
5. Run `pytest test_app.py` here — it should go green.
6. IDE → **Undo patch** restores the last checkpoint if you hate the edit.
7. “Remind me next Tuesday about the board meeting.” The time must come from memory, not an invented date.
8. `python scripts/eval_pack.py` from the repo root (no live LLM).
