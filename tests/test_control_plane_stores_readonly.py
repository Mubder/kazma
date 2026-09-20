"""Kazma's own databases are never writable by agent file tools.

``hitl_gates.db`` is the decision-truth store. Flip a row from ``pending``
to ``approved`` and the resume chokepoint believes a human authorised a
danger-tier action — the strongest safety control in the system, bypassed
by a file write. ``rbac.db`` decides who may do what, ``audit.db`` is the
evidence trail, ``vault.db`` holds secrets.

Until 2026-09-21 nothing in the policy mentioned any of them. They were safe
only by POSITION: the default coding sandbox is ``data_dir()/workspace``,
which makes ``data_dir()/hitl_gates.db`` a sibling and therefore outside the
workspace. Measured before the fix, with the workspace bound to a directory
containing the data dir:

    hitl_gates.db    write -> WRITABLE (inside active workspace)
    settings.db      write -> WRITABLE (inside active workspace)
    llm_ledger.db    write -> WRITABLE (inside active workspace)
    checkpoints.db   write -> WRITABLE (inside active workspace)

and with ``allow_absolute_paths()`` on — the documented dev escape hatch —
the real ``hitl_gates.db`` was writable with no workspace at all.

So the guard has to sit BEFORE the allow ladder, not inside it. Anything
evaluated after "under active workspace -> allow" is decoration.

``check_path_access`` is the only chokepoint that needs it:
``tool_scope._workspace_scope_error`` (used by ``file_append``,
``file_delete``, ``file_list``, ``file_search``, ``send_file``) is a
fail-closed wrapper around it, and ``file_write`` / ``file_apply_patch`` /
``file_read`` call it directly.

Reads are deliberately still allowed. Blocking them would break legitimate
self-audit, and the sensitive one — ``vault.db`` — is encrypted at rest, so
a read yields ciphertext. The severe failures here are write failures.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

#: Stores that must never be writable, whatever the workspace says.
PROTECTED = [
    "hitl_gates.db",
    "rbac.db",
    "audit.db",
    "vault.db",
    "settings.db",
    "checkpoints.db",
    "llm_ledger.db",
    "snapshots.db",
    # A store that does not exist yet. Suffix matching, not an enumerated
    # list, is what makes this pass — the next store is covered on the day
    # it is added rather than the day someone remembers this file.
    "some_future_store.db",
    # Sidecars: appending to the WAL decides what the next reader sees
    # without ever opening the main database.
    "hitl_gates.db-wal",
    "hitl_gates.db-shm",
    "hitl_gates.db-journal",
    "hitl_gates.sqlite3",
]

#: Must stay writable — these are the user's, not Kazma's.
ALLOWED = [
    "workspace/project.db",  # the default coding sandbox
    "workspace/nested/data.sqlite",
    "workspace/notes.txt",
]


def _run_probe(body: str, tmp_path) -> str:
    """Run *body* in a fresh process with KAZMA_DATA_DIR inside a workspace.

    Subprocess because ``data_dir()`` is resolved from the environment and
    several callers cache it at import; setting the variable after the fact
    inside the test process would prove nothing about a real deployment.
    """
    ws = tmp_path / "ws"
    data = ws / "kazma-data"
    (data / "workspace" / "nested").mkdir(parents=True)

    env = dict(os.environ)
    env["KAZMA_DATA_DIR"] = str(data)
    env["KAZMA_DB_BACKEND"] = "sqlite"
    env.pop("KAZMA_DATABASE_URL", None)

    script = textwrap.dedent(f"""
        from pathlib import Path
        from kazma_core.paths import data_dir
        from kazma_core.workspace import binding
        from kazma_core.workspace import path_policy
        from kazma_core.workspace.path_policy import check_path_access

        WS = Path({str(ws)!r}).resolve()
        DATA = data_dir().resolve()
        binding.configure_workspace(workspace=str(WS), allow_absolute=False)

        # resolve_active_root() consults the WorkspaceStore BEFORE the process
        # pin, and that store does not follow KAZMA_DATA_DIR — a subprocess
        # here picks up the developer's real active workspace and every path
        # lands "outside workspace; no grant". That made the first version of
        # this file pass vacuously: the protected stores were denied for the
        # wrong reason and a plain .txt was denied too. Pin the root so the
        # only thing under test is rule 0 versus the allow ladder.
        path_policy.resolve_active_root = lambda: WS
    """) + textwrap.dedent(body)

    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout


@pytest.mark.parametrize("name", PROTECTED)
def test_control_plane_store_is_not_writable(name: str, tmp_path) -> None:
    """Even with the data dir sitting inside the active workspace."""
    out = _run_probe(
        f"""
        t = DATA / {name!r}
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(b"")
        r = check_path_access(t.resolve(), "write")
        print("ALLOWED" if r.allowed else "DENIED", r.reason)
        """,
        tmp_path,
    )
    assert out.startswith("DENIED"), (
        f"{name} is writable by file tools ({out.strip()}). If this is "
        "hitl_gates.db, an agent can approve its own danger-tier gates."
    )
    # Deny for the RIGHT reason. The store sitting outside the workspace
    # would also print DENIED, and that is the positional accident this fix
    # exists to stop relying on.
    assert "control-plane" in out, (
        f"{name} was denied, but not by the control-plane rule ({out.strip()})"
    )


@pytest.mark.parametrize("rel", ALLOWED)
def test_user_files_stay_writable(rel: str, tmp_path) -> None:
    """The guard must not swallow the sandbox or the user's own databases."""
    out = _run_probe(
        f"""
        t = DATA / {rel!r}
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(b"")
        r = check_path_access(t.resolve(), "write")
        print("ALLOWED" if r.allowed else "DENIED", r.reason)
        """,
        tmp_path,
    )
    assert out.startswith("ALLOWED"), (
        f"{rel} is NOT writable ({out.strip()}) — the guard is too broad and "
        "has taken the user's own scratch area with it"
    )


