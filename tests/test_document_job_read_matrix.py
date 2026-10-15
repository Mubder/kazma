"""Every surface that reads a document job checks the caller's document ACL.

2026-10-13 (F05). The core gate skipped its check when the actor was None,
the agent's cancel tool passed none (it could cancel any job in the tenant
and report its state), the Web routes answered 403 for a job the caller
could not read but 404 for an unknown one, and the operations audit trail
listed every document, job and actor of the tenant to any caller.

Now: an actor is a required keyword (``SYSTEM_ACTOR`` for code that has
authorized the read itself), a missing one is refused, and an unreadable job
reads exactly like a job that does not exist, on every transport. The chat
surfaces -- the agent's tools and the gateway's ``/documents`` -- act as the
``agent`` principal (one operator per install); they see what that principal
may read and nothing else.
"""

from __future__ import annotations

import inspect
import io
import types
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import SYSTEM_ACTOR, DocumentIngestionService
from kazma_core.documents.repository import DocumentAccessError


@pytest.fixture
def service(tmp_path):
    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path / "store", security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
    ))
    yield svc
    svc.close()


def _upload(svc, actor: str, tenant: str = "t", body: bytes | None = None):
    return svc.ingest_stream(
        io.BytesIO(body or f"owned by {actor} {uuid.uuid4().hex}".encode()),
        filename="note.txt", tenant_id=tenant, actor_id=actor, workspace_id="w",
    )


# ── the class gate: no job read without a named caller ─────────────────────

def _job_readers(cls) -> dict[str, inspect.Signature]:
    """Public methods that read or act on a job, found from the class."""
    return {
        name: inspect.signature(fn)
        for name, fn in inspect.getmembers(cls, inspect.isfunction)
        if not name.startswith("_") and "job" in name and "actor_id" in inspect.signature(fn).parameters
    }


def _actor_is_required(sig: inspect.Signature) -> bool:
    param = sig.parameters["actor_id"]
    return param.kind is inspect.Parameter.KEYWORD_ONLY and param.default is inspect.Parameter.empty


def test_every_job_reader_requires_its_caller():
    readers = _job_readers(DocumentIngestionService)
    assert {"job_status", "job_events", "jobs_for_document", "cancel_job", "retry_job"} <= set(readers)
    optional = [name for name, sig in readers.items() if not _actor_is_required(sig)]
    assert optional == [], f"job readers with an optional actor: {optional}"


def test_the_gate_sees_an_optional_actor():
    # Negative control: the old signature shape is caught.
    class Old:
        def job_status(self, *, tenant_id, job_id, actor_id=None):
            return None

    assert not _actor_is_required(_job_readers(Old)["job_status"])


@pytest.mark.parametrize("method", ["job_status", "job_events", "cancel_job", "retry_job"])
@pytest.mark.parametrize("missing", [None, "", "   "])
def test_a_missing_actor_is_refused_not_unchecked(service, method, missing):
    job = _upload(service, "alice")
    with pytest.raises(DocumentAccessError):
        getattr(service, method)(tenant_id="t", job_id=job.job_id, actor_id=missing)


@pytest.mark.parametrize("method", ["job_status", "job_events", "cancel_job", "retry_job"])
def test_a_missing_actor_is_refused_for_an_unknown_job_too(service, method):
    """A caller with no name was answered "unknown" (None) when the job did
    not exist, and refused only when it did (Q04 defensive check C8,
    2026-10-15). It is refused either way now."""
    with pytest.raises(DocumentAccessError):
        getattr(service, method)(tenant_id="t", job_id=str(uuid.uuid4()), actor_id="")


def test_cancelling_needs_write_permission_like_a_retry(service):
    """Read access shows a job; changing its ingestion needs write (Q04
    defensive check C8, 2026-10-15: a reader could cancel)."""
    job = _upload(service, "alice")
    service.repository.grant_access(tenant_id="t", document_id=job.document_id,
                                    actor_id="alice", principal_id="bob", permission="read")
    assert service.job_status(tenant_id="t", job_id=job.job_id, actor_id="bob")
    with pytest.raises(DocumentAccessError):
        service.cancel_job(tenant_id="t", job_id=job.job_id, actor_id="bob")
    # The owner still can.
    assert service.cancel_job(tenant_id="t", job_id=job.job_id, actor_id="alice")


def test_the_system_actor_is_not_a_string_a_request_can_send(service):
    job = _upload(service, "alice")
    assert service.job_status(tenant_id="t", job_id=job.job_id, actor_id=SYSTEM_ACTOR)
    assert service.job_status(tenant_id="t", job_id=job.job_id, actor_id="SYSTEM_ACTOR") is None
    assert not isinstance(SYSTEM_ACTOR, str)


