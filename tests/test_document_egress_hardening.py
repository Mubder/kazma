"""Egress authorization/audit use live policy and immutable operation scope."""

import io
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.documents import extract_salvage as salvage
from kazma_core.documents.config import DocumentConfig, get_document_config
from kazma_core.documents.errors import DocumentSecurityError
from kazma_core.documents.ingestion import DocumentIngestionService

from tests.test_extract_salvage import _ir


def _svc(tmp_path):
    return DocumentIngestionService(config=DocumentConfig(storage_root=tmp_path,
        security_malware_scan='off', security_local_salvage=False))


def _job(svc, tenant, actor):
    result = svc.ingest_stream(io.BytesIO(b'a private document'), filename='note.txt',
        tenant_id=tenant, actor_id=actor, workspace_id=tenant + '-workspace')
    return svc.jobs.get(tenant_id=tenant, job_id=result.job_id)


@pytest.mark.parametrize('separate_services', [False, True])
def test_concurrent_egress_retains_all_identity_and_phase(tmp_path, monkeypatch, separate_services):
    first = _svc(tmp_path / 'one')
    second = _svc(tmp_path / 'two') if separate_services else first
    a, b = _job(first, 'tenant-a', 'alice'), _job(second, 'tenant-b', 'bob')
    barrier = threading.Barrier(2)
    def parse(job, *, force_ocr):
        barrier.wait(timeout=3)
        salvage._record_egress('mock-no-upload', tmp_path, 21)
        barrier.wait(timeout=3)
        salvage._record_egress('mock-no-upload', tmp_path, 21, 'failure')
    monkeypatch.setattr(first, '_parse_to_manifest_impl', parse)
    monkeypatch.setattr(second, '_parse_to_manifest_impl', parse)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(svc._parse_to_manifest, job, force_ocr=False) for svc, job in [(first, a), (second, b)]]
            for future in futures:
                future.result(timeout=5)
        for svc, job, actor in [(first, a, 'alice'), (second, b, 'bob')]:
            events = [e for e in svc.audit.list_events(tenant_id=job.tenant_id)['events'] if e['event_type'] == 'egress']
            assert {e['action'] for e in events} == {'remote_parse_attempt', 'remote_parse_failure'}
            for event in events:
                assert event['actor_id'] == actor and event['workspace_id'] == job.workspace_id
                assert event['document_id'] == str(job.document_id) and event['version_id'] == str(job.version_id)
                assert event['job_id'] == str(job.id)
                assert event['detail'] == {'provider': 'mock-no-upload', 'bytes': 21}
        with pytest.raises(DocumentSecurityError, match='audit scope'):
            salvage._record_egress('mock-no-upload', tmp_path, 21)
    finally:
        first.close()
        if second is not first:
            second.close()


def test_required_audit_failure_refuses_upload(tmp_path, monkeypatch):
    svc = _svc(tmp_path / 'store')
    job = _job(svc, 't', 'alice')
    path = tmp_path / 'x.pdf'
    path.write_bytes(b'never uploaded')
    monkeypatch.setenv('LLAMAPARSE_API_KEY', 'audit-fake-key')
    get_config_store().set('documents.security.remote_parse', True, category='documents')
    uploaded = []
    monkeypatch.setattr(salvage, '_llamaparse', lambda *a: uploaded.append(True) or '')
    def parse(job, *, force_ocr):
        # Native connection failure exercises DocumentAuditStore.required,
        # rather than merely replacing the whole store with a raising mock.
        svc.repository.close()
        with pytest.raises(RuntimeError, match='audit could not be persisted'):
            salvage.try_remote_parse(path, _ir('', 0))
    monkeypatch.setattr(svc, '_parse_to_manifest_impl', parse)
    try:
        svc._parse_to_manifest(job, force_ocr=False)
        assert not uploaded
    finally:
        svc.close()


def test_reused_version_keeps_each_new_job_actor(tmp_path, monkeypatch):
    svc = _svc(tmp_path / 'store')
    first = _job(svc, 't', 'alice')
    try:
        svc.repository.grant_access(tenant_id='t', document_id=first.document_id,
            actor_id='alice', principal_id='bob', permission='write')
        second = svc.ingest_stream(io.BytesIO(b'a private document'), filename='note.txt',
            tenant_id='t', actor_id='bob', workspace_id='bob-workspace',
            document_id=first.document_id, force_ocr=True, idempotency_key='bob-request')
        job = svc.jobs.get(tenant_id='t', job_id=second.job_id)
        assert job.version_id == first.version_id  # immutable source deduplication
        assert job.actor_id == 'bob' and job.force_ocr
        monkeypatch.setattr(svc, '_parse_to_manifest_impl', lambda *a, **k:
            salvage._record_egress('mock-no-upload', tmp_path, 18))
        svc._parse_to_manifest(job, force_ocr=True)
        events = [e for e in svc.audit.list_events(tenant_id='t')['events'] if e['event_type'] == 'egress']
        assert len(events) == 1
        assert events[0]['actor_id'] == 'bob' and events[0]['workspace_id'] == 'bob-workspace'
        assert events[0]['job_id'] == str(job.id)
        replay = svc.ingest_stream(io.BytesIO(b'a private document'), filename='note.txt',
            tenant_id='t', actor_id='bob', workspace_id='bob-workspace',
            document_id=first.document_id, force_ocr=True, idempotency_key='bob-request')
        assert replay.reused and replay.job_id == job.id
    finally:
        svc.close()


