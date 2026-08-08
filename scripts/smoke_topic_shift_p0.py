"""P0 smoke: subject-persistence intent policy (no server required).

Run:  .venv\\Scripts\\python.exe scripts/smoke_topic_shift_p0.py
"""

from __future__ import annotations

import sys

from kazma_core.agent.topic_drift import should_stub_prior_tools, topic_drift_config
from kazma_core.agent.turn_input import classify_turn_intent, latest_turn_priority_note


def main() -> int:
    cfg = topic_drift_config()
    print("topic_drift_config:", cfg)

    history = [
        {
            "role": "user",
            "content": (
                "Please clean up the memory graph entities for kazma and ShipX "
                "and merge junk true/false nodes under Mubder hierarchy"
            ),
        },
        {"role": "assistant", "content": "I will list entities and merge junk nodes."},
    ]
    goal = history[0]["content"]

    cases: list[tuple[str, str, str, str, str]] = [
        # name, text, status, goal, expected_mode
        ("pivot_en", "What's the weather?", "in_progress", goal, "shift"),
        ("pivot_ar", "موضوع ثاني وش الطقس", "in_progress", goal, "shift"),
        ("proceed", "Proceed", "in_progress", goal, "continue"),
        ("try_now", "try now", "in_progress", goal, "continue"),
        ("ok_after_done", "ok", "completed", goal, "normal"),
        (
            "store",
            "Now add this to the ShipX memory:\n\nOverview of ShipX\n" + ("x" * 700),
            "in_progress",
            "reminder about ZCode quota",
            "store",
        ),
        (
            "related_followup",
            "Also link ShipX under kazma in the graph please",
            "in_progress",
            goal,
            "normal",  # related; may be shift only if embed is very aggressive
        ),
    ]

    failed = 0
    for name, text, status, g, expected in cases:
        mode = classify_turn_intent(
            text,
            messages=history,
            task_status=status,
            task_goal_summary=g,
            use_embedding_drift=False,
        )
        mode_emb = classify_turn_intent(
            text,
            messages=history,
            task_status=status,
            task_goal_summary=g,
            use_embedding_drift=True,
        )
        stub = should_stub_prior_tools(intent_mode=mode, prev_task_status=status)
        note = latest_turn_priority_note(topic_shift=(mode == "shift"))
        ok = mode == expected
        if name == "related_followup" and mode in ("normal", "shift"):
            # Related graph follow-up is acceptable as normal; shift would be
            # over-aggressive but we only fail hard if it becomes continue.
            ok = mode != "continue"
        if not ok:
            failed += 1
        flag = "OK" if ok else "FAIL"
        print(
            f"[{flag}] {name:18} mode={mode:12} emb={mode_emb:12} "
            f"stub={str(stub):5} expected={expected}"
        )
        if mode == "shift" and "SUPERSEDED" not in note:
            print("  WARN: shift note missing SUPERSEDED")
            failed += 1
        if mode == "continue" and stub:
            print("  WARN: continue should not stub tools")
            failed += 1

    print()
    if failed:
        print(f"FAILED ({failed} checks)")
        return 1
    print("P0 smoke passed — restart server and spot-check Web/Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
