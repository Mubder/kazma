"""Request-bound replay and principal-authorized durable job reads."""

import io
import sqlite3
from contextlib import closing

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionError, DocumentIngestionService
from kazma_core.documents.jobs import JobNotFoundError
from kazma_core.documents.repository import DocumentAccessError


@pytest.fixture
def service(tmp_path):
    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path / 'store', security_malware_scan='off',
    ))
    yield svc
    svc.close()


def _upload(svc, content=b'private text', **changes):
    args = dict(filename='note.txt', tenant_id='t', workspace_id='w',
                actor_id='alice', idempotency_key='request-1')
    args.update(changes)
    return svc.ingest_stream(io.BytesIO(content), **args)


def test_identical_replay_requires_same_payload_and_request(service):
    first = _upload(service)
    second = _upload(service)
    assert second.reused and first.job_id == second.job_id
    with pytest.raises(DocumentIngestionError) as error:
        _upload(service, content=b'different text')
    assert error.value.code == 'idempotency_conflict'
    assert len(service.jobs_for_document(tenant_id='t', document_id=first.document_id, actor_id='alice')) == 1


@pytest.mark.parametrize('changes', [
    {'workspace_id': 'other'}, {'filename': 'other.txt'}, {'title': 'other'},
    {'force_ocr': True}, {'max_attempts': 19},
])
def test_replay_rejects_changed_request_before_consuming_bytes(service, changes):
    _upload(service)
    class Unreadable:
        def read(self, _):
            raise AssertionError('conflicting request must not consume bytes')
    with pytest.raises(DocumentIngestionError) as error:
        service.ingest_stream(Unreadable(), filename=changes.get('filename', 'note.txt'),
            tenant_id='t', workspace_id=changes.get('workspace_id', 'w'), actor_id='alice',
            idempotency_key='request-1', **{k:v for k,v in changes.items() if k not in {'filename', 'workspace_id'}})
    assert error.value.code == 'idempotency_conflict'


def test_other_actor_cannot_probe_replay_even_with_same_tenant_and_key(service):
    first = _upload(service)
    # The same conflict as any other request on the key: nothing says whose
    # document it named (it used to surface as "DocumentAccessError").
    with pytest.raises(DocumentIngestionError) as error:
        _upload(service, actor_id='bob')
    assert error.value.code == 'idempotency_conflict'
    service.repository.grant_access(tenant_id='t', document_id=first.document_id,
        actor_id='alice', principal_id='bob', permission='read')
    # Read permission permits status, not rebinding the original operation.
    with pytest.raises(DocumentIngestionError) as error:
        _upload(service, actor_id='bob')
    assert error.value.code == 'idempotency_conflict'


def _refused(call, args, method):
    # An unreadable job reads exactly like a job that does not exist.
    if method == 'job_status':
        assert call(**args, actor_id='bob') is None
    elif method == 'job_events':
        with pytest.raises(JobNotFoundError):
            call(**args, actor_id='bob')
    else:
        with pytest.raises(DocumentAccessError):
            call(**args, actor_id='bob')


@pytest.mark.parametrize('method', ['job_status', 'job_events', 'jobs_for_document'])
def test_job_reads_obey_document_acl_and_tombstone(service, method):
    first = _upload(service)
    call = getattr(service, method)
    args = dict(tenant_id='t', **({'document_id': first.document_id}
        if method == 'jobs_for_document' else {'job_id': first.job_id}))
    assert call(**args, actor_id='alice')
    _refused(call, args, method)
    service.repository.grant_access(tenant_id='t', document_id=first.document_id,
        actor_id='alice', principal_id='bob', permission='read')
    assert call(**args, actor_id='bob')
    service.repository.tombstone_document(tenant_id='t', document_id=first.document_id,
        actor_id='alice', reason='test access removal')
    _refused(call, args, method)


@pytest.mark.parametrize('suffix', ['', '/events'])
def test_http_job_read_keeps_request_actor(service, monkeypatch, suffix):
    from kazma_ui import documents_api
    first = _upload(service)
    async def scope(request):
        return 't', request.headers['x-test-actor'], 'w'
    monkeypatch.setattr(documents_api, '_resolve_scope', scope)
    app = FastAPI()
    app.state.documents = service
    app.include_router(documents_api.create_documents_router())
    with TestClient(app) as client:
        url = f'/api/documents/jobs/{first.job_id}{suffix}'
        assert client.get(url, headers={'x-test-actor': 'alice'}).status_code == 200
        refused = client.get(url, headers={'x-test-actor': 'bob'})
        assert refused.status_code in {403, 404}
        assert str(first.job_id) not in refused.text and 'ready_to_parse' not in refused.text


@pytest.mark.asyncio
@pytest.mark.parametrize('by_document', [False, True])
async def test_native_status_keeps_resolved_actor(service, monkeypatch, by_document):
    from kazma_skills.native.document_platform import tools
    first = _upload(service)
    async def ensure():
        return service
    monkeypatch.setattr(tools, '_ensure_service', ensure)
    args = {'document_id': str(first.document_id)} if by_document else {'job_id': str(first.job_id)}
    monkeypatch.setattr(tools, '_operator_scope', lambda: ('t', 'w', 'alice'))
    assert 'state:' in await tools.document_status(**args)
    monkeypatch.setattr(tools, '_operator_scope', lambda: ('t', 'w', 'bob'))
    refused = await tools.document_status(**args)
    assert refused.startswith('Error') and str(first.job_id) not in refused


def test_legacy_queue_migration_never_invents_original_actor(tmp_path):
    cfg = DocumentConfig(storage_root=tmp_path / 'store', security_malware_scan='off')
    original = DocumentIngestionService(config=cfg)
    try:
        first = _upload(original)
    finally:
        original.close()
    with closing(sqlite3.connect(cfg.storage_root / 'documents.db', isolation_level=None)) as conn:
        for column in ['actor_id', 'request_digest', 'force_ocr']:
            conn.execute(f'ALTER TABLE document_jobs DROP COLUMN {column}')
    reopened = DocumentIngestionService(config=cfg)
    try:
        job = reopened.jobs.get(tenant_id='t', job_id=first.job_id)
        assert job.actor_id == '' and job.request_digest == '' and not job.force_ocr
        assert reopened.job_status(tenant_id='t', job_id=first.job_id, actor_id='alice')
        with pytest.raises(DocumentIngestionError) as error:
            _upload(reopened)
        assert error.value.code == 'idempotency_conflict'
    finally:
        reopened.close()
