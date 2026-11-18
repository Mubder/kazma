"""Small hardening from the SonarCloud review of 2026-11-16, each with its
class gate where the defect has siblings.

* Windows ``cmd /c`` re-reads its command line: ``rmdir /s /q <path>`` and
  ``mkdir <path>`` with a path holding ``&`` ran a second command. No product
  ``cmd /c`` takes a value it did not write and quote itself.
* Raw direction-control characters in source (Trojan Source): the observer's
  sanitizer held them as raw characters. Source writes them as escapes.
* ``/repos/{owner}/{repo}`` paths took names a repository's own remote URL
  wrote, ``..`` included; GitHub's naming rules now hold both.
* ``recall(conn=...)`` read rows by column name on whatever connection it was
  given: a plain one raised TypeError, which no handler there expected.
"""

from __future__ import annotations

import ast
import os
import re
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tracked(*patterns: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--", *patterns], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p]


# ── cmd /c takes no value it did not write ────────────────────────────────

#: (file, function) -> why its cmd /c is safe.
CMD_DECLARED = {
    ("kazma-cli/kazma_cli/uninstall.py", "_remove_later"):
        "the one argument is a script Kazma writes, the path inside it in double quotes "
        "(a Windows path cannot hold a double quote)",
}


def cmd_calls_with_values(source: str) -> list[tuple[str, int]]:
    """(function, line) of each ``["cmd", "/c", ...]`` list or tuple holding
    anything but string literals."""
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            if isinstance(child, (ast.List, ast.Tuple)) and len(child.elts) >= 2:
                head = [e.value.lower() if isinstance(e, ast.Constant) and isinstance(e.value, str) else None
                        for e in child.elts[:2]]
                if head[0] in ("cmd", "cmd.exe") and head[1] == "/c":
                    if not all(isinstance(e, ast.Constant) for e in child.elts[2:]):
                        found.append((function, child.lineno))
            visit(child, name)

    visit(ast.parse(source), "<module>")
    return found


def test_no_cmd_line_takes_a_value_kazma_did_not_quote():
    found, used = [], set()
    for rel in _tracked("kazma-*", "scripts"):
        if not rel.endswith(".py") or "/tests/" in rel or "_tests/" in rel:
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "cmd" not in text:
            continue
        for function, line in cmd_calls_with_values(text):
            if (rel, function) in CMD_DECLARED:
                used.add((rel, function))
            else:
                found.append(f"{rel}:{line} in {function}()")
    assert found == [], found
    assert used == set(CMD_DECLARED), set(CMD_DECLARED) - used


def test_the_cmd_gate_sees_the_old_cleanup():
    old = 'def _force_rmtree(path):\n    _run(["cmd", "/c", "rmdir", "/s", "/q", str(path)])\n'
    assert cmd_calls_with_values(old) == [("_force_rmtree", 2)]
    assert cmd_calls_with_values('x = ("cmd", "/c", "ver")\n') == []


def test_the_wal_archive_folder_is_made_without_cmd():
    from types import SimpleNamespace

    from kazma_core.backup import wal_archive

    steps = wal_archive.prepare_steps(SimpleNamespace(os="windows"), Path("C:/Data/R&D/wal"))
    assert all(step.argv[0].lower() not in ("cmd", "cmd.exe") for step in steps)
    assert "R&D" in steps[0].argv[-1]


def test_the_restore_cleanup_removes_a_read_only_tree(tmp_path):
    from kazma_core.backup.restore import _force_rmtree

    tree = tmp_path / "R&D restore"
    (tree / "a" / "b").mkdir(parents=True)
    locked = tree / "a" / "b" / "file.txt"
    locked.write_text("x", encoding="utf-8")
    os.chmod(locked, stat.S_IREAD)
    _force_rmtree(tree)
    assert not tree.exists()


# ── no raw direction-control characters in source ─────────────────────────

#: Every character bandit's B613 (trojansource) fails CI's Security Scan on:
#: the embeddings and overrides, the isolates, and the right-to-left mark
#: (U+200F). Until 2026-11-18 this gate left out U+200F, so a raw mark in an
#: Arabic catalog string passed it and failed the Security Scan on main.
#: ``test_the_gate_covers_every_character_bandit_flags`` holds this set to
#: bandit's own list where bandit is installed.
_BIDI_CHARACTERS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200f")
_BIDI = re.compile("[" + "".join(sorted(_BIDI_CHARACTERS)) + "]")
#: Vendored files, as their makers ship them.
BIDI_DECLARED = {"kazma-ui/kazma_ui/static/js/mermaid.min.js"}
_SOURCE_SUFFIXES = (".py", ".js", ".mjs", ".ts", ".html", ".sh", ".ps1", ".yml", ".yaml", ".toml", ".css")


def test_no_source_file_holds_a_raw_direction_character():
    """Gate (Trojan Source): an override or isolate makes code read as other
    than it runs; source writes them as escapes (``\\u202e``)."""
    found = []
    for rel in _tracked():
        if not rel.endswith(_SOURCE_SUFFIXES) or rel in BIDI_DECLARED or "/vendor/" in rel:
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _BIDI.search(text):
            found.append(rel)
    assert found == [], found


def test_the_bidi_gate_sees_a_raw_override():
    assert _BIDI.search("x = '\u202e'") and not _BIDI.search("x = '\\u202e'")


def test_the_bidi_gate_sees_a_raw_right_to_left_mark():
    # The 2026-11-18 Security Scan failure: an Arabic label with a raw RLM.
    assert _BIDI.search('"ar": "\u0645\u0641\u0627\u062a\u064a\u062d X \u200f(OAuth 1.0a)"')
    assert not _BIDI.search('"ar": "\u0645\u0641\u0627\u062a\u064a\u062d X \\u200f(OAuth 1.0a)"')


def test_the_gate_covers_every_character_bandit_flags():
    trojansource = pytest.importorskip("bandit.plugins.trojansource")
    missing = set(trojansource.BIDI_CHARACTERS) - _BIDI_CHARACTERS
    assert not missing, sorted(f"U+{ord(c):04X}" for c in missing)


# ── GitHub names ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("url, expected", [
    ("git@github.com:Mubder/kazma.git", ("Mubder", "kazma")),
    ("https://github.com/Mubder/kazma", ("Mubder", "kazma")),
    ("https://github.com/../users", None),
    ("https://github.com/-bad/repo", None),
    ("https://github.com/a--b/repo", None),
])
def test_a_remote_names_a_github_repository_or_nothing(url, expected):
    from kazma_gateway.routers.github import parse_github_slug

    assert parse_github_slug(url) == expected


