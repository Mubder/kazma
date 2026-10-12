"""Real owned DNS children exercise cancellation, budgets and SSRF decisions."""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psutil
import pytest

from kazma_core.security import process_budget, ssrf
from kazma_core.web_acquire import dns_worker as dns, resources
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


@pytest.fixture
def resolver(tmp_path, monkeypatch):
    marker = tmp_path / 'dns-receipt.json'
    module = tmp_path / 'dns_fixture.py'
    original = process_budget.run_bounded
    calls, children = [], []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        attach = kwargs['on_started']
        def started(proc, tree, threads):
            children.append((proc, tree))
            attach(proc, tree, threads)
        kwargs['on_started'] = started
        program = (f'import sys,runpy;sys.path.insert(0,{str(tmp_path)!r});'
            f'import dns_fixture;runpy.run_path({dns.__file__!r},run_name="__main__")')
        return original([sys.executable, '-I', '-c', program], **kwargs)
    monkeypatch.setattr(process_budget, 'run_bounded', run)

    def install(body):
        module.write_text('import os,json,socket,time\n'
            'def resolve(host,port):\n'
            f' pending={str(marker)+".pending"!r}\n'
            ' with open(pending,"w") as receipt: receipt.write(json.dumps({"host":host,"pid":os.getpid(),"env":dict(os.environ)}))\n'
            f' os.replace(pending,{str(marker)!r})\n'
            + body + '\nsocket.getaddrinfo=resolve\n', encoding='utf-8')
    yield install, marker, calls, children
    assert dns._drain_cleanup(timeout=3), 'Owned DNS cleanup thread survived its drain'
    for proc, tree in children:
        assert tree.closed and proc.poll() is not None, 'An owned DNS child survived its caller'


async def published(marker, task):
    deadline = time.monotonic() + 5
    while not marker.exists():
        if task.done():
            await task
            pytest.fail('DNS worker did not publish readiness')
        if time.monotonic() >= deadline:
            pytest.fail('DNS worker did not start within fixture deadline')
        await asyncio.sleep(.01)
    return json.loads(marker.read_text())


@pytest.mark.asyncio
async def test_all_addresses_reach_the_existing_ssrf_validator_without_credentials(resolver, monkeypatch):
    install, marker, calls, _ = resolver
    install(" return [(0,0,0,'',(ip,0)) for ip in ['93.184.216.34','2001:4860:4860::8888','93.184.216.34']]\n")
    for key in ('KAZMA_SECRET', 'OPENAI_API_KEY', 'HTTP_PROXY', 'DDGS_PROXY', 'NODE_OPTIONS', 'PYTHONPATH'):
        monkeypatch.setenv(key, 'must-not-reach-resolver')
    assert await dns.validate_public_url('https://مثال.example/path?q=private') == ('93.184.216.34', '2001:4860:4860::8888')
    receipt = json.loads(marker.read_text())
    assert receipt['host'] == 'مثال.example'
    assert 'must-not-reach-resolver' not in receipt['env'].values()
    command, options = calls[0]
    assert command[1] == '-I' and 'private' not in repr(command)
    assert options['memory_bytes'] == 256 * 1024**2 and options['output_bytes'] == 16384
    assert options['timeout'] == 5 and ssrf._dns_budget.get() is None


@pytest.mark.asyncio
async def test_transport_endpoint_dns_uses_owned_child_and_trusts_only_explicit_proxy(resolver):
    install, marker, calls, _ = resolver
    install(" return [(0,0,0,'',('127.0.0.1',0))]\n")
    assert await dns.resolve_endpoint('operator-proxy.example', trusted_proxy=True) == '127.0.0.1'
    assert json.loads(marker.read_text())['host'] == 'operator-proxy.example'
    with pytest.raises(AcquisitionError) as failure:
        await dns.resolve_endpoint('hosted.example')
    assert failure.value.code == 'security_blocked' and len(calls) == 2


@pytest.mark.asyncio
async def test_transport_native_dns_deadline_terminates_its_child(resolver, monkeypatch):
    install, _, _, children = resolver
    install(' time.sleep(60);return []\n')
    monkeypatch.setattr(dns, '_TIMEOUT', .5)
    with pytest.raises(AcquisitionError) as failure:
        await dns.resolve_endpoint('stalled-proxy.example', trusted_proxy=True)
    assert failure.value.code == 'timeout'
    assert all(tree.closed and proc.poll() is not None for proc, tree in children)


