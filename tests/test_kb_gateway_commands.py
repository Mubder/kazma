"""``/kb`` in the chat apps does what ``/help``, the menu and the reference say
(Knowledge audit 2026-10-13, KB-S07).

``/kb add|crawl|refresh|delete`` were rewritten into a model turn before the
handler ran, so its branches were dead and no tool could delete a library.
They are handled in the gateway now, admin-only, and each reply reports what
the store or the crawl journal recorded.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from kazma_core import config_store
from kazma_core.stores import knowledge as kb_module
from kazma_core.stores import knowledge_index as ki
from kazma_core.stores.knowledge_ingest import IngestResult
from kazma_gateway.agent_handler import commands
from kazma_gateway.gateway import IncomingMessage
from kazma_gateway.slash_commands import changes_global_config


class _NoVectors:
    available = False
    identity = ""

    def drop(self):
        return False

    def query(self, *a, **k):
        return []


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    journal = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: journal)
    monkeypatch.setattr(ki.KnowledgeIndex, "_vector_store_for", lambda self, library_id: _NoVectors())
    monkeypatch.setattr(ki.KnowledgeIndex, "_open_vector_store", lambda self, library_id: _NoVectors())
    ki.reset_knowledge_index()
    store = kb_module.KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    monkeypatch.setattr(kb_module, "_knowledge_store", store)
    replies: list[str] = []

    async def reply(msg, session_store, manager, thread_id, text):
        replies.append(text)

    monkeypatch.setattr(commands, "_send_model_reply", reply)
    import kazma_core.security.ssrf as ssrf

    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ())
    yield SimpleNamespace(store=store, replies=replies, journal=journal)
    ki.reset_knowledge_index()
    store.close()
    journal.close()


def _say(text: str) -> bool:
    msg = IncomingMessage(platform="telegram", sender_id="telegram:1", text=text)
    return asyncio.run(_handled(msg))


async def _handled(msg) -> bool:
    handled = await commands._try_kb_command(msg, None, None, "th-1")
    # Background crawls report when they end: wait for them here.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not pending:
            break
        await asyncio.sleep(0.02)
    return handled


def _publish(store, library: str, text: str, url: str = "https://docs.example.test/a") -> None:
    from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict

    if not store.library_exists(library):
        store.create_library(library, library, seed_url="https://docs.example.test/")
    chunks = [chunk_to_dict(c) for c in chunk_markdown_doc(text, source_url=url, library_id=library)]
    ki.get_knowledge_index().index(library, chunks)


def _fake_crawl(monkeypatch, *, outcome: str):
    from kazma_core.stores import knowledge_ingest

    async def ingest_site(library_id, url, *, max_pages=None, job_id=None, _prepared=None, **kw):
        job = _prepared[0]
        pages = [url + "p1", url + "p2"]
        job.frontier(pages)
        job.begin_page(pages[0])
        job.end_page(pages[0], new=2)
        if outcome == "partial":
            job.begin_page(pages[1])
            job.end_page(pages[1], error="HTTP 500")
        else:
            job.begin_page(pages[1])
            job.end_page(pages[1], new=1)
        job.finish(outcome)
        yield SimpleNamespace(phase="done")

    monkeypatch.setattr(knowledge_ingest, "ingest_site", ingest_site)


# ── each subcommand ───────────────────────────────────────────────────────


def test_add_reports_the_pages_outcome(kb, monkeypatch):
    from kazma_core.stores import knowledge_ingest

    async def refused(library_id, url, **kw):
        return IngestResult(pages_failed=1, errors=["HTTP 404 for the page"])

    monkeypatch.setattr(knowledge_ingest, "ingest_url", refused)
    assert _say("/kb add docs https://docs.example.test/missing")
    assert kb.replies[-1].startswith("⚠️ Nothing was ingested into `docs`: HTTP 404")

    async def stored(library_id, url, **kw):
        return IngestResult(pages_fetched=1, chunks_new=4)

    monkeypatch.setattr(knowledge_ingest, "ingest_url", stored)
    assert _say("/kb add docs https://docs.example.test/start")
    assert kb.replies[-1] == "✅ Ingested 1 page into `docs`: 4 new chunks."


@pytest.mark.parametrize(("outcome", "said"), [("completed", "✅ The crawl of `docs` finished"),
                                               ("partial", "⚠️ The crawl of `docs` finished with failures")])
def test_crawl_reports_the_journals_outcome(kb, monkeypatch, outcome, said):
    _fake_crawl(monkeypatch, outcome=outcome)
    assert _say("/kb crawl docs https://docs.example.test/ 5")
    assert kb.replies[0].startswith("🚀 Started the crawl of `https://docs.example.test/`")
    assert kb.replies[-1].startswith(said), kb.replies
    assert kb.store.library_exists("docs")


def test_crawl_refuses_a_script_url_and_creates_nothing(kb):
    assert _say("/kb crawl docs javascript:alert(1)")
    assert kb.replies[-1].startswith("⚠️") and not kb.store.library_exists("docs")


def test_refresh_recrawls_from_the_seed(kb, monkeypatch):
    _publish(kb.store, "docs", "# A\n\nAlpha text.\n")
    _fake_crawl(monkeypatch, outcome="completed")
    assert _say("/kb refresh docs")
    assert kb.replies[0].startswith("🚀 Started the refresh of `https://docs.example.test/`")
    assert kb.replies[-1].startswith("✅ The refresh of `docs` finished")


def test_status_reads_the_journal(kb, monkeypatch):
    _fake_crawl(monkeypatch, outcome="partial")
    _say("/kb crawl docs https://docs.example.test/")
    kb.replies.clear()
    assert _say("/kb status docs")
    assert "partial" in kb.replies[-1] and "failed=1" in kb.replies[-1]


def test_delete_takes_the_library_and_refuses_under_a_crawl(kb):
    from kazma_core.web_acquire import crawl_jobs

    _publish(kb.store, "docs", "# A\n\nAlpha text.\n")
    job = crawl_jobs.start("docs", "https://docs.example.test/", {"mode": "local_only"})
    assert _say("/kb delete docs")
    assert "was not deleted" in kb.replies[-1] and kb.store.library_exists("docs")
    job.finish("completed")
    assert _say("/kb delete docs")
    assert kb.replies[-1].startswith("🗑️ Deleted library `docs`")
    assert not kb.store.library_exists("docs")


def test_search_answers_without_a_model(kb):
    _publish(kb.store, "docs", "# Webhooks\n\nWebhooks retry delivery five times.\n")
    assert _say("/kb search docs webhooks retry")
    assert "five times" in kb.replies[-1]


def test_a_multi_user_install_refuses_every_subcommand(kb, monkeypatch):
    import kazma_core.security.platform_rbac as rbac

    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [{"username": "a"}])
    for text in ("/kb", "/kb list", "/kb search docs x", "/kb delete docs"):
        assert _say(text)
        assert "single-operator install" in kb.replies[-1]


# ── who may change the Knowledge Library ──────────────────────────────────


@pytest.mark.parametrize("text", ["/kb add docs https://x.test/", "/kb ingest docs https://x.test/",
                                  "/kb crawl docs https://x.test/", "/kb refresh docs", "/kb delete docs"])
def test_changing_a_library_is_admin_only(text):
    assert changes_global_config(text) is True


@pytest.mark.parametrize("text", ["/kb", "/kb list", "/kb help", "/kb search docs x", "/kb status docs"])
def test_reading_stays_open(text):
    assert changes_global_config(text) is False


def test_the_old_rule_let_anyone_delete_a_library():
    """Negative control: ``changes_global_config`` had no ``/kb`` branch."""

    def old_rule(text: str) -> bool:
        head = text.split()[0]
        return head == "/_models_select"

    assert old_rule("/kb delete docs") is False


def test_kb_reaches_its_handler_unchanged():
    from kazma_core.agent.slash_turns import rewrite_work_slash

    for text in ("/kb delete docs", "/kb crawl docs https://docs.example.test 500", "/kb add docs https://x.test",
                 "/kb refresh docs"):
        assert rewrite_work_slash(text) is None