# ── Web: an unreadable job answers exactly like an unknown one ─────────────

def _client(service, monkeypatch, *, admin: str = "ok"):
    from kazma_ui import auth, documents_api

    async def scope(request):
        return "t", request.headers["x-test-actor"], "w"

    monkeypatch.setattr(documents_api, "_resolve_scope", scope)
    monkeypatch.setattr(auth, "admin_decision", lambda request: admin)
    app = FastAPI()
    app.state.documents = service
    app.include_router(documents_api.create_documents_router())
    return TestClient(app)


@pytest.mark.parametrize("route", [
    ("get", "/api/documents/jobs/{id}"),
    ("get", "/api/documents/jobs/{id}/events"),
    ("post", "/api/documents/jobs/{id}/cancel"),
    ("post", "/api/documents/jobs/{id}/retry"),
])
def test_web_job_routes_hide_a_job_the_caller_cannot_read(service, monkeypatch, route):
    job = _upload(service, "alice")
    method, template = route
    with _client(service, monkeypatch) as client:
        def call(job_id, actor):
            return getattr(client, method)(template.format(id=job_id), headers={"x-test-actor": actor})

        refused = call(job.job_id, "bob")
        unknown = call(uuid.uuid4(), "bob")
        assert refused.status_code == unknown.status_code == 404
        assert refused.json() == unknown.json()
        assert str(job.job_id) not in refused.text
        if method == "get":
            assert call(job.job_id, "alice").status_code == 200
    # Bob's cancel changed nothing.
    assert not service.job_status(tenant_id="t", job_id=job.job_id, actor_id="alice")["cancel_requested"]


def test_the_operations_audit_trail_is_an_operator_view(service, monkeypatch):
    _upload(service, "alice")
    with _client(service, monkeypatch, admin="forbidden") as client:
        refused = client.get("/api/documents/ops/audit", headers={"x-test-actor": "bob"})
        assert refused.status_code == 403
        assert "alice" not in refused.text
    with _client(service, monkeypatch, admin="ok") as client:
        allowed = client.get("/api/documents/ops/audit", headers={"x-test-actor": "operator"})
        assert allowed.status_code == 200 and allowed.json()["events"]


# ── the agent's tools ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_agent_cancel_tool_checks_the_agents_access(service, monkeypatch):
    from kazma_skills.native.document_platform import tools

    async def ensure():
        return service

    monkeypatch.setattr(tools, "_ensure_service", ensure)
    monkeypatch.setattr(tools, "_operator_scope", lambda: ("t", "w", "agent"))
    someone_elses = _upload(service, "alice")
    refused = await tools.document_cancel(str(someone_elses.job_id))
    assert refused.startswith("Error") and "state" not in refused.lower()
    assert not service.job_status(
        tenant_id="t", job_id=someone_elses.job_id, actor_id="alice")["cancel_requested"]
    own = _upload(service, "agent")
    assert "Cancellation requested" in await tools.document_cancel(str(own.job_id))
    assert service.job_status(tenant_id="t", job_id=own.job_id, actor_id="agent")["cancel_requested"]


# ── the gateway's /documents status ────────────────────────────────────────

class _Manager:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, outbound) -> None:  # type: ignore[no-untyped-def]
        self.sent.append(outbound.text)


class _Store:
    async def get(self, _tid: str):
        return None


@pytest.mark.asyncio
async def test_gateway_status_reads_as_the_operator(service):
    # Chat acts as the install's operator (DOC-A3): a job of its own tenant is
    # shown whoever ingested it; another tenant's reads exactly like unknown.
    from kazma_core.documents.ingestion import set_ingestion_service
    from kazma_core.memory.config import resolve_tenant_id
    from kazma_gateway.agent_handler.commands import _try_documents_command

    msg = types.SimpleNamespace(platform="slack", sender_id="U1", context_metadata={}, text="")
    tenant = resolve_tenant_id("slack", sender_id="U1", prefer_context=True)
    hidden = _upload(service, "alice", tenant=f"{tenant}-other")
    visible = _upload(service, "alice", tenant=tenant)
    set_ingestion_service(service)
    try:
        for job, shown in ((hidden, False), (visible, True)):
            manager = _Manager()
            msg.text = f"/documents status {job.job_id}"
            assert await _try_documents_command(msg, _Store(), manager, "thread") is True
            reply = manager.sent[-1]
            if shown:
                assert "state:" in reply
            else:
                assert "Not found" in reply and "state:" not in reply
    finally:
        set_ingestion_service(None)
