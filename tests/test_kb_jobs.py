"""Durable KB crawl job registry."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from kazma_core.stores import kb_jobs


def test_save_get_and_interrupt(monkeypatch) -> None:
    store_data: dict = {}

    mock_cs = MagicMock()

    def _get(key, default=None):
        return store_data.get(key, default)

    def _set(key, value, category="general"):
        store_data[key] = value

    mock_cs.get.side_effect = _get
    mock_cs.set.side_effect = _set

    with patch("kazma_core.config_store.get_config_store", return_value=mock_cs):
        kb_jobs.save_job(
            "lib:1",
            {
                "phase": "crawling",
                "library_id": "lib",
                "url": "https://example.com",
                "started_at": "2026-01-01T00:00:00+00:00",
            },
        )
        got = kb_jobs.get_job("lib:1")
        assert got is not None
        assert got["phase"] == "crawling"

        n = kb_jobs.mark_stale_jobs_interrupted()
        assert n == 1
        got2 = kb_jobs.get_job("lib:1")
        assert got2 is not None
        assert got2["phase"] == "interrupted"
        assert got2.get("finished_at")

        # Second boot is idempotent for finished jobs
        assert kb_jobs.mark_stale_jobs_interrupted() == 0


def test_upsert_merges_fields() -> None:
    store_data: dict = {}
    mock_cs = MagicMock()
    mock_cs.get.side_effect = lambda k, d=None: store_data.get(k, d)
    mock_cs.set.side_effect = lambda k, v, category="general": store_data.__setitem__(k, v)

    with patch("kazma_core.config_store.get_config_store", return_value=mock_cs):
        kb_jobs.upsert_job("j1", phase="starting", library_id="a")
        kb_jobs.upsert_job("j1", phase="fetching", fetched=3)
        j = kb_jobs.get_job("j1")
        assert j["library_id"] == "a"
        assert j["phase"] == "fetching"
        assert j["fetched"] == 3
