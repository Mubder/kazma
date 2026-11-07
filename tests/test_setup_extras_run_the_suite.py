"""What setup installs can run the test suite.

On the drill VM (2026-11-06) ``setup.ps1`` said "Could not collect tests":
pytest's addopts in pyproject.toml name ``--timeout`` (pytest-timeout), the
setup scripts sync the ``dev`` extra, and ``dev`` listed pytest without that
plugin, so pytest refused to start ("unrecognized arguments") on every fresh
install -- and so did ``scripts/fast_test.py``, which setup tells the owner to
run. CI installs the ``test`` extra, which had it, so nothing failed there.

The extras an install takes come from one table since 2026-11-07
(``kazma_core.install_components``: Express, Full, Custom); the scripts sync
Kazma's base and hand over to ``kazma setup install``. The extras of each
mode, resolved through their self-references (``kazma[test]``), must hold
every requirement of the ``test`` extra and every plugin the pytest
configuration needs; a pytest without the plugin is shown refusing the real
addopts, and the old ``dev`` list is the negative control.
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


def default_extras(text: str) -> set[str]:
    """The extras a setup script names of its own (``$DefaultExtras`` in
    setup.ps1, ``DEFAULT_EXTRAS`` in setup.sh, as they were until 2026-11-07)."""
    found = (re.search(r"\$DefaultExtras\s*=\s*@\(([^)]*)\)", text)
             or re.search(r"^DEFAULT_EXTRAS=\(([^)]*)\)", text, re.M))
    return set(re.findall(r"[\w-]+", found.group(1))) if found else set()


#: The part that sets Kazma up to use a service, and the extra it needs:
#: setup postgres writes KAZMA_DATABASE_URL (the drill VM, 2026-11-06:
#: written into an environment without psycopg).
EXTRAS_SETUP_CONFIGURES = {"postgres": "postgres"}


def test_every_install_mode_holds_the_suite_and_the_scripts_name_no_extras():
    from kazma_core.install_components import by_id, components_for, extras_for

    data = _project()
    for mode in ("express", "full", "custom"):
        extras = set(extras_for(components_for(mode)))
        assert not missing_for(extras, data), (mode, sorted(missing_for(extras, data)))
    for part, extra in EXTRAS_SETUP_CONFIGURES.items():
        assert extra in by_id(part).extras, part
    for script in SETUP_SCRIPTS:
        text = (ROOT / script).read_text(encoding="utf-8-sig")
        assert default_extras(text) == set(), f"{script}: the extras come from the install table"
        assert "kazma_cli setup install" in text or "setup install" in text, script
    # Negative control: the lines they had.
    assert default_extras('$DefaultExtras = @("rag", "dev", "tui", "postgres")\n') == {"rag", "dev", "tui", "postgres"}


#: A command line that runs uv sync (or tells the owner to): "uv sync" and
#: an argument, never the word in "Sync Handshake (uv sync)" or "uv sync failed".
_SYNC_COMMAND = re.compile(r"""uv sync(\s+(--?[\w-]+|@\w+|"?\$[{(\w]))""")


def exact_syncs(text: str) -> list[int]:
    """Lines of a shell or PowerShell script that run an exact ``uv sync``:
    one that removes every package outside the extras it names. A line passes
    with ``--inexact`` or the script's own sync arguments (``syncArgs`` /
    ``SYNC_ARGS``, which start with it). Comments are not commands."""
    lines = []
    for number, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0] if not line.lstrip().startswith(("Log-", "log_")) else line
        if line.lstrip().startswith("#"):
            continue
        if _SYNC_COMMAND.search(code) and "--inexact" not in code and not re.search(r"syncArgs|SYNC_ARGS", code):
            lines.append(number)
    return lines


def _shipped_shell_scripts() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "*.ps1", "*.sh"], cwd=ROOT, capture_output=True,
                            text=True, timeout=60, check=True, stdin=subprocess.DEVNULL).stdout.split()
    return [ROOT / name for name in listed]


def test_no_shipped_script_runs_an_exact_sync():
    """A re-run of setup removed every extra installed after its first run."""
    scripts = _shipped_shell_scripts()
    assert {p.name for p in scripts} >= {"setup.ps1", "setup.sh", "run.sh"}
    for script in scripts:
        assert exact_syncs(script.read_text(encoding="utf-8-sig")) == [], script.name
    assert '$syncArgs = @("--inexact")' in (ROOT / "setup.ps1").read_text(encoding="utf-8-sig")
    assert "SYNC_ARGS=(--inexact)" in (ROOT / "setup.sh").read_text(encoding="utf-8")
    # Negative controls: the lines of before.
    assert exact_syncs("uv sync --extra rag --extra dev --extra tui 2>&1 | tail -5\n") == [1]
    assert exact_syncs('    Log-Info "Fix: uv sync --reinstall"\n') == [1]
    assert exact_syncs('if uv sync --extra rag 2>&1 | tail -5; then\n') == [1]
    assert exact_syncs('Log-Header "2. Sync Handshake (uv sync)"\nlog_fail "uv sync failed"\n# uv sync --all-extras\n') == []


def test_a_rerun_syncs_the_extras_the_install_already_has(monkeypatch):
    """Both scripts ask the venv which extras it holds, without the settings
    store (it may be a Postgres that is not up while setup runs)."""
    for script in SETUP_SCRIPTS:
        assert "extras_in_this_environment" in (ROOT / script).read_text(encoding="utf-8-sig"), script
    import kazma_core.config_store as config_store
    from kazma_cli import update

    def no_store():
        raise AssertionError("setup must not open the settings store to read the extras")

    monkeypatch.setattr(config_store, "get_config_store", no_store)
    present = update.extras_in_this_environment()
    assert "dev" in present, present  # pytest is installed here


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
