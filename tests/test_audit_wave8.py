"""Wave 8: lows L-1..L-5 + H-7 pin-IP / post-connect peer abort."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kazma_core.security.ssrf import SSRFError, assert_peer_public, peer_ip_from_response


REPO = Path(__file__).resolve().parent.parent


def test_health_details_is_gated_live_ready_open() -> None:
    """L-1: /health/details is sensitive; live/ready stay public."""
    from kazma_ui.auth import ALWAYS_OPEN_PATHS, is_always_open, is_sensitive_path

    assert is_sensitive_path("/health/details") is True
    assert is_always_open("/health/details") is False
    assert "/health/live" in ALWAYS_OPEN_PATHS
    assert "/health/ready" in ALWAYS_OPEN_PATHS
    assert is_always_open("/health/live") is True
    assert is_always_open("/health/ready") is True


def test_listed_x_show_have_x_cloak() -> None:
    """L-4: first-paint flash — listed x-show nodes also carry x-cloak."""
    files = {
        REPO / "kazma-ui/kazma_ui/templates/base.html": (
            'x-show="$store.search.loading || $store.search.results.length',
            'x-show="$store.search.loading"',
        ),
        REPO / "kazma-ui/kazma_ui/templates/chat.html": (
            'x-show="$store.agent?.pendingApproval?.message"',
            'x-show="$store.agent && $store.agent.activeNode"',
        ),
        REPO / "kazma-ui/kazma_ui/templates/ide.html": (
            'x-show="result"',
            'x-show="currentFile"',
        ),
    }
    for path, needles in files.items():
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            idx = text.find(needle)
            assert idx >= 0, f"{path.name} missing {needle}"
            window = text[max(0, idx - 80) : idx + len(needle)]
            assert "x-cloak" in window, f"{path.name}: {needle} lacks x-cloak nearby"


def test_workspace_merge_uses_kazma_confirm() -> None:
    """L-3: Merge PR is kazmaConfirm, not a bare confirm()."""
    src = (REPO / "kazma-ui/kazma_ui/templates/workspace.html").read_text(
        encoding="utf-8"
    )
    assert "if (confirm(`Merge PR" not in src
    assert "kazmaConfirm" in src
    assert "kazmaPrompt" in src


def test_gc_symlink_target_is_inside_tmp() -> None:
    """L-5: the only test symlink points at tmp_path/outside, not C:\\Users."""
    src = (REPO / "tests/test_document_operations_phase9.py").read_text(
        encoding="utf-8"
    )
    assert 'outside = tmp_path / "outside"' in src
    assert "link_dir.unlink" in src


class _PeerStream:
    def __init__(self, ip: str) -> None:
        self._ip = ip

    def get_extra_info(self, name: str):
        if name == "peername":
            return (self._ip, 443)
        return None


def test_peer_abort_on_private_and_rebinding() -> None:
    """H-7: post-connect peer in a private range, or not in the pin set, aborts."""
    private = SimpleNamespace(extensions={"network_stream": _PeerStream("169.254.169.254")})
    assert peer_ip_from_response(private) == "169.254.169.254"
    with pytest.raises(SSRFError, match="private"):
        assert_peer_public(private, url="http://evil.example/", validated_ips=("1.2.3.4",))

    rebound = SimpleNamespace(extensions={"network_stream": _PeerStream("8.8.8.8")})
    with pytest.raises(SSRFError, match="rebinding"):
        assert_peer_public(
            rebound, url="http://evil.example/", validated_ips=("1.2.3.4",)
        )

    ok = SimpleNamespace(extensions={"network_stream": _PeerStream("1.2.3.4")})
    assert_peer_public(ok, url="http://ok.example/", validated_ips=("1.2.3.4",))


def test_pin_transport_skipped_when_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """H-7: do not replace get_scraping_client transport when a proxy is set."""
    from kazma_core.proxy.client import get_scraping_client
    from kazma_core.security.ssrf_pin import PinHostAsyncTransport

    monkeypatch.setattr(
        "kazma_core.proxy.client.get_active_proxy_url",
        lambda: "http://127.0.0.1:9",
    )
    client = get_scraping_client(pin_hosts={"example.com": "1.2.3.4"})
    try:
        transport = getattr(client, "_transport", None)
        assert not isinstance(transport, PinHostAsyncTransport)
    finally:
        # AsyncClient close is async; just don't leak the pin transport.
        pass


def test_pin_transport_used_direct() -> None:
    from kazma_core.proxy.client import get_scraping_client
    from kazma_core.security.ssrf_pin import PinHostAsyncTransport

    with patch("kazma_core.proxy.client.get_active_proxy_url", return_value=None):
        client = get_scraping_client(pin_hosts={"example.com": "1.2.3.4"})
    transport = getattr(client, "_transport", None)
    assert isinstance(transport, PinHostAsyncTransport)
    assert transport.pins["example.com"] == "1.2.3.4"


def test_read_url_wires_pin_and_peer() -> None:
    src = (REPO / "kazma-core/kazma_core/tools/read_url.py").read_text(encoding="utf-8")
    assert "pin_hosts=" in src
    assert "assert_peer_public" in src
    assert "get_scraping_client" in src
