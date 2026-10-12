"""Fixed search HTTP/parsing worker; no application config or ambient routes."""
from __future__ import annotations

import json
import os
import sys
import subprocess
from urllib.parse import urlsplit

_TIMEOUT = 30
_MEMORY = 512 * 1024**2
_BODY = 1024**2
_OUTPUT = 512 * 1024


def run_http_search(backend, query, count, *, base=None, local=False):
    from kazma_core.web_acquire.origins import origin_scheduler
    from kazma_core.web_acquire.search_sdk import search_cancel
    endpoint = (base.rstrip('/') + '/search') if backend == 'searxng' else 'https://www.bing.com/search' if backend == 'bing' else 'https://en.wikipedia.org/w/api.php'
    with origin_scheduler().request_sync(endpoint, cancel_event=search_cancel.get()):
        return _run_http_search(backend, query, count, base=base, local=local)


def _run_http_search(backend, query, count, *, base=None, local=False):
    from kazma_core.proxy.client import get_active_proxy_url
    from kazma_core.security.process_budget import OutputLimitExceeded, run_bounded
    from kazma_core.web_acquire.process_worker import current_process_owner
    from kazma_core.web_acquire.search_runtime import remaining
    from kazma_core.web_acquire.search_sdk import search_cancel
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

    proxy = None if local else get_active_proxy_url()
    owner = current_process_owner.get()
    if owner is not None:
        owner.before_dispatch()
    timeout = min(_TIMEOUT, remaining())
    from kazma_core.web_acquire.origins import origin_delay
    if origin_delay.get() > 30:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Search origin delay exceeds the bounded worker deadline; dispatch refused.')
    payload = json.dumps(dict(delay=origin_delay.get(), backend=backend, query=query, count=count, base=base, proxy=proxy,
        parent_pid=os.getpid(), lifetime=timeout)).encode('utf-8')
    if len(payload) > 32768:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Search worker input exceeds its 32KiB budget.')
    allowed = {'systemroot', 'windir', 'temp', 'tmp', 'path', 'ssl_cert_file', 'ssl_cert_dir'}
    env = {key: value for key, value in os.environ.items() if key.lower() in allowed}
    if proxy:
        from kazma_core.web_acquire import accounting

        # The fixed child performs at most six requests (five redirects).
        # Reserve that upper bound before the child can open a socket. Never
        # refund on failed launch/cancellation or infer actual proxy charges.
        accounting.reserve('proxy', accounting.account_fingerprint('proxy', proxy), units=6)
    try:
        result = run_bounded([sys.executable, '-I', os.path.abspath(__file__)], env=env,
            input=payload, timeout=timeout, memory_bytes=_MEMORY, output_bytes=_OUTPUT,
            text=True, cancel_event=search_cancel.get(), on_started=owner.attach if owner else None)
    except subprocess.TimeoutExpired:
        if proxy:
            raise AcquisitionError(FetchErrorCode.ROUTING, 'Search proxy worker timed out; direct fallback refused.', transient=True) from None
        return {'status': 'timeout', 'results': [], 'reached': False, 'detail': ''}
    except OutputLimitExceeded:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Search worker exceeded its output budget.') from None
    except OSError:
        raise AcquisitionError(FetchErrorCode.UNAVAILABLE, 'Bounded search worker could not run.') from None
    if owner is not None:
        owner.before_dispatch()  # no fallback over uncertain process cleanup
    if os.name == 'posix':
        import signal

        if result.returncode == -signal.SIGALRM:
            if proxy:
                raise AcquisitionError(FetchErrorCode.ROUTING, 'Search proxy reached its kernel deadline; direct fallback refused.', transient=True)
            return {'status': 'timeout', 'results': [], 'reached': False, 'detail': ''}
    try:
        data = json.loads(result.stdout)
        if result.returncode != 0 or not isinstance(data, dict):
            raise ValueError('Invalid search receipt')
        feedback = data.pop('origin_feedback', None)
        if feedback is not None:
            from kazma_core.web_acquire.origins import origin_scheduler, _origin
            endpoint = (base.rstrip('/') + '/search') if backend == 'searxng' else 'https://www.bing.com/search' if backend == 'bing' else 'https://en.wikipedia.org/w/api.php'
            if (not isinstance(feedback, dict) or type(feedback.get('status')) is not int
                    or _origin(feedback.get('url', '')) != _origin(endpoint)
                    or not isinstance(feedback.get('retry_after'), (str, type(None)))
                    or len(feedback.get('retry_after') or '') > 8192):
                raise ValueError('Invalid origin feedback')
            origin_scheduler().observe(endpoint, feedback['status'], {'retry-after': feedback['retry_after']})
            if feedback['status'] in (429, 503) or 300 <= feedback['status'] < 400 and feedback['retry_after'] is not None:
                raise AcquisitionError(FetchErrorCode.THROTTLED, 'Search origin requested a cooldown; no alternate backend dispatched.', transient=True)
        status = data.get('status')
        if status == 'limit':
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Search worker exceeded its memory or response budget.')
        if status == 'routing' or (proxy and status in {'error', 'timeout'}):
            raise AcquisitionError(FetchErrorCode.ROUTING, 'Selected search proxy failed; direct fallback refused.', transient=True)
        if set(data) != {'status', 'results', 'reached', 'detail'} or status not in {'ok', 'http', 'error', 'encoding', 'timeout'}:
            raise ValueError('Invalid search status')
        if type(data['reached']) is not bool or not isinstance(data['detail'], str) or len(data['detail']) > 1024:
            raise ValueError('Invalid search metadata')
        rows = data['results']
        if not isinstance(rows, list) or len(rows) > count:
            raise ValueError('Invalid search rows')
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'title', 'href', 'body'}:
                raise ValueError('Invalid search fields')
            if any(not isinstance(value, str) or len(value) > 8192 for value in row.values()):
                raise ValueError('Invalid search text')
    except (ValueError, TypeError):
        raise AcquisitionError(FetchErrorCode.DECODING, 'Search worker returned no valid complete receipt.') from None
    return data


