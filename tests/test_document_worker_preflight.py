"""Structural preflight and local salvage cannot run in the server process."""

import io
import os
from dataclasses import replace

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.errors import DocumentParseError
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.service import DocumentService
from pypdf import PdfWriter


def _blank(path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(path)
    return path


def test_durable_intake_never_constructs_pdf_reader_in_parent(tmp_path, monkeypatch):
    path = _blank(tmp_path / 'blank.pdf')
    def forbidden(*args, **kwargs):
        raise AssertionError('untrusted structure reached server process')
    monkeypatch.setattr('pypdf.PdfReader', forbidden)
    svc = DocumentIngestionService(config=DocumentConfig(storage_root=tmp_path / 'store',
        ocr_enabled=False, security_local_salvage=False, security_malware_scan='off'))
    try:
        result = svc.ingest_stream(io.BytesIO(path.read_bytes()), filename='blank.pdf',
            tenant_id='t', workspace_id='w', actor_id='alice')
        assert result.state.value == 'ready_to_parse'
        version = svc.repository.get_version(tenant_id='t', version_id=result.version_id)
        assert version.mime_type == 'application/pdf'
    finally:
        svc.close()


def _inject_docling(monkeypatch, *, stall=False):
    from kazma_core.documents import service as module
    original = module.run_isolated_subprocess
    # A dependency-shaped inert fixture is installed ONLY in the real child.
    # Native parsing, protocol, limits and tree cleanup remain production code.
    injected = (
        "import os,sys,types,time\n"
        "package=types.ModuleType('docling');package.__path__=[];sys.modules['docling']=package\n"
        "converter=types.ModuleType('docling.document_converter')\n"
        "def convert(path):\n"
        + (" time.sleep(60)\n" if stall else "")
        + " return types.SimpleNamespace(document=types.SimpleNamespace(export_to_markdown=lambda: 'WorkerPID'+str(os.getpid())+' '+('local salvage recovered readable source text. '*80)))\n"
        "converter.DocumentConverter=lambda: types.SimpleNamespace(convert=convert)\n"
        "sys.modules['docling.document_converter']=converter\n"
    )
    def run(request):
        command = list(request.command)
        bootstrap = next(i for i, arg in enumerate(command) if "runpy.run_module('kazma_core.documents.parser_worker'" in arg)
        command[bootstrap] = injected + command[bootstrap]
        return original(replace(request, command=tuple(command)))
    monkeypatch.setattr(module, 'run_isolated_subprocess', run)
    def forbidden(*args, **kwargs):
        raise AssertionError('local salvage reached server process')
    monkeypatch.setattr('kazma_core.documents.extract_salvage.try_docling', forbidden)


def test_local_salvage_executes_in_actual_child_with_supplied_limits(tmp_path, monkeypatch):
    _inject_docling(monkeypatch)
    source = _blank(tmp_path / 'blank.pdf')
    svc = DocumentService(config=DocumentConfig(storage_root=tmp_path / 'store', ocr_enabled=False))
    result = svc._parse_isolated(source)
    assert result.metadata['extractor'] == 'docling'
    text = '\n'.join(block.text for page in result.pages for block in page.blocks)
    assert 'WorkerPID' in text
    assert f'WorkerPID{os.getpid()} ' not in text
    assert not list((tmp_path / 'store' / 'parser-runs').iterdir())


def test_local_salvage_hang_is_bounded_by_worker_deadline(tmp_path, monkeypatch):
    _inject_docling(monkeypatch, stall=True)
    source = _blank(tmp_path / 'blank.pdf')
    svc = DocumentService(config=DocumentConfig(storage_root=tmp_path / 'store',
        ocr_enabled=False, worker_timeout_seconds=3))
    with pytest.raises(DocumentParseError) as error:
        svc._parse_isolated(source)
    assert error.value.code == 'parser_timeout' and error.value.retryable
    assert not list((tmp_path / 'store' / 'parser-runs').iterdir())
