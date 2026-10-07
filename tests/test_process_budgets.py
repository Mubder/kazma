"""Real disposable children prove resource budgets and descendant cleanup."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import psutil
import pytest
from kazma_core.security.child_env import tool_child_env
from kazma_core.security.process_budget import OutputLimitExceeded, run_bounded, run_bounded_async


def run(code, **kwargs):
    return run_bounded([sys.executable, "-I", "-c", code], env=tool_child_env(), text=True, **kwargs)


def ended(pid):
    try:
        psutil.Process(pid).wait(timeout=3)
    except psutil.NoSuchProcess:
        pass
    assert not psutil.pid_exists(pid)


def test_input_and_both_output_streams_keep_their_contract():
    result = run("import sys; print(sys.stdin.read()); print('error',file=sys.stderr)", input="العربية", timeout=3)
    assert result.returncode == 0
    assert result.stdout.strip() == "العربية"
    assert result.stderr.strip() == "error"


def test_excess_output_is_a_bounded_failure_without_pipe_deadlock():
    started = time.monotonic()
    with pytest.raises(OutputLimitExceeded):
        run("import os;\nwhile True: os.write(1,b'x'*4096)", output_bytes=8192, timeout=3)
    assert time.monotonic() - started < 6


def test_timeout_kills_the_descendant_not_only_the_launcher(tmp_path):
    marker = tmp_path / "child.pid"
    child = f"import os,time; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(30)"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-I','-c',{child!r}]); time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired):
        run(parent, timeout=1)
    assert marker.exists(), "descendant did not start; this would not test tree cleanup"
    ended(int(marker.read_text()))


def test_an_exited_parent_cannot_leave_a_background_child():
    code = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(30)']); print(p.pid,flush=True)"
    result = run(code, timeout=3)
    assert result.returncode == 0
    ended(int(result.stdout.strip()))


def test_memory_limit_is_enforced_by_the_operating_system():
    result = run("data=bytearray(128*1024*1024); print('unbounded')", memory_bytes=64*1024*1024, timeout=3)
    assert result.returncode != 0
    assert "unbounded" not in result.stdout


@pytest.mark.asyncio
async def test_cancellation_tells_the_worker_to_end_its_tree(tmp_path):
    marker = tmp_path / "parent.pid"
    code = f"import os,time; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(30)"
    task = asyncio.create_task(run_bounded_async([sys.executable, "-I", "-c", code], env=tool_child_env(), timeout=20))
    try:
        async with asyncio.timeout(3):
            while not marker.exists():
                await asyncio.sleep(0.01)
        pid = int(marker.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.to_thread(ended, pid)
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_cancelled_launch_still_cleans_up_the_child(monkeypatch):
    import threading

    from kazma_core.security import process_budget

    original = process_budget._start_process
    launched = threading.Event()
    release = threading.Event()
    children = []
    def slow_launch(*args, **kwargs):
        proc, tree = original(*args, **kwargs)
        children.append(proc)
        launched.set()
        assert release.wait(3)
        return proc, tree
    monkeypatch.setattr(process_budget, "_start_process", slow_launch)
    task = asyncio.create_task(process_budget.start_process_async(
        [sys.executable, "-I", "-c", "import time;time.sleep(30)"], env=tool_child_env(),
    ))
    try:
        assert await asyncio.to_thread(launched.wait, 3)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        ended(children[0].pid)
    finally:
        release.set()
        task.cancel()


@pytest.mark.asyncio
async def test_mcp_blocking_reader_rejects_an_overlong_protocol_line():
    import io

    from kazma_core.mcp.manager import _SyncReaderAdapter

    reader = _SyncReaderAdapter(io.BytesIO(b"x" * 1000 + b"\n"), limit=8)
    with pytest.raises(ValueError, match="byte budget"):
        await reader.readline()
    assert reader._stream.tell() == 9


@pytest.mark.asyncio
async def test_mcp_write_is_deferred_off_the_loop_and_bounded():
    import io

    from kazma_core.mcp.manager import _SyncWriterAdapter

    stream = io.BytesIO()
    writer = _SyncWriterAdapter(stream, limit=8)
    writer.write(b"proof")
    assert stream.getvalue() == b""
    with pytest.raises(ValueError, match="byte budget"):
        writer.write(b"more-data")
    await writer.drain()
    assert stream.getvalue() == b"proof"


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ["manager", "diagnostic"])
async def test_real_stdio_handshake_and_disconnect_reap_a_server_child(tmp_path, implementation):
    from kazma_core.mcp.manager import AsyncMCPManager
    from kazma_core.mcp_client import MCPClient

    marker = tmp_path / "mcp-child.pid"
    child = "import time; time.sleep(30)"
    script = tmp_path / "server.py"
    script.write_text(
        "import json,subprocess,sys\n"
        f"child=subprocess.Popen([sys.executable,'-I','-c',{child!r}])\n"
        f"open({str(marker)!r},'w').write(str(child.pid))\n"
        "for line in sys.stdin:\n"
        "    msg=json.loads(line)\n"
        "    if 'id' not in msg: continue\n"
        "    result={'tools':[]} if msg['method']=='tools/list' else "
        "{'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'proof','version':'1'}}\n"
        "    print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}),flush=True)\n",
        encoding="utf-8",
    )
    manager = AsyncMCPManager() if implementation == "manager" else MCPClient()
    try:
        cfg = {
            "name": "budget-proof", "transport": "stdio",
            "command": [sys.executable, "-I", str(script)], "timeout": 5,
        }
        if implementation == "manager":
            assert await manager.connect_from_config([cfg], raise_on_error=True) == 0
            assert "budget-proof" in manager._servers, manager.list_servers()
            assert manager._servers["budget-proof"].connected
        else:
            assert await manager.connect(cfg)
            assert await manager.list_tools() == []
        async with asyncio.timeout(3):
            while not marker.exists():
                await asyncio.sleep(0.01)
        pid = int(marker.read_text())
        assert psutil.pid_exists(pid)
    finally:
        if implementation == "manager":
            await manager.shutdown()
        else:
            await manager.disconnect()
    await asyncio.to_thread(ended, pid)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["initialize", "tools/list"])
async def test_cancelled_mcp_setup_closes_its_unregistered_child(tmp_path, monkeypatch, stage):
    from kazma_core.mcp.manager import AsyncMCPManager

    marker = tmp_path / "setup.pid"
    script = tmp_path / "blocked_server.py"
    script.write_text(
        "import json,os,sys,time\n"
        "for line in sys.stdin:\n"
        "    msg=json.loads(line)\n"
        "    if 'id' not in msg: continue\n"
        f"    if msg['method']=={stage!r}:\n"
        f"        open({str(marker)!r},'w').write(str(os.getpid()))\n"
        "        time.sleep(30)\n"
        "    else:\n"
        "        result={'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'proof','version':'1'}}\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}),flush=True)\n",
        encoding="utf-8",
    )
    manager = AsyncMCPManager()
    handles = []
    original = manager._send
    async def record_handle(handle, *args, **kwargs):
        if not handles:
            handles.append(handle)
        return await original(handle, *args, **kwargs)
    monkeypatch.setattr(manager, "_send", record_handle)
    task = asyncio.create_task(manager.connect_from_config([{
        "name": "cancel-proof", "transport": "stdio",
        "command": [sys.executable, "-I", str(script)], "timeout": 20,
    }], raise_on_error=True))
    try:
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(0.01)
        pid = int(marker.read_text())
        assert psutil.pid_exists(pid)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "cancel-proof" not in manager._servers
        await asyncio.to_thread(ended, pid)
    finally:
        task.cancel()
        if handles:
            await manager._close_handle(handles[0])
        await manager.shutdown()


@pytest.mark.asyncio
async def test_cancelled_diagnostic_handshake_closes_its_child(tmp_path):
    from kazma_core.mcp_client import MCPClient

    marker = tmp_path / "diagnostic.pid"
    script = tmp_path / "diagnostic.py"
    script.write_text(f"import os,time;open({str(marker)!r},'w').write(str(os.getpid()));time.sleep(30)", encoding="utf-8")
    client = MCPClient()
    task = asyncio.create_task(client.connect({
        "name": "diagnostic-cancel", "transport": "stdio",
        "command": [sys.executable, "-I", str(script)], "timeout": 20,
    }))
    try:
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(0.01)
        pid = int(marker.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.to_thread(ended, pid)
        assert not client.connected
    finally:
        task.cancel()
        await client.disconnect()


@pytest.mark.asyncio
async def test_diagnostic_protocol_read_stops_at_its_byte_cap(monkeypatch):
    import io
    from types import SimpleNamespace

    from kazma_core import mcp_client

    monkeypatch.setattr(mcp_client, "_STDIO_BYTES", 8)
    client = mcp_client.MCPClient()
    stream = io.BytesIO(b"x" * 1000 + b"\n")
    client._process = SimpleNamespace(stdin=io.BytesIO(), stdout=stream, stderr=io.BytesIO())
    client._config = mcp_client.MCPServerConfig(name="bounded", timeout=1)
    with pytest.raises(mcp_client.MCPConnectionError, match="byte budget"):
        await client._send_stdio("{}\n")
    assert stream.tell() == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["write", "read"])
async def test_mcp_cancellation_at_completed_io_is_not_swallowed(monkeypatch, stage):
    """Python 3.11 wait_for loses cancellation racing with completed I/O."""
    from types import SimpleNamespace

    from kazma_core.mcp.manager import AsyncMCPManager, MCPServerHandle

    manager = AsyncMCPManager()
    target = None

    async def write(*args):
        if stage == "write":
            target.cancel()

    async def read():
        if stage == "read":
            target.cancel()
        await asyncio.sleep(0)
        return b'{"jsonrpc":"2.0","id":1,"result":{}}\n'

    monkeypatch.setattr(manager, "_write_stdin", write)
    handle = MCPServerHandle(name="cancel-race", transport="stdio", process=SimpleNamespace(
        stdin=object(), stdout=SimpleNamespace(readline=read), stderr=None,
    ), timeout=1)
    target = asyncio.create_task(manager._send_stdio(handle, "{}\n"))
    with pytest.raises(asyncio.CancelledError):
        await target
