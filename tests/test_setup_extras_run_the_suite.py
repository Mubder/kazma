"""What setup installs can run the test suite.

On the drill VM (2026-11-06) ``setup.ps1`` said "Could not collect tests":
pytest's addopts in pyproject.toml name ``--timeout`` (pytest-timeout), the
setup scripts sync the ``dev`` extra, and ``dev`` listed pytest without that
plugin, so pytest refused to start ("unrecognized arguments") on every fresh
install -- and so did ``scripts/fast_test.py``, which setup tells the owner to
run. CI installs the ``test`` extra, which had it, so nothing failed there.

Each extra the setup scripts sync, resolved through its self-references
(``kazma[test]``), must hold every requirement of the ``test`` extra and every
plugin the pytest configuration needs; a pytest without the plugin is shown
refusing the real addopts, and the old ``dev`` list is the negative control.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPTS = ("setup.ps1", "setup.sh")

#: pytest options and settings, and the plugin each needs.
PLUGIN_FOR_OPTION = {"--timeout": "pytest-timeout", "--cov": "pytest-cov", "asyncio_mode": "pytest-asyncio"}

#: The dev extra before 2026-11-06 (the negative control).
_OLD_DEV = ["pytest>=8.0.0", "pytest-asyncio>=1.4.1", "pytest-cov>=5.0.0", "pytest-mock>=3.17.0",
            "ruff>=0.17.3", "mypy>=2.4.0", "locust>=2.47.0", "pre-commit>=4.7.0"]


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _name(requirement: str) -> str:
    return re.split(r"[<>=!~;\[ ]", requirement, maxsplit=1)[0].strip().lower().replace("_", "-")


def resolve(extras: set[str], optional: dict[str, list[str]], project: str) -> set[str]:
    """Every requirement name the *extras* install, self-references followed."""
    names: set[str] = set()
    seen: set[str] = set()
    todo = list(extras)
    while todo:
        extra = todo.pop()
        if extra in seen:
            continue
        seen.add(extra)
        for req in optional.get(extra, []):
            match = re.match(rf"^{re.escape(project)}\[([^\]]+)\]", req.strip())
            if match:
                todo.extend(e.strip() for e in match.group(1).split(","))
            else:
                names.add(_name(req))
    return names


def needed_by_the_suite(data: dict) -> set[str]:
    """The test extra's requirements and the plugins pytest's settings name."""
    optional = data["project"]["optional-dependencies"]
    names = {_name(r) for r in optional["test"]}
    ini = data["tool"]["pytest"]["ini_options"]
    said = ini.get("addopts", "") + " " + " ".join(ini)
    names |= {plugin for option, plugin in PLUGIN_FOR_OPTION.items() if option in said}
    return names


def missing_for(extras: set[str], data: dict) -> set[str]:
    optional = data["project"]["optional-dependencies"]
    return needed_by_the_suite(data) - resolve(extras, optional, data["project"]["name"])


def synced_extras(text: str) -> list[set[str]]:
    """Each ``uv sync --extra ...`` a setup script runs."""
    return [set(re.findall(r"--extra\s+([\w-]+)", m.group(0)))
            for m in re.finditer(r"uv sync(?:\s+--extra\s+[\w-]+)+", text)]


def test_every_sync_the_setup_scripts_run_holds_the_suite():
    data = _project()
    found = 0
    for script in SETUP_SCRIPTS:
        for extras in synced_extras((ROOT / script).read_text(encoding="utf-8")):
            found += 1
            assert not missing_for(extras, data), (script, sorted(extras), sorted(missing_for(extras, data)))
    assert found >= 2, "each setup script syncs its extras with uv sync --extra"


def test_the_dev_and_all_extras_hold_the_suite():
    data = _project()
    for extra in ("dev", "all"):
        assert not missing_for({extra}, data), (extra, sorted(missing_for({extra}, data)))


def test_the_old_dev_extra_lacked_the_timeout_plugin():
    """Negative control: the dev list of before."""
    data = _project()
    data["project"]["optional-dependencies"]["dev"] = list(_OLD_DEV)
    assert "pytest-timeout" in missing_for({"rag", "dev", "tui"}, data)


def test_pytest_without_the_plugin_refuses_the_real_settings(tmp_path):
    """What the VM met: pyproject's addopts, and pytest-timeout blocked."""
    pytest.importorskip("pytest_timeout")
    addopts = _project()["tool"]["pytest"]["ini_options"]["addopts"]
    (tmp_path / "pytest.ini").write_text(f"[pytest]\naddopts = {addopts}\n", encoding="utf-8")
    (tmp_path / "test_one.py").write_text("def test_one():\n    pass\n", encoding="utf-8")

    def collect(*flags: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", "-m", "pytest", *flags, "--co", "-q", "-c",
                               str(tmp_path / "pytest.ini"), "--rootdir", str(tmp_path), str(tmp_path)],
                              cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=120, check=False)

    without = collect("-p", "no:timeout")
    assert without.returncode != 0 and "--timeout" in without.stderr, without.stderr[-400:]
    present = collect()
    assert present.returncode == 0 and re.search(r"1 tests? collected", present.stdout), present.stdout[-400:]


def test_setup_names_the_reason_when_collection_fails():
    """Both scripts read the count from pytest's own line, and say pytest's
    last words when there is none (they said only "Could not collect")."""
    for script in SETUP_SCRIPTS:
        text = (ROOT / script).read_text(encoding="utf-8")
        assert "tests? collected" in text, script
        assert re.search(r"Could not collect tests \(non-critical\): \$", text), script
