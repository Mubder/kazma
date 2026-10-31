"""A crawl's outcome says what happened to each page (live 2026-10-31).

"Partial" covered a failed page, a page robots.txt kept out, the owner's
page cap and an uncertain fetch alike, so nearly every real crawl read as a
problem; and a one-second DNS answer of 10.0.0.1 during a connection drop
failed a page for good. Every page now ends ingested, unchanged, excluded
(a rule) or failed (with a reason), a temporary failure is tried again inside
the crawl, and only a real failure makes a crawl partial.
"""
from __future__ import annotations

import contextlib

import pytest
from kazma_core import config_store
from kazma_core.web_acquire import crawl_jobs, page_outcome
from kazma_core.web_acquire.result import FetchErrorCode, FetchResult

SEED = "https://docs.example.test/"


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


def ok(url):
    return FetchResult(ok=True, url=url, final_url=url, text="# Page\n\n" + "useful words " * 40, http_status=200)


def refused(url, code, *, status=None, transient=False):
    return FetchResult(ok=False, url=url, error=f"{code} for {url}", error_code=code, http_status=status,
                       transient=transient)


async def _crawl(tmp_path, monkeypatch, script, *, job_id="run", waits=(0, 0), budget=20):
    """Crawl ``script``'s pages; each page answers its list in turn."""
    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = KnowledgeStore(db_path=str(tmp_path / f"knowledge-{job_id}.db"))
    store.create_library("docs", "Docs")
    index = KnowledgeIndex(store)
    index._vector_store_for = lambda _lib: type("V", (), {"available": False})()  # type: ignore[method-assign]
    calls: list[str] = []
    answers = {url: list(results) for url, results in script.items()}

    async def fetch(url, **kwargs):
        calls.append(url)
        queue = answers[url]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    async def discover(*a, **k):
        return list(script)

    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ())
    monkeypatch.setattr(ki, "kb_discover_pages", discover)
    monkeypatch.setattr(ki, "_web_fetch_text", fetch)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(ki, "_pw_browser_scope", contextlib.nullcontext)
    monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
    monkeypatch.setattr(ki, "_PAGE_RETRY_WAITS", waits)
    monkeypatch.setattr(ki, "_PAGE_RETRY_BUDGET", budget)
    try:
        updates = [u async for u in ki.ingest_site("docs", SEED, job_id=job_id)]
    finally:
        store.close()
    return updates, crawl_jobs.projection(job_id), calls


def _outcomes(projection):
    return {page["url"].rsplit("/", 1)[-1]: (page["outcome"], page["reason"]) for page in projection["pages"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status,reason", [
    (FetchErrorCode.ROBOTS, None, "robots"),
    (FetchErrorCode.SCOPE, None, "out_of_scope"),
    (FetchErrorCode.LOGIN, None, "login"),
    (FetchErrorCode.HTTP, 404, "gone"),
])
async def test_a_page_a_rule_keeps_out_leaves_the_crawl_completed(journal, tmp_path, monkeypatch, code, status, reason):
    a, b = SEED + "a", SEED + "b"
    updates, view, _ = await _crawl(tmp_path, monkeypatch, {a: [ok(a)], b: [refused(b, code, status=status)]})
    assert view["acquisition"]["status"] == "completed", view["message"]
    assert view["failed"] == 0 and view["excluded"] == 1 and view["notes"] == ["excluded"]
    assert _outcomes(view) == {"a": ("ingested", ""), "b": ("excluded", reason)}
    assert updates[-1].excluded == 1 and "excluded" in updates[-1].message


