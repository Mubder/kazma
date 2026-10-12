"""Real contained children/Chromium exercise budgets, not only close mocks."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from types import SimpleNamespace
import json
import time

import psutil
import pytest

from kazma_core.security import process_budget
from kazma_core.security.child_env import tool_child_env
from kazma_core.web_acquire import browser_driver as bd
from kazma_core.web_acquire.result import AcquisitionError


@pytest.fixture
def fake_driver(monkeypatch):
    """A real child in the same OS Job used by the actual Windows driver."""
    if os.name != 'nt':
        pytest.skip('Real Windows Job qualification; Linux cgroup qualification is separate')
    children = []
    def install(script):
        def start():
            proc, tree = process_budget._start_process(
                [sys.executable, '-I', '-c', script], env=tool_child_env(), memory_bytes=bd._MEMORY)
            children.append(proc)
            return proc, tree
        monkeypatch.setattr(bd, '_start_driver', start)
    yield install, children
    for proc in children:
        assert proc.poll() is not None, 'The driver left its owned process alive'


@pytest.mark.asyncio
@pytest.mark.parametrize('script,reason', [
    ("import os,time; os.write(1,(2**32-1).to_bytes(4,'little')); time.sleep(30)", 'protocol'),
    ("import os,time; os.write(2,b'x'*1000000); time.sleep(30)", 'stderr'),
    ("import time; time.sleep(30)", 'lifetime'),
], ids=['oversized-frame', 'stderr-flood', 'hung-handshake'])
async def test_bad_driver_ends_its_process_without_unbounded_read_or_wait(fake_driver, monkeypatch, script, reason):
    install, children = fake_driver
    loop_errors = []
    monkeypatch.setattr(asyncio.get_running_loop(), 'call_exception_handler', loop_errors.append)
    install(script)
    monkeypatch.setattr(bd, '_LIFETIME', 0.15 if reason == 'lifetime' else 2)
    driver = bd.BrowserDriver()
    try:
        with pytest.raises(AcquisitionError):
            await asyncio.wait_for(driver.start(), 5)
        assert reason in str(driver.transport.failure).lower()
    finally:
        await driver.stop()
    assert children[0].poll() is not None
    assert driver.connection._init_task.done()
    assert not loop_errors


@pytest.mark.asyncio
async def test_command_queue_includes_the_blocked_write_and_refuses_more_data(fake_driver, monkeypatch):
    install, children = fake_driver
    install('import time; time.sleep(30)')
    monkeypatch.setattr(bd, '_FRAME', 128 * 1024)
    monkeypatch.setattr(bd, '_QUEUED', 128 * 1024)
    transport = bd._Transport(asyncio.get_running_loop())
    await transport.connect()
    try:
        transport.send({'payload': 'x' * 100000})
        with pytest.raises(AcquisitionError, match='queue budget'):
            transport.send({'payload': 'x' * 100000})
        assert transport._queued <= bd._QUEUED
        await asyncio.wait_for(transport.wait_until_stopped(), 5)
    finally:
        transport.request_stop()
        await transport.wait_until_stopped()
    assert children[0].poll() is not None


def test_driver_environment_does_not_receive_install_credentials_or_node_overrides(monkeypatch):
    from kazma_core.web_acquire import browser_cgroup
    captured = []
    def start(command, **kwargs):
        captured.append(kwargs['env'])
        return 'process', 'boundary'
    monkeypatch.setattr(process_budget, '_start_process', start)
    monkeypatch.setattr(browser_cgroup, 'start_linux_driver', start)
    monkeypatch.setenv('KAZMA_SECRET', 'fixture-vault-secret')
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-api-key')
    monkeypatch.setenv('HTTPS_PROXY', 'http://fixture:secret@proxy.example:80')
    monkeypatch.setenv('NODE_OPTIONS', '--require fixture-dangerous-module')
    monkeypatch.setenv('DEBUG', 'pw:*')
    assert bd._start_driver() == ('process', 'boundary')
    env = captured[0]
    assert not {'KAZMA_SECRET', 'OPENAI_API_KEY', 'HTTPS_PROXY', 'NODE_OPTIONS', 'DEBUG'} & env.keys()
    assert env['PW_LANG_NAME'] == 'python'


@pytest.mark.asyncio
async def test_real_chromium_hung_close_ends_descendants_before_releasing_capacity(monkeypatch):
    if os.name != 'nt':
        pytest.skip('Windows Job qualification; Linux runs require delegated cgroup v2')
    from kazma_core.playwright_loop import run_in_browser_loop
    from kazma_core.web_acquire.browser import BrowserJob
    from kazma_core.web_acquire import resources
    pool = resources._CapacityPool('hung-close browser', 1, 0)
    monkeypatch.setattr(resources, '_BROWSERS', pool)
    async def verify():
        owner = BrowserJob()
        await owner._start_impl()
        processes = [psutil.Process(owner._driver.transport.proc.pid)]
        processes += processes[0].children(recursive=True)
        assert len(processes) >= 3, 'Chromium descendants did not start'
        async def hang():
            await asyncio.Event().wait()
        owner.context.close = hang
        with pytest.raises(AcquisitionError, match='graceful cleanup exceeded'):
            await asyncio.wait_for(owner._close_impl(), 15)
        gone, alive = await asyncio.to_thread(psutil.wait_procs, processes, timeout=3)
        assert not alive and len(gone) == len(processes)
        assert owner._driver is owner.context is owner.browser is owner.pw is owner._deny_server is None
    await run_in_browser_loop(verify())
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 0


@pytest.mark.asyncio
async def test_watchdog_ends_real_browser_even_while_its_event_loop_is_blocked(monkeypatch):
    if os.name != 'nt':
        pytest.skip('Windows Job qualification; Linux runs require delegated cgroup v2')
    from kazma_core.playwright_loop import run_in_browser_loop
    from kazma_core.web_acquire.browser import BrowserJob
    monkeypatch.setattr(bd, '_LIFETIME', 2)
    ready, release = threading.Event(), threading.Event()
    processes = []
    async def blocked():
        owner = BrowserJob()
        try:
            await owner._start_impl()
            leader = psutil.Process(owner._driver.transport.proc.pid)
            processes.extend([leader, *leader.children(recursive=True)])
            ready.set()
            assert release.wait(6), 'Main loop did not finish the watchdog observation'
        finally:
            await owner._close_impl()
    task = asyncio.create_task(run_in_browser_loop(blocked(), timeout=12))
    try:
        assert await asyncio.to_thread(ready.wait, 4), 'Real Chromium did not initialize'
        gone, alive = await asyncio.to_thread(psutil.wait_procs, processes, timeout=5)
        assert len(processes) >= 3 and not alive and len(gone) == len(processes)
    finally:
        release.set()
        await task


def test_cgroup_unavailable_never_executes_an_unbounded_driver(monkeypatch):
    from kazma_core.web_acquire import browser_cgroup as cg
    starts = []
    def unavailable():
        raise OSError('fixture: no delegation')
    monkeypatch.setattr(cg, '_delegation', unavailable)
    monkeypatch.setattr(cg, 'os', SimpleNamespace(pidfd_open=lambda pid: None))
    monkeypatch.setattr(cg, 'subprocess', SimpleNamespace(Popen=lambda *a, **k: starts.append(a)))
    with pytest.raises(AcquisitionError, match='delegated cgroup'):
        cg.start_linux_driver(['node', 'run-driver'], env={}, memory_bytes=2**30)
    assert not starts


def test_cgroup_directory_open_failure_does_not_leak_its_owned_group(monkeypatch, tmp_path):
    from kazma_core.web_acquire import browser_cgroup as cg

    root = tmp_path / 'delegation'
    root.mkdir()
    opened, closed = [], []
    def open_directory(path, flags):
        opened.append(path)
        if len(opened) == 2:
            raise OSError('fixture: new cgroup directory unavailable')
        return 41
    monkeypatch.setattr(cg, 'os', SimpleNamespace(open=open_directory, close=closed.append,
        O_RDONLY=0, O_DIRECTORY=1, O_CLOEXEC=2, O_NOFOLLOW=4))
    monkeypatch.setattr(cg, '_is_cgroup2', lambda fd: True)
    with pytest.raises(OSError, match='directory unavailable'):
        cg._Cgroup(root, 2**30)
    assert closed == [41] and list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_browser_resource_failure_receipt_keeps_the_os_budget():
    if os.name != 'nt':
        pytest.skip('Windows browser qualification; Linux needs delegated cgroup v2')
    from kazma_core.playwright_loop import run_in_browser_loop
    from kazma_core.web_acquire import browser
    from kazma_core.web_acquire.result import FetchErrorCode

    async def verify():
        owner = browser.BrowserJob()
        try:
            await owner._start_impl()
            owner._driver.transport._fail('fixture: browser resource budget exhausted')
            with pytest.raises(AcquisitionError, match='resource budget exhausted') as caught:
                await owner.context.new_page()
            assert caught.value.code == FetchErrorCode.LIMIT
            await owner._driver.transport.wait_until_stopped()
            rendered = await browser._render_in_job(owner, 'https://example.com/',
                challenge=lambda html, status: False, profile='generic')
            evidence = rendered.result.evidence()
            assert not rendered.result.ok and rendered.result.error_code == FetchErrorCode.LIMIT
            assert evidence['browser_process']['memory_bytes'] == 2 * 1024**3
            assert evidence['browser_process']['failed'] is True
        finally:
            await owner._close_impl()
    await run_in_browser_loop(verify())


@pytest.mark.asyncio
async def test_failed_os_cleanup_keeps_its_handle_and_succeeds_on_retry(fake_driver):
    install, children = fake_driver
    install('import time; time.sleep(30)')
    transport = bd._Transport(asyncio.get_running_loop())
    await transport.connect()
    actual_tree = transport.tree
    first = True
    def close():
        nonlocal first
        if first:
            first = False
            raise OSError('fixture: OS proof temporarily unavailable')
        actual_tree.close()
    transport.tree = SimpleNamespace(close=close)
    driver = bd.BrowserDriver()
    driver.transport = transport
    try:
        with pytest.raises(AcquisitionError, match='quarantined'):
            await driver.stop()
        assert not actual_tree.closed and children[0].poll() is None
        await driver.stop()
        assert actual_tree.closed and children[0].poll() is not None
    finally:
        await driver.stop()


def test_windows_memory_budget_covers_combined_descendant_allocations():
    if os.name != 'nt':
        pytest.skip('Windows aggregate Job Object budget')
    child = "import sys; data=bytearray(64*1024*1024); print('ready',flush=True); sys.stdin.read(1)"
    script = (
        "import subprocess,sys; "
        f"a=subprocess.Popen([sys.executable,'-I','-c',{child!r}],stdin=subprocess.PIPE,stdout=subprocess.PIPE); "
        "assert a.stdout.readline().strip()==b'ready'; "
        f"b=subprocess.run([sys.executable,'-I','-c',{child!r}],input=b'1',capture_output=True); "
        "assert b.returncode != 0, 'combined descendants exceeded the aggregate memory budget'; "
        "a.stdin.write(b'1'); a.stdin.flush(); a.wait(); print('bounded')"
    )
    result = process_budget.run_bounded([sys.executable, '-I', '-c', script], env=tool_child_env(),
                                       memory_bytes=128 * 1024**2, timeout=5, text=True)
    assert result.returncode == 0 and result.stdout.strip() == 'bounded', result.stderr


@pytest.mark.asyncio
async def test_reader_keeps_call_context_in_its_owned_executor(fake_driver):
    from contextvars import ContextVar
    install, _ = fake_driver
    install('import time; time.sleep(30)')
    transport = bd._Transport(asyncio.get_running_loop())
    await transport.connect()
    context = ContextVar('browser_reader_test_scope', default='missing')
    context.set('expected-tenant')
    observed = []
    def read():
        observed.append(context.get())
        return None
    transport._read_frame = read
    await transport.run()
    assert observed == ['expected-tenant']


def test_killing_server_process_also_ends_its_real_browser_tree(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows kill-on-job-close proof; Linux guardian qualification is separate')
    marker = tmp_path / 'browser-tree.json'
    script = """import asyncio,json,os,psutil,sys