def _bing(html):
    import base64
    import urllib.parse
    from html.parser import HTMLParser

    def decode_bing_url(b_url: str) -> str:
        if not b_url.startswith("http"):
            return b_url
        parsed_url = urllib.parse.urlparse(b_url)
        q_params = urllib.parse.parse_qs(parsed_url.query)
        u_param = q_params.get("u", [None])[0]
        if u_param:
            b64_str = u_param[2:] if u_param.startswith("a1") else u_param
            padding = "=" * (4 - len(b64_str) % 4)
            try:
                return base64.b64decode(b64_str + padding).decode(
                    "utf-8", errors="ignore"
                )
            except Exception:
                pass
        return b_url

    class BingHTMLParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.results: list[dict[str, str]] = []
            self.current_result: dict[str, str] | None = None
            self.in_algo = False
            self.in_h2 = False
            self.in_snippet = False
            self.in_a = False
            self.depth_algo = 0
            self.current_text: list[str] = []

        def handle_starttag(self, tag, attrs):
            attrs_dict = dict(attrs)
            class_val = attrs_dict.get("class", "")
            if self.in_algo:
                self.depth_algo += 1
            if tag == "li" and "b_algo" in class_val.split():
                self.in_algo = True
                self.depth_algo = 0
                self.current_result = {"title": "", "href": "", "body": ""}
                self.results.append(self.current_result)
            if self.in_algo:
                if tag == "h2":
                    self.in_h2 = True
                    self.current_text = []
                elif tag == "a" and self.in_h2:
                    self.in_a = True
                    if "href" in attrs_dict and self.current_result is not None:
                        self.current_result["href"] = attrs_dict["href"]
                elif tag in ("p", "div", "span") and any(
                    x in class_val for x in ("b_caption", "b_linelimit", "tab-content")
                ):
                    self.in_snippet = True
                    self.current_text = []

        def handle_data(self, data):
            if self.in_algo and (self.in_h2 or self.in_snippet):
                self.current_text.append(data)

        def handle_endtag(self, tag):
            if not self.in_algo:
                return
            if tag == "a" and self.in_a:
                self.in_a = False
            elif tag == "h2" and self.in_h2:
                self.in_h2 = False
                if self.current_result is not None:
                    self.current_result["title"] = "".join(self.current_text).strip()
                self.current_text = []
            elif tag in ("p", "div", "span") and self.in_snippet:
                self.in_snippet = False
                if self.current_result is not None:
                    existing = self.current_result["body"]
                    new_text = "".join(self.current_text).strip()
                    if new_text:
                        self.current_result["body"] = (
                            (existing + " " + new_text).strip() if existing else new_text
                        )
                self.current_text = []
            if tag == "li" and self.depth_algo == 0:
                self.in_algo = False
                self.current_result = None
            elif tag == "li":
                self.depth_algo -= 1

    parser = BingHTMLParser()
    parser.feed(html)
    processed: list[dict[str, str]] = []
    for res in parser.results:
        if res.get("title") or res.get("href"):
            processed.append(
                {
                    "title": res.get("title", "Untitled"),
                    "href": decode_bing_url(res.get("href", "")),
                    "body": res.get("body", ""),
                }
            )
    return processed


