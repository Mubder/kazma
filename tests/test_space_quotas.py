"""Spaces S2-08: limits per space.

One space at its limit does not slow or stop another. Each space has its own
limits beside the install's (``kazma_core.spaces.space_limits``):

* its share of the install's slots for work that runs at once -- swarm
  tasks, research runs, Knowledge crawls. A space at its share is refused at
  admission and holds no slot; another space's work is admitted. The plan's
  control is the install-wide quota alone: with the shares taken out, one
  space fills the install's slots and the other is refused;
* the bytes of its own folder (disk), its documents and its libraries. A
  space at one is refused that write; another space writes. Each guard taken
  out is the control: the space past its limit writes on.

The admin sets them on People (``/api/people/spaces/{id}/limits``, the
limits for spaces ``/api/people/space-limits``) and sees each space's usage
beside them, recorded; a member cannot. An install that does not enforce
Spaces reads nothing here. The browser half is
``tests/e2e/test_space_limits_browser.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.spaces import identity, people, scope, space_limits
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._space_matrix import Matrix

A, B = Matrix.SPACE_A, Matrix.SPACE_B


@pytest.fixture
def matrix(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture(autouse=True)
def _planted_files_go():
    """The files a test puts in a space's folder, and the folder sizes
    measured, go with the test."""
    yield
    from kazma_core.spaces.files import space_dir

    for space in (A, B):
        (space_dir(space) / "files" / "big.bin").unlink(missing_ok=True)
        shutil.rmtree(space_dir(space) / "images", ignore_errors=True)
    space_limits._sizes.clear()


def _limits(space: str, **values: int) -> None:
    people.set_space_limits(space, values, by=OWNER_ACCOUNT_ID)


def _as(matrix: Matrix, space: str) -> Any:
    return scope.bind_scope(space, matrix.account_a if space == A else matrix.account_b)


async def _until(check: Any, seconds: float = 10.0) -> None:
    for _ in range(int(seconds / 0.01)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


# ── swarm tasks ──────────────────────────────────────────────────────────


def _swarm_world(monkeypatch, tmp: Path) -> tuple[Any, Any]:
    """A real engine on a private store; ``blocker`` holds each task until
    the test lets it go, so tasks stay running."""
    from kazma_core.safety import hitl_gates
    from kazma_core.swarm import SwarmConfig, SwarmEngine, TaskStore
    from kazma_core.swarm.worker import SwarmWorker

    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch)
    monkeypatch.setenv("KAZMA_SWARM_MAX_ACTIVE", "4")
    hitl_gates.set_db_path_for_tests(str(tmp / "hitl_gates.db"))
    gate = SimpleNamespace(entered=[], go=None)

    class _Blocker(SwarmWorker):
        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

        async def dispatch(self, task: str, context: Any = "") -> dict[str, Any]:
            gate.entered.append(scope.current_scope().space_id)
            await gate.go.wait()
            return {"worker": self.name, "task_id": "", "status": "success", "output": "done", "error": None}

    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=TaskStore(str(tmp / "tasks.db")))
    engine._workers["blocker"] = _Blocker(name="blocker")
    return engine, gate


def _swarm_run(matrix: Matrix, monkeypatch, starts: list[str], then: str) -> tuple[list[Any], Any, list[str]]:
    """Start *starts* (spaces) as running tasks, then dispatch one more of
    *then* while they run. What each start answered once let go, the last
    dispatch's answer, and the spaces whose tasks reached the worker."""
    from kazma_core.safety import hitl_gates
    from kazma_core.swarm.task import SwarmTask

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        engine, gate = _swarm_world(monkeypatch, Path(tmp))

        async def dispatch(space: str, n: int) -> Any:
            with _as(matrix, space):
                return await engine.dispatch(SwarmTask(prompt=f"task {n}", workers=["blocker"]))

        async def scenario() -> tuple[list[Any], Any]:
            gate.go = asyncio.Event()
            running = [asyncio.create_task(dispatch(space, n)) for n, space in enumerate(starts)]
            await _until(lambda: len(gate.entered) == len(running))
            last = asyncio.create_task(dispatch(then, 99))
            await _until(lambda: last.done() or len(gate.entered) > len(running))
            gate.go.set()
            return await asyncio.gather(*running), await last

        try:
            ran, last = asyncio.run(scenario())
        finally:
            hitl_gates.set_db_path_for_tests(None)
            engine._task_store.close()
        return ran, last, list(gate.entered)


