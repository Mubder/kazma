""""Sources loaded" counts the sources, not the run's own claims file (2026-11, FA-240).

The pipeline hands the synthesis its source digests and its claims file;
the header counted every path, so a run that acquired three sources said
"Sources loaded: 4/4" (and 3/3 over two). The pipeline now says how many of
the paths are sources (``source_count``) and the header counts those.

Measured on the real synthesis with a scripted model; the call without
``source_count`` (the old count) is the negative control, and a source that
fails to load is counted out.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kazma_core.tools import research_synthesize as rs


class _Scripted:
    async def chat(self, messages=None, tools=None, *args: Any, **kwargs: Any):
        from kazma_core.llm_provider import LLMResponse

        return LLMResponse(content="## Findings\nPearling was the main trade.", tool_calls=[],
                           finish_reason="stop", model="stub", usage={"total_tokens": 1}, cost_usd=0.0)


@pytest.fixture()
def research(tmp_path, monkeypatch):
    from kazma_core.web_acquire import research_llm

    folder = tmp_path / "research"
    folder.mkdir()
    for name in ("a", "b", "c"):
        (folder / f"{name}.md").write_text(f"Source {name}: pearling in Kuwait.", encoding="utf-8")
    (folder / "claims-0.md").write_text("## Claims\n- Pearling ended with oil.", encoding="utf-8")
    monkeypatch.setattr(rs, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(research_llm, "resolved_route", lambda: (_Scripted(), "stub", None))
    return ["research/a.md", "research/b.md", "research/c.md"]


def _header(paths, **kw) -> str:
    out = asyncio.run(rs.synthesize_from_digests(paths, question="Pearling in Kuwait?", language="en", **kw))
    return next(line for line in out.splitlines() if line.startswith("**Sources loaded"))


def test_the_claims_file_is_not_a_source(research):
    assert _header(research + ["research/claims-0.md"], source_count=3) == "**Sources loaded:** 3/3"


def test_a_source_that_did_not_load_is_counted_out(research):
    paths = research + ["research/missing.md", "research/claims-0.md"]
    assert _header(paths, source_count=4) == "**Sources loaded:** 3/4"


def test_negative_control_without_the_count_the_claims_file_counts(research):
    assert _header(research + ["research/claims-0.md"]) == "**Sources loaded:** 4/4"


def test_the_pipeline_says_how_many_paths_are_sources():
    import inspect

    from kazma_core.tools import research_pipeline

    body = inspect.getsource(research_pipeline._run_research_pipeline)
    calls = body.split("synthesize_from_digests(")[1:]
    assert calls and all("source_count=len(digests)" in call.split("))")[0] for call in calls)
