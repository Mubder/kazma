"""The crawl journal: one row per job, byte-aware retention, owners with an
install, a host and a lease, one crawl per library, one source of crawl
state (Knowledge audit 2026-10-13: KB-E04, KB-E07, KB-E14).
"""

from __future__ import annotations

import ast
import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
from kazma_core import config_store
from kazma_core.web_acquire import crawl_jobs
from kazma_core.web_acquire.result import AcquisitionError

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


def _policy(max_pages: int = 1000) -> dict:
    return {"mode": "local_only", "scope": "tree", "max_pages": max_pages}


def _finished_row(job_id: str, library: str, receipt_chars: int, age: float) -> dict:
    now = time.time() - age
    pages = [{"url": f"https://docs.example.test/{job_id}/page-{n:04d}", "status": "completed",
              "receipt": {"evidence": "e" * receipt_chars}, "error": ""} for n in range(1000)]
    return dict(job_id=job_id, library_id=library, seed="https://docs.example.test/",
                policy={"mode": "local_only"}, pages=pages, discovered=True, claim="0" * 32,
                owner=None, status="completed", created_at=now, updated_at=now, fetched=1000,
                failed=0, ingested=5000, skipped=0, pages_unchanged=0, progress={})


def _fill_with_finished(count: int, row_bytes: int) -> list[str]:
    base = len(json.dumps(_finished_row("old-0", "lib", 0, 1000), ensure_ascii=True))
    receipt = max(0, (row_bytes - base) // 1000)
    ids = [f"old-{i}" for i in range(count)]

    def change(jobs):
        for i, job_id in enumerate(ids):
            jobs[job_id] = _finished_row(job_id, f"lib-old-{i}", receipt, 1000 - i)

    crawl_jobs._mutate(change)
    return ids


def _registry_bytes() -> int:
    return sum(len(json.dumps(row, ensure_ascii=True)) for row in crawl_jobs._read()[1].values())


def _recording(store, monkeypatch) -> list[int]:
    written: list[int] = []
    real = store.batch_set

    def record(items, **kw):
        written.append(sum(len(json.dumps(v, ensure_ascii=True)) for _k, v, _c in items))
        return real(items, **kw)

    monkeypatch.setattr(store, "batch_set", record)
    return written


# ── KB-E04 ─────────────────────────────────────────────────────────────────


def test_a_registry_full_of_finished_crawls_admits_a_new_1000_page_crawl(journal):
    retained = _fill_with_finished(4, int(3.5 * 1024**2))
    assert _registry_bytes() > crawl_jobs._MAX_BYTES - crawl_jobs._MAX_JOB_BYTES  # no room for a full job
    big = crawl_jobs.start("newlib", "https://third.example.test/docs/", _policy())
    urls = [f"https://third.example.test/docs/page-{n:04d}" for n in range(1000)]
    big.frontier(urls)  # 1000 pages fit the job's budget
    for url in urls[:25]:  # and the crawl runs
        assert big.begin_page(url)
        big.end_page(url, new=3)
    big.finish("completed")
    jobs = crawl_jobs.inspect_jobs()
    kept = {row["job_id"] for row in jobs}
    assert retained[0] not in kept  # the oldest finished crawl made room
    assert crawl_jobs._read()[1][big.job_id]["fetched"] == 25
    assert _registry_bytes() <= crawl_jobs._MAX_BYTES


def test_the_count_only_eviction_kept_a_wedged_registry():
    """Negative control: the old rule evicted while len(jobs) >= 24 only --
    three large finished crawls held the budget and nothing was evicted."""
    jobs = {f"old-{i}": {"status": "completed", "updated_at": i, "bytes": 2_800_000} for i in range(3)}
    old = sorted(jobs, key=lambda key: jobs[key]["updated_at"])
    while len(jobs) >= 24 and old:
        del jobs[old.pop(0)]
    assert len(jobs) == 3 and sum(j["bytes"] for j in jobs.values()) > 8 * 1024**2


def test_a_page_update_writes_its_own_job_only(journal, monkeypatch):
    written = _recording(journal, monkeypatch)
    lonely = crawl_jobs.start("lib-a", "https://a.example.test/", _policy())
    lonely.frontier(["https://a.example.test/x", "https://a.example.test/y"])
    written.clear()
    assert lonely.begin_page("https://a.example.test/x")
    alone = written[-1]
    lonely.finish("completed")
    _fill_with_finished(3, 3 * 1024**2)
    crowded = crawl_jobs.start("lib-b", "https://b.example.test/", _policy())
    crowded.frontier(["https://b.example.test/x", "https://b.example.test/y"])
    written.clear()
    assert crowded.begin_page("https://b.example.test/x")
    beside = written[-1]
    crowded.finish("completed")
    # The same bytes, whatever the other jobs hold (they hold ~9 MB here).
    assert abs(beside - alone) < 256 and beside < 4096, (alone, beside)


def test_four_concurrent_crawls_never_find_the_journal_busy(journal):
    errors: list[BaseException] = []

    def crawl(i: int) -> None:
        try:
            job = crawl_jobs.start(f"lib-{i}", f"https://c{i}.example.test/", _policy())
            urls = [f"https://c{i}.example.test/p{n}" for n in range(30)]
            job.frontier(urls)
            for url in urls:
                job.begin_page(url)
                job.end_page(url, new=1)
            job.finish("completed")
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=crawl, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(row["fetched"] for row in crawl_jobs._read()[1].values()) == [30, 30, 30, 30]


def test_the_operator_purge_removes_finished_jobs_and_never_a_running_one(journal):
    _fill_with_finished(3, 10_000)
    running = crawl_jobs.start("live", "https://live.example.test/", _policy())
    assert crawl_jobs.purge_terminal() == 3
    assert [row["job_id"] for row in crawl_jobs.inspect_jobs()] == [running.job_id]
    running.finish("completed")


def test_the_cli_purge_reports_what_it_removed(journal, capsys):
    from kazma_cli.acquisition import run

    _fill_with_finished(2, 10_000)
    assert run(["purge-finished"]) == 0
    assert "Removed finished crawl records: 2" in capsys.readouterr().out
    assert crawl_jobs.inspect_jobs() == []


def test_a_registry_in_the_old_single_value_moves_in(journal):
    rows = {"legacy": _finished_row("legacy", "lib", 10, 50)}
    journal.batch_set([(crawl_jobs._KEY, rows, crawl_jobs._CATEGORY)])
    assert [row["job_id"] for row in crawl_jobs.inspect_jobs()] == ["legacy"]
    assert crawl_jobs._KEY not in journal.get_category(crawl_jobs._CATEGORY)
    assert crawl_jobs._job_key("legacy") in journal.get_category(crawl_jobs._CATEGORY)


# ── KB-E07 ─────────────────────────────────────────────────────────────────


def _running_row(job_id: str, owner, *, age: float = 0.0) -> dict:
    now = time.time() - age
    return dict(job_id=job_id, library_id=f"lib-{job_id}", seed="https://docs.example.test/",
                policy={"mode": "local_only"},
                pages=[{"url": "https://docs.example.test/p1", "status": "running", "receipt": None}],
                discovered=True, claim="a" * 32, owner=owner, status="running", created_at=now,
                updated_at=now, fetched=0, failed=0, ingested=0, skipped=0, pages_unchanged=0, progress={})


def _dead_pid() -> int:
    import psutil

    pid = 4_000_000
    while psutil.pid_exists(pid):
        pid += 4
    return pid


def _put(row: dict) -> None:
    crawl_jobs._mutate(lambda jobs: jobs.update({row["job_id"]: row}))


def test_a_live_crawl_on_another_host_survives_another_start(journal):
    install, _host = crawl_jobs._identity()
    owner = {"install": install, "host": "another-host", "pid": _dead_pid(), "started": 1.0,
             "lease_until": time.time() + 300}
    _put(_running_row("remote", owner))
    other = crawl_jobs.start("lib9", "https://unrelated.example.test/", _policy())
    try:
        assert crawl_jobs.inspect_jobs("remote")["status"] == "running"
    finally:
        other.finish("completed")


def test_a_dead_owner_on_this_host_is_recovered(journal):
    install, host = crawl_jobs._identity()
    owner = {"install": install, "host": host, "pid": _dead_pid(), "started": 1.0,
             "lease_until": time.time() + 300}
    _put(_running_row("local-dead", owner))
    other = crawl_jobs.start("lib9", "https://unrelated.example.test/", _policy())
    try:
        row = crawl_jobs.inspect_jobs("local-dead")
        assert row["status"] == "interrupted" and row["pages"][0]["status"] == "ambiguous"
    finally:
        other.finish("completed")


def test_a_lapsed_lease_is_taken_over_and_the_old_owner_is_fenced(journal):
    install, _host = crawl_jobs._identity()
    job = crawl_jobs.start("leased", "https://leased.example.test/", _policy())
    job.frontier(["https://leased.example.test/a", "https://leased.example.test/b"])

    def elsewhere(jobs):  # the owner is on another host, and stopped renewing
        jobs[job.job_id]["owner"] = {"install": install, "host": "another-host", "pid": 1,
                                     "started": 1.0, "lease_until": time.time() - 1}

    crawl_jobs._mutate(elsewhere)
    crawl_jobs.recover()
    assert crawl_jobs.inspect_jobs(job.job_id)["status"] == "interrupted"
    with pytest.raises(AcquisitionError, match="ownership"):
        job.begin_page("https://leased.example.test/a")  # the fence: its claim is gone


def test_another_installs_crawls_are_not_this_installs(journal):
    owner = {"install": "another-install", "host": socket.gethostname(), "pid": _dead_pid(),
             "started": 1.0, "lease_until": time.time() + 300}
    row = _running_row("theirs", owner)
    row["install"] = "another-install"
    journal.batch_set([(crawl_jobs._job_key("theirs"), row, crawl_jobs._CATEGORY)])
    assert crawl_jobs.inspect_jobs() == []  # scoped to this install
    other = crawl_jobs.start("lib9", "https://unrelated.example.test/", _policy())
    other.finish("completed")
    assert journal.get_category(crawl_jobs._CATEGORY)[crawl_jobs._job_key("theirs")]["status"] == "running"


def test_the_old_owner_identity_took_any_foreign_crawl():
    """Negative control: the old rule -- (pid, create_time) probed locally --
    calls a live crawl on another host dead whenever its pid is free here."""
    pid = _dead_pid()

    def old_alive(identity):
        import psutil

        try:
            process = psutil.Process(identity[0])
            return process.create_time() == identity[1]
        except psutil.NoSuchProcess:
            return False

    assert old_alive([pid, 1_700_000_000.0]) is False  # -> taken over, though it runs elsewhere


def test_one_process_has_one_install_id(tmp_path):
    """Crawl ownership is scoped by install id. The first callers raced: the
    exclusive create leaves an empty file before its id is written, and a
    second caller took it for a broken file and minted its own -- one
    process, two ids (found on real Postgres: more crawls admitted than the
    four allowed, the other id's running jobs read as another install's)."""
    from kazma_core.db.shared_store_peers import install_id

    for round_ in range(10):
        folder = tmp_path / f"round-{round_}"
        barrier = threading.Barrier(12)
        seen: list[str] = []

        def call():
            barrier.wait()
            seen.append(install_id(folder))

        threads = [threading.Thread(target=call) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(seen)) == 1, seen


def test_a_creator_that_has_not_written_yet_is_waited_for(tmp_path):
    from kazma_core.db.shared_store_peers import install_id

    (tmp_path / "install_id").write_text("", encoding="utf-8")  # created, not yet written
    theirs = "a" * 32

    def late_writer():
        time.sleep(0.1)
        (tmp_path / "install_id").write_text(theirs + "\n", encoding="utf-8")

    writer = threading.Thread(target=late_writer)
    writer.start()
    try:
        mine = install_id(tmp_path)
    finally:
        writer.join()
    assert mine == theirs


def test_the_old_minting_split_one_install_in_two(tmp_path):
    """Negative control: the old branch read the empty file once and
    replaced it with an id of its own."""
    import uuid

    path = tmp_path / "install_id"
    path.write_text("", encoding="utf-8")
    current = path.read_text(encoding="utf-8").strip()
    old_answer = current if len(current) == 32 else uuid.uuid4().hex  # the old fall-through
    path.write_text("b" * 32 + "\n", encoding="utf-8")  # the first creator's id lands
    assert old_answer != path.read_text(encoding="utf-8").strip()


def test_a_running_owner_renews_its_lease(journal):
    job = crawl_jobs.start("renew", "https://renew.example.test/", _policy())

    def lapse(jobs):  # its lease ran out a minute ago
        jobs[job.job_id]["owner"]["lease_until"] = time.time() - 60

    crawl_jobs._mutate(lapse)
    job.frontier(["https://renew.example.test/a"])
    renewed = crawl_jobs._read_job(journal, job.job_id)[0]["owner"]["lease_until"]
    assert renewed > time.time() + crawl_jobs._LEASE_S - 60  # the write renewed it
    job.finish("completed")


# ── KB-E14 ─────────────────────────────────────────────────────────────────


def test_a_second_crawl_of_a_running_library_is_refused(journal):
    first = crawl_jobs.start("docs", "https://docs.example.test/", _policy())
    try:
        with pytest.raises(crawl_jobs.CrawlLibraryBusy):
            crawl_jobs.start("docs", "https://docs.example.test/other/", _policy())
        # Another library, or a research crawl, is not held by it.
        crawl_jobs.start("docs-2", "https://docs.example.test/", _policy()).finish("completed")
    finally:
        first.finish("completed")
    crawl_jobs.start("docs", "https://docs.example.test/", _policy()).finish("completed")


@pytest.mark.asyncio
async def test_two_links_that_land_on_one_page_index_it_once(journal, tmp_path, monkeypatch):
    import contextlib

    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex
    from kazma_core.web_acquire.result import FetchResult

    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    try:
        store.create_library("docs", "Docs")
        index = KnowledgeIndex(store)
        index._vector_store_for = lambda _lib: type("V", (), {"available": False})()  # type: ignore[method-assign]
        landing = "https://docs.example.test/guide"
        pages = ["https://docs.example.test/old-guide", "https://docs.example.test/guide-v1"]

        async def fetch(url, **kwargs):
            return FetchResult(ok=True, url=url, final_url=landing, text="# Guide\n\n" + "useful words " * 40,
                               http_status=200)

        async def discover(*a, **k):
            return list(pages)

        monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ())
        monkeypatch.setattr(ki, "kb_discover_pages", discover)
        monkeypatch.setattr(ki, "_web_fetch_text", fetch)
        monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
        monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
        monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
        monkeypatch.setattr(ki, "_pw_browser_scope", contextlib.nullcontext)
        monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
        updates = [u async for u in ki.ingest_site("docs", "https://docs.example.test/", job_id="redirect")]
        assert updates[-1].phase == "done"
        assert store.list_source_urls("docs") == [landing]
    finally:
        store.close()


