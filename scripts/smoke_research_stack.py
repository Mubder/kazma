#!/usr/bin/env python3
"""Live smoke for SearXNG discovery, web_search, read_url, hard-page recovery.

Usage (from repo root):
  python scripts/smoke_research_stack.py
  KAZMA_SEARXNG_URL=http://127.0.0.1:15080 python scripts/smoke_research_stack.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# Repo layout: scripts/ → monorepo root packages
ROOT = Path(__file__).resolve().parents[1]
for p in (
    ROOT / "kazma-core",
    ROOT / "kazma-ui",
    ROOT / "kazma-gateway",
    ROOT / "kazma-skills",
):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _ok(label: str, detail: str = "") -> None:
    print(f"  PASS  {label}" + (f" — {detail}" if detail else ""))


def _fail(label: str, detail: str) -> None:
    print(f"  FAIL  {label} — {detail}")
    raise SystemExit(1)


async def main() -> None:
    from kazma_core.tools.read_url import (
        _recover_hard_page,
        clear_url_cache,
        read_url,
    )
    from kazma_core.tools.web_search import (
        _searxng_candidate_bases,
        _searxng_search,
        web_search,
    )

    # Prefer Kazma compose SearXNG (:8088) when unset.
    # Note: this script does NOT load .env — export the var or use
    #   set -a; source .env; set +a
    # before running. Discovery still probes other bases if this one fails.
    if not (os.environ.get("KAZMA_SEARXNG_URL") or "").strip():
        os.environ["KAZMA_SEARXNG_URL"] = "http://127.0.0.1:8088"

    print("=== Research stack smoke ===")
    print(f"KAZMA_SEARXNG_URL={os.environ.get('KAZMA_SEARXNG_URL')}")
    print(f"KAZMA_JINA_READER={os.environ.get('KAZMA_JINA_READER', '(unset=recovery-on)')}")
    print(f"KAZMA_FIRECRAWL_API_KEY={'set' if os.environ.get('KAZMA_FIRECRAWL_API_KEY') else 'unset'}")
    print()

    # 1) Discovery
    print("[1] SearXNG candidate bases")
    bases = _searxng_candidate_bases()
    print(f"  bases={bases}")
    if not bases:
        _fail("candidates", "empty list")
    _ok("candidates", f"{len(bases)} bases")

    # 2) Direct SearXNG
    print("[2] SearXNG /search?format=json")
    t0 = time.time()
    res, note = _searxng_search("OpenAI API documentation", 3)
    dt = time.time() - t0
    print(f"  note={note} n={0 if not res else len(res)} {dt:.1f}s")
    if res:
        print(f"  first={res[0].get('title')!r} {res[0].get('href')}")
        _ok("searxng", note)
    else:
        print("  (SearXNG unavailable — failover will cover web_search)")
        _ok("searxng-skip", note)

    # 3) Full web_search chain
    print("[3] web_search live")
    t0 = time.time()
    out = await web_search("Kazma AI agent framework")
    dt = time.time() - t0
    head = out[:600].replace("\n", " | ")
    print(f"  {dt:.1f}s len={len(out)}")
    print(f"  {head}")
    if out.startswith("Error:") and "No web results" not in out:
        _fail("web_search", out[:200])
    if "No web results" in out:
        _fail("web_search", "all backends empty")
    if "Source:" not in out and "http" not in out.lower():
        _fail("web_search", "no Source/links in output")
    _ok("web_search", "results returned")

    # 4) Static page (false bot-wall regression)
    print("[4] read_url https://example.com")
    clear_url_cache()
    t0 = time.time()
    page = await read_url("https://example.com")
    dt = time.time() - t0
    print(f"  {dt:.1f}s {page[:300].replace(chr(10), ' | ')}")
    if page.startswith("Error:"):
        _fail("example.com", page[:200])
    if "example" not in page.lower():
        _fail("example.com", "missing expected text")
    _ok("example.com", f"{len(page)} chars")

    # 5) Real content page
    print("[5] read_url Wikipedia AI")
    clear_url_cache()
    t0 = time.time()
    wiki = await read_url(
        "https://en.wikipedia.org/wiki/Artificial_intelligence",
        max_chars=2000,
    )
    dt = time.time() - t0
    print(f"  {dt:.1f}s len={len(wiki)}")
    print(f"  {wiki[:250].replace(chr(10), ' | ')}")
    if wiki.startswith("Error:"):
        _fail("wikipedia", wiki[:200])
    _ok("wikipedia", f"{len(wiki)} chars")

    # 6) Hard-page recovery cascade (Jina unless disabled)
    print("[6] _recover_hard_page(example.com)")
    t0 = time.time()
    recovered = await _recover_hard_page("https://example.com", why="smoke")
    dt = time.time() - t0
    print(f"  {dt:.1f}s recovered={bool(recovered)} chars={len(recovered or '')}")
    if recovered:
        print(f"  {recovered[:250].replace(chr(10), ' | ')}")
        if "example" not in recovered.lower():
            _fail("recovery", "body missing example")
        _ok("recovery", "got body via Firecrawl/Jina/Playwright")
    else:
        # Recovery can fail offline without jina/network; not fatal if read_url worked
        print("  WARN: recovery returned None (Jina/Firecrawl/Playwright unavailable)")
        _ok("recovery-soft", "none — local httpx path already verified")

    print()
    print("SMOKE_PASS research stack")


if __name__ == "__main__":
    asyncio.run(main())