def test_reads_are_still_allowed(tmp_path) -> None:
    """Self-audit must keep working; the severe failures here are writes."""
    out = _run_probe(
        """
        t = DATA / "hitl_gates.db"
        t.write_bytes(b"")
        r = check_path_access(t.resolve(), "read")
        print("ALLOWED" if r.allowed else "DENIED", r.reason)
        """,
        tmp_path,
    )
    assert out.startswith("ALLOWED"), (
        "reads were caught too; that blocks legitimate self-inspection for no "
        "safety gain, since the sensitive store is encrypted at rest"
    )


def test_allow_absolute_escape_hatch_cannot_reach_them(tmp_path) -> None:
    """The dev escape hatch must not reopen the control plane.

    This is the case that was measurably open: with ``allow_absolute`` on and
    no workspace bound at all, the real gate registry was writable.
    """
    out = _run_probe(
        """
        binding.configure_workspace(workspace=None, allow_absolute=True)
        t = DATA / "hitl_gates.db"
        t.write_bytes(b"")
        r = check_path_access(t.resolve(), "write")
        print("ALLOWED" if r.allowed else "DENIED", r.reason)
        """,
        tmp_path,
    )
    assert out.startswith("DENIED"), (
        f"allow_absolute_paths() reopens the control plane ({out.strip()}) — "
        "a dev escape hatch must not be able to authorise HITL self-approval"
    )


def test_session_grant_cannot_reach_them(tmp_path) -> None:
    """Nor may a path grant the agent can request for itself.

    ``request_path_access`` is agent-callable, so a grant is not an
    independent authority the way a human approval is.
    """
    out = _run_probe(
        """
        from kazma_core.workspace import path_grants
        binding.configure_workspace(workspace=str(WS / "elsewhere"),
                                    allow_absolute=False)
        path_grants.grant_session_path("t1", str(DATA), mode="write")
        t = DATA / "hitl_gates.db"
        t.write_bytes(b"")
        r = check_path_access(t.resolve(), "write", thread_id="t1")
        print("ALLOWED" if r.allowed else "DENIED", r.reason)
        """,
        tmp_path,
    )
    assert out.startswith("DENIED"), (
        f"a session path grant reaches the control plane ({out.strip()})"
    )
    assert "control-plane" in out