def test_live_revocation_overrides_existing_service_snapshot(tmp_path, monkeypatch):
    store = get_config_store()
    store.set('documents.security.remote_parse', True, category='documents')
    svc = DocumentIngestionService(config=replace(get_document_config(), storage_root=tmp_path / 'store'))
    job = _job(svc, 't', 'alice')
    path = tmp_path / 'x.pdf'
    path.write_bytes(b'never uploaded')
    monkeypatch.setenv('LLAMAPARSE_API_KEY', 'audit-fake-key')
    uploaded = []
    monkeypatch.setattr(salvage, '_llamaparse', lambda *a: uploaded.append(True) or '')
    store.set('documents.security.remote_parse', False, category='documents')
    def parse(job, *, force_ocr):
        assert svc.config.security_remote_parse  # the original stale boundary
        assert salvage.try_remote_parse(path, _ir('', 0)) is None
    monkeypatch.setattr(svc, '_parse_to_manifest_impl', parse)
    try:
        svc._parse_to_manifest(job, force_ocr=False)
        assert not uploaded
    finally:
        svc.close()


def test_transient_read_without_scope_cannot_upload(tmp_path, monkeypatch):
    get_config_store().set('documents.security.remote_parse', True, category='documents')
    monkeypatch.setenv('LLAMAPARSE_API_KEY', 'audit-fake-key')
    uploaded = []
    monkeypatch.setattr(salvage, '_llamaparse', lambda *a: uploaded.append(True) or '')
    path = tmp_path / 'x.pdf'
    path.write_bytes(b'never uploaded')
    with pytest.raises(DocumentSecurityError, match='audit scope'):
        salvage.try_remote_parse(path, _ir('', 0))
    assert not uploaded


def test_revocation_between_fallback_providers_blocks_second_upload(tmp_path, monkeypatch):
    store = get_config_store()
    store.set('documents.security.remote_parse', True, category='documents')
    monkeypatch.setenv('LLAMAPARSE_API_KEY', 'audit-fake-key')
    monkeypatch.setenv('REDUCTO_API_KEY', 'audit-fake-key')
    uploaded, phases = [], []
    def first(*args):
        uploaded.append('llama')
        store.set('documents.security.remote_parse', False, category='documents')
        return ''
    monkeypatch.setattr(salvage, '_llamaparse', first)
    monkeypatch.setattr(salvage, '_reducto', lambda *a: uploaded.append('reducto') or '')
    path = tmp_path / 'x.pdf'
    path.write_bytes(b'fake')
    with salvage.salvage_audit_scope(lambda provider, path, size, phase: phases.append(phase)):
        assert salvage.try_remote_parse(path, _ir('', 0)) is None
    assert uploaded == ['llama'] and phases == ['attempt', 'success']



@pytest.mark.parametrize("failure", ["invalid", "unavailable"])
def test_live_policy_failure_between_provider_attempts_refuses_second_upload(tmp_path, monkeypatch, failure):
    import sqlite3

    store = get_config_store()
    store.set("documents.security.remote_parse", True, category="documents")
    monkeypatch.setenv("LLAMAPARSE_API_KEY", "audit-fake-key")
    monkeypatch.setenv("REDUCTO_API_KEY", "audit-fake-key")
    uploaded = []

    def first(*args):
        uploaded.append("llama")
        if failure == "invalid":
            store.set("documents.security.malware_fail_closed", "perhaps")
        else:
            def unavailable(_):
                raise sqlite3.OperationalError("test settings unavailable")
            monkeypatch.setattr(store, "snapshot", unavailable)
        return ""

    monkeypatch.setattr(salvage, "_llamaparse", first)
    monkeypatch.setattr(salvage, "_reducto", lambda *a: uploaded.append("reducto") or "")
    source = tmp_path / "inert.pdf"
    source.write_bytes(b"inert bytes never sent to a real provider")
    with salvage.salvage_audit_scope(lambda *args: None):
        assert salvage.try_remote_parse(source, _ir("", 0)) is None
    assert uploaded == ["llama"]


