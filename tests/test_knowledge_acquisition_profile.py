from unittest.mock import AsyncMock

import pytest

from kazma_core.stores import knowledge_ingest as ki
from kazma_core.web_acquire import FetchResult
from kazma_core.web_acquire.document import extract_knowledge_document


def test_hidden_panels_code_tables_and_arabic_survive_without_scripts():
    html = '''<head><script>secret()</script></head><nav>menu</nav>
    <h1>وثائق API</h1><div hidden><p>Hidden panel remains readable</p>
    <pre>  x = "```"\n\tprint(x)\n</pre><table><tr><th>Name</th><th>Value</th></tr>
    <tr><td>enabled</td><td>true</td></tr></table></div><footer>footer</footer>'''
    result = extract_knowledge_document(html)
    assert 'وثائق API' in result.text and 'Hidden panel remains readable' in result.text
    assert '  x = "```"\n\tprint(x)\n' in result.text
    assert '````\n' in result.text and '\tName\tValue' in result.text
    assert 'secret()' not in result.text and 'menu' not in result.text and 'footer' not in result.text
    assert not result.truncated and not result.dynamic_tabs


def test_document_caps_and_dynamic_controls_are_explicit():
    result = extract_knowledge_document('<button role="tab">Python</button><script>show()</script>' + 'x' * 300, limit=100)
    assert result.truncated and result.dynamic_tabs and len(result.text) <= 100
    result = extract_knowledge_document('<pre>&lt;button role="tab"&gt;</pre><script>show()</script>')
    assert not result.dynamic_tabs


@pytest.mark.asyncio
async def test_kb_uses_one_scoped_knowledge_call_and_refuses_thin_content(monkeypatch):
    acquire = AsyncMock(return_value=FetchResult(ok=True, url='https://docs.example/a', text='tiny'))
    monkeypatch.setattr(ki, '_web_fetch_text', acquire)
    fallback = AsyncMock(side_effect=AssertionError('independent fallback'))
    monkeypatch.setattr(ki, '_render_with_playwright', fallback)
    text, status, reason = await ki._extract_page('https://docs.example/a')
    assert text is None and status == 'error' and 'Insufficient' in reason
    assert acquire.await_count == 1
    kwargs = acquire.await_args.kwargs
    assert kwargs['purpose'] == 'kb'
    assert kwargs['scope_check']('https://docs.example/b')
    assert not kwargs['scope_check']('https://other.example/b')
    fallback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [404, 429, 500])
async def test_kb_never_indexes_an_error_json_body_retained_for_inspection(monkeypatch, status):
    # The general reader intentionally keeps API JSON even for HTTP errors;
    # KB indexing must apply the stricter document-success boundary.
    result = FetchResult(ok=True, url='https://docs.example/api', http_status=status,
                         text='{"detail": "Useful diagnostic words retained for API inspection only"}', quality='json')
    monkeypatch.setattr(ki, '_web_fetch_text', AsyncMock(return_value=result))
    text, outcome, reason = await ki._extract_page(result.url)
    # A 404 is a page that is gone: excluded, not a failure (2026-10-31).
    assert text is None and outcome == ('excluded' if status == 404 else 'error') and f'HTTP {status}' in reason


@pytest.mark.asyncio
async def test_bfs_uses_acquired_links_and_retains_query_identity(monkeypatch):
    seed = 'https://docs.example/docs/start'
    first = seed + '?version=1'
    second = seed + '?version=2'
    calls = []
    async def acquire(url, **kwargs):
        calls.append(url)
        assert kwargs['purpose'] == 'kb_discovery'
        assert not kwargs['scope_check']('https://outside.example/escape')
        return FetchResult(ok=True, url=url, final_url=url, text='content', links_complete=True,
                           links=(first, second, first, 'https://outside.example/escape') if url == seed else ())
    monkeypatch.setattr(ki, '_web_fetch_text', acquire)
    monkeypatch.setattr(ki, '_kb_max_pages', lambda: 4)
    assert await ki._bfs_discover(seed, 'prefix') == [seed, first, second]
    assert calls == [seed, first, second]


@pytest.mark.asyncio
async def test_sparse_discovery_refuses_hosted_scope_bypass(monkeypatch):
    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'jina,firecrawl')
    monkeypatch.setenv('KAZMA_FIRECRAWL_API_KEY', 'fixture-only')
    for name in ('_llms_txt_discover', '_fetch_sitemaps', '_bfs_discover'):
        monkeypatch.setattr(ki, name, AsyncMock(return_value=[]))
    fire = AsyncMock(side_effect=AssertionError('hosted map bypass'))
    jina = AsyncMock(side_effect=AssertionError('hosted reader bypass'))
    monkeypatch.setattr(ki, '_firecrawl_map_discover', fire)
    monkeypatch.setattr(ki, '_jina_expand_seed', jina)
    messages = []
    await ki.kb_discover_pages('https://docs.example/docs/start', on_progress=messages.append)
    assert any('hosted discovery refused' in message for message in messages)
    fire.assert_not_awaited()
    jina.assert_not_awaited()