def test_a_space_at_its_swarm_share_is_refused_and_another_space_runs(matrix, enforced, monkeypatch) -> None:
    _limits(A, swarm=2)
    ran, refused, entered = _swarm_run(matrix, monkeypatch, [A, A], then=A)
    assert all(result.status == "success" for result in ran)
    assert refused.status == "failed" and refused.metadata.get("code") == "space_limit", refused
    assert "its share" in (refused.error or "") and entered == [A, A]

    _limits(A, swarm=2)
    ran, admitted, entered = _swarm_run(matrix, monkeypatch, [A, A], then=B)
    assert admitted.status == "success", admitted
    assert sorted(entered) == [A, A, B]


def test_control_without_the_shares_one_space_fills_the_install_and_another_is_refused(
        matrix, enforced, monkeypatch) -> None:
    """The plan's control: the install-wide quota alone (the share check
    taken out). A's four tasks take every slot, and B's task is refused."""
    from kazma_core.swarm.engine import SwarmEngine

    monkeypatch.setattr(SwarmEngine, "_space_share_refusal", staticmethod(lambda task, ahead: None))
    _limits(A, swarm=2)
    _ran, refused, entered = _swarm_run(matrix, monkeypatch, [A, A, A, A], then=B)
    assert entered == [A, A, A, A]
    assert refused.status == "failed" and refused.metadata.get("max_concurrent_tasks") == 4, refused


def test_two_dispatches_at_once_never_both_pass_nor_both_fail_the_share(matrix, enforced, monkeypatch) -> None:
    """The slot is held while the share is read: of two tasks of one space
    dispatched at the same moment with a share of one, exactly one runs."""
    from kazma_core.safety import hitl_gates
    from kazma_core.swarm.task import SwarmTask

    _limits(A, swarm=1)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        engine, gate = _swarm_world(monkeypatch, Path(tmp))

        async def dispatch(n: int) -> Any:
            with _as(matrix, A):
                return await engine.dispatch(SwarmTask(prompt=f"task {n}", workers=["blocker"]))

        async def scenario() -> list[Any]:
            gate.go = asyncio.Event()
            both = [asyncio.create_task(dispatch(n)) for n in (1, 2)]
            await _until(lambda: any(t.done() for t in both))
            gate.go.set()
            return await asyncio.gather(*both)

        try:
            results = asyncio.run(scenario())
        finally:
            hitl_gates.set_db_path_for_tests(None)
            engine._task_store.close()
    codes = sorted(str(r.metadata.get("code") or r.status) for r in results)
    assert codes == ["space_limit", "success"], codes
    assert gate.entered == [A] and engine.running_in(A) == 0


def test_the_install_s_own_cap_still_holds_for_every_space(matrix, enforced, monkeypatch) -> None:
    _limits(A, swarm=5)
    _limits(B, swarm=5)
    _ran, refused, _entered = _swarm_run(matrix, monkeypatch, [A, A, B, B], then=B)
    assert refused.metadata.get("max_concurrent_tasks") == 4, refused


# ── research runs ────────────────────────────────────────────────────────


