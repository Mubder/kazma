"""The space matrix's Documents cases (Spaces S1-12).

Every space route of ``/api/documents``, the Documents page and the agent's
document tools, driven as person A of space ``matrix-a`` against person B's
documents, jobs and artifacts in ``matrix-b``, on an install that enforces
Spaces. Each case checks that nothing of B is shown or changed, and that A's
own works. Registered in ``tests/_space_matrix.py``'s ``CASE_MODULES``.

The real Documents router runs behind the real tenant middleware (which
checks membership), on a private store whose documents are seeded ready
(``tests/_documents_seed.py``). Nothing launches a worker process: the
parser's inspection answers ``text/plain`` and the rendering and PDF engines
are a stub that records which space and asker reached it and answers a coded
failure, so a case sees an operation reach its engine for A's own document
and never for B's.

A document in a Knowledge library (index, unindex, the library search, and
their two tools) runs on a private Knowledge store with a library named
"docs" in each space (``knowledge_world``): Knowledge is per space since
S1-13, and a library of another space reads as none.
"""

from __future__ import annotations

import ast
import asyncio
import io
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.documents.ingestion import set_ingestion_service
from kazma_core.documents.sniff import SniffResult
from kazma_core.spaces import scope

from tests._documents_seed import Seeded, seed_document, seeded_service
from tests._space_matrix import Matrix, case

ITEM = "S1-12"
REPO = Path(__file__).resolve().parents[1]

#: The owner signing in with the install's secret: the admin, a member of
#: neither matrix space.
OWNER_PRINCIPAL = {"username": "operator", "role": "admin", "source": "secret"}


# ── the stubbed engines ─────────────────────────────────────────────────────


class _Failed:
    """A typed engine result that failed with :data:`_Engine.REACHED`."""

    ok = False
    message = "the matrix's engine stub answered"

    def __init__(self, code: str) -> None:
        self.code = code

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "message": self.message}


@dataclass
class _Engine:
    """The rendering and PDF engines: each call is recorded with the space and
    the asker it ran for, and answers a coded failure the routes pass on."""

    REACHED = "matrix_engine_reached"
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def install(self, patch: pytest.MonkeyPatch, service: Any) -> None:
        for op in ("convert", "pdf_info", "pdf_split", "pdf_fill_form", "redact", "pdf_merge", "generate"):
            patch.setattr(service, op, self._stub(op))
        patch.setattr(service, "inspect_path", lambda _path: SniffResult("text/plain", ".txt"))

    def _stub(self, op: str):
        async def stub(*_args: Any, **kwargs: Any) -> _Failed:
            self.calls.append((op, str(kwargs.get("tenant_id")), str(kwargs.get("actor_id"))))
            return _Failed(self.REACHED)

        return stub


# ── the world of one case ───────────────────────────────────────────────────


