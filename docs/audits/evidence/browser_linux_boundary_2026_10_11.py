"""Disposable Linux kernel qualification; requires an owned delegated cgroup.

Run with the isolated interpreter, repository core path and scratch directory
as arguments. This is outside normal test discovery and does not touch install
data, provider accounts or host controllers outside the supplied delegation.
"""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from kazma_core.web_acquire import browser_cgroup as cg
from kazma_core.web_acquire.browser import BrowserJob

root = Path(sys.argv[2])


def record(case, **fields):
    print(json.dumps({'case': case, **fields}), flush=True)


def kernel_case(case, script, memory=256 * 1024**2, lifetime=3):
    proc, group = cg.start_linux_driver([sys.executable, '-I', '-c', script],
        env={'PATH': '/usr/bin:/bin'}, memory_bytes=memory, lifetime=lifetime)
    try:
        limits = {key: group._read(key) for key in ['memory.max', 'memory.swap.max', 'pids.max', 'cpu.max']}
        out, err = proc.communicate(timeout=8)
        events = group._read('memory.events') if case == 'aggregate_memory' else group._read('pids.events')
        if case == 'aggregate_memory':
            assert 'unbounded' not in out.decode() and int(dict(line.split() for line in events.splitlines())['oom_kill']) > 0
        elif case == 'task_limit':
            assert b'bounded' in out and int(dict(line.split() for line in events.splitlines())['max']) > 0
        else:
            assert b'ready' in out and proc.returncode != 0
        assert 'populated 0' in group._read('cgroup.events')
        record(case, ok=True, limits=limits, exit=proc.returncode)
    finally:
        group.close()
        proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()
    assert not group.path.exists()


def parent_death():
    marker = root / 'parent-death.json'
    marker.unlink(missing_ok=True)
    script = """import os,sys,json,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from kazma_core.web_acquire.browser_cgroup import start_linux_driver
code="import os,subprocess,sys,time; child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(30)']); print(os.getpid(),child.pid,flush=True); time.sleep(30)"
proc,group=start_linux_driver([sys.executable,'-I','-c',code],env={'PATH':'/usr/bin:/bin'},memory_bytes=256*1024**2,lifetime=10)
pids=[proc.pid,*map(int,proc.stdout.readline().split())]
path=Path(sys.argv[2]); pending=path.with_suffix('.pending')
pending.write_text(json.dumps({'group':str(group.path),'pids':pids}));pending.replace(path)
time.sleep(30)
"""
    env = {**os.environ, 'KAZMA_BROWSER_CGROUP_ROOT': os.environ['KAZMA_BROWSER_CGROUP_ROOT']}
    server = subprocess.Popen([sys.executable, '-I', '-c', script, sys.argv[1], str(marker)], env=env)
    group = None
    try:
        deadline = time.monotonic() + 5
        while not marker.exists():
            assert server.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        data = json.loads(marker.read_text())
        path = Path(data['group'])
        assert path.parent == Path(os.environ['KAZMA_BROWSER_CGROUP_ROOT'])
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        group = object.__new__(cg._Cgroup)
        group.path, group.directory = path, directory
        group.closed = False
        server.kill()
        server.wait(timeout=3)
        deadline = time.monotonic() + 5
        while 'populated 0' not in group._read('cgroup.events'):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        record('parent_death', ok=True, tasks_observed=len(data['pids']))
    finally:
        if server.poll() is None:
            server.kill()
        server.wait(timeout=3)
        if group is not None:
            group.close()


