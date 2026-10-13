"""The Web page, chat and the agent see the same documents (one operator).

2026-10-13 (DOC-A3, found by the Documents documentation review). A document
belongs to whoever ingested it. The Web page acted as the signed-in name and
chat ``/documents`` and the agent's tools as ``agent``, so a document uploaded
on the page was invisible to chat and the agent, and theirs to the page.
Now every surface acts as ``documents.principal.OPERATOR`` on a single-operator
install, documents ingested under an older name get the operator's ACL row
(granted by their owner, audited), and with platform users chat and the agent
refuse (D1). Negative controls: the old ``agent`` principal cannot read a page
upload; a literal actor in a surface is caught by the static gate.
"""

from __future__ import annotations

import io
import re
import subprocess
import types
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService, set_ingestion_service
from kazma_core.documents.principal import MULTI_USER_REFUSAL, OPERATOR

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def service(tmp_path):
    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path / "store", security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
        worker_concurrency=1, worker_lease_seconds=5, worker_heartbeat_seconds=1,
    ))
    set_ingestion_service(svc)
    await svc.start_workers()
    try:
        yield svc
    finally:
        await svc.stop_workers()
        set_ingestion_service(None)
        svc.close()


def _web(service, monkeypatch):
    from kazma_ui import documents_api

    monkeypatch.setattr(documents_api, "_active_workspace_id", lambda: "w")
    app = FastAPI()
    app.state.documents = service
    app.include_router(documents_api.create_documents_router())
    return TestClient(app)


async def _ready(service, tenant: str, job_id) -> None:
    import asyncio

    for _ in range(400):
        state = service.job_status(tenant_id=tenant, job_id=job_id, actor_id=OPERATOR)["state"]
        if state in {"ready", "rejected", "dead_letter", "cancelled"}:
            assert state == "ready", state
            return
        await asyncio.sleep(0.05)
    raise AssertionError("document never became ready")


class _Manager:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, outbound) -> None:  # type: ignore[no-untyped-def]
        self.sent.append(outbound.text)


class _Store:
    async def get(self, _tid: str):
        return None


async def _chat(text: str) -> str:
    from kazma_gateway.agent_handler.commands import _try_documents_command

    msg = types.SimpleNamespace(platform="web", sender_id="U1", context_metadata={}, text=text)
    manager = _Manager()
    assert await _try_documents_command(msg, _Store(), manager, "thread") is True
    return manager.sent[-1]


def _chat_tenant() -> str:
    from kazma_core.memory.config import resolve_tenant_id

    return resolve_tenant_id("web", sender_id="U1", prefer_context=True)


@pytest.fixture
def tools(service, monkeypatch):
    from kazma_skills.native.document_platform import tools as module

    async def ensure():
        return service

    monkeypatch.setattr(module, "_ensure_service", ensure)
    return module


@pytest.mark.asyncio
async def test_a_page_upload_is_read_by_chat_and_the_agent(service, tools, monkeypatch):
    marker = f"page upload {uuid.uuid4().hex}"
    tenant = _chat_tenant()
    assert tenant == "default"  # the page's tenant on a single-operator install
    with _web(service, monkeypatch) as client:
        uploaded = client.post("/api/documents", content=marker.encode(),
                               headers={"X-Document-Filename": "note.txt"})
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    document_id, job_id = body["document_id"], body["job_id"]
    await _ready(service, tenant, job_id)

    assert marker in await _chat(f"/documents read {document_id}")
    monkeypatch.setattr(tools, "_tenant_and_workspace", lambda: (tenant, "w"))
    assert marker in await tools.document_read(document_id)


