"""Record the OSV advisories for installed packages, for the floor gate.

``tests/test_dependency_floors.py`` holds pyproject's security floors to a
recorded OSV answer (2026-11 functional audit, FA-258: five advisory packages
had no floor, and pillow's sat below its fix). This writes that recording.

    <python> scripts/record_osv_advisories.py --python <interpreter> [--write]

``--python`` names the environment to read installed versions from (the live
install's interpreter, so the recording is what live runs); default: this
one. Without ``--write`` it prints what it would record. Reads only: the
named interpreter lists its distributions, OSV is asked over the network.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "osv_advisories.json"
_LIST = (
    "import importlib.metadata as m, json\n"
    "print(json.dumps(sorted({(d.metadata['Name'] or '').lower(): d.version "
    "for d in m.distributions() if d.metadata['Name']}.items())))\n"
)


def _installed(python: str) -> list[tuple[str, str]]:
    out = subprocess.run([python, "-I", "-c", _LIST], capture_output=True, text=True, check=True, timeout=120)
    return [tuple(pair) for pair in json.loads(out.stdout)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(REPO / "kazma-core"))
    from kazma_core.security import dependency_scanner as scanner

    report = asyncio.run(scanner._audit_packages(_installed(args.python)))
    rows = sorted(
        ({"package": v.package, "version": v.version, "vuln_id": v.vuln_id, "fixed_version": v.fixed_version}
         for v in report.vulnerabilities),
        key=lambda r: (r["package"], r["vuln_id"]),
    )
    record = {"recorded": date.today().isoformat(),
              "source": f"OSV, for the versions installed in {args.python}", "advisories": rows}
    text = json.dumps(record, indent=1) + "\n"
    if args.write:
        FIXTURE.write_text(text, encoding="utf-8")
        print(f"wrote {len(rows)} advisories to {FIXTURE.relative_to(REPO)}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