@dataclass
class World:
    """Two spaces with a ready document each, on a private store."""

    matrix: Matrix
    service: Any
    engine: _Engine
    a: Seeded
    b: Seeded
    mark_a: str
    mark_b: str
    patch: pytest.MonkeyPatch
    root: Path

    def seed_a(self, word: str) -> Seeded:
        """Another ready document of person A's, for a case that changes one."""
        return seed_document(self.service, tenant_id=Matrix.SPACE_A, owner=self.matrix.account_a,
                             marker=self.matrix.plant(word).a)

    @contextmanager
    def client(self, principal: dict[str, Any]) -> Iterator[TestClient]:
        """The real router behind the real tenant middleware, signed in as
        *principal*."""
        from kazma_ui import auth, documents_api

        with pytest.MonkeyPatch.context() as signed_in:
            signed_in.setattr(auth, "get_request_principal", lambda request: principal)
            app = FastAPI()
            app.middleware("http")(auth.create_tenant_middleware())
            app.state.documents = self.service
            app.include_router(documents_api.create_documents_router())
            with TestClient(app) as client:
                yield client

    @contextmanager
    def as_a(self) -> Iterator[TestClient]:
        with self.client(self.matrix.principal_a) as client:
            client.headers.update(self.matrix.headers_a)
            yield client

    def run(self, coroutine: Any) -> Any:
        """An agent tool's coroutine, run here."""
        return asyncio.run(coroutine)

    # ── the checks every case shares ─────────────────────────────────────

    def b_is_untouched(self) -> None:
        """B's document, version, content and job are as seeded."""
        record = self.service.repository.get_document(tenant_id=Matrix.SPACE_B, document_id=self.b.document_id)
        assert record is not None and record.deleted_at is None, "B's document was changed"
        assert record.owner_id == self.matrix.account_b
        content = self.service.get_content(tenant_id=Matrix.SPACE_B, actor_id=self.matrix.account_b,
                                           document_id=self.b.document_id)
        assert self.mark_b in content["text"]
        job = self.service.job_status(tenant_id=Matrix.SPACE_B, job_id=self.b.job_id,
                                      actor_id=self.matrix.account_b)
        assert job is not None and job["state"] == "received", job
        assert not self.service.list_document_artifacts(
            tenant_id=Matrix.SPACE_B, actor_id=self.matrix.account_b, document_id=self.b.document_id,
        ), "an artifact was made of B's document"

    def nothing_of_b(self, answer: Any) -> None:
        text = answer if isinstance(answer, str) else answer.text
        for value in (self.mark_b, self.b.version_id, self.b.sha256):
            assert value not in text, f"B's {value!r} was shown: {text[:300]}"

    def refused(self, response: Any) -> None:
        """An id of B's reads exactly like an id nobody holds."""
        assert response.status_code == 404, (response.status_code, response.text[:300])
        self.nothing_of_b(response)

    def engine_calls(self) -> int:
        return len(self.engine.calls)

    def engine_ran_for_a(self, since: int) -> None:
        new = self.engine.calls[since:]
        assert new, "A's own operation never reached its engine"
        assert all(t == Matrix.SPACE_A and who == self.matrix.account_a for _op, t, who in new), new


@contextmanager
def world(matrix: Matrix) -> Iterator[World]:
    """Spaces enforced, two seeded documents, the engines stubbed."""
    from kazma_skills.native.document_platform import tools
    from kazma_ui import documents_api

    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        patch.setattr(scope, "_enforced", True)
        service = seeded_service(root / "store")
        engine = _Engine()
        engine.install(patch, service.service)
        patch.setattr(documents_api, "_active_workspace_id", lambda: "w")

        async def ensure() -> Any:
            return service

        patch.setattr(tools, "_ensure_service", ensure)
        patch.setattr(tools, "_workspace", lambda: "w")
        patch.setattr(tools, "_INGEST_WAIT_SECONDS", 0.05)
        set_ingestion_service(service)
        try:
            marks = matrix.plant("doc")
            a = seed_document(service, tenant_id=Matrix.SPACE_A, owner=matrix.account_a, marker=marks.a)
            b = seed_document(service, tenant_id=Matrix.SPACE_B, owner=matrix.account_b, marker=marks.b)
            yield World(matrix=matrix, service=service, engine=engine, a=a, b=b,
                        mark_a=marks.a, mark_b=marks.b, patch=patch, root=root)
        finally:
            set_ingestion_service(None)
            service.close()


def _seed_artifact(w: World, seeded: Seeded, owner: str, body: bytes) -> str:
    """A finished artifact of *seeded*: its bytes in the store and its row."""
    import hashlib

    sha = hashlib.sha256(body).hexdigest()
    w.service.storage.for_space(seeded.tenant_id).put_stream(io.BytesIO(body), kind="artifacts", max_bytes=1 << 20)
    blob = w.service.repository.register_blob(tenant_id=seeded.tenant_id, sha256=sha,
                                              byte_size=len(body), storage_kind="artifacts")
    record = w.service.repository.create_artifact(
        tenant_id=seeded.tenant_id, document_id=seeded.document_id, version_id=seeded.version_id,
        actor_id=owner, blob_id=blob.id, artifact_type="convert:markdown",
        metadata={"output": {"extension": "md", "mime_type": "text/markdown"}},
    )
    return str(record.id)


