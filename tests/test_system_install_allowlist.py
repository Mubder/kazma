"""Allowlist and API contract for system package/extra installer."""

from __future__ import annotations

from tests._module_source import module_source

from pathlib import Path

from kazma_core.system.installer import ALLOWED_EXTRAS, ALLOWED_PACKAGES

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / "pyproject.toml"


def _pyproject_extras() -> set[str]:
    import tomllib

    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return set(data["project"]["optional-dependencies"])


def test_allowed_extras_match_pyproject_optional_set():
    assert ALLOWED_EXTRAS == _pyproject_extras()


def test_rag_packages_allowlisted():
    assert "chromadb" in ALLOWED_PACKAGES
    assert "sentence-transformers" in ALLOWED_PACKAGES


def test_no_arbitrary_packages():
    assert "requests" not in ALLOWED_PACKAGES
    assert "evil-pkg" not in ALLOWED_PACKAGES


def _core_dep_names() -> list[str]:
    import tomllib
    import re

    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    names: list[str] = []
    for spec in data["project"]["dependencies"]:
        name = re.split(r"[\[<>=!~]", spec, maxsplit=1)[0].strip()
        names.append(name)
    return names


def test_packages_tab_catalog_lists_every_pyproject_extra():
    src = module_source(_ROOT / "kazma-ui" / "kazma_ui" / "routes_direct.py")
    missing = [extra for extra in (_pyproject_extras() - {"all"}) if f'"{extra}":' not in src]
    assert not missing, f"Settings Packages tab missing extras: {missing}"


def test_packages_tab_core_lists_every_pyproject_direct_dep():
    src = module_source(_ROOT / "kazma-ui" / "kazma_ui" / "routes_direct.py")
    missing = [name for name in _core_dep_names() if f'"{name}"' not in src]
    assert not missing, f"Settings core package list missing: {missing}"


def test_setup_scripts_do_not_use_removed_cli_extra():
    """kazma-cli is a wheel package; [cli] extra does not exist."""
    for rel in ("setup.ps1", "setup.sh", "run.sh"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        assert "--extra cli" not in text, rel
        assert ".[dev,cli" not in text, rel
        assert "--extra rag" in text, rel


def test_update_cli_knows_every_pyproject_extra():
    src = (_ROOT / "kazma-cli" / "kazma_cli" / "update.py").read_text(
        encoding="utf-8"
    )
    missing = [extra for extra in (_pyproject_extras() - {"all"}) if f'"{extra}"' not in src]
    assert not missing, f"kazma update extras map missing: {missing}"