@pytest.mark.asyncio
async def test_an_agent_import_is_listed_and_read_on_the_page(service, tools, monkeypatch, tmp_path):
    marker = f"agent import {uuid.uuid4().hex}"
    source = tmp_path / "agent-note.txt"
    source.write_text(marker, encoding="utf-8")
    monkeypatch.setattr(tools, "_tenant_and_workspace", lambda: ("default", "w"))
    monkeypatch.setattr(tools, "_resolve_workspace_file", lambda path: (source, None))
    reply = await tools.document_import(str(source))
    document_id = re.search(r"document_id: (\S+)", reply).group(1)
    job_id = re.search(r"job_id: (\S+)", reply).group(1)
    await _ready(service, "default", job_id)

    with _web(service, monkeypatch) as client:
        ids = [row["document_id"] for row in client.get("/api/documents").json()["documents"]]
        assert document_id in ids
        content = client.get(f"/api/documents/{document_id}/content")
        assert content.status_code == 200 and marker in content.text


@pytest.mark.asyncio
async def test_a_document_ingested_under_an_older_name_is_granted_to_the_operator(service, tools, monkeypatch):
    marker = f"legacy {uuid.uuid4().hex}"
    old = service.ingest_stream(io.BytesIO(marker.encode()), filename="old.txt",
                                tenant_id="default", actor_id="auto-cookie", workspace_id="w")
    await _ready_as(service, "default", old.job_id, "auto-cookie")
    monkeypatch.setattr(tools, "_tenant_and_workspace", lambda: ("default", "w"))
    assert marker in await tools.document_read(str(old.document_id))
    record = service.repository.get_document(tenant_id="default", document_id=old.document_id)
    assert record.owner_id == "auto-cookie"  # ownership is never changed
    events = service.audit.list_events(tenant_id="default", document_id=str(old.document_id))["events"]
    assert any(e["action"] == "grant_operator" for e in events)
    assert service.grant_operator_access(tenant_id="default", force=True) == 0  # once


async def _ready_as(service, tenant, job_id, actor) -> None:
    import asyncio

    for _ in range(400):
        state = service.job_status(tenant_id=tenant, job_id=job_id, actor_id=actor)["state"]
        if state == "ready":
            return
        await asyncio.sleep(0.05)
    raise AssertionError("document never became ready")


@pytest.mark.asyncio
async def test_the_old_agent_principal_cannot_read_a_page_upload(service, tools, monkeypatch):
    # Negative control: the principal chat and the agent used before.
    marker = f"page {uuid.uuid4().hex}"
    page = service.ingest_stream(io.BytesIO(marker.encode()), filename="p.txt",
                                 tenant_id="default", actor_id=OPERATOR, workspace_id="w")
    await _ready(service, "default", page.job_id)
    monkeypatch.setattr(tools, "_operator_scope", lambda: ("default", "w", "agent"))
    reply = await tools.document_read(str(page.document_id))
    assert reply.startswith("Error") and marker not in reply


@pytest.mark.asyncio
async def test_with_platform_users_chat_and_the_agent_refuse(service, tools, monkeypatch):
    monkeypatch.setenv("KAZMA_MULTI_USER", "1")
    monkeypatch.setattr(tools, "_tenant_and_workspace", lambda: ("default", "w"))
    assert MULTI_USER_REFUSAL in await tools.document_read("anything")
    assert MULTI_USER_REFUSAL in await _chat("/documents list")


# ── no document surface names its own principal ────────────────────────────

_LITERAL_ACTOR = re.compile(r"actor_id\s*=\s*[\"'][^\"']+[\"']")
_SURFACES = ("kazma-ui/kazma_ui/", "kazma-gateway/kazma_gateway/", "kazma-skills/kazma_skills/",
             "kazma-tui/kazma_tui/", "kazma-cli/kazma_cli/")


def _literal_actors(source: str) -> list[str]:
    return _LITERAL_ACTOR.findall(source)


def test_no_surface_passes_a_literal_actor():
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "kazma-*/kazma_*/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    found = {}
    for rel in sorted(set(listed)):
        if rel.startswith(_SURFACES):
            hits = _literal_actors((ROOT / rel).read_text(encoding="utf-8"))
            if hits:
                found[rel] = hits
    assert not found, f"resolve the actor through documents.principal: {found}"


def test_the_gate_sees_a_literal_actor():
    # Negative control: the chat command's old line.
    assert _literal_actors('docs = svc.list_documents(tenant_id=tenant, actor_id="agent")') == ['actor_id="agent"']