@pytest.mark.asyncio
async def test_the_old_rule_made_the_same_crawl_partial(journal, tmp_path, monkeypatch):
    """Negative control: judged a failure, the robots page makes it partial."""
    monkeypatch.setattr(page_outcome, "judge", lambda *a, **k: page_outcome.PageVerdict("failed", "other"))
    monkeypatch.setattr("kazma_core.stores.knowledge_ingest.judge", page_outcome.judge, raising=False)
    a, b = SEED + "a", SEED + "b"
    _, view, _ = await _crawl(tmp_path, monkeypatch, {a: [ok(a)], b: [refused(b, FetchErrorCode.ROBOTS)]},
                              job_id="control")
    assert view["acquisition"]["status"] == "partial" and view["failed"] == 1


@pytest.mark.asyncio
async def test_a_real_failure_still_makes_the_crawl_partial(journal, tmp_path, monkeypatch):
    a, b = SEED + "a", SEED + "b"
    _, view, calls = await _crawl(tmp_path, monkeypatch, {a: [ok(a)], b: [refused(b, FetchErrorCode.HTTP, status=403)]})
    assert view["acquisition"]["status"] == "partial"
    assert _outcomes(view)["b"] == ("failed", "http_error")
    assert calls.count(b) == 1  # a refusal is not a hiccup: no retry


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [
    refused(SEED + "b", FetchErrorCode.CONNECT, transient=True),
    refused(SEED + "b", FetchErrorCode.HTTP, status=503),
    # The live case: the router answered 10.0.0.1 while the line was down.
    refused(SEED + "b", FetchErrorCode.SECURITY),
], ids=["connection", "server-busy", "private-dns-once"])
async def test_a_temporary_failure_is_retried_inside_the_crawl(journal, tmp_path, monkeypatch, first):
    a, b = SEED + "a", SEED + "b"
    updates, view, calls = await _crawl(tmp_path, monkeypatch, {a: [ok(a)], b: [first, ok(b)]})
    assert view["acquisition"]["status"] == "completed" and view["failed"] == 0
    assert calls.count(b) == 2
    assert any("retrying" in u.message for u in updates)


@pytest.mark.asyncio
async def test_without_retries_the_same_hiccup_fails_the_page(journal, tmp_path, monkeypatch):
    """Negative control: no retry waits, the live outcome."""
    b = SEED + "b"
    _, view, calls = await _crawl(tmp_path, monkeypatch, {b: [refused(b, FetchErrorCode.SECURITY), ok(b)]},
                                  waits=(), job_id="no-retry")
    assert view["acquisition"]["status"] == "failed" and calls.count(b) == 1


@pytest.mark.asyncio
async def test_a_host_that_stays_private_fails_with_its_reason(journal, tmp_path, monkeypatch):
    a, b = SEED + "a", SEED + "b"
    _, view, calls = await _crawl(tmp_path, monkeypatch, {a: [ok(a)], b: [refused(b, FetchErrorCode.SECURITY)]})
    assert _outcomes(view)["b"] == ("failed", "blocked_address")
    assert calls.count(b) == 3  # the first try and two retries, every check run each time


@pytest.mark.asyncio
async def test_the_retry_budget_bounds_a_site_that_is_down(journal, tmp_path, monkeypatch):
    script = {SEED + f"p{n}": [refused(SEED + f"p{n}", FetchErrorCode.TIMEOUT, transient=True)] for n in range(4)}
    _, view, calls = await _crawl(tmp_path, monkeypatch, script, budget=2)
    assert len(calls) == 4 + 2
    assert all(outcome == ("failed", "network") for outcome in _outcomes(view).values())


@pytest.mark.parametrize("row,status,notes", [
    (dict(page_capped=True), "completed", ["page_cap"]),
    (dict(discovery={"limited": True}), "completed", ["link_walk_bounded"]),
    (dict(metadata_limited=True), "completed", ["sitemap_bounded"]),
    (dict(excluded=2, fetched=1), "completed", ["excluded"]),
    (dict(excluded=2), "completed", ["excluded", "nothing_stored"]),
    (dict(discovery={"ambiguous": ["x"], "limited": False}), "partial", []),
    (dict(metadata={"u": {"status": "ambiguous", "result": None}}), "partial", []),
])
def test_limits_and_exclusions_are_notes_uncertainty_is_partial(row, status, notes):
    from kazma_core.stores.knowledge_ingest import crawl_verdict

    assert crawl_verdict("completed", row) == (status, notes)
    assert crawl_verdict("partial", row)[0] == "partial"  # a failed page stays partial


