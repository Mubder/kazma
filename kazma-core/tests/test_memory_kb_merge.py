"""KB merge into chat memory + Neo4j topology export."""

from __future__ import annotations

from kazma_core.memory.federated_search import (
    format_kb_hits_for_prompt,
    format_source_footer,
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


def test_kb_hits_are_never_promoted_into_memory():
    """Memory audit M04: the promotion copied library text into chat memory as
    the user's words; it is gone (tests/test_memory_kb_never_in_memory.py)."""
    from kazma_core.memory import federated_search

    assert not hasattr(federated_search, "promote_kb_hits_to_episodes")


def test_neo4j_capability_primary_wording():
    from kazma_core.memory.graph_backend import graph_capability

    cap = graph_capability(
        {"graph": {"provider": "neo4j", "url": "bolt://localhost:7687"}}
    )
    assert cap["status"] == "primary_when_available"
