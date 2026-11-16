"""The advisories OSV-Scanner is told to pass over are the reviewed ones.

OpenSSF Scorecard's Vulnerabilities check runs OSV-Scanner, which reads
``osv-scanner.toml`` beside each lock. ``osv-scanner.toml`` (uv.lock) lists
exactly the advisories ``dependency_scanner.REVIEWED_ADVISORIES`` reviews as
out of Kazma's reach -- each under its GitHub id with the package first in
its reason, and its PyPI id naming that GitHub id -- so an ignore with no
review behind it, or a review the scanner is not told, fails here.
``docs/osv-scanner.toml`` (the docs site's build packages) names the package
and version each entry is about, and the lock must still hold exactly that
version: an ignore outlives no update. Negative controls: a planted ignore
and a stale version are found.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from kazma_core.security.dependency_scanner import REVIEWED_ADVISORIES

ROOT = Path(__file__).resolve().parents[1]


def _ignores(path: Path) -> list[dict]:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("IgnoredVulns", [])


def root_problems(entries: list[dict]) -> list[str]:
    problems = []
    ghsa = {e["id"]: e for e in entries if e["id"].startswith("GHSA-")}
    for vuln_id, review in REVIEWED_ADVISORIES.items():
        if vuln_id not in ghsa:
            problems.append(f"{vuln_id} is reviewed but not in osv-scanner.toml")
        elif not ghsa[vuln_id].get("reason", "").startswith(review.package + ":"):
            problems.append(f"{vuln_id}: the reason must start with '{review.package}:'")
    for entry in entries:
        vuln_id, reason = entry["id"], entry.get("reason", "")
        if not reason:
            problems.append(f"{vuln_id} has no reason")
        if vuln_id.startswith("GHSA-"):
            if vuln_id not in REVIEWED_ADVISORIES:
                problems.append(f"{vuln_id} is ignored with no review in REVIEWED_ADVISORIES")
            continue
        named = re.match(r"(GHSA-[\w-]+) \(the same advisory\)", reason)
        if not named or named.group(1) not in REVIEWED_ADVISORIES:
            problems.append(f"{vuln_id} names no reviewed GitHub advisory")
    return problems


def docs_problems(entries: list[dict], lock: dict) -> list[str]:
    problems = []
    packages = lock.get("packages", {})
    for entry in entries:
        match = re.match(r"([@\w./-]+) ([\w.+-]+) ", entry.get("reason", ""))
        if not match:
            problems.append(f"{entry['id']}: the reason must start with '<package> <version> '")
            continue
        name, version = match.groups()
        held = {meta.get("version") for key, meta in packages.items()
                if key.endswith("node_modules/" + name)}
        if version not in held:
            problems.append(f"{entry['id']}: docs/package-lock.json holds {name} {sorted(held)}, not {version}")
    return problems


def test_the_root_ignores_are_the_reviewed_advisories():
    assert not root_problems(_ignores(ROOT / "osv-scanner.toml"))


def test_each_docs_ignore_names_the_version_the_lock_holds():
    lock = json.loads((ROOT / "docs" / "package-lock.json").read_text(encoding="utf-8"))
    assert not docs_problems(_ignores(ROOT / "docs" / "osv-scanner.toml"), lock)


def test_control_an_unreviewed_ignore_is_found():
    planted = _ignores(ROOT / "osv-scanner.toml") + [{"id": "GHSA-aaaa-bbbb-cccc", "reason": "x: trust me"}]
    assert any("no review" in p for p in root_problems(planted))


def test_control_a_stale_docs_ignore_is_found():
    lock = {"packages": {"node_modules/deepmerge": {"version": "4.3.2"}}}
    entries = [{"id": "GHSA-gcqx-w48m-w2h4", "reason": "deepmerge 4.3.1 (no fixed release): ..."}]
    assert docs_problems(entries, lock)
