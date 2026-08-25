#!/usr/bin/env python
"""Run the industry eval pack (golden trajectories, no live LLM).

Exit 0 only if every case in tests/fixtures/eval_pack.json passes.
CI already gates this via tests/test_eval_pack.py in scripts/fast_test.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(REPO / "tests" / "test_eval_pack.py"),
        "-q",
        "--tb=short",
        "-m",
        "eval",
    ]
    print("eval pack:", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(REPO))


if __name__ == "__main__":
    raise SystemExit(main())
