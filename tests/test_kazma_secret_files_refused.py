"""Kazma's secret files are refused by every door the stores are (FA-001).

Live 2026-11-02 (functional audit, build eb0df553): the workspace IS the
install folder, and ``file_read(".env")`` and ``file_search("restic")`` --
read tier, no approval card -- returned the install's ``.env`` (vault key,
database DSN, provider keys) and ``restic.pass`` (the key to every offsite
backup). Only databases were Kazma's stores; a secret file was any file.

The shape here is the live one: the install folder is the workspace, the
data dir is ``<install>/kazma-data`` and its backups keep copies of ``.env``.
Every door is asked; a ``.env`` of the user's own project (in the coding
sandbox) and an ``.env.example`` must stay readable, and with the secret
check switched off the same doors open (negative control).
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from kazma_core.ide.workspace_scope import workspace_path_scope

from tests._tool_answer import answer

SECRETS = (
    ".env",
    ".env.local",
    "restic.pass",
    ".kazma/document_broker.token",
    ".kazma/observer.key",
    ".kazma/postgres_superuser.pass",
    "kazma-data/backups/universal/1793549979/.env",
    "kazma-data/migrate/bundle/meta.env",
)

#: Not Kazma's: the user's own project in the coding sandbox, and templates.
NOT_SECRETS = (
    ".env.example",
    "kazma-data/workspace/proj/.env",
    "notes/settings.txt",
)


@pytest.fixture()
def install(tmp_path, monkeypatch):
    """An install whose workspace is the install folder, as on live."""
    from kazma_core import paths

    root = tmp_path / "install"
    data = root / "kazma-data"
    (data / "workspace" / "proj").mkdir(parents=True)
    (root / ".kazma").mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_USER_HOME", str(root / ".kazma"))
    monkeypatch.delenv("KAZMA_ENV_FILE", raising=False)
    monkeypatch.setattr(paths, "installed_project_root", lambda: root)
    monkeypatch.setattr(paths, "get_project_root", lambda: root)
    monkeypatch.chdir(root)
    for rel in SECRETS + NOT_SECRETS:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("KAZMA_VAULT_KEY=demo-vault-key-value\n", encoding="utf-8")
    return root


def _doors(path: Path) -> dict[str, object]:
    """Each door's verdict on *path*: a refusal (truthy) or None (opened)."""
    from kazma_core.agent.tool_builtins.filesystem import _searchable_text
    from kazma_core.agent.tool_scope import _workspace_scope_error
    from kazma_core.safety.commitment.authorize import _exec_names_kazma_store
    from kazma_core.tools.read_url import _load_research_body
    from kazma_core.tools.research_eval import score_report_file
    from kazma_core.tools.research_synthesize import _resolve_under_workspace
    from kazma_core.tools.send_message import _file_attachment
    from kazma_core.workspace.path_policy import check_path_access, code_mentions_control_plane
    from kazma_skills.native.document_platform.tools import _resolve_workspace_file

    read = check_path_access(path, "read")
    doc = _resolve_workspace_file(str(path))[1]
    attachment = _file_attachment(str(path))
    return {
        "file_read": None if read.allowed else read.reason,
        "file_write": None if check_path_access(path, "write").allowed else "refused",
        "reads": _workspace_scope_error(path, str(path), "reads"),
        "searches": _workspace_scope_error(path, str(path), "searches"),
        "search_walk": None if _searchable_text(path, path.parent) is not None else "skipped",
        "python_exec": code_mentions_control_plane(f"open({str(path)!r}).read()"),
        "before_the_card_code": _exec_names_kazma_store({"code": f"open({str(path)!r})"}, ""),
        "before_the_card_shell": _exec_names_kazma_store({}, f'type "{path}"'),
        "research_reader": _refused_text(_load_research_body(str(path))),
        "research_synthesize": _refused_text(_resolve_under_workspace(str(path))),
        "research_score": None if score_report_file(path).checks.get("file_readable", True)
        else "refused",
        "document_tools": doc,
        "chat_attachment": _refused_text(attachment),
    }


