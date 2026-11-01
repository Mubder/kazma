"""The paper the agent writes from a research run is kept with that run.

Live 2026-10-31: after a Kuwait history run the owner opened the Research
page and found the run's working report and the chat's raw web searches. The
29-page paper the agent had written from the run (a Markdown file in the
workspace, a PDF in the document folder) was on no page. A run registered
in a chat turn now names that turn, and a PDF or Word document made in the
same turn is copied into the run's folder (``final/``) and named on its
entry, which the Research page lists and opens.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from kazma_core.config_store import get_config_store
from kazma_core.safety.commitment import proposals
from kazma_core.tools import research_final_paper as final_paper
from kazma_core.tools import research_pipeline as pipeline
from kazma_core.tools import research_session as sessions
from kazma_core.web_acquire import resources
from kazma_core.web_acquire.result import FetchResult

TURN = {"thread_id": "kuwait-chat", "created_at": "2026-10-31T20:18:22+00:00"}
OTHER_TURN = {"thread_id": "kuwait-chat", "created_at": "2026-10-31T21:01:15+00:00"}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    store = get_config_store()
    before = store.get("research.papers_index")
    store.set("research.papers_index", [], category="research")
    monkeypatch.setattr(pipeline, "_get_ws_root", lambda: tmp_path)
    yield tmp_path
    store.set("research.papers_index", before if before is not None else [], category="research")


def _bound(turn):
    token = proposals.bind_turn(turn)
    return token


def _run_folder(workspace, run_id="pipeline-17f8", turn=TURN, register=True):
    folder = workspace / "research" / "reports" / run_id
    folder.mkdir(parents=True)
    (folder / "report.md").write_text("# Research report\n\nWorking synthesis.\n", encoding="utf-8")
    if register:
        pipeline._register_paper({
            "id": run_id, "topic": "History of Kuwait", "kind": "research_paper",
            "report_path": f"research/reports/{run_id}/report.md", "sources": 4,
            "created_at": "2026-10-31T20:20:24+00:00",
            "turn": proposals._turn_of(turn) if turn else None,
        })
    return folder


def _paper(workspace, name="paper.md", text="# Kuwait: From the First Written Mention\n\n## Abstract\nPtolemy, 150 CE.\n"):
    md = workspace / "reports" / "kuwait-history-2026" / name
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(text, encoding="utf-8")
    return md


def _document(tmp_path, suffix=".pdf", data=b"%PDF-1.7 finished paper"):
    out = tmp_path / "documents" / f"Kuwait-paper-0001{suffix}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


def _entry(run_id="pipeline-17f8"):
    for item in get_config_store().get("research.papers_index") or []:
        if item.get("id") == run_id:
            return item
    return None


def test_a_document_made_in_the_research_turn_is_kept_with_the_run(workspace, tmp_path):
    folder = _run_folder(workspace)
    md, pdf = _paper(workspace), _document(tmp_path)
    final = final_paper.keep_final_paper(
        turn=proposals._turn_of(TURN), title="Kuwait: From the First Written Mention",
        document=pdf, kind="pdf", markdown=md)
    assert final["title"] == "Kuwait: From the First Written Mention"
    assert final["pdf_path"] == "research/reports/pipeline-17f8/final/paper.pdf"
    assert final["md_path"] == "research/reports/pipeline-17f8/final/paper.md"
    assert (folder / "final" / "paper.pdf").read_bytes() == pdf.read_bytes()
    assert (folder / "final" / "paper.md").read_text(encoding="utf-8") == md.read_text(encoding="utf-8")
    assert _entry()["final"] == final
    # The workspace index the page also reads holds it too.
    index = json.loads((workspace / "research" / "reports" / "index.json").read_text(encoding="utf-8"))
    assert index[0]["final"]["pdf_path"] == final["pdf_path"]
    # The page's file route serves the kept copies (inside research/reports).
    assert pipeline.resolve_report_file(final["pdf_path"]) == (folder / "final" / "paper.pdf").resolve()


def test_a_later_version_replaces_the_copy_and_a_word_copy_joins_it(workspace, tmp_path):
    folder = _run_folder(workspace)
    turn = proposals._turn_of(TURN)
    md = _paper(workspace)
    final_paper.keep_final_paper(turn=turn, title="Draft", document=_document(tmp_path, data=b"%PDF old"),
                                 kind="pdf", markdown=md)
    final_paper.keep_final_paper(turn=turn, title="Final", document=_document(tmp_path, data=b"%PDF new"),
                                 kind="pdf", markdown=md)
    final = final_paper.keep_final_paper(turn=turn, title="Final", document=_document(tmp_path, ".docx", b"PK docx"),
                                         kind="docx", markdown=md)
    assert (folder / "final" / "paper.pdf").read_bytes() == b"%PDF new"
    assert final["title"] == "Final"
    assert final["pdf_path"].endswith("final/paper.pdf") and final["docx_path"].endswith("final/paper.docx")


@pytest.mark.parametrize("turn", ["", proposals._turn_of(OTHER_TURN)])
def test_a_document_of_another_turn_or_of_no_turn_is_not_a_research_paper(workspace, tmp_path, turn):
    folder = _run_folder(workspace)
    assert final_paper.keep_final_paper(turn=turn, title="Shopping list", document=_document(tmp_path),
                                        kind="pdf") is None
    assert not (folder / "final").exists() and "final" not in _entry()


def test_a_run_registered_without_its_turn_takes_no_paper(workspace, tmp_path):
    # Negative control: the registration as it was, without the turn.
    _run_folder(workspace, turn=None)
    assert final_paper.keep_final_paper(turn=proposals._turn_of(TURN), title="Paper",
                                        document=_document(tmp_path), kind="pdf") is None


@pytest.fixture
def controlled(workspace, tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "_RESEARCH", resources._CapacityPool("research pipelines", 1, 0))
    monkeypatch.setattr(resources, "_RESEARCH_SESSIONS", resources._CapacityPool("background research sessions", 2, 0))
    monkeypatch.setattr(sessions, "_db_path", lambda: tmp_path / "sessions.db")
    monkeypatch.setattr("kazma_core.tools.research_readiness.research_readiness", lambda **kw: {"ready": True})
    from kazma_core.tools import read_url, research_evidence, research_planner, research_synthesize
    from kazma_core.web_acquire import serp
    import kazma_core.web_acquire as acquisition

    urls = [f"https://source{i}.example/kuwait" for i in range(2)]

    async def plan(*args, **kwargs):
        return research_planner.ResearchPlan(search_queries=["Kuwait history"])

    async def search(query, **kwargs):
        return serp.SearchResult(ok=True, query=query, urls=urls)

    async def fetch(url, **kwargs):
        return FetchResult(ok=True, url=url, text="Kuwait history evidence.")

    async def digest(path):
        return "Source digest"

    async def synthesize(*args, **kwargs):
        return "## Executive summary\nFixture synthesis.\n## Sources\n" + "\n".join(urls)

    monkeypatch.setattr(research_planner, "plan_research_queries", plan)
    monkeypatch.setattr(acquisition, "search", search)
    monkeypatch.setattr(read_url, "_workspace_root", lambda: workspace)
    monkeypatch.setattr(read_url, "_safe_workspace_path", lambda path, url: workspace / path)
    monkeypatch.setattr(read_url, "fetch_result", fetch)
    monkeypatch.setattr(read_url, "digest_research_file", digest)
    monkeypatch.setattr(research_evidence, "extract_claims_from_file", lambda *a, **k: [])
    monkeypatch.setattr(research_synthesize, "synthesize_from_digests", synthesize)
    monkeypatch.setenv("KAZMA_RESEARCH_GAP_LOOP", "0")
    return workspace


def _fake_documents(monkeypatch, pdf):
    from kazma_skills.native.document_generator import tools as generator

    class FakeService:
        async def generate(self, kind, payload, **kwargs):
            return SimpleNamespace(ok=True, data=SimpleNamespace(export_path=str(pdf)),
                                   artifact_id="artifact-1", message="", code="")

    monkeypatch.setattr(generator, "DocumentService", FakeService)
    monkeypatch.setattr(generator, "_verify_pdf_content", lambda *a, **k: None)
    return generator


@pytest.mark.asyncio
async def test_research_then_the_paper_in_one_turn_lists_the_paper(controlled, tmp_path, monkeypatch):
    generator = _fake_documents(monkeypatch, _document(tmp_path))
    md = _paper(controlled)
    token = _bound(TURN)
    try:
        report = await asyncio.wait_for(
            pipeline.run_research_pipeline("Kuwait history", depth="brief", max_sources=2), 60)
        answer = await generator.generate_pdf(title="Kuwait: From the First Written Mention",
                                              markdown_path=str(md))
    finally:
        proposals.reset_turn(token)
    assert report.startswith("# Deep research complete")
    entry, = get_config_store().get("research.papers_index")
    assert entry["turn"] == proposals._turn_of(TURN)
    assert "Research page: kept as the finished paper" in answer
    assert entry["final"]["title"] == "Kuwait: From the First Written Mention"
    # The Research page's list carries it.
    listed = [p for p in pipeline.list_research_papers() if p.get("id") == entry["id"]]
    assert listed and listed[0]["final"]["md_path"].endswith("final/paper.md")


@pytest.mark.asyncio
async def test_a_paper_made_in_a_later_turn_is_left_alone(controlled, tmp_path, monkeypatch):
    generator = _fake_documents(monkeypatch, _document(tmp_path))
    token = _bound(TURN)
    try:
        await asyncio.wait_for(pipeline.run_research_pipeline("Kuwait history", depth="brief", max_sources=2), 60)
    finally:
        proposals.reset_turn(token)
    token = _bound(OTHER_TURN)
    try:
        answer = await generator.generate_pdf(title="Shopping list", markdown_path=str(_paper(controlled)))
    finally:
        proposals.reset_turn(token)
    assert "Research page" not in answer
    entry, = get_config_store().get("research.papers_index")
    assert "final" not in entry


@pytest.mark.asyncio
async def test_a_failure_to_keep_it_never_fails_the_document(controlled, tmp_path, monkeypatch, caplog):
    generator = _fake_documents(monkeypatch, _document(tmp_path))
    _run_folder(controlled)

    def broken(**kwargs):
        raise RuntimeError("settings store unavailable")

    monkeypatch.setattr(final_paper, "keep_final_paper", broken)
    token = _bound(TURN)
    try:
        with caplog.at_level("WARNING", logger=generator.__name__):
            answer = await generator.generate_pdf(title="Paper", markdown_path=str(_paper(controlled)))
    finally:
        proposals.reset_turn(token)
    assert answer.startswith("PDF generated successfully.") and "Research page" not in answer
    assert "not kept with this turn's research run" in caplog.text
