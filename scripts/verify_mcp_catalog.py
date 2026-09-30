"""Check every MCP preset's package against its registry, and record what was found.

The /mcp page's presets (``kazma-skills/kazma_skills/certified_servers.yaml``)
each run a package by name -- ``npx -y <npm package>`` or ``uvx <PyPI
package>``. Until 2026-09-30 78 of its 81 entries named packages no registry
held. This script asks npm and PyPI about every preset's package and writes
what they said to ``tests/fixtures/mcp_catalog_registry.json``: that it
exists, its latest version, whether that version is deprecated, and who
publishes it (the source repository each registry names). The test
``tests/test_mcp_catalog.py`` holds the catalog to that record offline.

    python scripts/verify_mcp_catalog.py           # check, print, exit 1 on a problem
    python scripts/verify_mcp_catalog.py --write   # check and refresh the record

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "kazma-skills" / "kazma_skills" / "certified_servers.yaml"
RECORD = REPO / "tests" / "fixtures" / "mcp_catalog_registry.json"
_TIMEOUT = 20


def _get_json(url: str) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "kazma-verify-mcp-catalog"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:  # nosec B310 - fixed https registry URLs
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, None


def check_npm(name: str) -> dict[str, Any]:
    status, doc = _get_json("https://registry.npmjs.org/" + urllib.parse.quote(name, safe="@"))
    if status != 200 or not isinstance(doc, dict):
        return {"exists": False, "status": status}
    latest = (doc.get("dist-tags") or {}).get("latest", "")
    version = (doc.get("versions") or {}).get(latest) or {}
    repo = doc.get("repository")
    repo_url = repo.get("url", "") if isinstance(repo, dict) else str(repo or "")
    return {
        "exists": True,
        "latest": latest,
        "deprecated": bool(version.get("deprecated")),
        "published": str((doc.get("time") or {}).get(latest, ""))[:10],
        "repository": repo_url,
    }


def check_pypi(name: str) -> dict[str, Any]:
    status, doc = _get_json(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")
    if status != 200 or not isinstance(doc, dict):
        return {"exists": False, "status": status}
    info = doc.get("info") or {}
    latest = info.get("version", "")
    files = (doc.get("releases") or {}).get(latest) or []
    urls = info.get("project_urls") or {}
    repo = urls.get("Source") or urls.get("Repository") or info.get("home_page") or ""
    return {
        "exists": True,
        "latest": latest,
        "deprecated": all(f.get("yanked") for f in files) if files else False,
        "published": str(files[0].get("upload_time", ""))[:10] if files else "",
        "repository": repo,
    }


def load_catalog() -> dict[str, dict[str, Any]]:
    import yaml

    data = yaml.safe_load(CATALOG.read_text(encoding="utf-8")) or {}
    servers = data.get("servers") or {}
    return {sid: cfg for sid, cfg in servers.items() if isinstance(cfg, dict)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--write", action="store_true", help="refresh the record")
    args = parser.parse_args()

    packages: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for sid, cfg in sorted(load_catalog().items()):
        pkg = cfg.get("package") or {}
        registry, name = pkg.get("registry"), pkg.get("name")
        if registry not in ("npm", "pypi") or not name:
            problems.append(f"{sid}: no package {{registry, name}}")
            continue
        key = f"{registry}:{name}"
        if key not in packages:
            packages[key] = check_npm(name) if registry == "npm" else check_pypi(name)
        found = packages[key]
        state = "missing" if not found.get("exists") else ("DEPRECATED" if found.get("deprecated") else "ok")
        print(f"{state:10} {sid:22} {key}  {found.get('latest', '')}  {found.get('repository', '')}")
        if state != "ok":
            problems.append(f"{sid}: {key} is {state}")

    if args.write and not problems:
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        record = {"checked": datetime.now(UTC).strftime("%Y-%m-%d"), "packages": packages}
        RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {RECORD.relative_to(REPO)} ({len(packages)} packages)")
    for problem in problems:
        print("PROBLEM:", problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
