"""An unavailable capacity authority must not admit unbounded intake."""

import io

import pytest
from kazma_core.documents.capacity import CapacityError
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionError, DocumentIngestionService


@pytest.fixture
def service(tmp_path):
    svc = DocumentIngestionService(config=DocumentConfig(storage_root=tmp_path / 'store',
        intake_max_bytes=16, capacity_intake_bytes_per_minute=20, security_malware_scan='off'))
    yield svc
    svc.close()


@pytest.mark.parametrize('authority', ['queue', 'tenant', 'storage'])
def test_unavailable_authority_refuses_before_reading_any_source(service, monkeypatch, authority):
    def failed(*args, **kwargs):
        raise OSError('inert capacity fault')
    if authority == 'queue':
        monkeypatch.setattr(service.jobs, 'queue_stats', failed)
    elif authority == 'tenant':
        monkeypatch.setattr(service.jobs, 'tenant_load', failed)
    else:
        monkeypatch.setattr(service.capacity, '_free_bytes', lambda: None)
    class Unreadable:
        def read(self, _):
            raise AssertionError('capacity refusal must precede source read')
    with pytest.raises(CapacityError) as error:
        service.ingest_stream(Unreadable(), filename='x.txt', tenant_id='t', actor_id='a', workspace_id='w')
    assert error.value.status == 503 and error.value.code == 'capacity_unavailable'
    assert not service.list_documents(tenant_id='t', actor_id='a')
    assert not (service.config.storage_root / 'quarantine').exists()


def test_unsized_upload_cannot_consume_zero_byte_rate_credit(service):
    args = dict(filename='x.txt', tenant_id='t', actor_id='a', workspace_id='w')
    service.ingest_stream(io.BytesIO(b'first'), **args)
    # Unknown streams reserve the upper bound, then reconcile actual bytes.
    usage = service.admissions.snapshot(tenant_id='t')
    assert (usage['window_files'], usage['window_bytes']) == (1, 5)
    with pytest.raises(CapacityError) as error:
        service.ingest_stream(io.BytesIO(b'next'), **args)
    assert error.value.code == 'rate_limited'


@pytest.mark.parametrize('length', [-1, True, 2.5, '4'])
def test_invalid_declared_length_is_not_rate_credit(service, length):
    with pytest.raises(DocumentIngestionError) as error:
        service.ingest_stream(io.BytesIO(b'text'), filename='x.txt', tenant_id='t',
            actor_id='a', workspace_id='w', content_length=length)
    assert error.value.code == 'invalid_request'