# ── reads by document id ────────────────────────────────────────────────────


def _by_document(path: str, *, own_ok: tuple[int, ...] = (200,), marker_shown: bool = False):
    def run(matrix: Matrix) -> None:
        with world(matrix) as w, w.as_a() as client:
            other = client.get(path.format(document_id=w.b.document_id))
            w.refused(other)
            own = client.get(path.format(document_id=w.a.document_id))
            assert own.status_code in own_ok, (own.status_code, own.text[:300])
            w.nothing_of_b(own)
            if marker_shown:
                assert w.mark_a in own.text
            w.b_is_untouched()

    return run


case("GET /api/documents/{document_id}", ITEM)(_by_document("/api/documents/{document_id}"))
case("GET /api/documents/{document_id}/versions", ITEM)(
    _by_document("/api/documents/{document_id}/versions"))
case("GET /api/documents/{document_id}/content", ITEM)(
    _by_document("/api/documents/{document_id}/content", marker_shown=True))
case("GET /api/documents/{document_id}/artifacts", ITEM)(
    _by_document("/api/documents/{document_id}/artifacts"))


@case("GET /api/documents", ITEM)
def documents_list(matrix: Matrix) -> None:
    with world(matrix) as w, w.as_a() as client:
        listed = client.get("/api/documents")
        assert listed.status_code == 200
        assert [row["document_id"] for row in listed.json()["documents"]] == [w.a.document_id]
        assert w.b.document_id not in listed.text
        # Naming B's space is refused at the request boundary.
        named = client.get("/api/documents", headers=matrix.headers_b)
        assert named.status_code == 403 and named.json()["code"] == "not_a_member"
        w.nothing_of_b(named)


# ── operations by document id (their engine is stubbed) ─────────────────────


def _operation(path: str, body: dict[str, Any]):
    def run(matrix: Matrix) -> None:
        with world(matrix) as w, w.as_a() as client:
            before = w.engine_calls()
            other = client.post(path.format(document_id=w.b.document_id), json=body)
            w.refused(other)
            assert w.engine_calls() == before, "B's document reached an engine"
            own = client.post(path.format(document_id=w.a.document_id), json=body)
            assert own.status_code != 404, own.text[:300]
            w.nothing_of_b(own)
            if own.json().get("code") == _Engine.REACHED:
                w.engine_ran_for_a(before)
            w.b_is_untouched()

    return run


case("POST /api/documents/{document_id}/convert", ITEM)(
    _operation("/api/documents/{document_id}/convert", {"target_format": "markdown"}))
case("POST /api/documents/{document_id}/split", ITEM)(
    _operation("/api/documents/{document_id}/split", {"start_page": 1, "end_page": 1}))
case("POST /api/documents/{document_id}/fill-form", ITEM)(
    _operation("/api/documents/{document_id}/fill-form", {"fields": {"name": "x"}}))
case("POST /api/documents/{document_id}/redact", ITEM)(
    _operation("/api/documents/{document_id}/redact", {"terms": ["secret"], "confirm": True}))


@case("GET /api/documents/{document_id}/pdf-info", ITEM)
def documents_pdf_info(matrix: Matrix) -> None:
    with world(matrix) as w, w.as_a() as client:
        before = w.engine_calls()
        w.refused(client.get(f"/api/documents/{w.b.document_id}/pdf-info"))
        assert w.engine_calls() == before, "B's document reached an engine"
        own = client.get(f"/api/documents/{w.a.document_id}/pdf-info")
        assert own.status_code != 404, own.text[:300]
        if own.json().get("code") == _Engine.REACHED:
            w.engine_ran_for_a(before)
        w.b_is_untouched()


