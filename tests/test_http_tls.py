"""kazma_core.http_tls: one TLS context for every httpx client, never weaker."""

from __future__ import annotations

import ssl

import pytest
from kazma_core import http_tls


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(http_tls, "_CONTEXT", None)


def test_one_context_with_real_roots():
    first = http_tls.shared_ssl_context()
    assert first is http_tls.shared_ssl_context(), "built more than once"
    assert isinstance(first, ssl.SSLContext)
    assert first.verify_mode == ssl.CERT_REQUIRED and first.check_hostname
    assert first.cert_store_stats()["x509_ca"] > 0, "no CA roots loaded"


def test_a_failure_degrades_to_verification_not_to_none(monkeypatch):
    import httpx

    def broken():
        raise ssl.SSLError("bad bundle")

    monkeypatch.setattr(httpx, "create_ssl_context", broken)
    assert http_tls.shared_ssl_context() is True  # httpx's own "verify normally"


def test_prewarm_builds_it():
    http_tls.prewarm()
    assert isinstance(http_tls._CONTEXT, ssl.SSLContext)
