"""Real OS locks prevent two servers writing the same local stores."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from kazma_core.runtime_writer import RuntimeWriterLease

CORE = str(Path(__file__).resolve().parents[1] / "kazma-core")
CHILD = """
import sys
from pathlib import Path
from kazma_core.runtime_writer import RuntimeWriterLease, RuntimeWriterBusy
lease = RuntimeWriterLease(Path(sys.argv[1]))
try:
    lease.acquire()
except RuntimeWriterBusy:
    print('busy', flush=True)
    raise SystemExit(2)
print('owned', flush=True)
sys.stdin.readline()
lease.release()
"""


def _child(root):
    env = dict(os.environ, PYTHONPATH=CORE)
    return subprocess.Popen([sys.executable, "-c", CHILD, str(root)], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_second_process_is_refused_until_owner_releases(tmp_path):
    owner = RuntimeWriterLease(tmp_path)
    owner.acquire()
    try:
        other = _child(tmp_path)
        out, err = other.communicate("\n", timeout=15)
        assert other.returncode == 2, err
        assert out.strip() == "busy"
    finally:
        owner.release()
    successor = _child(tmp_path)
    out, err = successor.communicate("\n", timeout=15)
    assert successor.returncode == 0, err
    assert out.strip() == "owned"


def test_process_death_releases_without_stale_lock_deletion(tmp_path):
    child = _child(tmp_path)
    try:
        assert child.stdout.readline().strip() == "owned"
        child.kill()
        child.communicate(timeout=15)
        successor = RuntimeWriterLease(tmp_path)
        successor.acquire()
        successor.release()
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=15)
    assert (tmp_path / ".runtime-writer.lock").exists()


def test_references_in_one_process_keep_ownership(tmp_path):
    first, second = RuntimeWriterLease(tmp_path), RuntimeWriterLease(tmp_path)
    first.acquire()
    second.acquire()
    first.release()
    try:
        other = _child(tmp_path)
        out, _ = other.communicate("\n", timeout=15)
        assert other.returncode == 2 and out.strip() == "busy"
    finally:
        second.release()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork inheritance")
def test_fork_cannot_borrow_parent_ownership(tmp_path):
    from kazma_core.runtime_writer import RuntimeWriterBusy
    owner = RuntimeWriterLease(tmp_path)
    owner.acquire()
    child_pid = os.fork()
    if child_pid == 0:
        try:
            owner.acquire()
        except RuntimeWriterBusy:
            os._exit(0)
        os._exit(3)
    try:
        _, status = os.waitpid(child_pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        owner.release()


@pytest.mark.asyncio
async def test_cancelled_shutdown_retains_writer_fence(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from kazma_ui.app import KazmaAppBuilder
    builder = KazmaAppBuilder()
    owner = RuntimeWriterLease(tmp_path)
    owner.acquire()
    builder._server_lease = owner
    monkeypatch.setattr(builder, "_shutdown_services", AsyncMock(side_effect=asyncio.CancelledError))
    try:
        with pytest.raises(asyncio.CancelledError):
            await builder._on_shutdown()
        other = _child(tmp_path)
        out, _ = other.communicate("\n", timeout=15)
        assert other.returncode == 2 and out.strip() == "busy"
    finally:
        owner.release()
