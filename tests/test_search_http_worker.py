"""Actual isolated search entries under hostile HTTP, DNS and parser behavior."""
import asyncio
import importlib
import json
import sys
import threading
import time
from unittest.mock import Mock

import pytest

from kazma_core.security import process_budget
from kazma_core.web_acquire import resources, search_http, search_runtime
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


@pytest.fixture
def child_http(tmp_path, monkeypatch):
    module, marker = tmp_path / 'network.py', tmp_path / 'ready.json'
    original = process_budget.run_bounded
    calls, children = [], []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        attach = kwargs.get('on_started')
        def started(proc, tree, threads):
            children.append((proc, tree, threads))
            if attach:
                attach(proc, tree, threads)
        kwargs['on_started'] = started
        program = f'import runpy;runpy.run_path({str(module)!r});runpy.run_path({search_http.__file__!r},run_name="__main__")'
        return original([sys.executable, '-I', '-c', program], **kwargs)
    monkeypatch.setattr(process_budget, 'run_bounded', run)
    monkeypatch.setattr('kazma_core.proxy.client.get_active_proxy_url', lambda: None)
    def install(body):
        module.write_text('import httpx,os,json,time,socket\n'
            'original=httpx.Client\n'
            'def client(**kwargs):\n'
            f' with open({str(marker)+".pending"!r},"w") as f: json.dump({{"pid":os.getpid(),"env":dict(os.environ),"proxy":kwargs.get("proxy")}},f)\n'
            f' os.replace({str(marker)+".pending"!r},{str(marker)!r})\n'
            ' def handler(request):\n' + body + '\n'
            ' def raw(request):\n'
            '  response=handler(request)\n'
            '  if response.is_stream_consumed: return httpx.Response(response.status_code,headers=response.headers,stream=httpx.ByteStream(response.content))\n'
            '  return response\n'
            ' return original(**kwargs,transport=httpx.MockTransport(raw))\n'
            'httpx.Client=client\n', encoding='utf-8')
    yield install, marker, calls, children, module
    assert search_runtime._OWNERS.drain(timeout=3)
    for proc, tree, threads in children:
        assert tree.closed and proc.returncode is not None
        assert not any(thread.is_alive() for thread in threads)
        assert all(stream.closed for stream in (proc.stdin, proc.stdout, proc.stderr))


@pytest.mark.parametrize('backend,body', [
    ('searxng', '  return httpx.Response(200,json={"results":[{"title":"دليل","url":"https://docs.example/a?q=1","content":"snippet"}]})'),
    ('bing', '  return httpx.Response(200,text=\'<li class="b_algo"><h2><a href="https://docs.example/a?q=1">دليل</a></h2><div class="b_caption">snippet</div></li>\')'),
    ('wikipedia', '  return httpx.Response(200,json=["q",["دليل"],["snippet"],["https://docs.example/a?q=1"]])'),
])
def test_actual_child_extracts_each_backend_without_ambient_secrets(child_http, monkeypatch, backend, body):
    install, marker, calls, _, _ = child_http
    install(body)
    for key in ('HTTP_PROXY', 'OPENAI_API_KEY', 'NODE_OPTIONS', 'PYTHONPATH'):
        monkeypatch.setenv(key, 'fixture-private')
    result = search_http.run_http_search(backend, 'private query', 2, base='http://localhost:8088')
    assert result['results'] == [{'title': 'دليل', 'href': 'https://docs.example/a?q=1', 'body': 'snippet'}]
    assert 'fixture-private' not in json.loads(marker.read_text())['env'].values()
    command, options = calls[0]
    assert 'private query' not in repr(command) and command[1] == '-I'
    assert options['memory_bytes'] == 512 * 1024**2 and options['output_bytes'] == 512 * 1024


@pytest.mark.parametrize('body,status', [
    ('  return httpx.Response(429)', 'http'),
    ('  return httpx.Response(200,headers={"content-encoding":"gzip"},stream=httpx.ByteStream(b"invalid"))', 'encoding'),
    ('  return httpx.Response(200,json={"results":[]})', 'ok'),
    ('  return httpx.Response(200,content=b"not json")', 'error'),
    ('  return httpx.Response(302,headers={"location":"https://foreign.example/"})', 'error'),
])
def test_empty_status_and_redirect_refusals_are_explicit(child_http, body, status):
    child_http[0](body)
    if '429' in body:
        with pytest.raises(AcquisitionError) as failure:
            search_http.run_http_search('searxng', 'fixture', 2, base='http://localhost:8088', local=True)
        assert failure.value.code == FetchErrorCode.THROTTLED
        return
    result = search_http.run_http_search('searxng', 'fixture', 2, base='http://localhost:8088', local=True)
    assert result['status'] == status and result['reached'] and not result['results']


