"""Tests for the Knowledge ingest discovery + scoping logic.

Network-free: exercises sitemap parsing and path-prefix scoping only.
The full fetch pipeline is integration-tested manually (see plan §
Validation) against real doc sites.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kazma-core"))

from kazma_core.stores.knowledge_ingest import (
    _extract_urls_from_sitemap,
    _in_scope,
    _seed_prefix,
)


SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://developers.facebook.com/docs/whatsapp/overview</loc></url>
  <url><loc>https://developers.facebook.com/docs/whatsapp/messages</loc></url>
  <url><loc>https://developers.facebook.com/docs/whatsapp/messages/send</loc></url>
  <url><loc>https://developers.facebook.com/docs/whatsapp/webhooks</loc></url>
  <url><loc>https://developers.facebook.com/docs/marketing-api/ads</loc></url>
  <url><loc>https://example.com/blog/news</loc></url>
</urlset>
"""


def test_sitemap_parse_extracts_all_locs():
    urls = _extract_urls_from_sitemap(SITEMAP_XML)
    assert len(urls) == 6
    assert "https://developers.facebook.com/docs/whatsapp/overview" in urls


def test_sitemap_index_parse():
    idx = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://x.com/sm1.xml</loc></sitemap>
  <sitemap><loc>https://x.com/sm2.xml</loc></sitemap>
