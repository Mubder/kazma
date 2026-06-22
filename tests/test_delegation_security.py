"""Tests for DelegationSecurity — cryptographic signing and encryption."""

from __future__ import annotations

import pytest
from kazma_core.delegation.security import DelegationSecurity


class TestSecurityInit:
    """Test security initialization."""

    def test_default_generates_keys(self):
        sec = DelegationSecurity(agent_id="agent-1")
        assert sec.agent_id == "agent-1"
        assert len(sec.public_key_hex) == 64  # Ed25519 public key is 32 bytes = 64 hex
        assert len(sec.encryption_public_key_hex) == 64

    def test_hex_key_input(self):
        hex_key = "a" * 64
        sec = DelegationSecurity(agent_id="agent-2", private_key=hex_key)
        assert sec.agent_id == "agent-2"

    def test_key_info(self):
        sec = DelegationSecurity(agent_id="agent-3")
        info = sec.get_key_info()
        assert info["agent_id"] == "agent-3"
        assert "signing_public_key" in info
        assert "encryption_public_key" in info


class TestSigning:
    """Test request signing and verification."""

    def test_sign_returns_hex(self):
        sec = DelegationSecurity(agent_id="agent-1")
        payload = {"task": "summarize", "budget": 0.10}
        sig = sec.sign_request(payload)
        assert isinstance(sig, str)
        assert len(sig) == 128  # Ed25519 signature is 64 bytes = 128 hex

    def test_verify_valid_signature(self):
        sec = DelegationSecurity(agent_id="agent-1")
        payload = {"task": "analyze", "budget": 0.05}
        sig = sec.sign_request(payload)
        assert sec.verify_request(payload, sig) is True

    def test_verify_invalid_signature(self):
        sec = DelegationSecurity(agent_id="agent-1")
        payload = {"task": "analyze", "budget": 0.05}
        fake_sig = "0" * 128
        assert sec.verify_request(payload, fake_sig) is False

    def test_verify_tampered_payload(self):
        sec = DelegationSecurity(agent_id="agent-1")
        payload = {"task": "analyze", "budget": 0.05}
        sig = sec.sign_request(payload)
        tampered = {"task": "analyze", "budget": 0.99}
        assert sec.verify_request(tampered, sig) is False

    def test_sign_deterministic(self):
        sec = DelegationSecurity(agent_id="agent-1")
        payload = {"task": "test"}
        sig1 = sec.sign_request(payload)
        sig2 = sec.sign_request(payload)
        # Ed25519 is deterministic for same key+message
        assert sig1 == sig2

    def test_different_keys_different_signatures(self):
        sec1 = DelegationSecurity(agent_id="agent-1")
        sec2 = DelegationSecurity(agent_id="agent-2")
        payload = {"task": "shared-task"}
        sig1 = sec1.sign_request(payload)
        sig2 = sec2.sign_request(payload)
        assert sig1 != sig2

    def test_verify_cross_agent_signature_fails(self):
        sec1 = DelegationSecurity(agent_id="agent-1")
        sec2 = DelegationSecurity(agent_id="agent-2")
        payload = {"task": "test"}
        sig = sec1.sign_request(payload)
        assert sec2.verify_request(payload, sig) is False


class TestEncryption:
    """Test payload encryption and decryption."""

    def test_encrypt_decrypt_roundtrip(self):
        sender = DelegationSecurity(agent_id="sender")
        recipient = DelegationSecurity(agent_id="recipient")

        payload = {"task": "secret-analysis", "data": [1, 2, 3]}
        encrypted = sender.encrypt_payload(payload, recipient.encryption_public_key_hex)
        decrypted = recipient.decrypt_payload(encrypted, sender.encryption_public_key_hex)

        assert decrypted == payload

    def test_encrypted_is_bytes(self):
        sender = DelegationSecurity(agent_id="sender")
        recipient = DelegationSecurity(agent_id="recipient")
        encrypted = sender.encrypt_payload({"key": "value"}, recipient.encryption_public_key_hex)
        assert isinstance(encrypted, bytes)
        assert len(encrypted) > 0

    def test_decrypt_wrong_key_fails(self):
        sender = DelegationSecurity(agent_id="sender")
        recipient = DelegationSecurity(agent_id="recipient")
        eavesdropper = DelegationSecurity(agent_id="eve")

        encrypted = sender.encrypt_payload({"secret": True}, recipient.encryption_public_key_hex)
        with pytest.raises(Exception):
            eavesdropper.decrypt_payload(encrypted, sender.encryption_public_key_hex)

    def test_encrypt_complex_payload(self):
        sender = DelegationSecurity(agent_id="s")
        recipient = DelegationSecurity(agent_id="r")

        payload = {
            "nested": {"deep": {"value": 42}},
            "list": [1, "two", 3.0],
            "unicode": "مرحبا",
        }
        encrypted = sender.encrypt_payload(payload, recipient.encryption_public_key_hex)
        decrypted = recipient.decrypt_payload(encrypted, sender.encryption_public_key_hex)
        assert decrypted == payload

    def test_encryption_different_each_time(self):
        sender = DelegationSecurity(agent_id="s")
        recipient = DelegationSecurity(agent_id="r")

        payload = {"same": "payload"}
        enc1 = sender.encrypt_payload(payload, recipient.encryption_public_key_hex)
        enc2 = sender.encrypt_payload(payload, recipient.encryption_public_key_hex)
        # Different nonce each time
        assert enc1 != enc2
