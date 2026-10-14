"""Real inert PDFs exercise the shared intake/mutation active-content policy."""

from types import SimpleNamespace

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.errors import (
    DocumentFormatError,
    DocumentParseError,
    DocumentSecurityError,
    DocumentUnavailableError,
)
from kazma_core.documents.mutation_worker import WorkerError, _reader
from kazma_core.documents.pdf_policy import assert_pdf_safe
from kazma_core.documents.sniff import sniff_document
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)


def _pdf(path, location, *, indirect=False, dangerous=True):
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    action = DictionaryObject({NameObject('/S'): NameObject('/JavaScript' if dangerous else '/GoTo')})
    if dangerous:
        action[NameObject('/JS')] = TextStringObject('/* inert regression fixture */')
    else:
        action[NameObject('/D')] = ArrayObject([NumberObject(0), NameObject('/Fit')])
    ref = writer._add_object(action) if indirect else action
    aa = DictionaryObject({NameObject('/WC' if location == 'catalog' else '/O'): ref})
    if location == 'catalog':
        writer._root_object[NameObject('/AA')] = aa
    elif location == 'page':
        page[NameObject('/AA')] = aa
    elif location == 'annotation':
        page[NameObject('/Annots')] = ArrayObject([writer._add_object(DictionaryObject({
            NameObject('/Type'): NameObject('/Annot'), NameObject('/AA'): aa,
        }))])
    elif location == 'form':
        writer._root_object[NameObject('/AcroForm')] = DictionaryObject({
            NameObject('/Fields'): ArrayObject([writer._add_object(DictionaryObject({NameObject('/AA'): aa}))])
        })
    elif location == 'chain':
        writer._root_object[NameObject('/OpenAction')] = DictionaryObject({
            NameObject('/S'): NameObject('/GoTo'),
            NameObject('/D'): ArrayObject([NumberObject(0), NameObject('/Fit')]),
            NameObject('/Next'): ArrayObject([ref]),
        })
    writer.write(path)
    return path


@pytest.mark.parametrize('location', ['catalog', 'page', 'annotation', 'form', 'chain'])
@pytest.mark.parametrize('indirect', [False, True])
def test_intake_and_mutation_reject_nested_actions(tmp_path, location, indirect):
    path = _pdf(tmp_path / 'active.pdf', location, indirect=indirect)
    with pytest.raises(DocumentSecurityError):
        sniff_document(path, DocumentConfig(storage_root=tmp_path / 'store'))
    with pytest.raises(WorkerError) as error:
        _reader(path)
    assert error.value.code == 'unsafe_document'


@pytest.mark.parametrize('location', ['catalog', 'page', 'annotation', 'form', 'chain'])
def test_navigation_actions_remain_usable(tmp_path, location):
    path = _pdf(tmp_path / 'navigation.pdf', location, indirect=True, dangerous=False)
    assert sniff_document(path, DocumentConfig(storage_root=tmp_path / 'store')).mime_type == 'application/pdf'
    assert len(_reader(path).pages) == 1


def test_original_action_shape_misses_additional_action(tmp_path):
    path = _pdf(tmp_path / 'original-witness.pdf', 'catalog')
    reader = PdfReader(path)
    root = reader.trailer['/Root']
    # Original policy: checks catalog /OpenAction, not its /AA event map.
    assert root.get('/OpenAction') is None
    assert '/JavaScript' not in root
    assert '/S' not in root['/AA']
    with pytest.raises(DocumentSecurityError):
        assert_pdf_safe(reader)


@pytest.mark.parametrize('budget', [{'max_objects': 3}, {'max_depth': 1}])
def test_inspection_budgets_fail_closed(tmp_path, budget):
    path = _pdf(tmp_path / 'bounded.pdf', 'catalog', dangerous=False)
    with pytest.raises(DocumentSecurityError, match='budget'):
        assert_pdf_safe(PdfReader(path), **budget)


def test_unreadable_reference_fails_closed():
    from pypdf.generic import IndirectObject
    broken = IndirectObject(999, 0, SimpleNamespace(get_object=lambda *_: (_ for _ in ()).throw(ValueError('broken'))))
    reader = SimpleNamespace(trailer={'/Root': {'/AA': {'/O': broken}}})
    with pytest.raises(DocumentSecurityError, match='could not verify'):
        assert_pdf_safe(reader)


def test_tolerant_reader_missing_reference_is_not_a_safe_scalar():
    from pypdf.generic import IndirectObject
    # Tolerant readers may return None instead of raising for a dangling ref.
    broken = IndirectObject(999, 0, SimpleNamespace(get_object=lambda *_: None))
    reader = SimpleNamespace(trailer={'/Root': {'/AA': {'/O': broken}}})
    with pytest.raises(DocumentSecurityError, match='could not verify'):
        assert_pdf_safe(reader)


def test_missing_catalog_cannot_pass_an_empty_walk():
    with pytest.raises(DocumentSecurityError, match='catalog'):
        assert_pdf_safe(SimpleNamespace(trailer={'/Root': None}))


