"""Bounded real Linux DNS qualification; --wsl reuses the isolated WSL runtime.

No package installs, public network requests, host settings or live stores.
Controlled resolver fixtures run the exact standalone product entry point.
"""
import asyncio
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRATCH = '/home/balfa/.cache/kazma-browser-qualification-494866d8d64245159c10ebab204aec84'


def wsl():
    sys.path.insert(0, str(REPO / 'kazma-core'))
    from kazma_core.security.child_env import tool_child_env
    from kazma_core.security.process_budget import run_bounded
    probe = '/mnt/g/GitHubRepos/kazma/docs/audits/evidence/' + Path(__file__).name
    result = run_bounded(['wsl.exe', '-d', 'Ubuntu', '-u', 'balfa', '--exec',
        '/usr/bin/timeout', '-k', '2', '40', SCRATCH + '/runtime/bin/python',
        '-I', probe, '/mnt/g/GitHubRepos/kazma/kazma-core', SCRATCH],
        env=tool_child_env(), timeout=45, output_bytes=32768, memory_bytes=1024**3)
    output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
    log = REPO / 'docs/audits/evidence/dns_linux_boundary_2026_10_11.log'
    log.write_text(output, encoding='utf-8')
    print(output, flush=True)
    return result.returncode


async def qualify(core, scratch):
    sys.path.insert(0, str(core))
    import psutil
    from kazma_core.security import process_budget as pb, ssrf
    from kazma_core.web_acquire import dns_worker as dns, resources

    original = pb.run_bounded
    receipts = []
    loop_errors = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: loop_errors.append(context))
    with tempfile.TemporaryDirectory(prefix='dns-qualification-', dir=scratch) as directory:
        root = Path(directory)
        marker = root / 'started.json'
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'KAZMA_DATA_DIR': str(root / 'data'),
            'KAZMA_USER_HOME': str(root / 'home'), 'KAZMA_SKILLS_HOME': str(root / 'skills'),
            'KAZMA_WORKSPACE': str(root / 'workspace')}
        entry = dns.__file__
        children = []

        def program(body):
            return ('import json,os,socket,time,runpy\n'
                'def resolve(host,port):\n'
                f' with open({str(marker)!r},"w") as f: json.dump({{"pid":os.getpid()}},f)\n'
                + body + '\nsocket.getaddrinfo=resolve\n'
                f'runpy.run_path({entry!r},run_name="__main__")')

        body = " return [(0,0,0,'',('93.184.216.34',0)),(0,0,0,'',('10.0.0.1',0))]\n"
        def controlled(command, **kwargs):
            attach = kwargs['on_started']
            def started(proc, tree, threads):
                children.append((proc, tree, threads))
                attach(proc, tree, threads)
            kwargs['on_started'] = started
            return original([sys.executable, '-I', '-c', program(body)], **kwargs)

        request = json.dumps({'host': 'localhost', 'parent_pid': os.getpid(), 'lifetime': 5})
        native = original([sys.executable, '-I', entry], env=env, input=request,
            timeout=6, memory_bytes=256 * 1024**2, output_bytes=16384, text=True)
        data = json.loads(native.stdout)
        assert native.returncode == 0 and data['status'] == 'ok' and data['ips']
        receipts.append({'probe': 'native_localhost_resolution', 'addresses': data['ips'], 'passed': True})

        pb.run_bounded = controlled
        try:
            try:
                await dns.validate_public_url('https://mixed.fixture.example/')
            except ssrf.SSRFError:
                receipts.append({'probe': 'mixed_private_answer_refused', 'passed': True})
            else:
                raise AssertionError('Mixed private DNS was permitted')
            try:
                await dns.resolve_endpoint('hosted.fixture.example')
            except dns_exception() as exc:
                assert exc.code == 'security_blocked'
                receipts.append({'probe': 'hosted_endpoint_mixed_private_refused', 'passed': True})
            else:
                raise AssertionError('Hosted endpoint accepted a mixed private answer')
            body = " return [(0,0,0,'',('127.0.0.1',0))]\n"
            assert await dns.resolve_endpoint('proxy.fixture.example', trusted_proxy=True) == '127.0.0.1'
            receipts.append({'probe': 'operator_proxy_private_endpoint_owned', 'passed': True})
            body = ' data=bytearray(128*1024*1024); return []\n'
            dns._MEMORY = 64 * 1024**2
            try:
                await dns.validate_public_url('https://memory.fixture.example/')
            except dns_exception() as exc:
                assert exc.code == 'resource_limit'
                receipts.append({'probe': 'real_rlimit_memory', 'passed': True})
            else:
                raise AssertionError('Memory excess was permitted')
            finally:
                dns._MEMORY = 256 * 1024**2

            body = ' time.sleep(30)\n'
            dns._TIMEOUT = .25
            try:
                await dns.resolve_endpoint('stalled-proxy.fixture.example', trusted_proxy=True)
            except dns_exception() as exc:
                assert exc.code == 'timeout'
                receipts.append({'probe': 'proxy_native_dns_deadline_joins_child', 'passed': True})
            else:
                raise AssertionError('Stalled proxy DNS was permitted')
            finally:
                dns._TIMEOUT = 5
            marker.unlink(missing_ok=True)
            task = asyncio.create_task(dns.validate_public_url('https://cancel.fixture.example/'))
            try:
                deadline = time.monotonic() + 3
                while not marker.exists():
                    assert not task.done() and time.monotonic() < deadline
                    await asyncio.sleep(.01)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError('Cancellation was lost')
                assert resources.dns_capacity().snapshot()['active'] == 0
                assert ssrf._dns_budget.get() is None
                receipts.append({'probe': 'repeated_cancel_joins_owned_worker', 'passed': True})
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            pb.run_bounded = original
            assert dns._drain_cleanup(timeout=3)
            assert all(tree.closed and proc.returncode is not None and
                not any(thread.is_alive() for thread in threads) for proc, tree, threads in children)

        alarm = original([sys.executable, '-I', '-c', program(' time.sleep(30)\n')],
            env=env, input=json.dumps({'host': 'alarm.fixture', 'parent_pid': os.getpid(), 'lifetime': .25}),
            timeout=3, memory_bytes=256 * 1024**2, output_bytes=16384, text=True)
        assert alarm.returncode == -signal.SIGALRM
        receipts.append({'probe': 'independent_kernel_wall_clock', 'signal': 'SIGALRM', 'passed': True})

        marker.unlink(missing_ok=True)
        helper = (f'import sys,os,json,time;sys.path.insert(0,{str(core)!r});'
            'from kazma_core.security.process_budget import _start_process;'
            f'proc,tree=_start_process([sys.executable,"-I","-c",{program(" time.sleep(30)" + chr(10))!r}],'
            f'env={env!r},memory_bytes=256*1024**2);'
            'proc.stdin.write(json.dumps({"host":"parent.fixture","parent_pid":os.getpid(),"lifetime":5}).encode());'
            'proc.stdin.close();time.sleep(30)')
        owner, tree = pb._start_process([sys.executable, '-I', '-c', helper], env=env,
            memory_bytes=512 * 1024**2)
        orphan = None
        try:
            deadline = time.monotonic() + 3
            while not marker.exists():
                assert owner.poll() is None and time.monotonic() < deadline
                time.sleep(.01)
            orphan = psutil.Process(json.loads(marker.read_text())['pid'])
            identity = orphan.create_time()
            tree.close()
            owner.wait(timeout=2)
            deadline = time.monotonic() + 2
            while orphan.is_running() and orphan.status() != psutil.STATUS_ZOMBIE:
                assert time.monotonic() < deadline, 'Parent death did not independently end DNS'
                time.sleep(.01)
            state = 'zombie_waiting_for_init' if orphan.is_running() else 'reaped'
            receipts.append({'probe': 'verified_linux_parent_death', 'child_state': state, 'passed': True})
        finally:
            tree.close()
            owner.wait(timeout=2)
            for stream in (owner.stdin, owner.stdout, owner.stderr):
                stream.close()
            if orphan is not None and orphan.is_running() and orphan.create_time() == identity:
                if orphan.status() != psutil.STATUS_ZOMBIE:
                    os.killpg(orphan.pid, signal.SIGKILL)
        await asyncio.sleep(0)
        assert not loop_errors, repr(loop_errors)
        print(json.dumps({'platform': sys.platform, 'python': sys.version.split()[0],
            'passed': len(receipts), 'receipts': receipts}, indent=2), flush=True)


def dns_exception():
    from kazma_core.web_acquire.result import AcquisitionError
    return AcquisitionError


if __name__ == '__main__':
    if '--wsl' in sys.argv:
        raise SystemExit(wsl())
    asyncio.run(qualify(Path(sys.argv[1]), Path(sys.argv[2])))
