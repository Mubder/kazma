"""Real bounded parser children, cancellation ownership and hostile documents."""
import asyncio
import importlib
import json
import sys
import threading
import time

import pytest

from kazma_core.security import process_budget
from kazma_core.web_acquire import parser, resources
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


@pytest.fixture
def child_parser(tmp_path, monkeypatch):
    module = tmp_path / 'parser_fixture.py'
    marker = tmp_path / 'ready.json'
    original = process_budget.run_bounded
    children, calls = [], []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        attach = kwargs['on_started']
        def started(proc, tree, threads):
            children.append((proc, tree, threads))
            attach(proc, tree, threads)
        kwargs['on_started'] = started
        program = ('import importlib.util,runpy\noriginal=importlib.util.spec_from_file_location\n'
            'def fixture(name,location,*args,**kwargs):\n'
            f' return original(name,{str(module)!r} if name=="_kazma_parser_html_extract" else location,*args,**kwargs)\n'
            'importlib.util.spec_from_file_location=fixture\n'
            'run_original=runpy.run_path\n'
            'def xml_fixture(path,*args,**kwargs):\n'
            f' return run_original({str(module)!r} if str(path).replace(chr(92),"/").endswith("/safe_xml.py") else path,*args,**kwargs)\n'
            'runpy.run_path=xml_fixture\n'
            f'runpy.run_path({parser.__file__!r},run_name="__main__")')
        return original([sys.executable, '-I', '-c', program], **kwargs)
    monkeypatch.setattr(process_budget, 'run_bounded', run)
    def install(body, *, xml=False):
        module.write_text('import os,json,time\n'
            f'with open({str(marker)+".pending"!r},"w") as f: json.dump({{"pid":os.getpid(),"env":dict(os.environ)}},f)\n'
            f'os.replace({str(marker)+".pending"!r},{str(marker)!r})\n'
            'class UnsafeXMLError(ValueError): pass\n'
            + ('def parse_untrusted_xml(html):\n' if xml else 'def extract_article(html,limit=100000):\n')
            + body, encoding='utf-8')
    yield install, marker, calls, children
    assert parser._OWNERS.drain(timeout=3)
    for proc, tree, threads in children:
        assert tree.closed and proc.returncode is not None
        assert not any(thread.is_alive() for thread in threads)


@pytest.mark.asyncio
async def test_real_knowledge_parser_keeps_arabic_code_links_and_dynamic_metadata():
    html = '<h1>دليل</h1><button role="tab">API</button><script>secret()</script>'
    html += '<div hidden><pre>  x = "```"\n\tprint(x)</pre><a href="../b?q=1">Panel</a></div>'
    result = await parser.parse_document(html, 'https://docs.example/a/page', profile='knowledge')
    assert 'دليل' in result['text'] and '  x = "```"\n\tprint(x)' in result['text']
    assert 'secret()' not in result['text'] and result['dynamic_tabs']
    assert result['links'] == ('https://docs.example/b?q=1',) and result['links_complete']