@pytest.mark.asyncio
@pytest.mark.parametrize('body,code', [
    (" return [(0,0,0,'',('93.184.216.34',0)),(0,0,0,'',('10.0.0.2',0))]\n", 'security'),
    (" raise socket.gaierror('fixture unresolved')\n", 'security'),
    (" return [(0,0,0,'',('not-an-ip',0))]\n", 'backend_unavailable'),
    (" return [(0,0,0,'',(f'8.8.0.{i}',0)) for i in range(129)]\n", 'resource_limit'),
    (" print('x'*20000,flush=True); time.sleep(30)\n", 'resource_limit'),
], ids=['mixed-private','unresolved','malformed','address-overflow','output-overflow'])
async def test_failed_or_incomplete_dns_never_becomes_permission(resolver, body, code):
    install, _, _, _ = resolver
    install(body)
    if code == 'security':
        with pytest.raises(ssrf.SSRFError):
            await dns.validate_public_url('https://fixture.example/')
    else:
        with pytest.raises(AcquisitionError) as failure:
            await dns.validate_public_url('https://fixture.example/')
        assert failure.value.code == code


@pytest.mark.asyncio
async def test_dns_memory_budget_is_enforced_by_the_os(resolver, monkeypatch):
    install, _, _, _ = resolver
    install(" data=bytearray(128*1024*1024); return [(0,0,0,'',('93.184.216.34',0))]\n")
    monkeypatch.setattr(dns, '_MEMORY', 64 * 1024**2)
    with pytest.raises(AcquisitionError) as failure:
        await dns.validate_public_url('https://fixture.example/')
    assert failure.value.code == FetchErrorCode.LIMIT


@pytest.mark.asyncio
async def test_dns_deadline_ends_a_real_blocked_native_call(resolver, monkeypatch):
    install, marker, _, _ = resolver
    install(' time.sleep(30)\n')
    monkeypatch.setattr(dns, '_TIMEOUT', .5)
    with pytest.raises(AcquisitionError) as failure:
        await dns.validate_public_url('https://fixture.example/')
    assert marker.exists() and failure.value.code == FetchErrorCode.TIMEOUT


@pytest.mark.asyncio
async def test_repeated_cancellation_keeps_admission_until_child_cleanup_finishes(resolver, monkeypatch):
    install, marker, _, children = resolver
    install(' time.sleep(30)\n')
    pool = resources._CapacityPool('fixture DNS', 1, 0)
    monkeypatch.setattr(resources, '_DNS', pool)
    entered, release = threading.Event(), threading.Event()
    original = process_budget._ProcessTree.close
    loop_errors = []
    monkeypatch.setattr(asyncio.get_running_loop(), 'call_exception_handler', loop_errors.append)
    def held(tree):
        if any(tree is candidate for _, candidate in children) and not tree.closed:
            entered.set()
            if not release.wait(4):
                raise TimeoutError('Cleanup fixture was not released')
        original(tree)
    monkeypatch.setattr(process_budget._ProcessTree, 'close', held)
    task = asyncio.create_task(dns.validate_public_url('https://fixture.example/'))
    try:
        receipt = await published(marker, task)
        child = psutil.Process(receipt['pid'])
        task.cancel()
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        assert not task.done() and pool.snapshot()['active'] == 1
        assert psutil.pid_exists(receipt['pid'])
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert pool.snapshot()['active'] == pool.snapshot()['quarantined'] == 0
    child.wait(timeout=3)
    assert not child.is_running() and ssrf._dns_budget.get() is None
    await asyncio.sleep(0)
    assert not loop_errors, 'Owned cancellation must not log a shielded-future failure'


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True], ids=['failed','cancelled'])
async def test_uncertain_cleanup_retains_handles_quarantines_and_recovers_on_next_validation(resolver, monkeypatch, cancel):
    install, marker, _, children = resolver
    install(' time.sleep(30)\n' if cancel else " return [(0,0,0,'',('93.184.216.34',0))]\n")
    pool = resources._CapacityPool('fixture DNS', 1, 1, wait_seconds=3)
    monkeypatch.setattr(resources, '_DNS', pool)
    denied = threading.Event()
    denied.set()
    original = process_budget._ProcessTree.close
    def failed(tree):
        if denied.is_set() and any(tree is candidate for _, candidate in children):
            raise OSError('fixture: termination verification unavailable')
        original(tree)
    monkeypatch.setattr(process_budget._ProcessTree, 'close', failed)
    task = asyncio.create_task(dns.validate_public_url('https://fixture.example/'))
    try:
        if cancel:
            await published(marker, task)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(AcquisitionError, match='quarantined'):
                await task
        assert pool.snapshot()['quarantined'] == pool.snapshot()['active'] == 1
        report = importlib.import_module('kazma_core.tools.research_readiness').research_readiness()
        assert not next(check for check in report['checks'] if check['id'] == 'acquisition_dns')['ok']
        assert children and not children[0][1].closed and dns._QUARANTINED
    finally:
        denied.clear()
        # A new call starts one reaper and waits for the retained slot. Its own
        # literal address needs no child; it cannot replace the failed owner.
        assert await dns.validate_public_url('https://93.184.216.34/') == ('93.184.216.34',)
    assert not dns._QUARANTINED and pool.snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_full_dns_queue_refuses_before_starting_a_worker(resolver, monkeypatch):
    _, _, calls, _ = resolver
    pool = resources._CapacityPool('fixture DNS', 1, 0)
    monkeypatch.setattr(resources, '_DNS', pool)
    lease = await pool.acquire()
    try:
        with pytest.raises(AcquisitionError, match='queue is full'):
            await dns.validate_public_url('https://fixture.example/')
        assert calls == []
    finally:
        lease.release()