@pytest.mark.parametrize("code,status,outcome", [
    ("robots_disallowed", None, "excluded"), ("scope_blocked", None, "excluded"),
    ("login_required", None, "excluded"), ("http_error", 410, "excluded"),
    ("http_error", 500, "failed"), ("security_blocked", None, "failed"), ("timeout", None, "failed"),
])
def test_judge(code, status, outcome):
    assert page_outcome.judge(code, status).outcome == outcome


def test_a_page_larger_than_kazma_reads_is_kept_out_by_its_size():
    """Kazma's size limit is a rule like robots.txt (the recorded corpus,
    2026-10-31: the Rust book's print.html and Python's changelog)."""
    assert page_outcome.judge("acquisition_limit", 200, truncated=True) == page_outcome.PageVerdict("excluded", "too_large")


@pytest.mark.asyncio
async def test_a_crawl_whose_every_page_a_rule_keeps_out_stored_nothing_and_did_not_fail(journal, tmp_path, monkeypatch):
    """Robots.txt kept out the one page asked for: a warning that nothing was
    stored, never "failed" (the corpus's google and 404 sites, 2026-10-31)."""
    a = SEED + "a"
    updates, view, _ = await _crawl(tmp_path, monkeypatch, {a: [refused(a, FetchErrorCode.ROBOTS)]})
    assert view["acquisition"]["status"] == "completed" and updates[-1].phase == "done"
    assert view["notes"] == ["excluded", "nothing_stored"]


@pytest.mark.parametrize("seed,inside,outside", [
    ("https://docs.python.org/3/tutorial/", "https://docs.python.org/3/tutorial/appetite.html",
     "https://docs.python.org/3/whatsnew/changelog.html"),
    ("https://docs.example.test/v2/guide/", "https://docs.example.test/v2/guide/a", "https://docs.example.test/v2/blog/x"),
    ("https://example.test/2026/whatsapp/", "https://example.test/2026/whatsapp/a", "https://example.test/2026/other/b"),
])
def test_a_version_is_not_a_section(seed, inside, outside):
    """"This section" of docs.python.org/3/tutorial/ took "3" as its topic
    and so every Python 3 page (the recorded corpus, 2026-10-31)."""
    from kazma_core.stores.knowledge_ingest import _in_scope

    assert _in_scope(seed, inside, "tree") and not _in_scope(seed, outside, "tree")


@pytest.mark.asyncio
async def test_just_this_page_follows_the_page_moving_within_its_site(journal, tmp_path, monkeypatch):
    """rfc-editor.org moved /rfc/rfcNNNN to /info/rfcNNNN/: "just this page"
    refused the move as out of scope (the recorded corpus, 2026-10-31)."""
    from kazma_core.stores import knowledge_ingest as ki

    seen = []
    real = ki._extract_page

    async def extract(url, *, scope_check=None):
        seen.append((scope_check(SEED + "moved/"), scope_check("https://elsewhere.test/")))
        return await real(url, scope_check=scope_check)

    monkeypatch.setattr(ki, "_extract_page", extract)
    real_crawl = ki.ingest_site

    async def exact(*args, **kwargs):
        async for update in real_crawl(*args, scope="exact", **kwargs):
            yield update

    monkeypatch.setattr(ki, "ingest_site", exact)
    _, view, _ = await _crawl(tmp_path, monkeypatch, {SEED: [ok(SEED)]})
    assert view["acquisition"]["policy"]["scope"] == "exact"
    assert seen == [(True, False)]  # the same site, never another