def _research_run(matrix: Matrix, monkeypatch, starts: list[str], then: str) -> tuple[str, list[str]]:
    """Research runs of *starts* held running, then one more of *then*:
    the last run's answer and the spaces whose runs began."""
    from kazma_core.web_acquire import research_runtime, resources

    monkeypatch.setattr(resources, "_RESEARCH", resources._CapacityPool("research pipelines", 4, 0))

    @asynccontextmanager
    async def _no_scope(root_resolver: Any):
        yield

    monkeypatch.setattr(research_runtime, "_frozen", _no_scope)
    began: list[str] = []
    gate = SimpleNamespace(go=None)

    @research_runtime.bounded_pipeline(lambda: ".")
    async def pipeline(n: int) -> str:
        began.append(scope.current_scope().space_id)
        await gate.go.wait()
        return "done"

    async def run(space: str, n: int) -> str:
        with _as(matrix, space):
            return await pipeline(n)

    async def scenario() -> str:
        gate.go = asyncio.Event()
        held = [asyncio.create_task(run(space, n)) for n, space in enumerate(starts)]
        await _until(lambda: len(began) == len(held))
        last = asyncio.create_task(run(then, 99))
        await _until(lambda: last.done() or len(began) > len(held))
        gate.go.set()
        assert await asyncio.gather(*held) == ["done"] * len(held)
        return await last

    answer = asyncio.run(scenario())
    assert research_runtime.research_running_in(A) == 0 and research_runtime.research_running_in(B) == 0
    assert resources.research_capacity().snapshot()["active"] == 0
    return answer, began


def test_a_space_at_its_research_share_is_refused_and_another_space_runs(matrix, enforced, monkeypatch) -> None:
    _limits(A, research=2)
    answer, began = _research_run(matrix, monkeypatch, [A, A], then=A)
    assert answer.startswith("Error:") and "its share" in answer and began == [A, A]
    answer, began = _research_run(matrix, monkeypatch, [A, A], then=B)
    assert answer == "done" and sorted(began) == [A, A, B]


def test_control_without_the_research_share_one_space_fills_the_install(matrix, enforced, monkeypatch) -> None:
    from kazma_core.web_acquire import research_runtime

    async def no_share() -> None:
        return None

    monkeypatch.setattr(research_runtime, "_take_space_share", no_share)
    _limits(A, research=2)
    answer, began = _research_run(matrix, monkeypatch, [A, A, A, A], then=B)
    assert began == [A, A, A, A] and answer.startswith("Error:") and "capacity" in answer, answer


def test_a_background_session_holds_one_share_through_its_own_pipeline(matrix, enforced, monkeypatch) -> None:
    """A background research session counts once: its own pipeline, run in
    the session's task, is not a second run (it is never refused for the
    session's own share), and the share is held until that task ends."""
    from kazma_core.web_acquire import research_runtime, resources
    from kazma_core.web_acquire.result import AcquisitionError

    monkeypatch.setattr(resources, "_RESEARCH", resources._CapacityPool("research pipelines", 4, 0))
    monkeypatch.setattr(resources, "_RESEARCH_SESSIONS",
                        resources._CapacityPool("background research sessions", 4, 0))

    @asynccontextmanager
    async def _no_scope(root_resolver: Any):
        yield

    monkeypatch.setattr(research_runtime, "_frozen", _no_scope)
    _limits(A, research=1)
    gate = SimpleNamespace(go=None, ran=[])

    @research_runtime.bounded_pipeline(lambda: ".")
    async def pipeline() -> str:
        gate.ran.append(scope.current_scope().space_id)
        await gate.go.wait()
        return "done"

    @research_runtime.bounded_session(lambda started: started.task)
    async def session() -> Any:
        return SimpleNamespace(task=asyncio.create_task(pipeline()))

    async def start(space: str) -> Any:
        with _as(matrix, space):
            return await session()

    async def scenario() -> None:
        gate.go = asyncio.Event()
        mine = await start(A)
        await _until(lambda: gate.ran == [A])
        assert research_runtime.research_running_in(A) == 1
        with pytest.raises(AcquisitionError, match="its share"):
            await start(A)
        theirs = await start(B)
        await _until(lambda: sorted(gate.ran) == [A, B])
        gate.go.set()
        assert [await mine.task, await theirs.task] == ["done", "done"]
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert research_runtime.research_running_in(A) == 0 and research_runtime.research_running_in(B) == 0


# ── Knowledge crawls ─────────────────────────────────────────────────────


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """The crawl journal on a private store (the identity store stays where
    it is)."""
    from kazma_core import config_store
    from kazma_core.web_acquire import crawl_jobs

    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(crawl_jobs, "config_store",
                        SimpleNamespace(get_config_store=lambda: store, ConfigStore=config_store.ConfigStore))
    yield store
    store.close()


