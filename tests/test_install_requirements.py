"""The server says when the installed packages are behind this build.

A deploy is ``git pull`` and a reload; nothing installs what a newer
``pyproject.toml`` asks for. On 2026-10-02 the floors of 14 packages with
known advisories were raised and the live install still ran every affected
version, with nothing saying so. A checkout's installed ``kazma`` metadata is
no guide either: it is written at install time (the dev venv's still named a
dependency removed two days earlier).
"""

from __future__ import annotations

import ast
import importlib.metadata
import logging
from pathlib import Path

import pytest

from kazma_core import install_requirements as ir

_REPO = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path, base: list[str], extras: dict[str, list[str]] | None = None) -> Path:
    lines = ["[project]", 'name = "kazma"', 'version = "9.9.9"', "dependencies = ["]
    lines += [f'    "{spec}",' for spec in base]
    lines.append("]")
    if extras:
        lines.append("[project.optional-dependencies]")
        for extra, specs in extras.items():
            lines.append(f"{extra} = [" + ", ".join(f'"{s}"' for s in specs) + "]")
    (tmp_path / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A fake environment: name -> installed version."""
    versions: dict[str, str] = {}

    def version(name: str) -> str:
        if name not in versions:
            raise importlib.metadata.PackageNotFoundError(name)
        return versions[name]

    monkeypatch.setattr(ir.importlib.metadata, "version", version)
    return versions


def test_a_package_below_its_minimum_is_named(tmp_path: Path, installed: dict[str, str]) -> None:
    installed.update({"pyjwt": "2.13.0", "httpx": "0.28.1"})
    root = _project(tmp_path, ["PyJWT>=2.15.0", "httpx>=0.27.0"])
    unmet = ir.unmet_requirements(root)
    assert [(u.name, u.required, u.installed, u.groups) for u in unmet] == [
        ("pyjwt", ">=2.15.0", "2.13.0", ("base",)),
    ]
    assert unmet[0].describe() == "pyjwt >=2.15.0: 2.13.0 installed"


def test_a_missing_base_requirement_is_named_and_an_unchosen_extra_is_not(
    tmp_path: Path, installed: dict[str, str],
) -> None:
    installed.update({"chromadb": "1.5.9"})
    root = _project(
        tmp_path,
        ["packaging>=24.0"],
        {"rag": ["chromadb>=2.0", "sentence-transformers>=3.0"], "postgres": ["psycopg>=3.1"]},
    )
    described = [u.describe() for u in ir.unmet_requirements(root)]
    # sentence-transformers and psycopg are not installed: extras nobody chose.
    assert described == ["packaging >=24.0: not installed", "chromadb >=2.0 (extra rag): 1.5.9 installed"]


def test_markers_self_references_and_duplicates(tmp_path: Path, installed: dict[str, str]) -> None:
    installed.update({"aiohttp": "3.14.1", "torch": "2.12.1", "pypdf": "6.15.0"})
    root = _project(
        tmp_path,
        ["pypdf>=6.19.0", "uvloop>=0.20; sys_platform == 'no-such-platform'"],
        {
            "document": ["pypdf>=6.19.0"],
            "push": ["aiohttp>=3.14.3"],
            "rag": ["aiohttp>=3.14.3", "torch>=2.13.0"],
            "all": ["kazma[document,push,rag]"],
        },
    )
    described = [u.describe() for u in ir.unmet_requirements(root)]
    assert described == [
        "pypdf >=6.19.0: 6.15.0 installed",  # base wins over the document extra
        "aiohttp >=3.14.3 (extra push, rag): 3.14.1 installed",
        "torch >=2.13.0 (extra rag): 2.12.1 installed",
    ]


def test_a_checkout_reads_its_pyproject_not_its_install_metadata(
    tmp_path: Path, installed: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The installed metadata of an editable install goes stale with every pull."""
    installed.update({"httpx": "0.28.1"})
    monkeypatch.setattr(ir.importlib.metadata, "requires", lambda name: ["aiogram>=3.0.0", "httpx>=0.27"])
    root = _project(tmp_path, ["httpx>=0.27"])
    assert ir.unmet_requirements(root) == []
    # Negative control: the metadata path names the removed dependency.
    assert [u.name for u in ir.unmet_requirements(tmp_path / "no-checkout-here")] == ["aiogram"]


def test_the_repository_declares_only_readable_requirements() -> None:
    declared = ir._declared_requirements(_REPO)
    assert len(declared) > 40
    from packaging.requirements import Requirement

    for spec, _group in declared:
        Requirement(spec)  # raises on a requirement nothing could check


def test_boot_says_so_with_the_fix(
    tmp_path: Path, installed: dict[str, str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    installed.update({"pyjwt": "2.13.0"})
    root = _project(tmp_path, ["PyJWT>=2.15.0"])
    sent: list[tuple[str, str, str]] = []

    def alert(key: str, title: str, detail: str = "", **_: object) -> bool:
        sent.append((key, title, detail))
        return True

    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", alert)
    with caplog.at_level(logging.WARNING, logger="kazma_core.install_requirements"):
        unmet = ir.report_unmet_requirements(root)
    assert [u.name for u in unmet] == ["pyjwt"]
    assert any("kazma update" in r.getMessage() and "pyjwt" in r.getMessage() for r in caplog.records)
    assert sent and sent[0][0] == "install.requirements_unmet" and "kazma update" in sent[0][2]

    # Nothing behind: no warning, no page.
    sent.clear()
    installed["pyjwt"] = "2.15.1"
    assert ir.report_unmet_requirements(root) == [] and sent == []


def test_the_server_runs_the_check_at_boot() -> None:
    """``_on_startup`` hands the check to a background thread (it reads every
    installed distribution, and must not delay boot or block the loop)."""
    tree = ast.parse((_REPO / "kazma-ui/kazma_ui/app.py").read_text(encoding="utf-8"))
    startup = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_on_startup"
    )
    calls = [
        ast.unparse(n) for n in ast.walk(startup)
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "spawn_background"
    ]
    assert any("to_thread(report_unmet_requirements)" in c for c in calls), calls