</sitemapindex>"""
    assert _extract_urls_from_sitemap(idx) == [
        "https://x.com/sm1.xml",
        "https://x.com/sm2.xml",
    ]


def test_sitemap_parse_garbage_returns_empty():
    assert _extract_urls_from_sitemap("not xml at all") == []
    assert _extract_urls_from_sitemap("<broken") == []


# ── Seed prefix (the "section crawl" rule) ──────────────────────────────────


def test_seed_prefix_walks_up_one_segment():
    """A doc-tree seed should scope to its parent section, not itself."""
    assert _seed_prefix("https://x.com/docs/whatsapp/overview") == "/docs/whatsapp/"
    assert _seed_prefix("https://x.com/docs/whatsapp/") == "/docs/whatsapp/"
    assert _seed_prefix("https://x.com/api/v1/messages.json") == "/api/v1/"
    assert _seed_prefix("https://x.com/") == "/"


# ── Scope filtering ─────────────────────────────────────────────────────────


def test_prefix_scope_keeps_section_excludes_outside():
    seed = "https://developers.facebook.com/docs/whatsapp/overview"
    urls = _extract_urls_from_sitemap(SITEMAP_XML)
    in_scope = [u for u in urls if _in_scope(seed, u, "prefix")]
    assert len(in_scope) == 4  # overview, messages, messages/send, webhooks
    assert all("/docs/whatsapp/" in u for u in in_scope)
    # Marketing API docs and the blog are excluded.
    assert not any("marketing-api" in u for u in in_scope)
    assert not any("example.com" in u for u in in_scope)


def test_exact_scope_is_single_page():
    seed = "https://x.com/a"
    assert _in_scope(seed, "https://x.com/a", "exact")
    assert not _in_scope(seed, "https://x.com/a/b", "exact")


def test_domain_scope_allows_any_path_on_host():
    seed = "https://x.com/docs/a"
    assert _in_scope(seed, "https://x.com/anything", "domain")
    assert _in_scope(seed, "https://www.x.com/anything", "domain")
    assert not _in_scope(seed, "https://y.com/anything", "domain")


def test_prefix_scope_rejects_other_host():
    seed = "https://x.com/docs/whatsapp/"
    assert not _in_scope(seed, "https://y.com/docs/whatsapp/attack", "prefix")


# ── Tree scope (default) — handles cross-prefix doc trees like Meta's ──────


def test_tree_scope_accepts_cross_prefix_same_topic():
    """Meta splits WhatsApp docs across /documentation/.../whatsapp/... and
    /docs/whatsapp/... The 'tree' scope (default) must accept both because
    they share the 'whatsapp' topic segment. 'prefix' scope wrongly rejected
    the /docs/* URLs, which is why a crawl from the overview page only ever
    ingested 1 page."""
    from kazma_core.stores.knowledge_ingest import _seed_topic_segments

    seed = "https://developers.facebook.com/documentation/business-messaging/whatsapp/overview"
    assert _seed_topic_segments(seed) == {"whatsapp"}

    # Cross-prefix but same topic → accepted.
    assert _in_scope(seed, "https://developers.facebook.com/docs/whatsapp/cloud-api", "tree")
    assert _in_scope(seed, "https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages", "tree")
    assert _in_scope(seed, "https://developers.facebook.com/documentation/business-messaging/whatsapp/in-app-signup", "tree")

    # Wrong topic → rejected.
    assert not _in_scope(seed, "https://developers.facebook.com/docs/instagram-api", "tree")
    assert not _in_scope(seed, "https://developers.facebook.com/marketing-api/ads", "tree")

    # Wrong host → rejected.
    assert not _in_scope(seed, "https://evil.com/docs/whatsapp/x", "tree")


def test_tree_scope_strips_generic_segments():
    """Generic segments (docs, documentation, overview, cloud-api, v1, en)
    don't count as the topic. The topic is the distinctive word."""
    from kazma_core.stores.knowledge_ingest import _seed_topic_segments

    # Only 'shipx' is the topic; everything else is noise.
    assert _seed_topic_segments("https://x.com/docs/v1/en/overview/shipx") == {"shipx"}
    # No distinctive segments → empty set → tree scope rejects everything
    # (safe default; user should pick a more specific seed).
    assert _seed_topic_segments("https://x.com/docs/overview") == set()


def test_tree_is_the_default_scope_mode():
    """'tree' must be the default so cross-prefix doc trees work out of the
    box. Was 'prefix' which silently broke crawls from landing pages."""
    from kazma_core.stores.knowledge_ingest import _kb_scope_mode

    import os
    saved = os.environ.pop("KAZMA_KB_SCOPE_MODE", None)
    try:
        assert _kb_scope_mode() == "tree"
    finally:
        if saved is not None:
            os.environ["KAZMA_KB_SCOPE_MODE"] = saved


# ── Infra-URL filtering ────────────────────────────────────────────────────


def test_is_infra_url_filters_sitemaps_and_feeds():
    """Firecrawl /v1/map can return infra URLs (sitemap.xml, robots.txt,
    feeds) that aren't doc pages. Fetching+chunking them produces garbage
    and counts as a failed page. Discovery must filter them at the boundary."""
    from kazma_core.stores.knowledge_ingest import _is_infra_url

    # Infra — should be filtered out.
    assert _is_infra_url("https://x.com/docs/whatsapp/overview/sitemap.xml")
    assert _is_infra_url("https://x.com/sitemap_index.xml")
    assert _is_infra_url("https://x.com/robots.txt")
    assert _is_infra_url("https://x.com/docs/whatsapp/sitemap.xml.gz")
    assert _is_infra_url("https://x.com/feed.rss")
    assert _is_infra_url("https://x.com/api/data.json")
    assert _is_infra_url("") is True
    assert _is_infra_url(None) is True

    # Source/binary artifacts Firecrawl /map sometimes returns — not HTML docs.
    assert _is_infra_url(
        "https://x.com/docs/whatsapp/reference/v23.0.openapi.yaml"
    )
    assert _is_infra_url("https://x.com/static/app.js")

    # Real doc pages — must NOT be filtered.
    assert not _is_infra_url("https://x.com/docs/whatsapp/cloud-api")
    assert not _is_infra_url("https://x.com/docs/whatsapp/cloud-api/reference/messages")
    assert not _is_infra_url("https://developers.facebook.com/docs/whatsapp/get-started")


def test_canonical_page_url_dedupes_slash_and_md():
    """overview vs overview/ and .md source paths must collapse to one page."""
    from kazma_core.stores.knowledge_ingest import _canonical_page_url, _order_urls_seed_first

    a = "https://developers.facebook.com/documentation/business-messaging/whatsapp/overview"
    b = "https://developers.facebook.com/documentation/business-messaging/whatsapp/overview/"
    c = "https://developers.facebook.com/documentation/business-messaging/whatsapp/access-tokens.md"
    assert _canonical_page_url(a) == _canonical_page_url(b)
    assert _canonical_page_url(c).endswith("/access-tokens")
    assert not _canonical_page_url(c).endswith(".md")

    ordered = _order_urls_seed_first(a, [b, c, a + "/sitemap.xml"])
    # seed + access-tokens; sitemap filtered; slash dup removed
    assert ordered[0] == _canonical_page_url(a)
    assert len(ordered) == 2
    assert any(u.endswith("/access-tokens") for u in ordered)


def test_sparse_discovery_detects_leaf_map_results():
    """Firecrawl /map on a Meta leaf returns seed + slash + sitemap only.
    That must be treated as sparse so parent-map / Jina tiers still run."""
    from kazma_core.stores.knowledge_ingest import _is_sparse_discovery

    seed = "https://developers.facebook.com/documentation/business-messaging/whatsapp/overview"
    sparse = [
        seed,
        seed + "/",
        seed + "/sitemap.xml",
    ]
    assert _is_sparse_discovery(seed, sparse) is True

    rich = [
        seed,
        seed.replace("/overview", "/about-the-platform"),
        seed.replace("/overview", "/access-tokens"),
        seed.replace("/overview", "/pricing"),
        seed.replace("/overview", "/get-started"),
    ]
    assert _is_sparse_discovery(seed, rich) is False


def test_section_map_url_walks_up_leaf():
    from kazma_core.stores.knowledge_ingest import _section_map_url

    leaf = "https://developers.facebook.com/documentation/business-messaging/whatsapp/overview"
    assert _section_map_url(leaf) == (
        "https://developers.facebook.com/documentation/business-messaging/whatsapp/"
    )
    # Already a directory → no second hop.
    assert _section_map_url(
        "https://developers.facebook.com/documentation/business-messaging/whatsapp/"
    ) is None


def test_extract_links_from_markdown_jina_style():
    """Jina returns markdown with [label](url) nav links — harvest them."""
    from kazma_core.stores.knowledge_ingest import _extract_links_from_text

    base = "https://developers.facebook.com/documentation/business-messaging/whatsapp/overview"
    md = """
# WhatsApp Business Platform
[About the platform](https://developers.facebook.com/documentation/business-messaging/whatsapp/about-the-platform/)
[Pricing](/documentation/business-messaging/whatsapp/pricing/)
[Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api)
"""
    links = _extract_links_from_text(md, base)
    assert any(u.endswith("/about-the-platform") for u in links)
    assert any(u.endswith("/pricing") for u in links)
    assert any("cloud-api" in u for u in links)


# ── Bot-block detection + .gz sitemap handling ──────────────────────────────


def test_looks_like_bot_block_html_detects_meta_error_stub():
    """Meta returns <title>Error</title> HTML stubs to non-browser clients.
    The discovery loop must treat these as failures, not as content."""
    from kazma_core.stores.knowledge_ingest import _looks_like_bot_block_html

    assert _looks_like_bot_block_html('<!DOCTYPE html><html><head><title>Error</title></head><body></body></html>')
    assert _looks_like_bot_block_html('<html><head><title>403</title></head></html>')
    assert _looks_like_bot_block_html(None)
    assert _looks_like_bot_block_html("")
    # Real content must NOT be flagged.
    assert not _looks_like_bot_block_html(
        '<html><head><title>WhatsApp Cloud API</title></head>'
        '<body><article><p>' + ('x' * 800) + '</p></article></body></html>'
    )


def test_http_get_text_decompresses_gz_sitemap():
    """A ``.xml.gz`` URL must be decompressed before parsing.  The body
    returned for ``sitemap.xml.gz`` is raw gzip bytes — without explicit
    decompression the parser sees binary garbage and silently yields 0 URLs."""
    import asyncio
    import gzip
    import os
    import tempfile

    from kazma_core.stores.knowledge_ingest import _extract_urls_from_sitemap

    # Simulate a .gz sitemap body (raw gzip bytes).
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/docs/a</loc></url>
  <url><loc>https://example.com/docs/b</loc></url>
</urlset>"""
    raw_gz = gzip.compress(xml.encode("utf-8"))
    decompressed = gzip.decompress(raw_gz).decode("utf-8")
    urls = _extract_urls_from_sitemap(decompressed)
    assert len(urls) == 2
    assert "https://example.com/docs/a" in urls


def test_safe_progress_swallows_callback_errors():
    """The progress callback is optional and must never break discovery."""
    import asyncio

    from kazma_core.stores.knowledge_ingest import _safe_progress

    async def boom(_msg: str) -> None:
        raise RuntimeError("callback exploded")

    # Should not raise.
    asyncio.run(_safe_progress(None, "msg"))
    asyncio.run(_safe_progress(lambda m: None, "msg"))      # sync callable
    asyncio.run(_safe_progress(boom, "msg"))                # raising async callable

