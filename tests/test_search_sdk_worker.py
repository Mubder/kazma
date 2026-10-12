"""Real isolated SDK workers, with a controlled no-network SDK implementation."""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from unittest.mock import Mock

import pytest
import psutil

from kazma_core.security import process_budget
from kazma_core.web_acquire import search_sdk as sdk
from kazma_core.web_acquire.result import AcquisitionError


@pytest.fixture
def child_sdk(tmp_path, monkeypatch):
    """Run the exact product entry point and limits with a no-network SDK."""
    marker = tmp_path / 'worker-route.json'
    module = tmp_path / 'duckduckgo_search.py'
    original = process_budget.run_bounded
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        program = f'import sys,runpy;sys.path.insert(0,{str(tmp_path)!r});runpy.run_path({sdk.__file__!r},run_name="__main__")'
        return original([sys.executable, '-I', '-c', program], **kwargs)
    monkeypatch.setattr(process_budget, 'run_bounded', run)

    def install(body):
        module.write_text('import os,json,time\nclass DDGS:\n'
            ' def __init__(self, proxy=None, timeout=None):\n'
            f'  with open({str(marker)+".pending"!r},"w") as receipt: receipt.write(json.dumps({{"proxy":proxy,"env":dict(os.environ),"pid":os.getpid()}}))\n'
            f'  os.replace({str(marker)+".pending"!r},{str(marker)!r})\n'
            ' def __enter__(self): return self\n'
            ' def __exit__(self,*args): pass\n'
            ' def text(self,*args,**kwargs):\n' + body, encoding='utf-8')
    return install, marker, calls


def test_worker_uses_direct_route_without_ambient_proxy_or_account_keys(monkeypatch, child_sdk):
    proxy = None
    install, marker, calls = child_sdk
    for key in ('DDGS_PROXY', 'HTTP_PROXY', 'https_proxy', 'ALL_PROXY', 'NO_PROXY', 'OPENAI_API_KEY'):
        monkeypatch.setenv(key, 'must-never-reach-sdk')
    install('  return [{"title":"Result","href":"https://example.com","body":"text"}]\n')
    rows, note = sdk._run_ddg('fixture query', 2, proxy)
    receipt = json.loads(marker.read_text())
    assert rows[0]['title'] == 'Result' and note == 'duckduckgo:ok'
    assert receipt['proxy'] == proxy
    assert not any('must-never-reach-sdk' == value for value in receipt['env'].values())
    argv, options = calls[0]
    assert argv[1] == '-I' and (proxy is None or proxy not in repr(argv))
    assert options['timeout'] == 30 and options['output_bytes'] == 256 * 1024


def test_worker_bounds_fields_and_result_count(child_sdk):
    install, _, _ = child_sdk
    install('  return ({"title":"x"*10000,"href":"https://example.com","body":"body"} for _ in range(1000))\n')
    rows, _ = sdk._run_ddg('fixture', 2, None)
    assert len(rows) == 2 and len(rows[0]['title']) == 8192


def test_worker_error_never_leaks_proxy_credentials_or_changes_route(child_sdk):
    install, _, _ = child_sdk
    install('  raise RuntimeError("fixture-secret")\n')
    with pytest.raises(AcquisitionError) as failure:
        sdk._run_ddg('fixture', 1, 'http://user:fixture-secret@proxy.example:1080')
    assert failure.value.code == 'policy_error'
    assert 'fixture-secret' not in str(failure.value)
    assert sdk._run_ddg('fixture', 1, None) == (None, 'duckduckgo:worker_error')


def test_deadline_and_excess_output_kill_worker(child_sdk, monkeypatch):
    install, marker, _ = child_sdk
    original = process_budget.run_bounded
    # The fixture wrapper still launches the real bounded process.
    def shortened(command, **kwargs):
        kwargs['timeout'] = 0.5
        return original(command, **kwargs)
    monkeypatch.setattr(process_budget, 'run_bounded', shortened)
    install('  time.sleep(60)\n')
    assert sdk._run_ddg('fixture', 1, None)[1].startswith('duckduckgo:deadline')
    assert marker.exists()
    install('  print("x"*300000); return []\n')
    assert sdk._run_ddg('fixture', 1, None) == (None, 'duckduckgo:worker_failed')


@pytest.mark.asyncio
async def test_cancel_joins_real_worker_and_prevents_next_backend(child_sdk, monkeypatch):
    install, marker, calls = child_sdk
    install('  time.sleep(60)\n')
    ws = importlib.import_module('kazma_core.tools.web_search')
    monkeypatch.setattr('kazma_core.proxy.client.get_active_proxy_url', lambda: None)
    monkeypatch.setattr(ws, '_searxng_search', lambda *args: (None, 'fixture:off'))
    # Explicit legacy-worker fixture; production skips opaque SDK fanout.
    monkeypatch.setattr(ws, '_ddg_search', lambda query, count: sdk._run_ddg(query, count, None))
    fallback = Mock(side_effect=AssertionError('No backend after cancellation'))
    monkeypatch.setattr(ws, '_bing_search', fallback)
    task = asyncio.create_task(ws.web_search('fixture cancellation'))
    async with asyncio.timeout(10):
        while not marker.exists():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls[0][1]['cancel_event'].is_set()
    fallback.assert_not_called()
    # Process-tree cleanup was joined before web_search acknowledged cancellation.
    pid = json.loads(marker.read_text())['pid']
    try:
        psutil.Process(pid).wait(timeout=3)
    except psutil.NoSuchProcess:
        pass
    assert not psutil.pid_exists(pid)



@pytest.mark.asyncio
async def test_saturated_search_refuses_before_any_backend(monkeypatch):
    from kazma_core.web_acquire import resources

    ws = importlib.import_module('kazma_core.tools.web_search')
    pool = resources._CapacityPool('fixture searches', 1, 0)
    monkeypatch.setattr(resources, '_ACQUISITIONS', pool)
    backend = Mock(side_effect=AssertionError('No dispatch when saturated'))
    monkeypatch.setattr(ws, '_run_search', backend)
    lease = await pool.acquire()
    try:
        assert (await ws.web_search('fixture saturation')).startswith('Error: resource_limit')
        backend.assert_not_called()
    finally:
        lease.release()