def _refused_text(verdict: object) -> str | None:
    """A reader's refusal (its error text), or None when it opened the file."""
    return verdict if isinstance(verdict, str) else None


async def test_every_door_refuses_every_secret_file(install):
    async with workspace_path_scope(install):
        for rel in SECRETS:
            doors = _doors(install / rel)
            open_doors = [d for d, verdict in doors.items() if not verdict]
            assert not open_doors, f"{rel}: open through {open_doors}"
            assert "Kazma secret file" in str(doors["file_read"]), doors["file_read"]
            assert "holds Kazma's own secrets" in str(doors["reads"]), doors["reads"]
            assert "demo-vault-key-value" not in repr(doors)


async def test_the_users_own_files_pass_every_door(install):
    """Negative control: a project's .env in the sandbox, a template and an
    ordinary file are the user's, and stay readable."""
    async with workspace_path_scope(install):
        for rel in NOT_SECRETS:
            doors = _doors(install / rel)
            refused = {d: v for d, v in doors.items() if v and d != "file_write"}
            assert not refused, f"{rel}: refused by {refused}"


async def test_without_the_secret_check_the_doors_open(install, monkeypatch):
    """Negative control: the doors refuse because of the secret check, not by
    accident. With it off, file_read reads .env -- the live bug."""
    from kazma_core import store_registry

    monkeypatch.setattr(store_registry, "is_kazma_secret_file", lambda _p: False)
    monkeypatch.setattr(store_registry, "is_secret_file_name", lambda _n: False)
    async with workspace_path_scope(install):
        doors = _doors(install / ".env")
    assert doors["file_read"] is None and doors["reads"] is None, doors


async def test_the_real_tools_refuse_the_live_calls(install):
    """The calls the audit made live: file_read('.env'), file_search for
    restic, generate_pdf with markdown_path='.env', codebase_search."""
    from kazma_core.agent.tool_builtins import filesystem
    from kazma_core.code_index.ripgrep import search_text
    from kazma_core.tools.file_read import clear_read_cache, file_read
    from kazma_skills.native.document_generator.tools import generate_pdf

    class _Capture:
        def __init__(self) -> None:
            self.tools: dict[str, object] = {}

        def register(self, **_kw):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    cap = _Capture()
    filesystem.register_filesystem_tools(cap)
    file_search = cap.tools["file_search"]

    async with workspace_path_scope(install):
        clear_read_cache()
        read = await answer(file_read(".env"))
        assert "demo-vault-key-value" not in read and "secret" in read.lower(), read
        found = await answer(file_search("KAZMA_VAULT_KEY", path="."))
        # The sandbox project's .env is the user's and may be found; Kazma's
        # own copies may not.
        assert "restic.pass" not in found and "backups" not in found, found
        assert ".env.local" not in found and "meta.env" not in found, found
        assert "document_broker.token" not in found, found
        assert not any(
            line.split(":", 1)[0].strip() in (".env", "./.env") for line in found.splitlines()
        ), found
        pdf = await answer(generate_pdf(title="t", markdown_path=".env"))
        assert "demo-vault-key-value" not in pdf and "secret" in pdf.lower(), pdf
        hits = "\n".join(search_text(install, "KAZMA_VAULT_KEY", limit=50)).replace("\\", "/")
        assert "settings.txt:" in hits, hits  # the search itself ran
        for rel in SECRETS:
            assert f"{(install / rel).as_posix()}:" not in hits and f"\n{rel}:" not in (
                "\n" + hits
            ), (rel, hits)