def test_wire_body_excess_is_not_accepted_as_empty_or_partial(child_http):
    child_http[0]('  return httpx.Response(200,content=b"x"*(1024**2+1))')
    with pytest.raises(AcquisitionError) as failure:
        search_http.run_http_search('bing', 'fixture', 2)
    assert failure.value.code == FetchErrorCode.LIMIT


@pytest.mark.parametrize('proxy', [None, 'http://user:fixture-secret@proxy.example:8080'])
def test_native_dns_stall_is_stopped_by_worker_deadline(child_http, monkeypatch, proxy):
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'proxy')
    install, marker, _, _, module = child_http
    install('  raise AssertionError("Native transport required")')
    with module.open('a', encoding='utf-8') as stream:
        stream.write('httpx.Client=original\nsocket.getaddrinfo=lambda *a,**k:time.sleep(30)\n')
    monkeypatch.setattr('kazma_core.proxy.client.get_active_proxy_url', lambda: proxy)
    monkeypatch.setattr(search_http, '_TIMEOUT', .8)
    if proxy:
        with pytest.raises(AcquisitionError) as failure:
            search_http.run_http_search('bing', 'fixture', 2)
        assert failure.value.code == FetchErrorCode.ROUTING and 'fixture-secret' not in str(failure.value)
    else:
        assert search_http.run_http_search('bing', 'fixture', 2)['status'] == 'timeout'


@pytest.mark.asyncio
async def test_repeated_cancel_retains_real_http_worker_until_cleanup(child_http, monkeypatch):
    install, marker, _, children, _ = child_http
    install('  time.sleep(30)\n  return httpx.Response(200,json={"results":[]})')
    ws = importlib.import_module('kazma_core.tools.web_search')
    monkeypatch.setattr(ws, '_searxng_candidate_bases', lambda: ['http://localhost:8088'])
    monkeypatch.setattr(ws, '_searxng_cache', dict(base=None, dead_until=0, note=''))
    fallback = Mock(side_effect=AssertionError('No backend after cancellation'))
    monkeypatch.setattr(ws, '_ddg_search', fallback)
    pool = resources._CapacityPool('fixture search', 1, 0)
    monkeypatch.setattr(resources, '_ACQUISITIONS', pool)
    original = process_budget._ProcessTree.close
    entered, release = threading.Event(), threading.Event()
    def held(tree):
        if any(tree is candidate for _, candidate, _ in children) and not tree.closed:
            entered.set()
            assert release.wait(5)
        return original(tree)
    monkeypatch.setattr(process_budget._ProcessTree, 'close', held)
    task = asyncio.create_task(ws.web_search('fixture cancellation'))
    try:
        async with asyncio.timeout(10):
            while not marker.exists():
                await asyncio.sleep(.01)
            task.cancel()
            while not entered.is_set():
                await asyncio.sleep(.01)
            task.cancel()
            assert not task.done() and pool.snapshot()['active'] == 1
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert pool.snapshot()['active'] == 0
        fallback.assert_not_called()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_uncertain_http_cleanup_quarantines_and_blocks_fallback(child_http, monkeypatch):
    install, _, _, children, _ = child_http
    install('  return httpx.Response(200,json={"results":[]})')
    ws = importlib.import_module('kazma_core.tools.web_search')
    monkeypatch.setattr(ws, '_searxng_candidate_bases', lambda: ['http://localhost:8088'])
    monkeypatch.setattr(ws, '_searxng_cache', dict(base=None, dead_until=0, note=''))
    pool = resources._CapacityPool('fixture search', 1, 0)
    monkeypatch.setattr(resources, '_ACQUISITIONS', pool)
    fallback = Mock(side_effect=AssertionError('No fallback with uncertain cleanup'))
    monkeypatch.setattr(ws, '_ddg_search', fallback)
    original, fail = process_budget._ProcessTree.close, threading.Event()
    fail.set()
    def uncertain(tree):
        if fail.is_set() and any(tree is candidate for _, candidate, _ in children):
            raise OSError('Fixture cleanup unavailable')
        return original(tree)
    monkeypatch.setattr(process_budget._ProcessTree, 'close', uncertain)
    try:
        assert (await ws.web_search('fixture')).startswith('Error: resource_limit')
        assert pool.snapshot()['quarantined'] == 1
        fallback.assert_not_called()
        readiness = importlib.import_module('kazma_core.tools.research_readiness')
        report = readiness.research_readiness()
        assert not next(check['ok'] for check in report['checks'] if check['id'] == 'acquisition_search')
    finally:
        fail.clear()
        search_runtime._OWNERS._request_reap()
        assert await asyncio.to_thread(search_runtime._OWNERS.drain, 5)
    assert pool.snapshot()['active'] == 0