def _crawl(matrix: Matrix, space: str, n: int) -> Any:
    from kazma_core.web_acquire import crawl_jobs

    with _as(matrix, space):
        return crawl_jobs.start(f"lib-{space}-{n}", f"https://c{n}.example.test/",
                                {"mode": "local_only", "scope": "tree", "max_pages": 10})


def test_a_space_at_its_crawl_share_is_refused_and_another_space_crawls(matrix, enforced, journal) -> None:
    from kazma_core.web_acquire import crawl_jobs
    from kazma_core.web_acquire.result import AcquisitionError

    _limits(A, crawls=2)
    running = [_crawl(matrix, A, 1), _crawl(matrix, A, 2)]
    assert crawl_jobs.running_in(A) == 2
    with pytest.raises(AcquisitionError) as refused:
        _crawl(matrix, A, 3)
    assert "its share" in str(refused.value)
    running.append(_crawl(matrix, B, 4))
    assert crawl_jobs.running_in(B) == 1
    assert {row["space"] for row in crawl_jobs._read()[1].values()} == {A, B}
    for job in running:
        job.finish("completed")
    assert crawl_jobs.running_in(A) == 0
    _crawl(matrix, A, 5).finish("completed")  # the share is free again


def test_control_without_the_crawl_share_one_space_fills_the_install(matrix, enforced, journal,
                                                                    monkeypatch) -> None:
    from kazma_core.web_acquire import crawl_jobs
    from kazma_core.web_acquire.result import AcquisitionError

    monkeypatch.setattr(crawl_jobs, "_space_share", lambda: ("", None))
    _limits(A, crawls=2)
    running = [_crawl(matrix, A, n) for n in range(4)]
    with pytest.raises(AcquisitionError) as refused:
        _crawl(matrix, B, 9)
    assert "Installation crawl limit" in str(refused.value)
    for job in running:
        job.finish("completed")


# ── disk ─────────────────────────────────────────────────────────────────


def _fill_folder(space: str, size: int) -> Path:
    from kazma_core.spaces.files import space_dir

    folder = space_dir(space) / "files"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "big.bin").write_bytes(b"x" * size)
    space_limits._sizes.clear()
    return folder


def test_a_space_at_its_disk_limit_takes_no_more_files_and_another_does(matrix, enforced) -> None:
    from kazma_core.workspace.path_policy import check_path_access

    _limits(A, disk_bytes=1024)
    mine = _fill_folder(A, 4096)
    theirs = _fill_folder(B, 4096)  # B's limit is Kazma's own, far above
    with _as(matrix, A):
        refused = check_path_access(mine / "new.txt", "write")
        assert not refused.allowed and "reached their limit" in refused.reason
        assert check_path_access(mine / "big.bin", "read").allowed  # reading is not refused
        with pytest.raises(space_limits.SpaceOverLimit) as over:
            space_limits.refuse_write_for_disk(10)
        assert over.value.code == "space_limit" and over.value.limit == "disk_bytes"
    with _as(matrix, B):
        assert check_path_access(theirs / "new.txt", "write").allowed
        space_limits.refuse_write_for_disk(10)


def test_control_without_the_disk_check_a_full_space_writes_on(matrix, enforced, monkeypatch) -> None:
    from kazma_core.workspace import path_policy

    monkeypatch.setattr(path_policy, "_space_disk_refusal", lambda resolved: None)
    _limits(A, disk_bytes=1024)
    mine = _fill_folder(A, 4096)
    with _as(matrix, A):
        assert path_policy.check_path_access(mine / "new.txt", "write").allowed


