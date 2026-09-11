#!/usr/bin/env python
"""Score Kazma's prompt-injection defenses and print the result.

    python scripts/injection_report.py
    python scripts/injection_report.py --sync   # rewrite measured fields

Two numbers, measured against ``tests/fixtures/injection_corpus.json`` with no
model in the loop:

* **Containment** — payloads that cannot break out of the untrusted-data
  fence. A hard gate; anything below 100% is a defect.
* **Denylist** — of the payloads whose purpose is to plant a standing
  directive in a future system prompt, how many ``filter_injection`` refuses
  to persist. Currently below 100%; the gap is named rather than hidden.

``--sync`` re-records the measured ``persistable`` field after you change the
denylist, so the fixture never drifts from reality. Read what it changes
before committing it.

Exit code is 0 unless containment is broken or the denylist regressed past the
ceiling in ``tests/test_injection_containment.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "tests" / "fixtures" / "injection_corpus.json"

sys.path.insert(0, str(REPO / "kazma-core"))


def _load() -> dict:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sync",
        action="store_true",
        help="rewrite the measured `persistable` field from current behaviour",
    )
    args = parser.parse_args()

    from kazma_core.safety.prompt_fence import filter_injection, format_untrusted_block
    data = _load()
    ceiling = int(data["denylist_miss_ceiling"])
    cases = data["cases"]

    escaped: list[str] = []
    for case in cases:
        fenced = format_untrusted_block(case["payload"], source="web")
        if (
            fenced.count("</kazma:data>") != 1
            or fenced.count("--- BEGIN OBSERVATION ---") != 1
            or fenced.count("--- END OBSERVATION ---") != 1
        ):
            escaped.append(case["id"])

    if args.sync:
        for case in cases:
            case["persistable"] = filter_injection(case["payload"]) is not None
        CORPUS.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"synced measured fields into {CORPUS.relative_to(REPO)}\n")

    accountable = [c for c in cases if c.get("denylist_should_catch")]
    missed = [c for c in accountable if filter_injection(c["payload"]) is not None]
    caught = len(accountable) - len(missed)

    by_cat: dict[str, int] = {}
    for case in cases:
        by_cat[case["category"]] = by_cat.get(case["category"], 0) + 1

    print("Kazma prompt-injection report")
    print("=" * 58)
    print(f"corpus            {len(cases)} payloads")
    for cat in sorted(by_cat):
        print(f"    {cat:<22} {by_cat[cat]}")
    print()
    print(f"containment       {len(cases) - len(escaped)}/{len(cases)}   (hard gate)")
    if escaped:
        for cid in escaped:
            print(f"    ESCAPED  {cid}")
    print(
        f"denylist          {caught}/{len(accountable)}   "
        f"(ratchet, ceiling {ceiling} misses)"
    )
    for case in missed:
        print(f"    missed   {case['id']:<30} {case['payload'][:46]}")
    print()
    print("Structural only — no model was called. Containment proves a payload")
    print("cannot forge the fence, not that a model obeys it. See docs/INJECTION.md.")

    if escaped:
        print("\nFAIL: fence escape", file=sys.stderr)
        return 1
    if len(missed) > ceiling:
        print("\nFAIL: denylist regressed past its ceiling", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