@case("POST /api/documents/merge", ITEM)
def documents_merge(matrix: Matrix) -> None:
    with world(matrix) as w, w.as_a() as client:
        second = w.seed_a("second")
        before = w.engine_calls()
        mixed = client.post("/api/documents/merge", json={"document_ids": [w.a.document_id, w.b.document_id]})
        w.refused(mixed)
        assert w.engine_calls() == before, "a merge holding B's document reached an engine"
        own = client.post("/api/documents/merge", json={"document_ids": [w.a.document_id, second.document_id]})
        assert own.status_code != 404, own.text[:300]
        if own.json().get("code") == _Engine.REACHED:
            w.engine_ran_for_a(before)
        w.b_is_untouched()


@case("POST /api/documents/generate", ITEM)
def documents_generate(matrix: Matrix) -> None:
    with world(matrix) as w, w.as_a() as client:
        before = w.engine_calls()
        made = client.post("/api/documents/generate", json={
            "target_format": "markdown", "payload": {"title": "t", "sections": []},
        })
        assert made.json().get("code") == _Engine.REACHED, made.text[:300]
        w.engine_ran_for_a(before)
        w.b_is_untouched()


# ── documents made in a space ───────────────────────────────────────────────


def _made_in_a(w: World, document_id: str) -> None:
    record = w.service.repository.get_document(tenant_id=Matrix.SPACE_A, document_id=document_id)
    assert record is not None and record.owner_id == w.matrix.account_a, "made outside A, or not as A"
    assert w.service.repository.get_document(tenant_id=Matrix.SPACE_B, document_id=document_id) is None


@case("POST /api/documents", ITEM)
def documents_upload(matrix: Matrix) -> None:
    with world(matrix) as w, w.as_a() as client:
        marker = matrix.plant("upload").a
        uploaded = client.post("/api/documents", content=marker.encode(),
                               headers={"X-Document-Filename": "note.txt"})
        assert uploaded.status_code == 200, uploaded.text[:300]
        _made_in_a(w, uploaded.json()["document_id"])
        # The same upload naming B's space never reaches B.
        named = client.post("/api/documents", content=b"x", headers={
            "X-Document-Filename": "note.txt", **matrix.headers_b,
        })
        assert named.status_code == 403
        w.b_is_untouched()


@case("POST /api/documents/import", ITEM)
def documents_import(matrix: Matrix) -> None:
    from kazma_ui import documents_api

    with world(matrix) as w, w.as_a() as client:
        source = w.root / "import.txt"
        source.write_text(matrix.plant("import").a, encoding="utf-8")
        w.patch.setattr(documents_api, "_resolve_workspace_file", lambda _rel: source)
        imported = client.post("/api/documents/import", json={"path": "import.txt"})
        assert imported.status_code == 200, imported.text[:300]
        _made_in_a(w, imported.json()["document_id"])
        w.b_is_untouched()


# ── jobs ────────────────────────────────────────────────────────────────────


def _by_job(path: str, *, method: str = "get", own_ok: tuple[int, ...] = (200,)):
    def run(matrix: Matrix) -> None:
        with world(matrix) as w, w.as_a() as client:
            call = getattr(client, method)
            w.refused(call(path.format(job_id=w.b.job_id)))
            own = call(path.format(job_id=w.a.job_id))
            assert own.status_code in own_ok, (own.status_code, own.text[:300])
            w.nothing_of_b(own)
            w.b_is_untouched()

    return run


case("GET /api/documents/jobs/{job_id}", ITEM)(_by_job("/api/documents/jobs/{job_id}"))
case("GET /api/documents/jobs/{job_id}/events", ITEM)(_by_job("/api/documents/jobs/{job_id}/events"))
case("POST /api/documents/jobs/{job_id}/cancel", ITEM)(
    _by_job("/api/documents/jobs/{job_id}/cancel", method="post"))
