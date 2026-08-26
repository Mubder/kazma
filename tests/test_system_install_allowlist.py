"""Allowlist and API contract for system package/extra installer."""

from __future__ import annotations

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


def test_packages_tab_catalog_lists_new_extras():
    src = (
        _ROOT / "kazma-ui" / "kazma_ui" / "routes_direct.py"
    ).read_text(encoding="utf-8")
    for extra in (
        "index",
        "sandbox",
        "durable",
        "docling",
        "ocr",
        "convert",
        "document-platform",
        "push",
    ):
        assert f'"{extra}":' in src, extra


def test_update_cli_knows_new_extras():
    src = (_ROOT / "kazma-cli" / "kazma_cli" / "update.py").read_text(
        encoding="utf-8"
    )
    for extra in ("index", "sandbox", "durable", "docling", "push"):
        assert f'"{extra}"' in src, extra