def test_an_upload_into_a_full_space_is_507(matrix, enforced, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import routes_chat_upload

    _limits(A, disk_bytes=1024)
    _fill_folder(A, 4096)
    app = FastAPI()
    app.include_router(routes_chat_upload.router)

    @app.middleware("http")
    async def _in_space_a(request: Any, call_next: Any) -> Any:
        with _as(matrix, A):
            return await call_next(request)

    with TestClient(app) as client:
        answer = client.post("/api/chat/upload", files={"file": ("note.txt", b"hello", "text/plain")})
    assert answer.status_code == 507, answer.text
    assert answer.json()["detail"]["code"] == "space_limit"


def test_a_full_space_gets_no_image_made(matrix, enforced, monkeypatch) -> None:
    """The image tool asks before the provider is called: an image a full
    space could not save is never made (or paid for)."""
    from kazma_core.tools import image_gen
    from kazma_core.tools.image_backends import router

    made: list[str] = []

    class _Backend:
        async def generate(self, prompt: str, width: int, height: int) -> bytes:
            made.append(scope.current_scope().space_id)
            return b"\x89PNG\r\n\x1a\n" + b"0" * 64

    monkeypatch.setattr(router, "get_backend", lambda provider: _Backend())
    monkeypatch.setattr(router, "resolve_provider", lambda provider: "stand-in")
    _limits(A, disk_bytes=1024)
    _fill_folder(A, 4096)
    with _as(matrix, A):
        answer = asyncio.run(image_gen.generate_image("a lighthouse"))
    assert answer.startswith("Error:") and "reached their limit" in answer and made == []
    with _as(matrix, B):
        answer = asyncio.run(image_gen.generate_image("a lighthouse"))
    assert made == [B] and not answer.startswith("Error:"), answer


# ── documents and libraries ──────────────────────────────────────────────


def _publish(repo: Any, tenant: str, doc: Any, size: int, salt: str) -> Any:
    digest = hashlib.sha256(f"{tenant}-{salt}".encode()).hexdigest()
    blob = repo.register_blob(tenant_id=tenant, sha256=digest, byte_size=size, storage_kind="originals")
    return repo.create_version(
        tenant_id=tenant, document_id=doc.id, actor_id="owner", source_blob_id=blob.id,
        source_sha256=digest, original_filename=f"{salt}.txt", mime_type="text/plain",
    )


def _documents(tmp_path: Path) -> tuple[Any, Any, Any]:
    from kazma_core.documents.repository import DocumentRepository

    repo = DocumentRepository(tmp_path / "documents.db", tenant_quota_bytes=10**9)
    return (repo, repo.create_document(tenant_id=A, owner_id="owner", title="a"),
            repo.create_document(tenant_id=B, owner_id="owner", title="b"))


def test_a_space_at_its_documents_limit_is_refused_and_another_publishes(matrix, enforced, tmp_path) -> None:
    _limits(A, documents_bytes=1000)
    repo, mine, theirs = _documents(tmp_path)
    try:
        _publish(repo, A, mine, 600, "first")
        with pytest.raises(ValueError, match="quota exceeded"):
            _publish(repo, A, mine, 600, "second")
        _publish(repo, B, theirs, 600, "first")
        _publish(repo, B, theirs, 600, "second")  # B's limit is its own
    finally:
        repo.close()


def test_control_the_install_s_one_value_lets_the_space_past_its_limit(matrix, enforced, tmp_path,
                                                                      monkeypatch) -> None:
    from kazma_core.documents import repository

    monkeypatch.setattr(repository, "_space_quota", lambda tenant, install_value: install_value)
    _limits(A, documents_bytes=1000)
    repo, mine, _theirs = _documents(tmp_path)
    try:
        _publish(repo, A, mine, 600, "first")
        _publish(repo, A, mine, 600, "second")
    finally:
        repo.close()


def _knowledge(matrix: Matrix, tmp_path: Path) -> Any:
    from kazma_core.stores.knowledge import KnowledgeStore

    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    for space in (A, B):
        with _as(matrix, space):
            made = store.create_library("Docs", name="Docs")
            url = f"https://{space}.example/"
            text = "words of the page " * 20
            store.publish_page(made["id"], url, [{
                "library_id": made["id"], "source_url": url, "chunk_index": 0, "content": text,
                "content_hash": hashlib.sha256(text.encode()).hexdigest(), "document_title": "T",
                "section_header": "S"}])
    return store


def test_a_space_at_its_knowledge_limit_is_refused_and_another_goes_on(matrix, enforced, tmp_path,
                                                                     monkeypatch) -> None:
    from kazma_core.stores import knowledge
    from kazma_core.stores.knowledge import KnowledgeSpaceFull

    monkeypatch.setattr(knowledge, "_SPACE_BYTES_TTL_S", 0.0)
    store = _knowledge(matrix, tmp_path)
    try:
        _limits(A, knowledge_bytes=1)
        with _as(matrix, A), pytest.raises(KnowledgeSpaceFull):
            store.ensure_capacity("more")
        with _as(matrix, B):
            store.ensure_capacity("more")
    finally:
        store.close()


def test_control_without_the_space_s_own_knowledge_limit_it_goes_on(matrix, enforced, tmp_path,
                                                                   monkeypatch) -> None:
    from kazma_core.stores import knowledge

    monkeypatch.setattr(knowledge, "_SPACE_BYTES_TTL_S", 0.0)
    monkeypatch.setattr(space_limits, "knowledge_bytes", lambda space, default: default)
    store = _knowledge(matrix, tmp_path)
    try:
        _limits(A, knowledge_bytes=1)
        with _as(matrix, A):
            store.ensure_capacity("more")
    finally:
        store.close()


# ── whose limits ─────────────────────────────────────────────────────────


def test_a_space_s_own_limits_over_the_limits_for_spaces_over_kazma_s(matrix, enforced) -> None:
    assert space_limits._limits_of(A) == space_limits.SHIPPED
    people.set_default_space_limits({"crawls": 1, "swarm": 3}, by=OWNER_ACCOUNT_ID)
    _limits(A, swarm=7)
    assert space_limits._limits_of(A).swarm == 7 and space_limits._limits_of(A).crawls == 1
    assert space_limits._limits_of(B).swarm == 3
    assert space_limits._limits_of(B).disk_bytes == space_limits.SHIPPED.disk_bytes
    people.set_space_limits(A, None, by=OWNER_ACCOUNT_ID)
    assert space_limits._limits_of(A).swarm == 3
    kinds = [e["kind"] for e in identity.read_registry()["events"]]
    assert kinds[-3:] == ["default_space_limits_set", "space_limits_set", "space_limits_set"]
    assert people.broken_at() is None


def test_with_no_limit_set_the_install_s_own_values_apply(matrix, enforced) -> None:
    """Documents and knowledge follow the install's values until a limit is
    set for the space or for every space."""
    assert space_limits.documents_bytes(A, 123) == 123
    assert space_limits.knowledge_bytes(A, 456) == 456
    people.set_default_space_limits({"knowledge_bytes": 7000}, by=OWNER_ACCOUNT_ID)
    assert space_limits.knowledge_bytes(A, 456) == 7000 and space_limits.documents_bytes(A, 123) == 123
    _limits(A, documents_bytes=9000)
    assert space_limits.documents_bytes(A, 123) == 9000 and space_limits.documents_bytes(B, 123) == 123


def test_the_owner_s_space_has_no_disk_limit(matrix, enforced) -> None:
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        space_limits.refuse_write_for_disk(10**12)
    assert space_limits.disk_refusal(OWNER_SPACE_ID, 10**12) is None


def test_not_enforced_nothing_is_read_and_the_install_s_values_apply(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)

    def no_reading() -> Any:
        raise AssertionError("the identity store was read")

    monkeypatch.setattr(identity, "read_registry", no_reading)
    from kazma_core.spaces import fairness

    assert fairness.shares("swarm", A) is None
    assert space_limits.running_refusal("crawls", A, 99) is None
    assert space_limits.disk_refusal(A, 10**12) is None
    assert space_limits.documents_bytes(A, 123) == 123
    assert space_limits.knowledge_bytes(A, 456) == 456
    with scope.bind_scope(A, "someone"):
        space_limits.refuse_write_for_disk(10**12)


@pytest.mark.parametrize("raw, code", [
    ({"swarm": 0}, "bad_limit"), ({"swarm": "lots"}, "bad_limit"), ({"swarm": True}, "bad_limit"),
    ({"crawls": 101}, "bad_limit"), ({"speed": 3}, "unknown_limit"), ([1, 2], "bad_limits"),
])
def test_limits_that_are_not_limits_are_refused(raw, code) -> None:
    with pytest.raises(space_limits.LimitsRefused) as refused:
        space_limits.parse_limits(raw)
    assert refused.value.code == code


# ── the admin's page ─────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.people_api import create_people_router

    who: dict[str, Any] = {"principal": {"source": "secret", "role": "admin", "user_id": "",
                                         "actor": "secret", "username": "admin"}}
    monkeypatch.setattr(auth, "get_request_principal", lambda request: who["principal"])
    monkeypatch.setattr(auth, "admin_decision",
                        lambda request: "ok" if who["principal"].get("role") == "admin" else "forbidden")
    app = FastAPI()
    app.include_router(create_people_router())
    with TestClient(app) as made:
        made.who = who
        yield made


def test_the_admin_sets_limits_and_sees_each_space_s_usage(matrix, enforced, client) -> None:
    _fill_folder(A, 3000)
    assert client.put(f"/api/people/spaces/{A}/limits", json={"limits": {"swarm": 3, "disk_bytes": 4096}}
                      ).json()["ok"]
    assert client.put("/api/people/space-limits", json={"limits": {"crawls": 1}}).json()["ok"]
    usage = client.get("/api/people/spaces/usage").json()
    assert usage["ok"] and usage["enforced"] and usage["set"]
    assert usage["limits_for_spaces"]["crawls"] == 1 and usage["shipped"] == space_limits.SHIPPED.as_dict()
    spaces = {s["id"]: s for s in usage["spaces"]}
    assert usage["spaces"][0]["id"] == OWNER_SPACE_ID and not spaces[OWNER_SPACE_ID]["has_folder"]
    a = spaces[A]
    assert a["own_limits"] == {"swarm": 3, "disk_bytes": 4096}
    assert a["limits"]["swarm"] == 3 and a["limits"]["crawls"] == 1 and a["disk_bytes"] == 3000
    assert a["crawls"] == 0 and a["research"] == 0 and a["has_folder"]
    assert spaces[B]["own_limits"] == {} and spaces[B]["limits"]["swarm"] == space_limits.SHIPPED.swarm

    assert client.put(f"/api/people/spaces/{A}/limits", json={"limits": None}).json()["ok"]
    assert client.put("/api/people/space-limits", json={"limits": None}).json()["ok"]
    assert space_limits._limits_of(A) == space_limits.SHIPPED


@pytest.mark.parametrize("path, body, status, code", [
    (f"/api/people/spaces/{A}/limits", {"limits": {"speed": 1}}, 400, "unknown_limit"),
    (f"/api/people/spaces/{A}/limits", {"limits": {"swarm": 0}}, 400, "bad_limit"),
    (f"/api/people/spaces/{A}/limits", {"limits": [1]}, 400, "bad_limits"),
    ("/api/people/spaces/no-such-space/limits", {"limits": {"swarm": 2}}, 404, "no_such_space"),
    ("/api/people/space-limits", {"limits": {"research": "x"}}, 400, "bad_limit"),
])
def test_the_page_s_refusals(matrix, enforced, client, path, body, status, code) -> None:
    answer = client.put(path, json=body)
    assert answer.status_code == status and answer.json()["error"] == code, answer.text


def test_a_member_cannot_set_or_read_limits(matrix, enforced, client) -> None:
    client.who["principal"] = dict(matrix.principal_a)
    assert client.put(f"/api/people/spaces/{A}/limits", json={"limits": {"swarm": 100}}).status_code == 403
    assert client.put("/api/people/space-limits", json={"limits": {"swarm": 100}}).status_code == 403
    assert client.get("/api/people/spaces/usage").status_code == 403
    assert space_limits._limits_of(A) == space_limits.SHIPPED


def test_limits_are_set_only_once_spaces_is_on(matrix, client, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    answer = client.put(f"/api/people/spaces/{A}/limits", json={"limits": {"swarm": 2}})
    assert answer.status_code == 409 and answer.json()["error"] == "spaces_off"
