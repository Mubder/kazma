"""Documents follow the space: the page, chat and the agent see the same
documents within a space, and none across (Spaces S1-12).

Replaces ``tests/test_document_operator_principal.py`` (DOC-A3, 2026-10-13),
whose one stand-in principal, ``operator``, every surface of a
single-operator install acted as. Now a document's tenant is its space and its
owner is the account that ingested it; every surface asks
``documents.principal.surface_scope`` and acts as the scope's asker:

* one operator, Spaces not enforced: the owner's account in ``default``; the
  documents an install made before (owned by ``operator``, ``auto-cookie``,
  ``agent``) are the owner's through an audited grant, with no step to run;
* platform users, Spaces not enforced: as before -- the page acts as each
  user's name, chat and the agent refuse (D1);
* Spaces enforced: the asker in the request's or turn's space, members of one
  space share its documents, and nothing crosses spaces.

Most documents here are seeded the way a finished intake leaves them
(``tests/_documents_seed.py``): what is checked is who reads them. One real
intake per surface that makes documents (the page's upload, the agent's
import) runs the sandboxed parser.

Negative controls: the old ``agent`` principal cannot read a page upload; a
member of a space with no grant cannot read another member's document; a
literal actor in a surface is caught by the source gate.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import subprocess
import types
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.config_store import get_config_store
from kazma_core.documents.ingestion import set_ingestion_service
from kazma_core.documents.principal import (
    MULTI_USER_REFUSAL,
    NO_SPACE_REFUSAL,
    DocumentsRefused,
    surface_scope,
)
from kazma_core.security import platform_rbac
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._documents_seed import seed_document, seeded_service
from tests._space_matrix import Matrix

ROOT = Path(__file__).resolve().parents[1]

#: How the shared secret signs in: the owner's own entry (``space_binding``).
OWNER_PRINCIPAL = {"username": "operator", "role": "admin", "source": "secret"}


@pytest.fixture
def service(tmp_path):
    """A private document store; no workers (documents are seeded ready)."""
    svc = seeded_service(tmp_path / "store")
    set_ingestion_service(svc)
    try:
        yield svc
    finally:
        set_ingestion_service(None)
        svc.close()


@pytest.fixture
async def working_service(service):
    """The same store with its workers running, for a real intake."""
    await service.start_workers()
    try:
        yield service
    finally:
        await service.stop_workers()


@pytest.fixture
def no_people():
    """No identity registry before or after: one operator."""
    get_config_store().delete(identity.REGISTRY_KEY)
    yield
    get_config_store().delete(identity.REGISTRY_KEY)


@pytest.fixture
def tools(service, monkeypatch):
    from kazma_skills.native.document_platform import tools as module

    async def ensure():
        return service

    monkeypatch.setattr(module, "_ensure_service", ensure)
    monkeypatch.setattr(module, "_workspace", lambda: "w")
    return module


def _web(service, monkeypatch, principal) -> TestClient:
    """The real Documents router behind the real tenant middleware, signed in
    as *principal*."""
    from kazma_ui import auth, documents_api

    monkeypatch.setattr(documents_api, "_active_workspace_id", lambda: "w")
    monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())
    app.state.documents = service
    app.include_router(documents_api.create_documents_router())
    return TestClient(app)


async def _ready(service, tenant: str, job_id, actor: str) -> None:
    for _ in range(1200):
        status = service.job_status(tenant_id=tenant, job_id=job_id, actor_id=actor)
        assert status is not None, "the job is not readable by the actor that made it"
        if status["state"] in {"ready", "rejected", "dead_letter", "cancelled"}:
            assert status["state"] == "ready", status["state"]
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


def _content(client: TestClient, document_id: str, headers: dict[str, str] | None = None):
    return client.get(f"/api/documents/{document_id}/content", headers=headers or {})


def _listed(client: TestClient, headers: dict[str, str] | None = None) -> list[str]:
    answer = client.get("/api/documents", headers=headers or {})
    assert answer.status_code == 200, answer.text
    return [row["document_id"] for row in answer.json()["documents"]]


def _person(name: str) -> tuple[dict, str]:
    """A local user (the older multi-user mode's): its session principal and
    its account."""
    from kazma_ui import space_binding

    user = platform_rbac.create_local_user(name, secrets.token_urlsafe(16))
    principal = {"username": name, "role": "operator", "user_id": user.user_id,
                 "actor": "login", "source": "session"}
    return principal, space_binding.account_of(principal)


def _marker(word: str) -> str:
    return f"{word}{uuid.uuid4().hex}"


# ── one operator, Spaces not enforced: the owner's account ──────────────────


async def test_a_page_upload_is_read_by_chat_and_the_agent(working_service, tools, monkeypatch, no_people):
    """One real intake through the page, then chat and the agent read it."""
    service = working_service
    marker = _marker("pageupload")
    with _web(service, monkeypatch, OWNER_PRINCIPAL) as client:
        uploaded = client.post("/api/documents", content=marker.encode(),
                               headers={"X-Document-Filename": "note.txt"})
    assert uploaded.status_code == 200, uploaded.text
    document_id, job_id = uploaded.json()["document_id"], uploaded.json()["job_id"]
    await _ready(service, OWNER_SPACE_ID, job_id, OWNER_ACCOUNT_ID)

    record = service.repository.get_document(tenant_id=OWNER_SPACE_ID, document_id=document_id)
    assert record.owner_id == OWNER_ACCOUNT_ID, "the owner is the account, not a stand-in"
    assert marker in await _chat(f"/documents read {document_id}")
    assert marker in await tools.document_read(document_id)


async def test_an_agent_import_is_the_owners_on_the_page(service, tools, monkeypatch, tmp_path, no_people):
    marker = _marker("agentimport")
    source = tmp_path / "agent-note.txt"
    source.write_text(marker, encoding="utf-8")
    monkeypatch.setattr(tools, "_resolve_workspace_file", lambda path: (source, None))
    monkeypatch.setattr(tools, "_INGEST_WAIT_SECONDS", 0.1)  # no workers: the intake only
    reply = await tools.document_import(str(source))
    document_id = re.search(r"document_id: (\S+)", reply).group(1)

    record = service.repository.get_document(tenant_id=OWNER_SPACE_ID, document_id=document_id)
    assert record.owner_id == OWNER_ACCOUNT_ID
    with _web(service, monkeypatch, OWNER_PRINCIPAL) as client:
        assert document_id in _listed(client)
    assert document_id in await _chat("/documents list")


async def test_page_chat_and_agent_read_the_same_documents(service, tools, monkeypatch, no_people):
    marker = _marker("same")
    seeded = seed_document(service, tenant_id=OWNER_SPACE_ID, owner=OWNER_ACCOUNT_ID, marker=marker)
    with _web(service, monkeypatch, OWNER_PRINCIPAL) as client:
        page = _content(client, seeded.document_id)
    assert page.status_code == 200 and marker in page.text
    assert marker in await _chat(f"/documents read {seeded.document_id}")
    assert marker in await tools.document_read(seeded.document_id)


@pytest.mark.parametrize("older_owner", ["operator", "auto-cookie", "agent"])
async def test_documents_made_before_spaces_stay_the_owners(service, tools, monkeypatch, no_people, older_owner):
    """An install's documents from before Spaces keep working: no step to run."""
    marker = _marker("older")
    old = seed_document(service, tenant_id=OWNER_SPACE_ID, owner=older_owner, marker=marker)

    assert marker in await tools.document_read(old.document_id)
    with _web(service, monkeypatch, OWNER_PRINCIPAL) as client:
        assert old.document_id in _listed(client)
        assert marker in _content(client, old.document_id).text
    assert old.document_id in await _chat("/documents list")
    assert service.job_status(tenant_id=OWNER_SPACE_ID, job_id=old.job_id, actor_id=OWNER_ACCOUNT_ID)

    record = service.repository.get_document(tenant_id=OWNER_SPACE_ID, document_id=old.document_id)
    assert record.owner_id == older_owner, "ownership is never changed"
    events = service.audit.list_events(tenant_id=OWNER_SPACE_ID, document_id=old.document_id)["events"]
    assert [e["action"] for e in events].count("grant_member") == 1, "the grant is audited, once"
    assert service.grant_member_access(tenant_id=OWNER_SPACE_ID, member=OWNER_ACCOUNT_ID, force=True) == 0


async def test_control_the_old_agent_principal_cannot_read_the_owners_document(service, tools, monkeypatch, no_people):
    # Negative control: the principal chat and the agent resolved before
    # DOC-A3 -- the turn's tenant and the fixed name ``agent``, granted
    # nothing. The tool's own scope path runs (``_scope`` -> ``_document_scope``
    # -> the principal); only the resolution is the old one.
    from kazma_core.documents import principal

    marker = _marker("owners")
    seeded = seed_document(service, tenant_id=OWNER_SPACE_ID, owner=OWNER_ACCOUNT_ID, marker=marker)
    resolved: list[principal.DocumentScope] = []

    def old_resolution(_service, *, page_user=None):
        acting = principal.DocumentScope(tenant_id=scope.require_space_id(), actor_id="agent")
        resolved.append(acting)
        return acting

    monkeypatch.setattr(principal, "surface_scope", old_resolution)
    reply = await tools.document_read(seeded.document_id)
    assert resolved == [principal.DocumentScope(OWNER_SPACE_ID, "agent")], "the tool resolved its scope"
    assert reply.startswith("Error") and marker not in reply


async def test_control_without_the_grant_the_owner_cannot_read_older_documents(service, tools, monkeypatch, no_people):
    # Negative control: a single-account install's documents from before
    # Spaces are owned by ``operator``; the owner's account reads them only
    # through grant_member_access.
    marker = _marker("ungranted")
    old = seed_document(service, tenant_id=OWNER_SPACE_ID, owner="operator", marker=marker)
    monkeypatch.setattr(service, "grant_member_access", lambda **_kw: 0)
    reply = await tools.document_read(old.document_id)
    assert reply.startswith("Error") and marker not in reply
    assert old.document_id not in await _chat("/documents list")
    with _web(service, monkeypatch, OWNER_PRINCIPAL) as client:
        assert old.document_id not in _listed(client)


async def test_the_agents_first_call_grants_before_the_service_is_installed(service, tools, monkeypatch, no_people):
    """A chat turn during boot, or a CLI process: no service is installed yet
    when the tool starts. The tool builds it first (``_ensure_service``) and
    grants through it, so the first call already reads the older documents
    (the first version granted through ``peek_ingestion_service()``, None
    then, and failed that call)."""
    marker = _marker("firstcall")
    old = seed_document(service, tenant_id=OWNER_SPACE_ID, owner="operator", marker=marker)
    set_ingestion_service(None)  # nothing installed: the tool builds the service
    assert marker in await tools.document_read(old.document_id)


async def test_control_granting_through_the_installed_service_fails_the_first_call(
    service, tools, monkeypatch, no_people,
):
    # Negative control: the first version's scope, granted through the
    # service installed so far (none during boot).
    from kazma_core.documents.ingestion import peek_ingestion_service

    marker = _marker("beforeboot")
    old = seed_document(service, tenant_id=OWNER_SPACE_ID, owner="operator", marker=marker)
    set_ingestion_service(None)
    real = tools._document_scope
    monkeypatch.setattr(tools, "_document_scope", lambda _built: real(peek_ingestion_service()))
    reply = await tools.document_read(old.document_id)
    assert reply.startswith("Error") and marker not in reply


# ── a stored retired memory mode: chat reads its own tenant ─────────────────


@pytest.fixture
def per_platform():
    """An install from before Spaces that chose per-platform memory."""
    store = get_config_store()
    store.set("memory.tenant_mode", "per_platform")
    try:
        yield
    finally:
        store.delete("memory.tenant_mode")


async def _chat_from(platform: str, text: str) -> str:
    from kazma_gateway.agent_handler.commands import _try_documents_command

    msg = types.SimpleNamespace(platform=platform, sender_id=f"{platform}:1", context_metadata={}, text=text)
    manager = _Manager()
    assert await _try_documents_command(msg, _Store(), manager, "thread") is True
    return manager.sent[-1]


async def test_with_a_retired_memory_mode_chat_reads_the_documents_its_turns_made(
    service, tools, per_platform, no_people,
):
    """A Telegram chat's documents sit under ``telegram``, where its turns put
    them (the tool worker binds the turn's tenant): chat ``/documents`` and the
    agent in that chat read them, as before Spaces, until Spaces is enforced
    or the owner presses "Use one memory" (S1-09's rule)."""
    marker = _marker("telegramdoc")
    in_chat = seed_document(service, tenant_id="telegram", owner="operator", marker=marker)
    on_page = seed_document(service, tenant_id=OWNER_SPACE_ID, owner="operator", marker=_marker("page"))

    listed = await _chat_from("telegram", "/documents list")
    assert in_chat.document_id in listed and on_page.document_id not in listed
    assert marker in await _chat_from("telegram", f"/documents read {in_chat.document_id}")
    from kazma_core.tenant_context import tenant_scope

    with tenant_scope("telegram"):  # what the tool worker binds from the turn's state
        assert marker in await tools.document_read(in_chat.document_id)
    # The files stay where they were written: the root, as before Spaces.
    assert (service.storage.root / "originals" / "sha256" / in_chat.sha256[:2]
            / in_chat.sha256[2:4] / in_chat.sha256).is_file()
    assert not (service.storage.root / "spaces").exists()


async def test_control_the_first_chat_rule_loses_the_chats_documents(service, per_platform, no_people):
    # Negative control: the first version's chat read the bound space, which
    # a chat-app message has none of: ``default``.
    in_chat = seed_document(service, tenant_id="telegram", owner="operator", marker=_marker("lost"))
    acting = surface_scope(service)
    assert acting.tenant_id == OWNER_SPACE_ID
    listed = service.list_documents(tenant_id=acting.tenant_id, actor_id=acting.actor_id)
    assert in_chat.document_id not in [row["document_id"] for row in listed]


# ── platform users, Spaces not enforced: as before ──────────────────────────


async def test_with_platform_users_and_no_spaces_chat_and_the_agent_refuse(
    service, tools, monkeypatch, no_people,
):
    principal, _account = _person(f"layla{secrets.token_hex(3)}")
    assert platform_rbac.multi_user_enabled() and not scope.spaces_enforced()
    assert MULTI_USER_REFUSAL in await tools.document_read("anything")
    assert MULTI_USER_REFUSAL in await _chat("/documents list")

    # The page acts as each user's name, as before Spaces, and shares nothing.
    mine = seed_document(service, tenant_id=OWNER_SPACE_ID, owner=principal["username"], marker=_marker("mine"))
    owners = seed_document(service, tenant_id=OWNER_SPACE_ID, owner=OWNER_ACCOUNT_ID, marker=_marker("owner"))
    with _web(service, monkeypatch, principal) as client:
        listed = _listed(client)
        assert mine.document_id in listed and owners.document_id not in listed
        assert _content(client, owners.document_id).status_code in (403, 404)


# ── Spaces enforced: the asker, in its space ────────────────────────────────


@pytest.fixture
def spaces(monkeypatch):
    """Two people, each in a space of their own, on an install that enforces
    Spaces."""
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setattr(scope, "_enforced", True)
    with Matrix() as made:
        yield made


async def test_enforced_page_chat_and_agent_share_a_space_and_never_cross(service, tools, monkeypatch, spaces):
    marks = spaces.plant("document")
    in_a = seed_document(service, tenant_id=Matrix.SPACE_A, owner=spaces.account_a, marker=marks.a)
    in_b = seed_document(service, tenant_id=Matrix.SPACE_B, owner=spaces.account_b, marker=marks.b)

    # Person A's three surfaces, in space A: A's document, never B's.
    with _web(service, monkeypatch, spaces.principal_a) as client:
        assert _listed(client, spaces.headers_a) == [in_a.document_id]
        own = _content(client, in_a.document_id, spaces.headers_a)
        assert own.status_code == 200 and marks.a in own.text
        other = _content(client, in_b.document_id, spaces.headers_a)
        assert other.status_code in (403, 404) and marks.b not in other.text
        job = client.get(f"/api/documents/jobs/{in_b.job_id}", headers=spaces.headers_a)
        assert job.status_code == 404 and in_b.document_id not in job.text
        # Naming B's space is refused at the boundary.
        refused = client.get("/api/documents", headers=spaces.headers_b)
        assert refused.status_code == 403 and refused.json()["code"] == "not_a_member"
    with spaces.as_a():
        assert marks.a in await _chat(f"/documents read {in_a.document_id}")
        assert marks.a in await tools.document_read(in_a.document_id)
        listed = await _chat("/documents list")
        assert in_a.document_id in listed and in_b.document_id not in listed
        for reply in (await _chat(f"/documents read {in_b.document_id}"),
                      await tools.document_read(in_b.document_id),
                      await tools.document_status(job_id=in_b.job_id)):
            assert marks.b not in reply, reply
            assert "ready" not in reply.lower(), reply
    # B's document is unchanged, and B reads it in B's space.
    with spaces.as_b():
        assert marks.b in await tools.document_read(in_b.document_id)


async def test_enforced_a_page_upload_is_the_space_and_the_account(service, monkeypatch, spaces):
    """One real intake as person A: tenant A, owner A's account."""
    marker = spaces.plant("upload").a
    with _web(service, monkeypatch, spaces.principal_a) as client:
        uploaded = client.post("/api/documents", content=marker.encode(),
                               headers={"X-Document-Filename": "note.txt", **spaces.headers_a})
    assert uploaded.status_code == 200, uploaded.text
    document_id = uploaded.json()["document_id"]
    record = service.repository.get_document(tenant_id=Matrix.SPACE_A, document_id=document_id)
    assert record is not None and record.owner_id == spaces.account_a
    assert service.repository.get_document(tenant_id=Matrix.SPACE_B, document_id=document_id) is None


async def test_enforced_members_of_one_space_share_its_documents(service, tools, monkeypatch, spaces):
    principal_c, account_c = _person(f"matrix-person-c{secrets.token_hex(2)}")
    identity.add_member(Matrix.SPACE_A, account_c)
    marks = spaces.plant("shared")
    from_a = seed_document(service, tenant_id=Matrix.SPACE_A, owner=spaces.account_a, marker=marks.a)

    # C, the other member of A, reads A's document on the page and as the agent.
    with _web(service, monkeypatch, principal_c) as client:
        content = _content(client, from_a.document_id, spaces.headers_a)
        assert content.status_code == 200 and marks.a in content.text
    with scope.bind_scope(Matrix.SPACE_A, account_c):
        assert marks.a in await tools.document_read(from_a.document_id)
    # B, a member of another space, does not.
    with spaces.as_b():
        reply = await tools.document_read(from_a.document_id)
        assert marks.a not in reply and reply.startswith("Error")


async def test_control_without_the_grant_a_member_cannot_read_anothers_document(
    service, tools, monkeypatch, spaces,
):
    # Negative control: the space's members share through grant_member_access.
    _principal_c, account_c = _person(f"matrix-person-c{secrets.token_hex(2)}")
    identity.add_member(Matrix.SPACE_A, account_c)
    marks = spaces.plant("unshared")
    from_a = seed_document(service, tenant_id=Matrix.SPACE_A, owner=spaces.account_a, marker=marks.a)
    monkeypatch.setattr(service, "grant_member_access", lambda **_kw: 0)
    with scope.bind_scope(Matrix.SPACE_A, account_c):
        reply = await tools.document_read(from_a.document_id)
    assert marks.a not in reply and reply.startswith("Error")


async def test_enforced_with_no_space_bound_chat_and_the_agent_refuse(service, tools, spaces):
    assert NO_SPACE_REFUSAL in await tools.document_read("anything")
    assert NO_SPACE_REFUSAL in await _chat("/documents list")


async def test_enforced_a_scope_whose_asker_is_not_a_member_reads_nothing(service, tools, spaces):
    # A scope that names a space its asker is not in: the request boundary
    # refuses it, and the surface refuses it again.
    marks = spaces.plant("guarded")
    in_b = seed_document(service, tenant_id=Matrix.SPACE_B, owner=spaces.account_b, marker=marks.b)
    with scope.bind_scope(Matrix.SPACE_B, spaces.account_a):
        reply = await tools.document_read(in_b.document_id)
        assert NO_SPACE_REFUSAL in reply and marks.b not in reply
        with pytest.raises(DocumentsRefused):
            surface_scope(service)


async def test_enforced_platform_users_no_longer_refuse(service, tools, spaces):
    """Spaces lifts D1's refusal: the asker is known on every surface."""
    assert platform_rbac.multi_user_enabled()
    with spaces.as_a():
        assert MULTI_USER_REFUSAL not in await _chat("/documents list")
        assert MULTI_USER_REFUSAL not in await tools.document_status()


# ── the space matrix's Documents cases catch a crossing ──────────────────────


@pytest.mark.parametrize("target", [
    "GET /api/documents", "GET /api/documents/{document_id}/content",
    "tool:document_read", "tool:document_convert",
])
def test_control_the_matrix_cases_catch_a_surface_that_reads_another_space(monkeypatch, target):
    """Negative control of ``tests/_document_space_cases.py``: a surface that
    acts in space B whatever space it was asked for fails A's cases."""
    import tests._document_space_cases  # noqa: F401  (registers the cases)
    from kazma_core.documents import principal
    from tests._space_matrix import CASES

    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as matrix:
        def crossing(service, *, page_user=None):
            return principal.DocumentScope(tenant_id=Matrix.SPACE_B, actor_id=matrix.account_b)

        monkeypatch.setattr(principal, "surface_scope", crossing)
        with pytest.raises(AssertionError):
            CASES[target].run(matrix)


# ── no document surface names its own principal ─────────────────────────────

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
    assert not found, f"resolve the actor through documents.principal.surface_scope: {found}"


def test_the_gate_sees_a_literal_actor():
    # Negative control: the chat command's old line.
    assert _literal_actors('docs = svc.list_documents(tenant_id=tenant, actor_id="agent")') == ['actor_id="agent"']