def test_the_clone_route_refuses_a_name_github_cannot_hold():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_gateway.routers.github import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    for name in ("../users", "a/..", "owner", "-x/y"):
        resp = client.post("/api/github/repos/clone", json={"full_name": name})
        assert resp.status_code == 422, (name, resp.status_code)


# ── recall on a caller's plain connection ─────────────────────────────────


def test_recall_reads_a_plain_connection_and_gives_it_back(tmp_path, monkeypatch):
    from kazma_core.memory import recall as recall_mod
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"))
    ensure_primary_schema(conn)
    row = episode_row(session_id="s1", turn_number=1, source="benchmark",
                      user_text="Which harbour does the ferry to Failaka leave from?",
                      assistant_text="From Salmiya marina, twice a day.")
    conn.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
        "summary_text, tier, structural_importance, created_at, metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,'{}')",
        (row["id"], "default", "s1", 1, row["user_text"], row["assistant_text"],
         row["summary_text"], "episodic", 1, time.time()),
    )
    conn.commit()
    assert conn.row_factory is None
    result = recall_mod.recall("ferry to Failaka harbour", conn=conn)
    assert not result.degraded
    assert [h.id for h in result.episodes] == [row["id"]]
    assert conn.row_factory is None, "the caller's connection is given back as it was"
    conn.close()
