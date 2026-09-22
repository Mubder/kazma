"""`.env` is loaded by entry points, from an explicit ladder — never by an import.

Regression for the 2026-09-22 audit. ``cost_breaker`` ran a bare
``load_dotenv()`` at import time and ``kazma_core/__init__`` imports it, so the
first ``import kazma_core...`` in any process searched upward for a ``.env``.
The document sandbox launches its workers as ``python -I -c "runpy..."`` in a
run directory under ``kazma-data``, scrubs the environment to a handful of
variables, and parses an untrusted file. The import walked up from that run
directory, found the installation's ``.env``, and put ``KAZMA_VAULT_KEY``,
``KAZMA_SECRET`` and every provider key back into the process parsing the
hostile document.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest
from kazma_core.documents.sandbox import SandboxRequest, run_isolated_subprocess
from kazma_core.env_files import env_file_ladder, load_env_files

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = REPO_ROOT / "kazma-core"
SENTINEL = "KAZMA_ENV_LOADING_SENTINEL"


def _sandbox_worker_modules() -> list[str]:
    """Every worker module the documents package launches, from its own source."""
    found: set[str] = set()
    docs = CORE_ROOT / "kazma_core" / "documents"
    for path in docs.glob("*.py"):
        if path.stem.endswith("_worker"):
            continue
        found |= set(re.findall(r"kazma_core\.documents\.\w+_worker", path.read_text(encoding="utf-8")))
    return sorted(found)


def test_worker_enumeration_is_not_blind():
    """If the launch sites stop matching, the test below would pass vacuously."""
    assert {
        "kazma_core.documents.parser_worker",
        "kazma_core.documents.renderer_worker",
        "kazma_core.documents.mutation_worker",
    } <= set(_sandbox_worker_modules())


@pytest.mark.parametrize("module", _sandbox_worker_modules())
def test_sandboxed_worker_import_loads_no_dotenv(module: str, tmp_path: Path):
    """Launched exactly as production does, a worker must not pick up a `.env`.

    The planted `.env` sits above the sandbox's working directory, which is
    where the installation's own `.env` sits relative to a real run directory.
    """
    (tmp_path / ".env").write_text(f"{SENTINEL}=from-dotenv\n", encoding="utf-8")
    run_dir = tmp_path / "kazma-data" / "documents" / "run-1"
    run_dir.mkdir(parents=True)
    bootstrap = (
        "import os,sys;"
        f"sys.path.insert(0,{str(CORE_ROOT)!r});"
        "before=set(os.environ);"
        f"import {module};"
        "print(sorted(set(os.environ)-before))"
    )
    result = run_isolated_subprocess(
        SandboxRequest(
            command=(sys.executable, "-I", "-c", bootstrap),
            work_dir=run_dir,
            timeout_seconds=120,
        )
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-2000:]
    added = ast.literal_eval(result.stdout.decode().strip().splitlines()[-1])
    assert SENTINEL not in added, (
        f"importing {module} inside the sandbox loaded a .env from above its "
        f"working directory; variables added: {added}"
    )
    # The two git switches kazma_core/__init__ sets on purpose are the only
    # variables an import may add (see test_static_gates allowlist).
    assert set(added) <= {"GIT_ASKPASS", "GIT_TERMINAL_PROMPT"}, added


# ── The ladder itself ───────────────────────────────────────────────────────


@pytest.fixture
def ladder_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The root conftest replaces dotenv.load_dotenv with a no-op so no test can
    # read a developer's .env. These tests only ever point it at tmp files.
    import dotenv
    import dotenv.main

    monkeypatch.setattr(dotenv, "load_dotenv", dotenv.main.load_dotenv)
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    explicit = tmp_path / "explicit.env"
    monkeypatch.setenv("KAZMA_USER_HOME", str(home))
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("KAZMA_ENV_FILE", raising=False)
    monkeypatch.delenv(SENTINEL, raising=False)
    return home / ".env", cwd / ".env", explicit


def test_ladder_order_is_home_then_cwd_then_explicit(ladder_dirs, monkeypatch):
    home_env, cwd_env, explicit = ladder_dirs
    monkeypatch.setenv("KAZMA_ENV_FILE", str(explicit))
    assert env_file_ladder() == [home_env, cwd_env, explicit]


def test_override_true_most_specific_file_wins_over_the_shell(ladder_dirs, monkeypatch):
    home_env, cwd_env, _ = ladder_dirs
    home_env.write_text(f"{SENTINEL}=home\n", encoding="utf-8")
    cwd_env.write_text(f"{SENTINEL}=cwd\n", encoding="utf-8")
    monkeypatch.setenv(SENTINEL, "")  # a stale empty shell value
    loaded = load_env_files()
    import os

    assert os.environ[SENTINEL] == "cwd"
    assert loaded == [str(home_env), str(cwd_env)]


def test_override_false_keeps_the_shell_and_still_prefers_the_specific_file(
    ladder_dirs, monkeypatch
):
    import os

    home_env, cwd_env, _ = ladder_dirs
    home_env.write_text(f"{SENTINEL}=home\n", encoding="utf-8")
    cwd_env.write_text(f"{SENTINEL}=cwd\n", encoding="utf-8")
    load_env_files(override=False)
    assert os.environ[SENTINEL] == "cwd"

    monkeypatch.setenv(SENTINEL, "shell")
    load_env_files(override=False)
    assert os.environ[SENTINEL] == "shell"


# ── Every entry point states its policy ─────────────────────────────────────

#: Entry points that must NOT load `.env`, and why.
ENV_FREE_ENTRY_POINTS: dict[str, str] = {
    "kazma-core/kazma_core/documents/parser_worker.py": "sandboxed: parses untrusted documents with a scrubbed environment",
    "kazma-core/kazma_core/documents/renderer_worker.py": "sandboxed: renders documents with a scrubbed environment",
    "kazma-core/kazma_core/documents/mutation_worker.py": "sandboxed: mutates documents with a scrubbed environment",
    "kazma-gateway/kazma_gateway/mcp_server.py": (
        "stdio IDE server that runs shell commands for an external client; it "
        "never imported kazma_core at load, so it never had the secrets"
    ),
}


def _entry_point_files() -> set[str]:
    """Console scripts, `__main__.py` modules, `__main__` blocks, serve.py."""
    out = {"serve.py"}
    scripts = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for target in scripts["project"]["scripts"].values():
        module = target.split(":", 1)[0]
        for pkg in REPO_ROOT.glob("kazma-*"):
            candidate = pkg / (module.replace(".", "/") + ".py")
            if candidate.is_file():
                out.add(candidate.relative_to(REPO_ROOT).as_posix())
    for pkg in REPO_ROOT.glob("kazma-*/kazma_*"):
        for path in pkg.rglob("*.py"):
            if "__pycache__" in path.parts or "_tests" in str(path):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.name == "__main__.py" or '__name__ == "__main__"' in text:
                out.add(path.relative_to(REPO_ROOT).as_posix())
    return out


def _delegate_target(rel: str) -> str | None:
    """For a `__main__.py` that only imports `main` from a sibling, that sibling."""
    tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and any(
            a.name == "main" for a in node.names
        ):
            pkg_root = REPO_ROOT / rel.split("/", 1)[0]
            target = pkg_root / (node.module.replace(".", "/") + ".py")
            if target.is_file():
                return target.relative_to(REPO_ROOT).as_posix()
    return None


def test_every_entry_point_declares_its_env_policy():
    missing: list[str] = []
    for rel in sorted(_entry_point_files()):
        if rel in ENV_FREE_ENTRY_POINTS:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "load_env_files" not in text, f"{rel} is env-free but loads .env"
            continue
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if "load_env_files(" in text:
            continue
        target = _delegate_target(rel) if rel.endswith("__main__.py") else None
        if target and "load_env_files(" in (REPO_ROOT / target).read_text(encoding="utf-8"):
            continue
        missing.append(rel)
    assert not missing, (
        "Entry point with no `.env` policy. Call "
        "`kazma_core.env_files.load_env_files()` before reading configuration, "
        "or add it to ENV_FREE_ENTRY_POINTS with the reason it must not load "
        "one:\n  " + "\n  ".join(missing)
    )


def test_entry_point_enumeration_is_not_blind():
    found = _entry_point_files()
    assert {"serve.py", "kazma-cli/kazma_cli/main.py", "kazma-ui/kazma_ui/app.py"} <= found
    assert set(ENV_FREE_ENTRY_POINTS) <= found, set(ENV_FREE_ENTRY_POINTS) - found
