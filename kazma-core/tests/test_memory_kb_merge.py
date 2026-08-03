"""KB merge into chat memory + Neo4j topology export."""

from __future__ import annotations

from kazma_core.memory.federated_search import (
    format_kb_hits_for_prompt,
    format_source_footer,
    promote_kb_hits_to_episodes,
)


def test_format_kb_hits_for_prompt():
    hits = [
        {
            "store": "knowledge",
            "content": "Use Bearer tokens for auth.",
            "provenance": {
                "source_url": "https://docs.example/auth",
                "document_title": "Auth",
                "library_id": "lib1",
            },
        },
        {"store": "memory", "content": "should skip"},
    ]
    md = format_kb_hits_for_prompt(hits)
    assert "Knowledge Library" in md
    assert "Bearer" in md
    assert "not user identity" in md.lower() or "documentation" in md.lower()


def test_format_kb_empty():
    assert format_kb_hits_for_prompt([]) == ""
    assert format_kb_hits_for_prompt([{"store": "memory"}]) == ""


def test_promote_kb_hits(monkeypatch, tmp_path):
    written = []

    def _mirror(**kwargs):
        written.append(kwargs)
        return "ep-kb-1"

    monkeypatch.setattr(
        "kazma_core.memory.dual_write.mirror_episode",
        _mirror,
    )
    hits = [
        {
            "store": "knowledge",
            "content": "x" * 50 + " documentation about webhooks",
            "provenance": {"document_title": "Webhooks", "source_url": "https://x"},
        }
    ]
    n = promote_kb_hits_to_episodes(hits, session_id="s1", tenant_id="default")
    assert n == 1
    assert written
    assert written[0]["source"] == "knowledge_library_promote"
    assert "Webhooks" in written[0]["user_text"]


def test_neo4j_capability_primary_wording():
    from kazma_core.memory.graph_backend import graph_capability

    cap = graph_capability(
        {"graph": {"provider": "neo4j", "url": "bolt://localhost:7687"}}
    )
    assert cap["status"] == "primary_when_available"
