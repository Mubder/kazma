"""Terminal tool failures and cached service evidence retain their meaning."""
from types import SimpleNamespace

import pytest


def test_gateway_status_distinguishes_zero_balance_unknown_charges_and_reservations():
    from kazma_gateway.agent_handler.commands import _kb_acquisition_status

    job = dict(job_id='fixture', acquisition=dict(status='partial', pending=1, ambiguous=0,
        metadata_ambiguous=1, discovery_ambiguous=2, discovery_limited=True,
        last_receipt=dict(backend='httpx'), allowances=dict(job_reserved=None, job_limit=256,
            day_reserved=6, day_limit=1024), provider_balances=[
                dict(provider='firecrawl', remaining=0, fresh=True, status='observed', observed_at=42),
                dict(provider='jina', remaining=99, fresh=False, status='stale', observed_at=1)]))
    text = _kb_acquisition_status(job)
    assert 'Discovery uncertain=3; limited=True' in text
    assert 'job=unknown/256' in text and 'installation UTC day=6/1024' in text
    assert 'Provider=firecrawl; account credits=0' in text and 'Provider=jina; account credits=unavailable' in text
    assert 'charges for these requests: unavailable' in text
    job['acquisition']['usage'] = {'observations': [{'reported': 0}]}
    assert 'reported tokens=0' in _kb_acquisition_status(job)


@pytest.mark.asyncio
@pytest.mark.parametrize('empty', [False, True])
async def test_agent_ingest_admission_failure_is_an_error_not_a_finished_site(monkeypatch, empty):
    from kazma_core.agent.tool_builtins.knowledge import register_knowledge_tools
    from kazma_core.stores import knowledge, knowledge_ingest

    tools = {}
    class Registry:
        def register(self, **kwargs):
            def install(function):
                tools[function.__name__] = function
                return function
            return install
    register_knowledge_tools(Registry())
    monkeypatch.setattr(knowledge, 'get_knowledge_store', lambda: SimpleNamespace(get_library=lambda identity: {'chunk_count': 2}))
    async def failed(*args, **kwargs):
        if not empty:
            yield knowledge_ingest.ProgressUpdate(phase='error', message='policy_error: robots setting invalid')
    monkeypatch.setattr(knowledge_ingest, 'ingest_site', failed)
    # Admitted: this test is about the crawl's outcome. Without this the
    # unresolvable seed was refused at admission, and the test passed on the
    # refusal's text (a refused seed is tests/test_refusals_keep_the_turn.py).
    async def admitted(*args, **kwargs):
        return (SimpleNamespace(job_id='job-1', finish=lambda *_: None), None, None, 1)
    monkeypatch.setattr(knowledge_ingest, '_prepare_ingest_site', admitted)
    answer = await tools['knowledge_ingest_site']('lib', 'https://docs.example/tree/')
    assert answer.startswith('Error: knowledge_ingest_site failed')
    assert 'Site ingest finished' not in answer