# A received job cannot be retried: A's own answers the state, never "unknown".
case("POST /api/documents/jobs/{job_id}/retry", ITEM)(
    _by_job("/api/documents/jobs/{job_id}/retry", method="post", own_ok=(200, 409)))


# ── deletion ────────────────────────────────────────────────────────────────


def _delete(method: str, path: str):
    def run(matrix: Matrix) -> None:
        with world(matrix) as w, w.as_a() as client:
            call = getattr(client, method)
            w.refused(call(path.format(document_id=w.b.document_id)))
            w.b_is_untouched()
            mine = w.seed_a("deleted")
            own = call(path.format(document_id=mine.document_id))
            assert own.status_code == 200, own.text[:300]
            gone = w.service.repository.get_document(tenant_id=Matrix.SPACE_A, document_id=mine.document_id)
            assert gone is None or gone.deleted_at is not None

    return run


case("POST /api/documents/{document_id}/delete", ITEM)(
    _delete("post", "/api/documents/{document_id}/delete"))
case("DELETE /api/documents/{document_id}", ITEM)(_delete("delete", "/api/documents/{document_id}"))


# ── artifacts ───────────────────────────────────────────────────────────────


@case("GET /api/documents/artifacts/{artifact_id}/download", ITEM)
def documents_artifact_download(matrix: Matrix) -> None:
    with world(matrix) as w:
        b_bytes = f"artifact {w.mark_b}".encode()
        b_artifact = _seed_artifact(w, w.b, matrix.account_b, b_bytes)
        a_artifact = _seed_artifact(w, w.a, matrix.account_a, f"artifact {w.mark_a}".encode())
        with w.as_a() as client:
            w.refused(client.get(f"/api/documents/artifacts/{b_artifact}/download"))
            own = client.get(f"/api/documents/artifacts/{a_artifact}/download")
            assert own.status_code == 200 and w.mark_a in own.text, own.status_code
        record = w.service.repository.get_artifact(tenant_id=Matrix.SPACE_B, artifact_id=b_artifact)
        assert record is not None


# ── the tenant's counts and its audit trail ─────────────────────────────────


def _counts(path: str):
    def run(matrix: Matrix) -> None:
        with world(matrix) as w, w.as_a() as client:
            own = client.get(path)
            assert own.status_code == 200, own.text[:300]
            w.nothing_of_b(own)
            assert w.b.document_id not in own.text
            named = client.get(path, headers=matrix.headers_b)
            assert named.status_code == 403 and named.json()["code"] == "not_a_member"

    return run


case("GET /api/documents/ops/metrics", ITEM)(_counts("/api/documents/ops/metrics"))
case("GET /api/documents/ops/capacity", ITEM)(_counts("/api/documents/ops/capacity"))


@case("GET /api/documents/ops/audit", ITEM)
def documents_audit(matrix: Matrix) -> None:
    """The trail names every document and actor of a space. Read in A's
    space it is A's trail; naming B's space is refused, the admin's request
    too (the admin is a member of neither matrix space)."""
    with world(matrix) as w:
        for seeded, who in ((w.a, matrix.account_a), (w.b, matrix.account_b)):
            w.service.audit.record(tenant_id=seeded.tenant_id, event_type="access", action="read",
                                   outcome="success", actor_id=who, document_id=seeded.document_id)
        with w.as_a() as client:
            own = client.get("/api/documents/ops/audit")
            assert own.status_code in (200, 403), own.text[:300]  # admin only where sign-in is on
            if own.status_code == 200:
                assert w.a.document_id in own.text, "A's own trail was not shown"
            w.nothing_of_b(own)
            assert w.b.document_id not in own.text and matrix.account_b not in own.text
            named = client.get("/api/documents/ops/audit", headers=matrix.headers_b)
            assert named.status_code == 403 and named.json()["code"] == "not_a_member"
        with w.client(OWNER_PRINCIPAL) as client:
            admin = client.get("/api/documents/ops/audit", headers=matrix.headers_b)
            assert admin.status_code == 403 and admin.json()["code"] == "not_a_member"
            w.nothing_of_b(admin)


