"""Fixed local HTML parser in an owned resource-bounded child, never a callback."""
from __future__ import annotations

import json
import os
import subprocess
import sys

_TIMEOUT = 15
_MEMORY = 768 * 1024**2
_INPUT = 24 * 1024**2
# A page's 1M characters (document.PAGE_TEXT_MAX) at up to three UTF-8
# bytes each, plus 1 MiB of links.
_OUTPUT = 8 * 1024**2

if __name__ != '__main__':
    from kazma_core.web_acquire import resources
    from kazma_core.web_acquire.process_worker import OwnedWorkers

    _OWNERS = OwnedWorkers('parser', lambda: resources.parser_capacity())


async def parse_document(html, url, *, profile='generic', login_only=False):
    """Return text/link/login metadata without leaving parsing on server threads."""
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode
    from kazma_core.web_acquire.trace import active_trace

    if (not isinstance(html, str) or len(html) > 20 * 1024**2 or
            not isinstance(url, str) or len(url) > 8192 or profile not in {'generic', 'knowledge', 'discovery'}):
        raise AcquisitionError(FetchErrorCode.LIMIT, 'HTML parser input exceeds its document/URL budget.')
    profile = 'knowledge' if profile == 'knowledge' else 'generic'
    result = await _OWNERS.call(lambda owner: _parse(owner, html, url, profile, login_only))
    trace = active_trace.get()
    if trace is not None and not login_only:
        trace.extract_truncated, trace.dynamic_tabs = result['truncated'], result['dynamic_tabs']
    return result


async def parse_sitemap(text, url):
    """Parse untrusted XML within the same owned process and capacity budget."""
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

    if (not isinstance(text, str) or len(text) > 10 * 1024**2
            or not isinstance(url, str) or len(url) > 8192):
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Sitemap input exceeds its document/URL budget.')
    return await _OWNERS.call(lambda owner: _parse(owner, text, url, 'sitemap', False))


def _parse(owner, html, url, profile, login_only):
    from kazma_core.security.process_budget import OutputLimitExceeded, run_bounded
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

    try:
        payload = json.dumps({'html': html, 'url': url, 'profile': profile, 'login_only': login_only,
            'parent_pid': os.getpid(), 'lifetime': min(_TIMEOUT, 15)}, ensure_ascii=False).encode('utf-8')
    except UnicodeEncodeError:
        raise AcquisitionError(FetchErrorCode.DECODING, 'HTML parser input contains invalid Unicode.') from None
    if len(payload) > _INPUT:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'HTML parser input exceeds its 24MiB encoded budget.')
    if owner.cancelled.is_set():
        raise InterruptedError('HTML parsing cancelled before child dispatch')
    allowed = {'systemroot', 'windir', 'temp', 'tmp', 'path'}
    env = {key: value for key, value in os.environ.items() if key.lower() in allowed}
    try:
        result = run_bounded([sys.executable, '-I', os.path.abspath(__file__)], env=env,
            input=payload, timeout=_TIMEOUT, memory_bytes=_MEMORY, output_bytes=_OUTPUT,
            cancel_event=owner.cancelled, on_started=owner.attach, text=True)
    except subprocess.TimeoutExpired:
        raise AcquisitionError(FetchErrorCode.TIMEOUT, 'Local HTML parser exceeded its 15-second deadline.', transient=True) from None
    except OutputLimitExceeded:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Local HTML parser exceeded its output budget.') from None
    except OSError:
        raise AcquisitionError(FetchErrorCode.UNAVAILABLE, 'Bounded local HTML parser could not run; content was refused.') from None
    if os.name == 'posix':
        import signal

        if result.returncode == -signal.SIGALRM:
            raise AcquisitionError(FetchErrorCode.TIMEOUT, 'Local HTML parser reached its kernel deadline.', transient=True)
    return _decode(result, sitemap=profile == 'sitemap')


def _decode(result, *, sitemap=False):
    from kazma_core.web_acquire.document import PAGE_TEXT_MAX
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

    try:
        data = json.loads(result.stdout)
        if result.returncode != 0 or not isinstance(data, dict):
            raise ValueError('Invalid parser receipt')
        if data.get('status') == 'limit':
            what = 'sitemap' if sitemap else 'HTML'
            raise AcquisitionError(FetchErrorCode.LIMIT, f'Local {what} parser exceeded its memory/document budget.')
        if sitemap:
            if (set(data) != {'status', 'kind', 'urls', 'limited'} or data['status'] != 'ok'
                    or type(data['limited']) is not bool
                    or data['kind'] not in {'urlset', 'sitemapindex', 'invalid'}
                    or not isinstance(data['urls'], list) or len(data['urls']) > 4000
                    or any(not isinstance(url, str) or len(url) > 8192 for url in data['urls'])
                    or data['kind'] == 'sitemapindex' and len(data['urls']) > 64):
                raise ValueError('Invalid sitemap output')
            return data
        keys = {'status', 'text', 'truncated', 'dynamic_tabs', 'links', 'links_complete', 'login'}
        if set(data) != keys or data['status'] != 'ok' or not isinstance(data['text'], str) or len(data['text']) > PAGE_TEXT_MAX:
            raise ValueError('Invalid parser output')
        if any(type(data[key]) is not bool for key in ('truncated', 'dynamic_tabs', 'links_complete', 'login')):
            raise ValueError('Invalid parser metadata')
        if not isinstance(data['links'], list) or len(data['links']) > 2000:
            raise ValueError('Invalid parser links')
        if any(not isinstance(url, str) or len(url) > 8192 for url in data['links']):
            raise ValueError('Invalid parser URL')
    except (ValueError, TypeError):
        raise AcquisitionError(FetchErrorCode.DECODING, 'Local HTML parser returned no complete, valid receipt.') from None
    data['links'] = tuple(data['links'])
    return data


