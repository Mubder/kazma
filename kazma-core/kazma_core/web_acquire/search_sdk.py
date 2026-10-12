"""Own the optional DuckDuckGo SDK in a resource-bounded, clean-env process.

The SDK reads DDGS_PROXY even when given an explicit proxy. Never run it in
the server process or modify the server's environment to work around that.
This file also serves as the isolated child entry point (standard library
imports only until the optional SDK is loaded).
"""
from __future__ import annotations

import json
import os
import sys
import threading
from contextvars import ContextVar

search_cancel: ContextVar[threading.Event | None] = ContextVar('search_cancel', default=None)


def _run_ddg(query: str, max_results: int, proxy: str | None) -> tuple[list[dict] | None, str]:
    """Launch one SDK call without ambient proxies, account keys or user imports."""
    import subprocess

    from kazma_core.security.process_budget import OutputLimitExceeded, run_bounded
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode
    from kazma_core.web_acquire.process_worker import current_process_owner
    from kazma_core.web_acquire.search_runtime import remaining

    if proxy:
        raise AcquisitionError(FetchErrorCode.POLICY,
            'Proxy SDK search is refused because its internal request count cannot be bounded. Use the metered HTTP search backends.')

    owner = current_process_owner.get()
    if owner is not None:
        owner.before_dispatch()
    timeout = remaining()
    # A whitelist also keeps unrelated provider credentials out of the child.
    allowed = {'systemroot', 'windir', 'temp', 'tmp', 'path', 'ssl_cert_file', 'ssl_cert_dir'}
    env = {key: value for key, value in os.environ.items() if key.lower() in allowed}
    payload = json.dumps({'query': query, 'max_results': max_results, 'proxy': proxy,
        'parent_pid': os.getpid(), 'lifetime': timeout})
    if len(payload.encode('utf-8')) > 32768:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'DuckDuckGo worker input exceeds its 32KiB budget.')
    try:
        result = run_bounded(
            [sys.executable, '-I', os.path.abspath(__file__)], env=env,
            input=payload,
            timeout=timeout, output_bytes=256 * 1024, text=True, cancel_event=search_cancel.get(),
            on_started=owner.attach if owner else None,
        )
    except InterruptedError:
        raise
    except subprocess.TimeoutExpired:
        if proxy:
            raise AcquisitionError(FetchErrorCode.ROUTING,
                'DuckDuckGo proxy request exceeded its worker deadline; direct fallback was refused.',
                transient=True) from None
        return None, 'duckduckgo:deadline (30s worker limit)'
    except (OSError, OutputLimitExceeded):
        if proxy:
            raise AcquisitionError(FetchErrorCode.ROUTING,
                'DuckDuckGo proxy worker failed; verify the selected route. Direct fallback was refused.') from None
        return None, 'duckduckgo:worker_failed'
    if owner is not None:
        owner.before_dispatch()
    if os.name == 'posix':
        import signal

        if result.returncode == -signal.SIGALRM:
            if proxy:
                raise AcquisitionError(FetchErrorCode.ROUTING, 'DuckDuckGo proxy reached its kernel deadline; direct fallback refused.', transient=True)
            return None, 'duckduckgo:deadline (kernel worker limit)'
    try:
        data = json.loads(result.stdout)
        if result.returncode != 0 or not isinstance(data, dict):
            raise ValueError('Invalid worker receipt')
        status, rows = data.get('status'), data.get('results')
        if status == 'unavailable':
            return None, 'duckduckgo:unavailable (optional SDK missing)'
        if status != 'ok' or not isinstance(rows, list) or len(rows) > min(15, max_results):
            raise ValueError('Invalid worker receipt')
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'title', 'href', 'body'}:
                raise ValueError('Invalid worker result')
            if any(not isinstance(value, str) or len(value) > 8192 for value in row.values()):
                raise ValueError('Invalid worker field')
    except (ValueError, TypeError):
        if proxy:
            raise AcquisitionError(FetchErrorCode.ROUTING,
                'DuckDuckGo proxy search failed; verify the selected route. Direct fallback was refused.',
                transient=True) from None
        return None, 'duckduckgo:worker_error'
    return (rows, 'duckduckgo:ok') if rows else (None, 'duckduckgo:empty')


def _worker() -> None:
    import contextlib
    import itertools
    import runpy
    from pathlib import Path

    # Refuse a mislaunched child before importing any network library.
    forbidden = {'ddgs_proxy', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}
    if any(key.lower() in forbidden for key in os.environ):
        print('{"status":"error"}')
        return
    request = json.loads(sys.stdin.read(32769))
    query, count, proxy = request['query'], request['max_results'], request['proxy']
    if not isinstance(query, str) or len(query) > 4096 or type(count) is not int or not 1 <= count <= 15:
        raise ValueError('Invalid worker input')
    if proxy is not None and not isinstance(proxy, str):
        raise ValueError('Invalid worker route')
    if (type(request['parent_pid']) is not int or request['parent_pid'] <= 0
            or type(request['lifetime']) not in (int, float) or not 0 < request['lifetime'] <= 30):
        raise ValueError('Invalid worker lifetime')
    guard = Path(__file__).resolve().parents[1] / 'security' / 'worker_lifetime.py'
    runpy.run_path(str(guard), run_name='kazma_core.security.worker_lifetime')['arm_deadline'](request['parent_pid'], request['lifetime'])
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        print('{"status":"unavailable"}')
        return
    try:
        with contextlib.redirect_stdout(sys.stderr):
            with DDGS(proxy=proxy, timeout=10) as client:
                rows = [{key: str(row.get(key, ''))[:8192] for key in ('title', 'href', 'body')}
                        for row in itertools.islice(client.text(query, max_results=count), count)]
    except Exception:
        # Provider exceptions can contain query text and proxy credentials.
        print('{"status":"error"}')
        return
    print(json.dumps({'status': 'ok', 'results': rows}, ensure_ascii=True))


if __name__ == '__main__':
    _worker()
