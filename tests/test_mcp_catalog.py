"""Every MCP preset runs a package that exists, is maintained, and is on record.

Until 2026-09-30 the /mcp page offered 81 "certified" servers ("Last verified
2026-06-20"). 78 named packages no registry has ever held
(``@anthropic-ai/*-mcp``, ``@modelcontextprotocol/server-git`` ...), so adding
one failed its connection test; the SQLite entry pointed a server at Kazma's
own settings database; and the page's own "Time" preset ran
``@modelcontextprotocol/server-time``, which is not on npm either (the time
server is the PyPI package ``mcp-server-time``). ``kazma_ui/mcp_presets.py``
also found the file through the repository's folder layout, so an install
made from a release wheel showed none of them.

``scripts/verify_mcp_catalog.py --write`` asks npm and PyPI and records what
they said in ``tests/fixtures/mcp_catalog_registry.json``; this test holds the
presets to that record, offline, each rule with a negative control.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "kazma-skills" / "kazma_skills" / "certified_servers.yaml"
RECORD = json.loads((REPO / "tests" / "fixtures" / "mcp_catalog_registry.json").read_text(encoding="utf-8"))


def run_target(command: list[str]) -> tuple[str, str] | None:
    """``(registry, package)`` a command runs: ``npx -y <pkg>[@v]`` or ``uvx <pkg>[@v]``."""
    if not command:
        return None
    exe = Path(str(command[0])).name.lower()
    for suffix in (".cmd", ".exe"):
        exe = exe.removesuffix(suffix)
    registry = {"npx": "npm", "uvx": "pypi"}.get(exe)
    specs = [str(a) for a in command[1:] if not str(a).startswith("-")]
    if registry is None or not specs:
        return None
    spec = specs[0]
    if spec.startswith("@"):
        scope, _, rest = spec[1:].partition("/")
        return registry, "@" + scope + "/" + rest.split("@", 1)[0]
    return registry, re.split(r"@|==", spec, maxsplit=1)[0]


def _repo_slug(url: str) -> str:
    match = re.search(r"github\.com[/:]([^/]+)/([^/#?]+)", str(url))
    if not match:
        return ""
    return f"{match.group(1)}/{match.group(2).removesuffix('.git')}".lower()


def catalog_problems(presets: list[dict], record: dict) -> list[str]:
    problems = []
    for p in presets:
        target = run_target(p.get("command") or [])
        if target is None:
            problems.append(f"{p['id']}: not an npx/uvx package command")
            continue
        package = p.get("package") or {}
        declared = (package.get("registry"), package.get("name"))
        if declared != target:
            problems.append(f"{p['id']}: runs {target} but declares {declared}")
        entry = record["packages"].get(f"{target[0]}:{target[1]}")
        if not entry or not entry.get("exists"):
            problems.append(f"{p['id']}: {target[0]}:{target[1]} is not on record as published")
            continue
        if entry.get("deprecated"):
            problems.append(f"{p['id']}: {target[0]}:{target[1]} is deprecated")
        if not _repo_slug(p.get("source", "")) or _repo_slug(p["source"]) != _repo_slug(entry.get("repository", "")):
            problems.append(f"{p['id']}: source {p.get('source')!r} is not the repository the registry names")
    return problems


def _presets() -> list[dict]:
    from kazma_ui.mcp_presets import list_presets

    return list_presets()  # cached per process; every test reads the one file


def test_every_preset_runs_a_published_package_from_its_own_repository():
    presets = _presets()
    assert len(presets) == len(yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["servers"])
    problems = catalog_problems(presets, RECORD)
    assert not problems, (
        "A preset must run a package npm or PyPI has published, not deprecated, from "
        "the repository it names (python scripts/verify_mcp_catalog.py --write "
        "refreshes the record):\n  " + "\n  ".join(problems)
    )


def test_the_old_presets_fail_the_gate():
    """Negative control: three entries the page offered until 2026-09-30."""
    old = [
        {"id": "pypi_mcp", "command": ["npx", "-y", "@anthropic-ai/pypi-mcp"], "source": ""},
        {"id": "time", "command": ["npx", "-y", "@modelcontextprotocol/server-time"], "source": ""},
        {"id": "postgres", "command": ["npx", "-y", "@modelcontextprotocol/server-postgres"],
         "package": {"registry": "npm", "name": "@modelcontextprotocol/server-postgres"},
         "source": "https://github.com/modelcontextprotocol/servers"},
    ]
    record = {"packages": {**RECORD["packages"], "npm:@modelcontextprotocol/server-postgres": {
        "exists": True, "deprecated": True, "repository": "git+https://github.com/modelcontextprotocol/servers.git",
    }}}
    problems = catalog_problems(old, record)
    assert any("pypi_mcp" in p and "not on record" in p for p in problems)
    assert any(p.startswith("time:") and "not on record" in p for p in problems)
    assert any("postgres" in p and "deprecated" in p for p in problems)


def test_the_record_holds_only_packages_the_presets_run():
    used = {"{}:{}".format(*run_target(p["command"])) for p in _presets()}
    assert set(RECORD["packages"]) == used


def test_no_preset_points_at_kazmas_own_data():
    from kazma_core.store_registry import is_kazma_store

    offenders = [
        f"{p['id']}: {arg}"
        for p in _presets()
        for arg in p["command"]
        if "kazma-data" in str(arg) or str(arg).endswith(".db") and is_kazma_store(Path(str(arg)))
    ]
    assert not offenders, offenders
    old_sqlite = ["npx", "-y", "@modelcontextprotocol/server-sqlite", "--db-path", "kazma-data/kazma.db"]
    assert any("kazma-data" in a for a in old_sqlite), "the control: the old SQLite preset"


def test_a_preset_takes_its_key_from_the_environment_never_the_command():
    """Keys go in ``env``, which Kazma keeps in the vault (kazma_core.mcp.secrets)."""
    from kazma_core.mcp.secrets import _secret_name, _secret_paths

    for p in _presets():
        assert not [path for path in _secret_paths({"command": p["command"]}) if path[0] == "command"], p["id"]
        for key in p["env_keys"]:
            assert _secret_name(key), f"{p['id']}: {key} would not be kept in the vault"
    stripe_readme = {"command": ["npx", "-y", "@stripe/mcp", "--api-key=YOUR_STRIPE_SECRET_KEY"]}
    assert _secret_paths(stripe_readme), "the control: the key as Stripe's README passes it"


def test_the_presets_load_from_an_installed_wheel_layout(tmp_path):
    """kazma_ui and kazma_skills side by side in site-packages, no repository around them."""
    site = tmp_path / "site-packages"
    (site / "kazma_ui").mkdir(parents=True)
    (site / "kazma_ui" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(REPO / "kazma-ui" / "kazma_ui" / "mcp_presets.py", site / "kazma_ui" / "mcp_presets.py")
    shutil.copytree(REPO / "kazma-skills" / "kazma_skills", site / "kazma_skills",
                    ignore=shutil.ignore_patterns("native", "__pycache__"))
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import kazma_ui.mcp_presets as m, kazma_skills;"
        "assert kazma_skills.__file__.startswith(sys.argv[1]), kazma_skills.__file__;"
        "assert m.__file__.startswith(sys.argv[1]), m.__file__;"
        "print(len(m.list_presets()))"
    )
    result = subprocess.run([sys.executable, "-c", probe, str(site)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert int(result.stdout.strip()) == len(yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["servers"])
    # The control: the old lookup, from the same layout, finds nothing.
    old_path = (site / "kazma_ui" / "mcp_presets.py").resolve().parent.parent.parent / "kazma-skills" / "kazma_skills" / "certified_servers.yaml"
    assert not old_path.exists()