def test_shared_references_and_cycles_are_bounded(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    action = DictionaryObject({NameObject('/S'): NameObject('/GoTo')})
    ref = writer._add_object(action)
    action[NameObject('/Next')] = ref
    writer._root_object[NameObject('/OpenAction')] = ref
    path = tmp_path / 'cycle.pdf'
    writer.write(path)
    assert_pdf_safe(PdfReader(path))


def test_embedded_file_and_script_name_tree_are_rejected(tmp_path):
    for key in ['/JavaScript', '/EmbeddedFiles']:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer._root_object[NameObject('/Names')] = DictionaryObject({NameObject(key): DictionaryObject()})
        path = tmp_path / (key[1:] + '.pdf')
        writer.write(path)
        with pytest.raises(DocumentSecurityError):
            sniff_document(path, DocumentConfig(storage_root=tmp_path / 'store'))


def test_unreadable_structure_cannot_be_authorized_by_absent_markers(tmp_path):
    path = tmp_path / 'malformed.pdf'
    path.write_bytes(b'%PDF-1.7\n1 0 obj << /OpenAction << /S /GoTo >> >>\nendobj\n%%EOF\n')
    # Original fallback accepted this because no dangerous byte marker exists.
    assert b'/JavaScript' not in path.read_bytes()
    with pytest.raises(DocumentFormatError, match='could not be verified'):
        sniff_document(path, DocumentConfig(storage_root=tmp_path / 'store'))


def test_missing_structural_inspector_fails_closed(tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__
    def without_pypdf(name, *args, **kwargs):
        if name == 'pypdf':
            raise ImportError('unavailable test dependency')
        return original(name, *args, **kwargs)
    path = _pdf(tmp_path / 'inert.pdf', 'page', dangerous=False)
    monkeypatch.setattr(builtins, '__import__', without_pypdf)
    with pytest.raises(DocumentUnavailableError):
        sniff_document(path, DocumentConfig(storage_root=tmp_path / 'store'))


# ── 2026-10-13 (F01): actions that leave the document, XFA, untyped files ──


def _with_action(tmp_path, name, action, *, where='open'):
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    if where == 'open':
        writer._root_object[NameObject('/OpenAction')] = action
    else:
        page[NameObject('/Annots')] = ArrayObject([writer._add_object(DictionaryObject({
            NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject('/Link'),
            NameObject('/Rect'): ArrayObject([NumberObject(0)] * 4), NameObject('/A'): action,
        }))])
    path = tmp_path / f'{name}.pdf'
    writer.write(path)
    return path


def _action(kind, **fields):
    action = DictionaryObject({NameObject('/S'): NameObject(kind)})
    for key, value in fields.items():
        action[NameObject('/' + key)] = value
    return action


@pytest.mark.parametrize('kind', ['/GoToR', '/SubmitForm', '/ImportData', '/Rendition',
                                  '/RichMediaExecute', '/Sound', '/Movie'])
def test_actions_that_leave_the_document_are_refused(tmp_path, kind):
    path = _with_action(tmp_path, kind[1:], _action(kind, F=TextStringObject('x')))
    with pytest.raises(DocumentSecurityError):
        assert_pdf_safe(PdfReader(path))
    with pytest.raises(WorkerError) as error:
        _reader(path)
    assert error.value.code == 'unsafe_document'


_UNC = '\\\\attacker.invalid\\share'


@pytest.mark.parametrize('target', [
    'file:///C:/Windows/win.ini', _UNC, 'smb://attacker.invalid/x',
    'javascript:alert(1)', 'relative/path.pdf', '  FILE:///etc/passwd',
])
def test_links_outside_the_web_are_refused(tmp_path, target):
    path = _with_action(tmp_path, 'link', _action('/URI', URI=TextStringObject(target)), where='annot')
    with pytest.raises(DocumentSecurityError, match='outside the web'):
        assert_pdf_safe(PdfReader(path))


@pytest.mark.parametrize('target', ['https://example.com/a?b=1', 'http://example.com',
                                    'mailto:team@example.com', '  HTTPS://Example.com/'])
def test_web_links_and_navigation_stay_usable(tmp_path, target):
    path = _with_action(tmp_path, 'web', _action('/URI', URI=TextStringObject(target)), where='annot')
    assert_pdf_safe(PdfReader(path))
    for kind, fields in (('/Named', {'N': NameObject('/NextPage')}), ('/ResetForm', {}), ('/Hide', {})):
        assert_pdf_safe(PdfReader(_with_action(tmp_path, kind[1:], _action(kind, **fields))))


def test_an_indirect_link_target_shared_with_a_safe_role_is_still_judged(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    target = writer._add_object(TextStringObject('file:///etc/passwd'))
    # The same indirect string is first reached under a harmless role, then as /URI.
    writer._root_object[NameObject('/Lang')] = target
    writer._root_object[NameObject('/OpenAction')] = _action('/URI', URI=target)
    path = tmp_path / 'shared.pdf'
    writer.write(path)
    with pytest.raises(DocumentSecurityError, match='outside the web'):
        assert_pdf_safe(PdfReader(path))


def test_xfa_and_untyped_embedded_files_are_refused(tmp_path):
    from pypdf.generic import StreamObject

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer._root_object[NameObject('/AcroForm')] = DictionaryObject({
        NameObject('/Fields'): ArrayObject(), NameObject('/XFA'): ArrayObject(),
    })
    xfa = tmp_path / 'xfa.pdf'
    writer.write(xfa)
    with pytest.raises(DocumentSecurityError):
        assert_pdf_safe(PdfReader(xfa))
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    stream = StreamObject()
    stream.set_data(b'inert attachment')
    filespec = DictionaryObject({
        NameObject('/Type'): NameObject('/Filespec'),
        NameObject('/EF'): DictionaryObject({NameObject('/F'): writer._add_object(stream)}),
    })
    page[NameObject('/Annots')] = ArrayObject([writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject('/FileAttachment'),
        NameObject('/FS'): filespec,
    }))])
    untyped = tmp_path / 'untyped.pdf'
    writer.write(untyped)
    stored = PdfReader(untyped).pages[0]['/Annots'][0].get_object()['/FS']['/EF']['/F'].get_object()
    assert '/Type' not in stored
    with pytest.raises(DocumentSecurityError):
        assert_pdf_safe(PdfReader(untyped))