@pytest.mark.asyncio
async def test_dns_does_not_wait_for_an_unrelated_stalled_shared_executor(resolver):
    install, _, _, _ = resolver
    install(" return [(0,0,0,'',('93.184.216.34',0))]\n")
    loop = asyncio.get_running_loop()
    previous = loop._default_executor
    shared = ThreadPoolExecutor(max_workers=1)
    release, entered = threading.Event(), threading.Event()
    loop.set_default_executor(shared)
    def block():
        entered.set()
        release.wait(5)
    busy = shared.submit(block)
    try:
        assert entered.wait(1)
        assert await asyncio.wait_for(dns.validate_public_url('https://fixture.example/'), 2) == ('93.184.216.34',)
    finally:
        release.set()
        busy.result(timeout=2)
        shared.shutdown(wait=True)
        loop._default_executor = previous


@pytest.mark.asyncio
async def test_actual_fetch_dns_failure_refuses_http_browser_and_paid_fallback(resolver, monkeypatch):
    install, _, _, _ = resolver
    install(" return [(0,0,0,'',('93.184.216.34',0)),(0,0,0,'',('127.0.0.1',0))]\n")
    ru = importlib.import_module('kazma_core.tools.read_url')
    def forbidden(*args, **kwargs):
        pytest.fail('No network or paid fallback after mixed private DNS')
    monkeypatch.setattr(ru, '_fetch_via_optional_backends', forbidden)
    monkeypatch.setattr(ru, '_recover_hard_page', forbidden)
    result = await ru.fetch_result('https://mixed.fixture.example/')
    assert not result.ok and result.error_code == FetchErrorCode.SECURITY and not result.attempts


@pytest.mark.asyncio
async def test_research_seed_and_page_prevalidation_use_the_owned_resolver(resolver, monkeypatch):
    install, _, _, children = resolver
    install(" return [(0,0,0,'',('93.184.216.34',0))]\n")
    from kazma_core.tools import web_research
    from kazma_core.web_acquire.result import FetchResult

    async def fetch(url, **kwargs):
        return FetchResult(ok=True, url=url, final_url=url, text='Fixture article.', backend='httpx')
    monkeypatch.setattr(web_research, '_central_fetch', fetch)
    result = await web_research.crawl_site('https://owned.fixture.example/', max_pages=1,
        max_depth=0, delay_ms=0, save=False, respect_robots=False)
    assert 'ok=1' in result
    # The old to_thread(validate_url) shape never dispatches these resolver
    # children and instead leaks native DNS onto the shared executor.
    assert len(children) == 2