# ── the page ────────────────────────────────────────────────────────────────


@case("GET /documents", ITEM)
def documents_page(matrix: Matrix) -> None:
    """The page is a shell that loads through ``/api/documents`` (whose routes
    have their own cases): it renders nothing of a space, and naming another
    space is refused at the request boundary."""
    source = (REPO / "kazma-ui/kazma_ui/routes_direct/misc.py").read_text(encoding="utf-8")
    handler = next(node for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.FunctionDef) and node.name == "documents_page")
    context = next(node for node in ast.walk(handler) if isinstance(node, ast.Dict))
    keys = {key.value for key in context.keys if isinstance(key, ast.Constant)}
    assert keys == {"config", "active_page"}, f"the page renders more than its shell: {keys}"
    with matrix.requests_as("a") as client:
        own = client.get("/documents", headers=matrix.headers_a)
        other = client.get("/documents", headers=matrix.headers_b)
    assert own.status_code == 200 and own.json()["space"] == Matrix.SPACE_A
    assert other.status_code == 403 and other.json()["code"] == "not_a_member"


# ── the agent's document tools ──────────────────────────────────────────────


def _tools() -> Any:
    from kazma_skills.native.document_platform import tools

    return tools


@case("tool:document_read", ITEM)
def tool_document_read(matrix: Matrix) -> None:
    tools = _tools()
    with world(matrix) as w, matrix.as_a():
        other = w.run(tools.document_read(w.b.document_id))
        assert other.startswith("Error"), other
        w.nothing_of_b(other)
        assert w.mark_a in w.run(tools.document_read(w.a.document_id))
        w.b_is_untouched()


@case("tool:document_status", ITEM)
def tool_document_status(matrix: Matrix) -> None:
    tools = _tools()
    with world(matrix) as w, matrix.as_a():
        by_job = w.run(tools.document_status(job_id=w.b.job_id))
        by_document = w.run(tools.document_status(document_id=w.b.document_id))
        assert by_job.startswith("Error") and by_document.startswith("Error"), (by_job, by_document)
        overview = w.run(tools.document_status())
        for reply in (by_job, by_document, overview):
            w.nothing_of_b(reply)
        assert w.b.document_id[:8] not in overview, "B's document is in A's overview"
        assert "received" in w.run(tools.document_status(job_id=w.a.job_id))


@case("tool:document_import", ITEM)
def tool_document_import(matrix: Matrix) -> None:
    import re

    tools = _tools()
    with world(matrix) as w, matrix.as_a():
        source = w.root / "agent-import.txt"
        source.write_text(matrix.plant("toolimport").a, encoding="utf-8")
        w.patch.setattr(tools, "_resolve_workspace_file", lambda _path: (source, None))
        reply = w.run(tools.document_import(str(source)))
        document_id = re.search(r"document_id: (\S+)", reply).group(1)
        _made_in_a(w, document_id)
        w.b_is_untouched()


def _tool_operation(name: str, **kwargs: Any):
    def run(matrix: Matrix) -> None:
        tools = _tools()
        with world(matrix) as w, matrix.as_a():
            call = getattr(tools, name)
            before = w.engine_calls()
            other = w.run(call(w.b.document_id, **kwargs))
            assert other.startswith("Error"), other
            w.nothing_of_b(other)
            assert w.engine_calls() == before, "B's document reached an engine"
            own = w.run(call(w.a.document_id, **kwargs))
            assert "unavailable" not in own.lower(), own
            w.engine_ran_for_a(before)
            w.b_is_untouched()

    return run