@pytest.mark.asyncio
async def test_sitemap_xml_runs_in_owned_child_and_preserves_literal_urls(monkeypatch):
    calls, children = [], []
    actual = process_budget.run_bounded
    def bounded(command, **kwargs):
        attach = kwargs['on_started']
        def started(proc, tree, threads):
            children.append((proc, tree, threads))
            attach(proc, tree, threads)
        kwargs['on_started'] = started
        calls.append(kwargs)
        return actual(command, **kwargs)
    monkeypatch.setattr(process_budget, 'run_bounded', bounded)
    literal = 'https://docs.example/guide.md;a=1?x=1&amp;x=2&amp;blank='
    result = await parser.parse_sitemap('<urlset><url><loc>' + literal + '</loc></url></urlset>', 'https://docs.example/')
    assert result == {'status': 'ok', 'kind': 'urlset', 'urls': [literal.replace('&amp;', '&')], 'limited': False}
    assert len(children) == len(calls) == 1
    assert calls[0]['memory_bytes'] == 768 * 1024**2 and calls[0]['timeout'] == 15
    assert children[0][0].poll() is not None and children[0][1].closed
    assert not any(thread.is_alive() for thread in children[0][2])


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['<broken', '<!DOCTYPE urlset [<!ENTITY x "bad">]><urlset/>', '<other><loc>bad</loc></other>'])
async def test_sitemap_invalid_and_entity_documents_are_refused(text):
    assert await parser.parse_sitemap(text, 'https://docs.example/') == {'status': 'ok', 'kind': 'invalid', 'urls': [], 'limited': False}


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,entry,count,kept', [('urlset','url',6000,4000), ('sitemapindex','sitemap',65,64)])
async def test_sitemap_frontier_excess_is_kept_bounded_and_said(kind, entry, count, kept):
    """An oversized sitemap is cut at the bound and says so -- never silent,
    and never a failed crawl: rfc-editor.org's sitemap-1.xml holds 6,000 URLs,
    and the old "limit" answer failed every crawl of that site (2026-10-31)."""
    text = '<' + kind + '>' + ''.join(f'<{entry}><loc>https://docs.example/{i}</loc></{entry}>' for i in range(count)) + '</' + kind + '>'
    result = await parser.parse_sitemap(text, 'https://docs.example/')
    assert result['limited'] is True and result['kind'] == kind
    assert result['urls'] == [f'https://docs.example/{i}' for i in range(kept)]


@pytest.mark.asyncio
async def test_a_sitemap_at_its_bound_is_not_cut():
    text = '<urlset>' + ''.join(f'<url><loc>https://docs.example/{i}</loc></url>' for i in range(4000)) + '</urlset>'
    result = await parser.parse_sitemap(text, 'https://docs.example/')
    assert result['limited'] is False and len(result['urls']) == 4000


@pytest.mark.asyncio
async def test_login_is_refused_without_running_article_extractor(child_parser):
    install, marker, _, _ = child_parser
    install(' raise AssertionError("Login content must not be extracted")\n')
    result = await parser.parse_document('<title>Sign in</title><input type="password">', 'https://docs.example/')
    assert result['login'] and not result['text'] and not marker.exists()


@pytest.mark.asyncio
async def test_parser_child_has_no_credentials_and_uses_fixed_limits(child_parser, monkeypatch):
    install, marker, calls, _ = child_parser
    for key in ('OPENAI_API_KEY', 'HTTP_PROXY', 'NODE_OPTIONS', 'PYTHONPATH'):
        monkeypatch.setenv(key, 'private-fixture-secret')
    install(' return html,False\n')
    result = await parser.parse_document('Some private document', 'https://docs.example/?token=private')
    assert result['text'] == 'Some private document'
    assert 'private-fixture-secret' not in json.loads(marker.read_text())['env'].values()
    command, options = calls[0]
    assert command[1] == '-I' and 'private' not in repr(command)
    assert options['timeout'] == 15 and options['memory_bytes'] == 768 * 1024**2
    assert options['output_bytes'] == 8 * 1024**2


@pytest.mark.asyncio
@pytest.mark.parametrize('body,code', [
    (' time.sleep(30)\n', FetchErrorCode.TIMEOUT),
    (' return "x"*(9*1024**2),False\n', FetchErrorCode.LIMIT),
    (' data=bytearray(128*1024**2); return "bad",False\n', FetchErrorCode.LIMIT),
    (' return 123,False\n', FetchErrorCode.DECODING),
], ids=['deadline','output','memory','malformed'])
async def test_native_parser_excess_is_a_typed_failure(child_parser, monkeypatch, body, code):
    install, _, _, _ = child_parser
    install(body)
    if code == FetchErrorCode.TIMEOUT:
        monkeypatch.setattr(parser, '_TIMEOUT', .5)
    if 'bytearray' in body:
        monkeypatch.setattr(parser, '_MEMORY', 64 * 1024**2)
    with pytest.raises(AcquisitionError) as failure:
        await parser.parse_document('Bounded input', 'https://docs.example/')
    assert failure.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize('body,code', [(' time.sleep(30)\n', FetchErrorCode.TIMEOUT),
    (' data=bytearray(128*1024**2)\n', FetchErrorCode.LIMIT)], ids=['xml-deadline', 'xml-memory'])