def _load(name):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location('_kazma_parser_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sitemap(text):
    import runpy
    from pathlib import Path
    from xml.etree.ElementTree import ParseError

    safe = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'security' / 'safe_xml.py'),
        run_name='kazma_core.security.safe_xml')
    # A sitemap past the bounds keeps its first URLs and says it was cut
    # (``limited``). It answered "limit", which failed the whole crawl: live
    # 2026-10-31, rfc-editor.org's sitemap-1.xml holds 6,000 URLs.
    result = {'status': 'ok', 'kind': 'invalid', 'urls': [], 'limited': False}
    try:
        root = safe['parse_untrusted_xml'](text)
    except (ParseError, safe['UnsafeXMLError']):
        return result
    kind = root.tag.split('}')[-1].lower()
    if kind not in {'urlset', 'sitemapindex'}:
        return result
    result['kind'] = kind
    entry_tag, limit = ('url', 4000) if kind == 'urlset' else ('sitemap', 64)
    used = 0
    for entry in root:
        if entry.tag.split('}')[-1].lower() != entry_tag:
            continue
        for loc in entry:
            if loc.tag.split('}')[-1].lower() != 'loc' or not loc.text:
                continue
            url = loc.text.strip()
            if len(url) > 8192:
                result['limited'] = True
                continue
            used += len(url.encode('utf-8'))
            if len(result['urls']) >= limit or used > 1024**2:
                result['limited'] = True
                return result
            result['urls'].append(url)
    return result


def _worker():
    import contextlib
    import runpy
    from pathlib import Path

    # Isolated Windows interpreters inherit the ANSI pipe encoding. The wire
    # contract is UTF-8, including Arabic documents and diagnostic output.
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    request = json.loads(sys.stdin.buffer.read(_INPUT + 1))
    html, url, profile = request['html'], request['url'], request['profile']
    if (not isinstance(html, str) or len(html) > 20 * 1024**2 or not isinstance(url, str) or len(url) > 8192
            or profile not in {'generic', 'knowledge', 'sitemap'} or type(request['parent_pid']) is not int
            or request['parent_pid'] <= 0 or type(request['lifetime']) not in (int, float) or not 0 < request['lifetime'] <= 15
            or type(request['login_only']) is not bool):
        raise ValueError('Invalid parser request')
    guard = Path(__file__).resolve().parents[1] / 'security' / 'worker_lifetime.py'
    runpy.run_path(str(guard), run_name='kazma_core.security.worker_lifetime')['arm_deadline'](request['parent_pid'], request['lifetime'])
    if profile == 'sitemap':
        print(json.dumps(_sitemap(html), ensure_ascii=False))
        return
    data = {'status': 'ok', 'text': '', 'truncated': False, 'dynamic_tabs': False,
        'links': [], 'links_complete': True, 'login': _load('quality').is_login_document(html)}
    if not request['login_only'] and not data['login']:
        with contextlib.redirect_stdout(sys.stderr):
            reader = _load('document')
            if profile == 'knowledge':
                document = reader.extract_knowledge_document(html, reader.PAGE_TEXT_MAX)
                data.update(text=document.text, truncated=document.truncated, dynamic_tabs=document.dynamic_tabs)
            else:
                article, truncated = _load('html_extract').extract_article(html, reader.PAGE_TEXT_MAX)
                data['text'], data['truncated'] = reader.article_or_whole(article, truncated, html)
        links, data['links_complete'] = _load('links').extract_document_links(html, url)
        used = 0
        for link in links:
            used += len(link.encode('utf-8'))
            if len(link) > 8192 or used > 1024**2:
                data['links_complete'] = False
                break
            data['links'].append(link)
    print(json.dumps(data, ensure_ascii=False))


if __name__ == '__main__':
    try:
        _worker()
    except MemoryError:
        print('{"status":"limit"}')
    except (ValueError, TypeError, KeyError, OSError):
        print('{"status":"error"}')