# Files that may still NAME the retired registry, and why.
_NAMES_THE_RETIRED_REGISTRY = {
    # It removes the retired key once.
    "kazma-core/kazma_core/web_acquire/crawl_jobs.py",
    # An older settings backup can hold the key: it stays classed RUNTIME so a
    # restore never writes it back.
    "kazma-core/kazma_core/settings_restore.py",
}


def _crawl_state_sources(path: str, source: str) -> list[str]:
    """Code outside the journal that keeps crawl state of its own (comments
    and docstrings are not code)."""
    if path in _NAMES_THE_RETIRED_REGISTRY:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant)}
    words: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            words.append(node.value)
        elif isinstance(node, ast.Name):
            words.append(node.id)
        elif isinstance(node, ast.Attribute):
            words.append(node.attr)
        elif isinstance(node, ast.alias):
            words.append(node.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            words.append(node.module)
    found = []
    for marker in ("kb.ingest_jobs", "kb_jobs", "_kb_api_jobs"):
        if any(marker in word for word in words):
            found.append(f"{path}: {marker}")
    return found


def test_crawl_state_has_one_source():
    """Nothing but the crawl journal keeps Knowledge crawl state: the second
    registry (kb.ingest_jobs, the API's in-memory copy, /kb status's
    fallback) is gone."""
    listed = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True,
                            check=True).stdout.split()
    offenders = []
    for rel in listed:
        if not rel.startswith("kazma-") or "/tests/" in rel or "_tests/" in rel:
            continue
        path = REPO / rel
        if path.exists():
            offenders += _crawl_state_sources(rel, path.read_text(encoding="utf-8", errors="replace"))
    assert offenders == [], offenders
    assert not (REPO / "kazma-core/kazma_core/stores/kb_jobs.py").exists()


def test_the_single_source_gate_sees_a_second_registry():
    """Negative control: the API's old copy of the job state."""
    assert _crawl_state_sources("kazma-ui/kazma_ui/kb_api.py", "_kb_api_jobs: dict = {}\nupsert_job('kb.ingest_jobs')")


def test_the_api_reads_crawl_state_from_the_journal_only():
    tree = ast.parse((REPO / "kazma-ui/kazma_ui/kb_api.py").read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                and (node.module or "").endswith("crawl_jobs") for alias in node.names}
    assert {"inspect_jobs", "projection"} <= imported