async def test_a_secret_file_in_a_skill_folder_is_refused(install, monkeypatch):
    """Skill folders are readable for their scripts and references; a secret
    file placed there is not (the home holds the skill folders and .kazma)."""
    from kazma_core.agent_skills import discovery

    skills = install / ".kazma" / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo" / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    (skills / "demo" / "restic.pass").write_text("demo-restic-pass\n", encoding="utf-8")
    monkeypatch.setattr(discovery, "skill_base_dirs", lambda *a, **k: [("user", skills)])
    from kazma_core.agent.tool_scope import _workspace_scope_error

    outside = install.parent / "elsewhere"
    outside.mkdir()
    async with workspace_path_scope(outside):
        ok = _workspace_scope_error(skills / "demo" / "SKILL.md", "SKILL.md", "reads")
        refused = _workspace_scope_error(skills / "demo" / "restic.pass", "restic.pass", "reads")
    assert ok is None, ok
    assert refused and "secrets" in refused, refused


# ── class gate: a tool that resolves a model-given path checks access ────


_TOOL_DIRS = (
    "kazma-core/kazma_core/tools",
    "kazma-core/kazma_core/agent/tool_builtins",
    "kazma-skills/kazma_skills/native",
)

#: Calls that decide whether a resolved path may be opened.
_ACCESS_CHECKS = frozenset(
    {
        "check_path_access",
        "is_kazma_store",
        "resolve_in_scope",
        "resolve_and_check",
        "_workspace_scope_error",
        "_resolve_for_read",
        "_resolve_workspace_file",
        "_markdown_source",
        "_file_attachment",
        "_checked_sqlite_file",
    }
)

#: Functions that resolve a path and need no check, with why.
_DECLARED = {
    ("kazma-core/kazma_core/tools/file_apply_patch.py", "_nearby_tests"): (
        "lists test_*.py beside files the patch set already wrote through "
        "check_path_access; it opens none of them"
    ),
}


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _tracked(prefixes: tuple[str, ...]) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *prefixes],
        cwd=_repo(),
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p.endswith(".py")]


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.add(fn.attr)
    return names


def _unchecked_resolutions(source: str) -> list[str]:
    """Functions that call ``resolve_tool_path`` and no access check."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        called = _called_names(node)
        if "resolve_tool_path" in called and not (called & _ACCESS_CHECKS):
            found.append(node.name)
    return found


def test_every_tool_that_resolves_a_path_checks_it():
    """A path the model gives is opened only after the file tools' check.

    ``send_message._file_attachment`` and the document tools'
    ``_resolve_workspace_file`` resolved the path and checked only that it
    was in the workspace -- the install folder, on live.
    """
    missing: list[str] = []
    for rel in _tracked(_TOOL_DIRS):
        source = (_repo() / rel).read_text(encoding="utf-8")
        for name in _unchecked_resolutions(source):
            if (rel, name) not in _DECLARED:
                missing.append(f"{rel}:{name}")
    assert not missing, (
        "These functions resolve a model-given path and never ask whether it "
        "may be opened (check_path_access / resolve_in_scope / is_kazma_store): "
        f"{missing}"
    )


def test_the_declarations_still_exist():
    for rel, name in _DECLARED:
        source = (_repo() / rel).read_text(encoding="utf-8")
        assert name in _unchecked_resolutions(source), (
            f"{rel}:{name} is declared but no longer resolves a path unchecked; remove it"
        )


def test_the_gate_sees_the_old_attachment_and_document_shapes():
    """Negative control: the two shapes fixed on 2026-11-02."""
    old_attachment = (
        "def _file_attachment(file_path):\n"
        "    p = resolve_tool_path(file_path)\n"
        "    if not p.exists():\n"
        "        return 'Error'\n"
        "    return {'data': p.read_bytes()}\n"
    )
    old_document = (
        "def _resolve_workspace_file(path):\n"
        "    root = resolve_active_root().resolve()\n"
        "    candidate = resolve_tool_path(path, root=root)\n"
        "    candidate.relative_to(root)\n"
        "    return candidate, None\n"
    )
    assert _unchecked_resolutions(old_attachment) == ["_file_attachment"]
    assert _unchecked_resolutions(old_document) == ["_resolve_workspace_file"]
