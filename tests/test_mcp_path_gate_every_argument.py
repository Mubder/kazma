"""The MCP path gate checks every argument that names a path.

Audit 2026-10-26 AUD-059: ``_gate_mcp_path_access`` inspected a fixed list
of argument names (path, paths, directory, filePath, source, destination,
src, dst, target, folder). ``file_path``, ``repo_path``, ``uri``, ``file``,
``filename``, ``dir``, ``root`` and ``cwd`` passed unchecked, and this gate
is the ONLY filesystem containment for MCP tools (§10A): a trusted,
allowlisted, YOLO or granted call reached outside the workspace through one.
Now every path-named key (any spelling, any depth) is checked, and every
other string whose whole value is path-shaped.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

OUTSIDE = "C:/Windows/System32/drivers/etc/hosts"


@pytest.fixture()
def policy(monkeypatch):
    """The path policy, recording each path it is asked about and denying
    the ones outside the workspace (absolute here)."""
    asked: list[str] = []

    def check(path, mode="read", *, thread_id=None):
        asked.append(str(path))
        text = str(path).replace("\\", "/")
        outside = text.startswith(("/", "~")) or text[1:3] == ":/" or text.startswith("..")
        return SimpleNamespace(allowed=not outside, reason="outside the workspace")

    monkeypatch.setattr("kazma_core.workspace.path_policy.check_path_access", check)
    return asked


def _gate(name, args):
    from kazma_core.mcp.manager import _gate_mcp_path_access

    return _gate_mcp_path_access(name, args)


@pytest.mark.parametrize("key", ["file_path", "repo_path", "uri", "file", "filename", "dir",
                                 "root", "cwd", "workingDirectory", "output-path"])
def test_each_spelling_the_audit_named_is_checked(policy, key):
    denial = _gate("read_file", {key: OUTSIDE})
    assert denial is not None and denial["is_error"]
    assert policy == [OUTSIDE]


def test_a_file_uri_is_checked_as_its_path(policy):
    denial = _gate("read_resource", {"uri": "file:///etc/shadow"})
    assert denial is not None
    assert policy == ["/etc/shadow"]


def test_a_path_shaped_value_under_any_key_is_checked(policy):
    assert _gate("run", {"options": {"workspace_location": "/etc/ssh/"}}) is not None
    assert _gate("run", {"items": [{"x": "..\\..\\secrets.txt"}]}) is not None


def test_prose_and_web_addresses_are_not_judged(policy):
    assert _gate("send_message", {"message": "/remind me tomorrow", "url": "https://example.com/a/b",
                                  "query": "what is in /etc/hosts?\nthanks"}) is None
    assert policy == []


def test_a_relative_path_inside_the_workspace_passes(policy):
    assert _gate("write_file", {"file_path": "notes/today.md", "content": "hello"}) is None
    assert policy == ["notes/today.md"]


def test_the_old_keys_are_still_checked(policy):
    for key in ("path", "paths", "directory", "filePath", "source", "destination", "src", "dst",
                "target", "folder"):
        policy.clear()
        assert _gate("move_file", {key: [OUTSIDE]}) is not None, key


def test_negative_control_the_old_fixed_list_let_these_through():
    """The old collection, run on the same arguments: nothing to check."""
    old_keys = ("path", "paths", "directory", "filePath", "filepath",
                "source", "destination", "src", "dst", "target", "folder")
    for args in ({"file_path": OUTSIDE}, {"cwd": OUTSIDE}, {"uri": "file:///etc/shadow"},
                 {"options": {"workspace_location": "/etc/ssh/"}}):
        paths: list[str] = []
        for key in old_keys:
            val = args.get(key)
            if val is None:
                continue
            items = val if isinstance(val, list) else [val]
            paths.extend(str(v) for v in items if isinstance(v, (str, int)) and str(v).strip())
        assert paths == [], args  # unchecked: the call went to the server