from pathlib import Path
from kazma_core.web_acquire.browser import BrowserJob
async def main():
 owner=BrowserJob()
 await owner._start_impl()
 leader=psutil.Process(owner._driver.transport.proc.pid)
 pids=[os.getpid(),leader.pid,*[p.pid for p in leader.children(recursive=True)]]
 path=Path(sys.argv[1]); pending=path.with_suffix('.pending')
 pending.write_text(json.dumps(pids)); pending.replace(path)
 await asyncio.Event().wait()
asyncio.run(main())
"""
    env = tool_child_env({key: str(tmp_path / name) for key, name in [
        ('KAZMA_DATA_DIR', 'data'), ('KAZMA_USER_HOME', 'home'), ('KAZMA_SKILLS_HOME', 'skills')]})
    # This proves Job Object parent-death cleanup, not strict Windows network
    # isolation. The filtered child environment drops the fixture's KAZMA_* mode.
    env['KAZMA_BROWSER_NETWORK_MODE'] = 'compatibility'
    proc, tree = process_budget._start_process([sys.executable, '-I', '-c', script, str(marker)], env=env)
    try:
        deadline = time.monotonic() + 8
        while not marker.exists():
            if tree.finished() or time.monotonic() >= deadline:
                pytest.fail('The server fixture did not launch its real browser')
            time.sleep(0.01)
        pids = json.loads(marker.read_text())
        assert len(pids) >= 4
        parent, *descendants = [psutil.Process(pid) for pid in pids]
        parent.kill()  # kill the interpreter, not the Windows venv launcher
        gone, alive = psutil.wait_procs(descendants, timeout=5)
        assert len(gone) == len(descendants) and not alive
    finally:
        tree.close()
        proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()