# ── 2026-10-13 (F03/F04): fallback, transport and response bounds ───────


def _enable_remote(monkeypatch, *keys):
    get_config_store().set('documents.security.remote_parse', True, category='documents')
    for key in keys:
        monkeypatch.setenv(key, 'audit-fake-key')


def test_a_failed_provider_is_audited_and_the_next_one_is_tried(tmp_path, monkeypatch):
    import httpx

    _enable_remote(monkeypatch, 'LLAMAPARSE_API_KEY', 'REDUCTO_API_KEY')
    def failing(*args):
        raise httpx.ConnectError('provider unreachable')
    monkeypatch.setattr(salvage, '_llamaparse', failing)
    monkeypatch.setattr(salvage, '_reducto', lambda *a: '## Recovered\n\nReadable text from the fallback provider.')
    path = tmp_path / 'x.pdf'
    path.write_bytes(b'inert bytes')
    events = []
    with salvage.salvage_audit_scope(lambda provider, p, size, phase: events.append((provider, phase))):
        recovered = salvage.try_remote_parse(path, _ir('', 0))
    assert recovered is not None
    assert events == [('llamaparse', 'attempt'), ('llamaparse', 'failure'),
                      ('reducto', 'attempt'), ('reducto', 'success')]


def test_revocation_after_a_failed_provider_blocks_the_fallback(tmp_path, monkeypatch):
    _enable_remote(monkeypatch, 'LLAMAPARSE_API_KEY', 'REDUCTO_API_KEY')
    store = get_config_store()
    uploaded = []
    def failing(*args):
        uploaded.append('llama')
        store.set('documents.security.remote_parse', False, category='documents')
        raise RuntimeError('provider failed after the operator revoked consent')
    monkeypatch.setattr(salvage, '_llamaparse', failing)
    monkeypatch.setattr(salvage, '_reducto', lambda *a: uploaded.append('reducto') or 'x')
    path = tmp_path / 'x.pdf'
    path.write_bytes(b'inert bytes')
    events = []
    with salvage.salvage_audit_scope(lambda provider, p, size, phase: events.append(phase)):
        assert salvage.try_remote_parse(path, _ir('', 0)) is None
    assert uploaded == ['llama'] and events == ['attempt', 'failure']


def _provider(handler):
    import httpx

    real = salvage._provider_client
    return lambda: real(transport=httpx.MockTransport(handler))


def test_the_provider_client_ignores_ambient_proxies_and_redirects(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    client = salvage._provider_client()
    try:
        assert client.trust_env is False and client.follow_redirects is False
    finally:
        client.close()


def test_a_redirect_is_never_followed_with_the_document(tmp_path, monkeypatch):
    import httpx

    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={'location': 'https://elsewhere.invalid/collect'})
    monkeypatch.setattr(salvage, '_provider_client', _provider(handler))
    path = tmp_path / 'x.pdf'
    path.write_bytes(b'inert bytes')
    with pytest.raises(httpx.HTTPStatusError):
        salvage._reducto(path, 'audit-fake-key')
    assert seen == ['https://platform.reducto.ai/parse']


def test_an_oversized_provider_answer_is_refused(tmp_path, monkeypatch):
    import httpx

    monkeypatch.setattr(salvage, '_PROVIDER_RESPONSE_LIMIT', 1024)
    monkeypatch.setattr(salvage, '_provider_client', _provider(
        lambda request: httpx.Response(200, content=b'x' * 4096)))
    path = tmp_path / 'x.pdf'
    path.write_bytes(b'inert bytes')
    with pytest.raises(DocumentSecurityError) as caught:
        salvage._reducto(path, 'audit-fake-key')
    assert caught.value.code == 'remote_parse_response_limit'


def test_the_llamaparse_job_flow_through_the_bounded_client(tmp_path, monkeypatch):
    import httpx

    seen = []
    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get('authorization')))
        if request.url.path.endswith('/upload'):
            return httpx.Response(200, json={'id': 'job-1'})
        return httpx.Response(200, json={'markdown': '# Title\n\nBody text'})
    monkeypatch.setattr(salvage, '_provider_client', _provider(handler))
    path = tmp_path / 'x.pdf'
    path.write_bytes(b'inert bytes')
    assert salvage._llamaparse(path, 'audit-fake-key') == '# Title\n\nBody text'
    assert seen == [
        ('POST', '/api/parsing/upload', 'Bearer audit-fake-key'),
        ('GET', '/api/parsing/job/job-1/result/markdown', 'Bearer audit-fake-key'),
    ]
