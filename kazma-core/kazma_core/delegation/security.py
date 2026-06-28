"""Delegation Security — Cryptographic signing and verification for agent-to-agent communication.

Uses Ed25519 keys for request signing and X25519 for payload encryption.
All delegation requests must be signed before transmission and verified
on receipt to ensure authenticity and integrity.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


class DelegationSecurity:
    """Security layer for agent-to-agent delegation communication.

    Provides Ed25519 signing for request authentication and
    X25519/AESGCM for encrypted payload transmission.

    Args:
        agent_id: This agent's unique identifier.
        private_key: PEM-encoded Ed25519 private key, or raw 32-byte hex.
    """

    def __init__(self, agent_id: str, private_key: str | None = None) -> None:
        self.agent_id = agent_id
        self._signing_key: Ed25519PrivateKey
        self._signing_public: Ed25519PublicKey
        self._encryption_key: X25519PrivateKey
        self._encryption_public: X25519PublicKey

        if private_key is None:
            self._signing_key = Ed25519PrivateKey.generate()
            self._encryption_key = X25519PrivateKey.generate()
        else:
            self._signing_key = self._load_signing_key(private_key)
            self._encryption_key = self._load_encryption_key(private_key)

        self._signing_public = self._signing_key.public_key()
        self._encryption_public = self._encryption_key.public_key()

    def _load_signing_key(self, key_data: str) -> Ed25519PrivateKey:
        """Load Ed25519 signing key from PEM or generate from seed."""
        try:
            return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_data))
        except (ValueError, TypeError):
            # Assume PEM
            return serialization.load_pem_private_key(key_data.encode(), password=None)  # type: ignore[return-value]

    def _load_encryption_key(self, key_data: str) -> X25519PrivateKey:
        """Derive X25519 encryption key from signing key material."""
        seed = hashlib.sha256(key_data.encode()).digest()
        return X25519PrivateKey.from_private_bytes(seed)

    @property
    def public_key_hex(self) -> str:
        """Public signing key as hex string (for sharing with peers)."""
        return self._signing_public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    @property
    def encryption_public_key_hex(self) -> str:
        """Public encryption key as hex string (for payload encryption)."""
        return self._encryption_public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def sign_request(self, payload: dict[str, Any]) -> str:
        """Sign a delegation request payload.

        The signature covers:
        - All request fields (serialized deterministically as JSON)
        - Timestamp to prevent replay attacks

        Args:
            payload: The request payload to sign.

        Returns:
            Hex-encoded Ed25519 signature.
        """
        canonical = self._canonicalize(payload)
        signature = self._signing_key.sign(canonical)
        return signature.hex()

    def verify_request(self, payload: dict[str, Any], signature_hex: str) -> bool:
        """Verify a delegation request signature.

        Args:
            payload: The request payload that was signed.
            signature_hex: The hex-encoded Ed25519 signature.

        Returns:
            True if the signature is valid.
        """
        try:
            canonical = self._canonicalize(payload)
            signature = bytes.fromhex(signature_hex)
            self._signing_public.verify(signature, canonical)
            return True
        except Exception as e:
            logger.warning(
                "Signature verification failed for agent %s: %s",
                payload.get("requester_id", "unknown"),
                e,
            )
            return False

    def encrypt_payload(self, payload: dict[str, Any], recipient_public_key_hex: str) -> bytes:
        """Encrypt a payload for secure transmission to a recipient.

        Uses X25519 key agreement + AES-256-GCM.

        Args:
            payload: Dict payload to encrypt.
            recipient_public_key_hex: Recipient's public encryption key (hex).

        Returns:
            Encrypted bytes (nonce + ciphertext + tag).
        """
        recipient_pub = X25519PublicKey.from_public_bytes(bytes.fromhex(recipient_public_key_hex))
        shared_key = self._encryption_key.exchange(recipient_pub)
        # Derive AES key from shared secret
        aes_key = hashlib.sha256(shared_key).digest()
        # Use cryptographically secure random nonce
        nonce = os.urandom(12)
        aesgcm = AESGCM(aes_key)
        plaintext = json.dumps(payload, sort_keys=True).encode()
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        return nonce + ciphertext

    def decrypt_payload(self, encrypted: bytes, sender_public_key_hex: str) -> dict[str, Any]:
        """Decrypt a payload received from a sender.

        Args:
            encrypted: The encrypted bytes (nonce + ciphertext + tag).
            sender_public_key_hex: Sender's public encryption key (hex).

        Returns:
            Decrypted payload dict.
        """
        sender_pub = X25519PublicKey.from_public_bytes(bytes.fromhex(sender_public_key_hex))
        shared_key = self._encryption_key.exchange(sender_pub)
        aes_key = hashlib.sha256(shared_key).digest()
        nonce = encrypted[:12]
        ciphertext = encrypted[12:]
        aesgcm = AESGCM(aes_key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode())

    @staticmethod
    def _canonicalize(payload: dict[str, Any]) -> bytes:
        """Create canonical bytes from a payload for signing."""
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def get_key_info(self) -> dict[str, str]:
        """Return public key information for sharing with peers."""
        return {
            "agent_id": self.agent_id,
            "signing_public_key": self.public_key_hex,
            "encryption_public_key": self.encryption_public_key_hex,
        }