async def chromium(hung=False):
    from unittest.mock import patch
    from playwright.async_api import BrowserType

    original_launch = BrowserType.launch
    async def launch(browser_type, **kwargs):
        # Test-only wrapper supplies missing distro libraries from our own
        # extracted packages. Browser sandbox and production launch flags stay.
        wrapper = root / 'chromium-with-native-libraries'
        if wrapper.exists():
            kwargs['executable_path'] = str(wrapper)
        return await original_launch(browser_type, **kwargs)

    owner = BrowserJob()
    forced = False
    try:
        with patch.object(BrowserType, 'launch', launch):
            await owner._start_impl()
        group = owner._driver.transport.tree
        assert group._read('memory.max') == str(2 * 1024**3)
        page = await owner.context.new_page()
        await page.set_content('<html><body><p>Bounded Linux browser</p></body></html>')
        assert await page.text_content('p') == 'Bounded Linux browser'
        await page.close()
        budget = owner._driver.evidence()
        if hung:
            async def hang():
                await asyncio.Event().wait()
            owner.context.close = hang
    finally:
        try:
            await owner._close_impl()
        except cg.AcquisitionError as exc:
            assert hung and 'graceful cleanup exceeded' in str(exc)
            forced = True
    assert not group.path.exists()
    assert owner._capacity_lease is owner._driver is None
    assert not hung or forced
    record('chromium_hung_close' if hung else 'chromium_sandbox', ok=True, budget=budget)


async def blocked_loop():
    from unittest.mock import patch
    from kazma_core.web_acquire import browser_driver

    ready, release = threading.Event(), threading.Event()
    paths, errors = [], []
    async def worker():
        owner = BrowserJob()
        # Reuse the isolated library wrapper without disabling the sandbox.
        from playwright.async_api import BrowserType
        original = BrowserType.launch
        async def launch(browser_type, **kwargs):
            kwargs['executable_path'] = str(root / 'chromium-with-native-libraries')
            return await original(browser_type, **kwargs)
        try:
            with patch.object(BrowserType, 'launch', launch):
                await owner._start_impl()
            paths.append(owner._driver.transport.tree.path)
            ready.set()
            assert release.wait(6)
        finally:
            await owner._close_impl()
    def run():
        try:
            asyncio.run(worker())
        except BaseException as exc:
            errors.append(repr(exc))
            ready.set()
    with patch.object(browser_driver, '_LIFETIME', 1):
        thread = threading.Thread(target=run)
        thread.start()
        try:
            assert await asyncio.to_thread(ready.wait, 4)
            assert paths and not errors
            deadline = time.monotonic() + 4
            while True:
                try:
                    events = (paths[0] / 'cgroup.events').read_text()
                except FileNotFoundError:
                    # The independent closer removes only after kernel empty
                    # verification; the root harness is still waiting for us.
                    break
                if 'populated 0' in events:
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(.01)
            record('chromium_stalled_loop', ok=True, descendants_ended_before_unblock=True)
        finally:
            release.set()
            await asyncio.to_thread(thread.join, 4)
        assert not thread.is_alive() and not errors
        assert not paths[0].exists()


if __name__ == '__main__':
    child = "import time; data=bytearray(64*1024*1024); print('ready',flush=True); time.sleep(30)"
    memory = ("import subprocess,sys,time; "
        f"a=subprocess.Popen([sys.executable,'-I','-c',{child!r}],stdout=subprocess.PIPE); "
        "assert a.stdout.readline().strip()==b'ready'; "
        f"b=subprocess.Popen([sys.executable,'-I','-c',{child!r}],stdout=subprocess.PIPE); "
        "assert b.stdout.readline().strip()==b'ready'; print('unbounded',flush=True); time.sleep(30)")
    kernel_case('aggregate_memory', memory, memory=128 * 1024**2)
    kernel_case('task_limit', "import threading,time\ntry:\n for i in range(300): threading.Thread(target=lambda:time.sleep(30),daemon=True).start()\nexcept RuntimeError:\n print('bounded',flush=True)\n")
    kernel_case('hard_lifetime', "import time; print('ready',flush=True); time.sleep(30)", lifetime=0.3)
    parent_death()
    asyncio.run(chromium())
    asyncio.run(chromium(hung=True))
    asyncio.run(blocked_loop())