def test_the_original_policy_missed_these_shapes():
    # Negative control: the action and key lists before 2026-10-13.
    old_actions = {'/JavaScript', '/JS', '/Launch', '/RichMedia', '/GoToE'}
    old_keys = {'/JS', '/JavaScript', '/Launch', '/RichMedia', '/EmbeddedFiles'}
    for kind in ('/GoToR', '/SubmitForm', '/ImportData', '/Rendition'):
        assert kind not in old_actions
    assert '/XFA' not in old_keys and '/EF' not in old_keys


def test_a_rendered_pdf_is_checked_before_it_leaves(tmp_path):
    from kazma_core.documents.renderer_worker import WorkerError as RendererWorkerError
    from kazma_core.documents.renderer_worker import _assert_rendered_pdf_safe

    safe = _with_action(tmp_path, 'safe', _action('/GoTo', D=ArrayObject([NumberObject(0), NameObject('/Fit')])))
    _assert_rendered_pdf_safe(safe)
    assert safe.exists()
    active = _with_action(tmp_path, 'active', _action('/JavaScript', JS=TextStringObject('/* inert */')))
    with pytest.raises(RendererWorkerError) as error:
        _assert_rendered_pdf_safe(active)
    assert error.value.code == 'unsafe_document' and not active.exists()
    broken = tmp_path / 'broken.pdf'
    broken.write_bytes(b'%PDF-1.7\nnot a pdf body')
    with pytest.raises(RendererWorkerError):
        _assert_rendered_pdf_safe(broken)
    assert not broken.exists()


@pytest.mark.parametrize('operation', ['pdf:info', 'pdf:merge', 'pdf:split', 'pdf:fill-form', 'pdf:redact'])
def test_every_pdf_operation_refuses_active_content(tmp_path, operation):
    # The call-site matrix: the real service and worker, no bypass per operation.
    from kazma_core.documents.operations import OperationScope
    from kazma_core.documents.service import DocumentService

    active = _pdf(tmp_path / 'active.pdf', 'page', indirect=True)
    service = DocumentService(config=DocumentConfig(storage_root=tmp_path / 'store', ocr_enabled=False))
    parameters = {'pdf:split': {'start_page': 1, 'end_page': 1}, 'pdf:fill-form': {'fields': {}},
                  'pdf:redact': {'terms': ['x']}}.get(operation)
    sources = (active, active) if operation == 'pdf:merge' else (active,)
    result = service.operations.pdf_operation(
        operation, sources, scope=OperationScope('t', 'w', 'a'), output_name=None,
        export_dir=None, parameters=parameters,
    )
    assert not result.ok
    assert result.code == 'unsafe_document', (operation, result.code, result.message)


async def test_intake_refuses_active_content_through_the_service(tmp_path):
    from kazma_core.documents.service import DocumentService

    service = DocumentService(config=DocumentConfig(storage_root=tmp_path / 'store', ocr_enabled=False))
    for location in ('catalog', 'annotation', 'chain'):
        path = _pdf(tmp_path / f'{location}.pdf', location, indirect=True)
        # The worker boundary carries the error by code, not by class.
        with pytest.raises(DocumentParseError) as inspected:
            service.inspect_path(path)
        assert inspected.value.code == 'unsafe_document'
        with pytest.raises(DocumentParseError) as parsed:
            await service.parse_path(path, tenant_id='t', workspace_id='w', actor_id='a')
        assert parsed.value.code == 'unsafe_document'
