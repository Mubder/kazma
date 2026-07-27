#!/usr/bin/env python3
"""Smoke deep research plumbing (no full multi-minute run by default).

Usage:
  python scripts/smoke_research_deep.py
  KAZMA_SMOKE_FULL_RESEARCH=1 python scripts/smoke_research_deep.py  # live pipeline
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "kazma-core", ROOT / "kazma-ui", ROOT / "kazma-skills"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def main() -> None:
    from kazma_core.agent.research_policy import (
        RESEARCH_PROTOCOL,
        is_deep_research_intent,
        should_nudge_more_sources,
    )
    from kazma_core.tools.research_pipeline import list_research_papers

    assert "read_url_to_file" in RESEARCH_PROTOCOL
    assert is_deep_research_intent("deep research on fusion energy")
    nudge = should_nudge_more_sources(
        [{"role": "user", "content": "deep research on fusion"}],
        ["web_search"],
    )
    assert nudge
    papers = list_research_papers(limit=5)
    assert isinstance(papers, list)
    print("PASS policy + list_research_papers count=", len(papers))

    if (os.environ.get("KAZMA_SMOKE_FULL_RESEARCH") or "").strip() in (
        "1",
        "true",
        "yes",
    ):
        from kazma_core.tools.research_pipeline import run_research_pipeline

        async def _run() -> None:
            out = await run_research_pipeline(
                "Python programming language overview",
                depth="standard",
                max_sources=3,
                export_docx=False,
            )
            print(out[:800])
            assert "report" in out.lower() or "Error" in out

        asyncio.run(_run())
        print("PASS full pipeline smoke")
    else:
        print("SKIP full pipeline (set KAZMA_SMOKE_FULL_RESEARCH=1)")


if __name__ == "__main__":
    main()
    print("SMOKE_OK research_deep plumbing")