case("tool:document_convert", ITEM)(_tool_operation("document_convert", target_format="markdown"))
case("tool:document_redact", ITEM)(_tool_operation("document_redact", terms=["secret"]))


@case("tool:document_cancel", ITEM)
def tool_document_cancel(matrix: Matrix) -> None:
    tools = _tools()
    with world(matrix) as w, matrix.as_a():
        other = w.run(tools.document_cancel(w.b.job_id))
        w.nothing_of_b(other)
        w.b_is_untouched()  # B's job is still received
        mine = w.seed_a("cancelled")
        w.run(tools.document_cancel(mine.job_id))
        job = w.service.job_status(tenant_id=Matrix.SPACE_A, job_id=mine.job_id, actor_id=matrix.account_a)
        assert job["state"] == "cancelled", job



# ── a document in a Knowledge library (per space since S1-13) ───────────────


@dataclass
class KnowledgeWorld:
    """:class:`World` with a private Knowledge store: a library named "docs"
    in each space, and B's document indexed into B's, by B."""

    w: World
    store: Any
    lib_a: str
    lib_b: str

    def texts(self, library_id: str) -> list[str]:
        """The active chunks a library holds, whoever asks."""
        return [row[0] for row in self.store._get_conn().execute(
            "SELECT content FROM knowledge_chunks WHERE library_id = ? AND active = 1", (library_id,))]

    def index_a(self) -> None:
        """A's document into A's library, by A (the service, as a surface would)."""
        with self.w.matrix.as_a():
            self.w.service.index_document(tenant_id=Matrix.SPACE_A, actor_id=self.w.matrix.account_a,
                                          document_id=self.w.a.document_id, library_id=self.lib_a)
        assert any(self.w.mark_a in text for text in self.texts(self.lib_a))

    def b_library_is_untouched(self) -> None:
        texts = self.texts(self.lib_b)
        assert any(self.w.mark_b in text for text in texts), "B's indexed document left B's library"
        assert not any(self.w.mark_a in text for text in texts), "A's document reached B's library"


@contextmanager
def knowledge_world(matrix: Matrix) -> Iterator[KnowledgeWorld]:
    from kazma_core.documents.knowledge import DocumentKnowledgeAdapter
    from kazma_core.stores.knowledge import get_knowledge_store, reset_knowledge_store
    from kazma_core.stores.knowledge_index import knowledge_index_for, reset_knowledge_index

    from tests._no_models import keep_models_out

    # The store's own folder: Chroma keeps its file open past the store's
    # reset, so its removal may fail (the Knowledge cases' way too).
    with world(matrix) as w, tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as data:
        w.patch.setenv("KAZMA_DATA_DIR", data)
        keep_models_out(w.patch)  # keyword search answers: no embedder
        reset_knowledge_index()
        reset_knowledge_store()
        try:
            store = get_knowledge_store()
            adapter = DocumentKnowledgeAdapter(
                repository=w.service.repository, knowledge_store=store,
                knowledge_index=knowledge_index_for(store),
                config=w.service._policy.initial, policy=w.service._policy,
            )
            w.patch.setattr(w.service, "knowledge_adapter", adapter)
            w.patch.setattr(w.service.service, "knowledge_adapter", adapter)
            with matrix.as_a():
                lib_a = store.create_library("docs", name="docs of A")["id"]
            with matrix.as_b():
                lib_b = store.create_library("docs", name="docs of B")["id"]
                w.service.index_document(tenant_id=Matrix.SPACE_B, actor_id=matrix.account_b,
                                         document_id=w.b.document_id, library_id=lib_b)
            made = KnowledgeWorld(w=w, store=store, lib_a=lib_a, lib_b=lib_b)
            made.b_library_is_untouched()
            yield made
        finally:
            reset_knowledge_index()
            reset_knowledge_store()


def _refused_or_empty(w: World, response: Any) -> None:
    """Another space's document or library reads as none: refused, or
    answered with nothing of B's."""
    assert response.status_code in (200, 403, 404), (response.status_code, response.text[:300])
    w.nothing_of_b(response)


