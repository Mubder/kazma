"""Record the OSV advisories for installed packages, for the floor gate.

``tests/test_dependency_floors.py`` holds pyproject's security floors to a
recorded OSV answer (2026-11 functional audit, FA-258: five advisory packages
had no floor, and pillow's sat below its fix). This writes that recording.

    <python> scripts/record_osv_advisories.py --python <live python> --python <locked python> [--write]

``--python`` names an environment to read installed versions from; give the
live install's (what live runs) and one synced from uv.lock (what a fresh
install gets: this checkout's .venv). Default: this one. Without ``--write`` it prints what it would record. Reads only: the
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
    # Several: the floors hold what live runs AND what a fresh install gets
    # from the lock (an environment synced from it). Recorded from live
    # alone, a fresh install's older locked versions went unchecked: the
    # drill VM (2026-11-06) installed certifi, oauthlib and pypdf below their
    # fixes, past a green gate.
    parser.add_argument("--python", action="append",
                        help="an interpreter whose installed versions to check (repeatable)")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    pythons = args.python or [sys.executable]
    sys.path.insert(0, str(REPO / "kazma-core"))
    from kazma_core.security import dependency_scanner as scanner

    found: dict[tuple[str, str, str], dict] = {}
    for python in pythons:
        report = asyncio.run(scanner._audit_packages(_installed(python)))
        for v in report.vulnerabilities:
            row = {"package": v.package, "version": v.version, "vuln_id": v.vuln_id,
                   "fixed_version": v.fixed_version}
            found[(v.package, v.vuln_id, v.version)] = row
    # Rows only accumulate: an advisory recorded once keeps holding its
    # floor after the environments moved past it (a floor that nothing
    # held could be lowered with the gate green).
    if FIXTURE.is_file():
        for row in json.loads(FIXTURE.read_text(encoding="utf-8")).get("advisories", []):
            found.setdefault((row["package"], row["vuln_id"], row["version"]), row)
    rows = sorted(found.values(), key=lambda r: (r["package"], r["vuln_id"], r["version"]))
    record = {"recorded": date.today().isoformat(),
              "source": "OSV, for the versions installed in " + ", ".join(pythons)
                        + " (added to the earlier recordings)", "advisories": rows}
    text = json.dumps(record, indent=1) + "\n"
    if args.write:
        FIXTURE.write_text(text, encoding="utf-8")
        print(f"wrote {len(rows)} advisories to {FIXTURE.relative_to(REPO)}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
