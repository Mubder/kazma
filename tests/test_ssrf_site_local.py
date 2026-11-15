"""The public-address check refuses the IPv6 site-local range.

Security audit 2026-11-15. ``ssrf._is_blocked_ip`` let the deprecated IPv6
site-local range ``fec0::/10`` (RFC 3879: a network's own hosts) through:
Python counts it neither private nor reserved. A name answering such an
address passed every public check, and the pinned transport accepted it as
a pin. NAT64's well-known prefix ``64:ff9b::/96`` was already refused
(Python counts it reserved); it stays refused.
"""

from __future__ import annotations

import ipaddress

import pytest
from kazma_core.security import ssrf, ssrf_pin
from kazma_core.security.ssrf import SSRFError, _is_blocked_ip, validate_url


@pytest.mark.parametrize("address", [
    "fec0::1", "fec0:0:0:1::5", "feff:ffff::1", "::ffff:fec0:1",
    "64:ff9b::a00:5", "64:ff9b::808:808",
])
def test_blocked(address: str) -> None:
    assert _is_blocked_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", ["2606:4700:4700::1111", "2001:4860:4860::8888"])
def test_public_stays_public(address: str) -> None:
    assert not _is_blocked_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", ["fec0::1", "feff:ffff::1"])
def test_control_python_alone_calls_site_local_public(address: str) -> None:
    """Negative control: the checks the function used before see nothing."""
    ip = ipaddress.ip_address(address)
    assert not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_unspecified or ip.is_multicast)


def test_a_name_answering_site_local_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(ssrf, "_resolve_host_ips", lambda host: ["fec0::5"])
    with pytest.raises(SSRFError):
        validate_url("https://site-local.example/x", block_unresolved=True)


def test_the_pin_transport_refuses_a_site_local_pin() -> None:
    with pytest.raises(SSRFError):
        ssrf_pin._validated_pin({"h.example": "fec0::5"}, "h.example")