@case("POST /api/documents/{document_id}/index", ITEM)
def documents_index(matrix: Matrix) -> None:
    with knowledge_world(matrix) as k, k.w.as_a() as client:
        w = k.w
        other = client.post(f"/api/documents/{w.b.document_id}/index", json={"library_id": k.lib_a})
        w.refused(other)
        into_b = client.post(f"/api/documents/{w.a.document_id}/index", json={"library_id": k.lib_b})
        assert into_b.status_code in (403, 404), (into_b.status_code, into_b.text[:300])
        w.nothing_of_b(into_b)
        k.b_library_is_untouched()
        assert not any(w.mark_b in text for text in k.texts(k.lib_a)), "B's document reached A's library"
        own = client.post(f"/api/documents/{w.a.document_id}/index", json={"library_id": k.lib_a})
        assert own.status_code == 200, (own.status_code, own.text[:300])
        assert any(w.mark_a in text for text in k.texts(k.lib_a))
        k.b_library_is_untouched()
        w.b_is_untouched()


@case("POST /api/documents/{document_id}/unindex", ITEM)
def documents_unindex(matrix: Matrix) -> None:
    with knowledge_world(matrix) as k, k.w.as_a() as client:
        w = k.w
        for library_id in (k.lib_b, k.lib_a):
            other = client.post(f"/api/documents/{w.b.document_id}/unindex", json={"library_id": library_id})
            _refused_or_empty(w, other)
            k.b_library_is_untouched()
        k.index_a()
        own = client.post(f"/api/documents/{w.a.document_id}/unindex", json={"library_id": k.lib_a})
        assert own.status_code == 200, (own.status_code, own.text[:300])
        assert not any(w.mark_a in text for text in k.texts(k.lib_a)), "A's own unindex did nothing"
        w.b_is_untouched()


@case("POST /api/documents/search", ITEM)
def documents_search(matrix: Matrix) -> None:
    with knowledge_world(matrix) as k, k.w.as_a() as client:
        w = k.w
        k.index_a()
        other = client.post("/api/documents/search", json={"library_id": k.lib_b, "query": w.mark_b})
        _refused_or_empty(w, other)
        mine = client.post("/api/documents/search", json={"library_id": k.lib_a, "query": w.mark_b})
        _refused_or_empty(w, mine)
        own = client.post("/api/documents/search", json={"library_id": k.lib_a, "query": w.mark_a})
        assert own.status_code == 200 and w.mark_a in own.text, (own.status_code, own.text[:300])


@case("tool:document_index", ITEM)
def tool_document_index(matrix: Matrix) -> None:
    tools = _tools()
    with knowledge_world(matrix) as k, matrix.as_a():
        w = k.w
        other = w.run(tools.document_index(w.b.document_id, k.lib_a))
        into_b = w.run(tools.document_index(w.a.document_id, k.lib_b))
        for reply in (other, into_b):
            assert reply.startswith("Error"), reply
            w.nothing_of_b(reply)
        k.b_library_is_untouched()
        assert not any(w.mark_b in text for text in k.texts(k.lib_a))
        assert w.run(tools.document_index(w.a.document_id, k.lib_a)).startswith("Indexed")
        assert any(w.mark_a in text for text in k.texts(k.lib_a))
        w.b_is_untouched()


@case("tool:document_search", ITEM)
def tool_document_search(matrix: Matrix) -> None:
    tools = _tools()
    with knowledge_world(matrix) as k:
        w = k.w
        k.index_a()
        with matrix.as_a():
            for reply in (w.run(tools.document_search(k.lib_b, w.mark_b)),
                          w.run(tools.document_search(k.lib_a, w.mark_b))):
                w.nothing_of_b(reply)
            assert w.mark_a in w.run(tools.document_search(k.lib_a, w.mark_a))
