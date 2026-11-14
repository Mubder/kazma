"""Is a fresh install's Kazma ready, or not ready only because its owner has a
model to set up? CI's fresh-install gate asks this of /health/ready.

    python scripts/fresh_install_ready.py <http status> <ready.json>

Exit 0 when the install is fine, 1 with the reason otherwise. Standard library
only. The gate grepped the answer for ``"needs_setup": true``, which readiness
never writes (it lists the checks: ``"needs_setup":["llm_provider"]``), so it
refused every fresh install from the day the job was added (2026-11-14).
"""

from __future__ import annotations

import json
import sys


def verdict(status: int, body: dict) -> str | None:
    """None when the install is fine, else why it is not."""
    if status == 200:
        return None
    need = set(body.get("needs_setup") or ())
    down = set(body.get("unavailable_capabilities") or ())
    if not need:
        return f"not ready (HTTP {status}), and not for setup: {sorted(down) or body.get('status')}"
    if not down <= need:
        return f"not ready for more than setup: {sorted(down - need)}"
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        body = json.load(fh)
    why = verdict(int(argv[0]), body)
    if why:
        print(f"fresh install: {why}")
        return 1
    print("fresh install: ready" if int(argv[0]) == 200
          else f"fresh install: up, waiting for its owner to set up {sorted(body['needs_setup'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