def _request(request):
    import httpx

    backend, query, count = request['backend'], request['query'], request['count']
    if backend == 'searxng':
        url = request['base'].rstrip('/') + '/search'
        params = dict(q=query, format='json', categories='general', language='all', pageno=1)
    elif backend == 'bing':
        url, params = 'https://www.bing.com/search', {'q': query}
    else:
        url = 'https://en.wikipedia.org/w/api.php'
        params = dict(action='opensearch', search=query, limit=min(count, 8), namespace=0, format='json')
    initial = urlsplit(url)
    if initial.scheme not in {'http', 'https'} or not initial.hostname or initial.username or initial.password or initial.query or initial.fragment:
        raise ValueError('Invalid search endpoint')
    headers = {'Accept-Encoding': 'identity', 'User-Agent': 'KazmaSearch/0.6', 'Accept': '*/*'}
    data = {'status': 'ok', 'results': [], 'reached': False, 'detail': ''}
    try:
        with httpx.Client(proxy=request['proxy'], trust_env=False, follow_redirects=False,
                timeout=8, headers=headers) as client:
            for hop in range(6):
                if hop and request.get('delay'):
                    import time
                    time.sleep(request['delay'])
                with client.stream('GET', url, params=params) as response:
                    data['reached'] = True
                    retry = response.headers.get('retry-after')
                    if retry is not None and len(retry) > 8192:
                        retry = '9' * 300  # unbounded valid feedback cannot shorten a cooldown
                    if response.status_code in (429, 503) or 300 <= response.status_code < 400 and retry is not None:
                        data.update(status='http', detail=str(response.status_code), origin_feedback=dict(url=str(response.url), status=response.status_code, retry_after=retry))
                        return data  # headers precede body/redirect dispatch
                    if response.status_code in {301, 302, 303, 307, 308}:
                        from urllib.parse import urljoin
                        destination = urljoin(str(response.url), response.headers.get('location', ''))
                        parsed = urlsplit(destination)
                        if (hop == 5 or (parsed.scheme, parsed.hostname, parsed.port) !=
                                (initial.scheme, initial.hostname, initial.port) or parsed.username or parsed.password):
                            raise ValueError('Search service redirect refused')
                        url, params = destination, None
                        continue
                    if response.status_code != 200:
                        data.update(status='http', detail=str(response.status_code))
                        return data
                    if response.headers.get('content-encoding', 'identity').strip().lower() not in {'', 'identity'}:
                        data['status'] = 'encoding'
                        return data
                    raw = bytearray()
                    for chunk in response.iter_raw(chunk_size=16384):
                        if len(raw) + len(chunk) > _BODY:
                            return {'status': 'limit'}
                        raw.extend(chunk)
                    break
        if backend == 'bing':
            rows = _bing(raw.decode('utf-8', errors='replace'))[:count]
        elif backend == 'searxng':
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get('results', []), list):
                raise ValueError('Invalid SearXNG shape')
            rows = [{'title': row.get('title', 'Untitled'), 'href': row.get('url', ''), 'body': row.get('content', '')}
                    for row in payload.get('results', [])[:count]]
            engines = payload.get('unresponsive_engines') or []
            if isinstance(engines, list):
                names = [str(row[0])[:128] for row in engines[:4] if isinstance(row, list) and row]
                if names:
                    data['detail'] = ' (engines suspended: ' + ', '.join(names) + ')'
        else:
            payload = json.loads(raw)
            if not isinstance(payload, list) or len(payload) < 4 or any(not isinstance(value, list) for value in payload[1:4]):
                raise ValueError('Invalid Wikipedia shape')
            titles, descriptions, urls = payload[1:4]
            rows = [{'title': title, 'href': urls[i] if i < len(urls) else '',
                'body': descriptions[i] if i < len(descriptions) else ''} for i, title in enumerate(titles[:count])]
        data['results'] = [{key: str(row[key])[:8192] for key in ('title', 'href', 'body')} for row in rows]
        return data
    except httpx.ProxyError:
        return {'status': 'routing'}
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        data['status'] = 'error'
        return data


def _worker():
    import runpy
    from pathlib import Path

    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    request = json.loads(sys.stdin.buffer.read(32769))
    if (request['backend'] not in {'searxng', 'bing', 'wikipedia'} or not isinstance(request['query'], str)
            or len(request['query']) > 4096 or type(request['count']) is not int or not 1 <= request['count'] <= 15
            or (request['base'] is not None and (not isinstance(request['base'], str) or len(request['base']) > 8192))
            or (request['proxy'] is not None and (not isinstance(request['proxy'], str) or len(request['proxy']) > 8192))
            or type(request['parent_pid']) is not int or request['parent_pid'] <= 0
            or type(request['lifetime']) not in (int, float) or not 0 < request['lifetime'] <= 30):
        raise ValueError('Invalid search input')
    import math
    delay = request.get('delay', 0)
    if not isinstance(delay, (int, float)) or not math.isfinite(delay) or not 0 <= delay <= 30:
        raise ValueError('Invalid search origin delay')
    if request['backend'] == 'searxng' and request['base'] is None:
        raise ValueError('Missing service endpoint')
    guard = Path(__file__).resolve().parents[1] / 'security' / 'worker_lifetime.py'
    runpy.run_path(str(guard), run_name='kazma_core.security.worker_lifetime')['arm_deadline'](request['parent_pid'], request['lifetime'])
    print(json.dumps(_request(request), ensure_ascii=True))


if __name__ == '__main__':
    try:
        _worker()
    except MemoryError:
        print('{"status":"limit"}')
    except (ValueError, TypeError, KeyError, OSError):
        print('{"status":"error","results":[],"reached":false,"detail":""}')