async def test_native_xml_parser_excess_terminates_owned_child(child_parser, monkeypatch, body, code):
    install, _, _, _ = child_parser
    install(body, xml=True)
    if code == FetchErrorCode.TIMEOUT:
        monkeypatch.setattr(parser, '_TIMEOUT', .5)
    else:
        monkeypatch.setattr(parser, '_MEMORY', 64 * 1024**2)
    with pytest.raises(AcquisitionError) as failure:
        await parser.parse_sitemap('<urlset/>', 'https://docs.example/')
    assert failure.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize('xml', [False, True], ids=['html', 'xml'])
async def test_repeated_cancel_waits_for_process_and_pipe_cleanup(child_parser, monkeypatch, xml):
    install, marker, _, children = child_parser
    install(' time.sleep(30)\n', xml=xml)
    pool = resources._CapacityPool('fixture parser', 1, 0)
    monkeypatch.setattr(resources, '_PARSERS', pool)
    entered, release = threading.Event(), threading.Event()
    original = process_budget._ProcessTree.close
    def held(tree):
        if any(tree is candidate for _, candidate, _ in children) and not tree.closed:
            entered.set()
            if not release.wait(4):
                raise TimeoutError('Cleanup fixture was not released')
        original(tree)
    monkeypatch.setattr(process_budget._ProcessTree, 'close', held)
    errors = []
    monkeypatch.setattr(asyncio.get_running_loop(), 'call_exception_handler', errors.append)
    parse = parser.parse_sitemap if xml else parser.parse_document
    task = asyncio.create_task(parse('Document', 'https://docs.example/'))
    try:
        deadline = time.monotonic() + 3
        while not marker.exists():
            assert not task.done() and time.monotonic() < deadline
            await asyncio.sleep(.01)
        task.cancel()
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        assert not task.done() and pool.snapshot()['active'] == 1
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert pool.snapshot()['active'] == 0 and not errors


@pytest.mark.asyncio
async def test_failed_cleanup_quarantines_readiness_and_next_call_recovers(child_parser, monkeypatch):
    install, _, _, children = child_parser
    install(' return html,False\n')
    denied = threading.Event()
    denied.set()
    original = process_budget._ProcessTree.close
    def failed(tree):
        if denied.is_set() and any(tree is candidate for _, candidate, _ in children):
            raise OSError('fixture: cannot prove cleanup')
        original(tree)
    monkeypatch.setattr(process_budget._ProcessTree, 'close', failed)
    pool = resources._CapacityPool('fixture parser', 1, 1)
    monkeypatch.setattr(resources, '_PARSERS', pool)
    try:
        with pytest.raises(AcquisitionError, match='quarantined'):
            await parser.parse_document('First', 'https://docs.example/')
        assert pool.snapshot()['quarantined'] == 1 and parser._OWNERS.quarantined
        report = importlib.import_module('kazma_core.tools.research_readiness').research_readiness()
        assert not next(check for check in report['checks'] if check['id'] == 'acquisition_parsers')['ok']
    finally:
        denied.clear()
        assert (await parser.parse_document('Second', 'https://docs.example/'))['text'] == 'Second'
    assert not parser._OWNERS.quarantined and pool.snapshot()['active'] == 0


@pytest.mark.asyncio
async def test_parser_saturation_and_input_limits_refuse_before_dispatch(child_parser, monkeypatch):
    _, _, calls, _ = child_parser
    pool = resources._CapacityPool('fixture parser', 1, 0)
    monkeypatch.setattr(resources, '_PARSERS', pool)
    lease = await pool.acquire()
    try:
        with pytest.raises(AcquisitionError, match='queue is full'):
            await parser.parse_document('Document', 'https://docs.example/')
        assert not calls
    finally:
        lease.release()
    with pytest.raises(AcquisitionError, match='input exceeds'):
        await parser.parse_document('x' * (20*1024**2+1), 'https://docs.example/')
    assert not calls
