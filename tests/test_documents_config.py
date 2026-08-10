from __future__ import annotations

import logging

from kazma_core.config_store import get_config_store
from kazma_core.documents.config import get_document_config


def test_document_config_safe_defaults(tmp_path) -> None:
    get_config_store().set("documents.storage_root", str(tmp_path / "documents"))

    config = get_document_config()

    assert config.storage_root == (tmp_path / "documents").resolve()
    assert config.intake_max_bytes == 50 * 1024 * 1024
    assert config.intake_max_files == 10
    assert config.worker_lease_seconds > 0
    assert config.worker_max_retries >= 0
    assert config.security_allow_encrypted_documents is False
    assert config.ocr_languages
    assert config.indexing_overlap_tokens < config.indexing_chunk_tokens
    assert config.retention_failed_days >= 0
    assert config.quota_tenant_bytes > config.intake_max_bytes


def test_document_config_is_live_and_explicitly_coerced(tmp_path) -> None:
    store = get_config_store()
    store.batch_set(
        [
            ("documents.storage_root", str(tmp_path / "one"), "documents"),
            ("documents.intake.max_bytes", "4096", "documents"),
            ("documents.intake.max_files", "4", "documents"),
            ("documents.security.malware_fail_closed", "yes", "documents"),
            ("documents.ocr.languages", "eng+fra", "documents"),
            ("documents.worker.max_retries", 0, "documents"),
        ]
    )

    first = get_document_config()
    store.set("documents.storage_root", str(tmp_path / "two"))
    store.set("documents.intake.max_bytes", 8192)
    second = get_document_config()

    assert first.storage_root == (tmp_path / "one").resolve()
    assert second.storage_root == (tmp_path / "two").resolve()
    assert first.intake_max_bytes == 4096
    assert second.intake_max_bytes == 8192
    assert first.intake_max_files == 4
    assert first.security_malware_fail_closed is True
    assert first.ocr_languages == ("eng", "fra")
    assert first.worker_max_retries == 0


def test_invalid_document_config_logs_and_falls_back(caplog) -> None:
    store = get_config_store()
    store.set("documents.intake.max_bytes", "-1")
    store.set("documents.security.malware_fail_closed", "perhaps")
    store.set("documents.indexing.chunk_tokens", 50)
    store.set("documents.indexing.overlap_tokens", 50)
    store.set("documents.ocr.dpi", 1200)

    with caplog.at_level(logging.WARNING):
        config = get_document_config()

    assert config.intake_max_bytes == 50 * 1024 * 1024
    assert config.security_malware_fail_closed is False
    assert config.indexing_overlap_tokens == 120
    assert config.ocr_dpi == 200
    assert "Invalid ConfigStore value" in caplog.text
    assert "overlap must be smaller" in caplog.text
